# Copyright (c) 2026 Vanderbilt University
#
# Projects: a first-class workspace (name, description, custom instructions,
# a scoped knowledge base, and scoped memory) that chats can live inside.
#
# v1 scope, deliberately: owner-only (no group/team sharing yet — see
# amplify-assistants' object-permission pattern used by assistants for how
# that would be layered on later), CRUD only. Knowledge-base file scoping
# reuses the existing files service's `knowledgeBase` field (a project's
# `id` doubles as its knowledgeBase key, so `POST /files/query` already
# supports filtering to a project's files with no files-service changes).
# Instructions and approved memory are injected server-side by amplify-lambda-js
# (see amplify-lambda-js/projects/projectContext.js).

from datetime import datetime, timezone
from urllib.parse import urlparse
import os
import uuid

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

logger = getLogger("projects")

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _create_dynamodb_resource():
    """Use DynamoDB Local only when an explicit *loopback* endpoint is configured.

    A non-loopback DYNAMODB_ENDPOINT_URL, or one set without the explicit
    PROJECTS_LOCAL_MODE opt-in, is refused so a stray environment variable can
    never redirect production traffic (or send real credentials) elsewhere.
    """
    endpoint_url = os.getenv("DYNAMODB_ENDPOINT_URL")
    if not endpoint_url:
        return boto3.resource("dynamodb")

    host = (urlparse(endpoint_url).hostname or "").lower()
    if os.getenv("PROJECTS_LOCAL_MODE", "").lower() != "true" or host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            "DYNAMODB_ENDPOINT_URL is only allowed for a loopback host with PROJECTS_LOCAL_MODE=true"
        )
    return boto3.resource(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "local"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "local"),
    )


dynamodb = _create_dynamodb_resource()

PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_ARCHIVED = "archived"
MAX_PROJECTS_PER_USER = 100
NOT_FOUND_MESSAGE = "Project not found."


def _projects_table():
    return dynamodb.Table(os.environ["PROJECTS_DYNAMODB_TABLE"])


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_project_by_id(project_id):
    """Internal helper (also used by project-memory ownership checks)."""
    response = _projects_table().get_item(Key={"id": project_id}, ConsistentRead=True)
    return response.get("Item")


def _is_owner(project, current_user):
    return bool(project) and project.get("createdBy") == current_user


def _query_all(table, **kwargs):
    """Exhaust a DynamoDB query without silently truncating at 1 MB."""
    items = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def _load_owned_project(project_id, current_user, action, require_active=False):
    """Return (project, error_response). Missing and foreign projects are
    indistinguishable to the caller so ids cannot be probed.

    This is the single ownership check for all three project-scoped
    services (projects, project memory, project files) — do not
    reimplement it locally; import and call this one.
    """
    try:
        project = get_project_by_id(project_id)
    except Exception as e:
        logger.error("Failed to load project %s: %s", project_id, e)
        return None, {"success": False, "message": "Failed to load project."}
    if not _is_owner(project, current_user):
        if project:
            logger.warning("User %s attempted to %s project %s they do not own", current_user, action, project_id)
        return None, {"success": False, "message": NOT_FOUND_MESSAGE}
    if require_active and project.get("status") != PROJECT_STATUS_ACTIVE:
        # Matches the prior per-service checks: archived is reported the same
        # as not-owned, not as a distinct error, to keep the existing API
        # contract (no client currently branches on message text here).
        return None, {"success": False, "message": NOT_FOUND_MESSAGE}
    return project, None


@required_env_vars(
    {
        "PROJECTS_DYNAMODB_TABLE": [
            DynamoDBOperation.PUT_ITEM,
            DynamoDBOperation.QUERY,
        ],
    }
)
@validated(op="create")
def create_project(event, context, current_user, name, data):
    """
    Creates a project owned by the current user.
    """
    extracted_data = data["data"]
    project_name = extracted_data["name"].strip()
    if not project_name:
        return {"success": False, "message": "Project name is required."}

    project_id = f"proj/{str(uuid.uuid4())}"
    now = _now_iso()

    item = {
        "id": project_id,
        "createdBy": current_user,
        "name": project_name,
        "description": extracted_data.get("description", ""),
        "instructions": extracted_data.get("instructions", ""),
        # A project's knowledge base is keyed by its own id, so the existing
        # /files/upload + /files/query endpoints work unchanged: files are
        # uploaded with knowledgeBase=<project id> and queried the same way.
        "knowledgeBase": project_id,
        "memoryEnabled": extracted_data.get("memoryEnabled", False),
        # Optional default assistant for new chats (resolved client-side from the
        # user's own assistants, so it is only ever a reference, never trusted).
        "assistantId": extracted_data.get("assistantId", ""),
        "assistantName": extracted_data.get("assistantName", ""),
        "status": PROJECT_STATUS_ACTIVE,
        "createdAt": now,
        "updatedAt": now,
    }

    try:
        # Best-effort limit: this check-then-put is not atomic, so concurrent
        # creates can overshoot slightly. That is acceptable for a soft quota.
        existing = _query_all(
            _projects_table(),
            IndexName="CreatedByIndex",
            KeyConditionExpression=Key("createdBy").eq(current_user),
            ProjectionExpression="id",
        )
        if len(existing) >= MAX_PROJECTS_PER_USER:
            return {"success": False, "message": f"Project limit reached ({MAX_PROJECTS_PER_USER})."}
        _projects_table().put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
    except Exception as e:
        logger.error("Failed to create project: %s", e)
        return {"success": False, "message": "Failed to create project."}

    logger.info("Created project %s for user %s", project_id, current_user)
    return {"success": True, "message": "Project created.", "data": item}


@required_env_vars(
    {
        "PROJECTS_DYNAMODB_TABLE": [
            DynamoDBOperation.QUERY,
        ],
    }
)
@validated(op="list")
def list_projects(event, context, current_user, name, data):
    """
    Lists all projects owned by the current user (at most MAX_PROJECTS_PER_USER),
    most recently created first. Archived projects are included; the frontend
    applies the default "hide archived" filter.
    """
    try:
        items = _query_all(
            _projects_table(),
            IndexName="CreatedByIndex",
            KeyConditionExpression=Key("createdBy").eq(current_user),
            ScanIndexForward=False,
        )
    except Exception as e:
        logger.error("Failed to list projects for %s: %s", current_user, e)
        return {"success": False, "message": "Failed to list projects."}

    return {"success": True, "message": "Projects retrieved.", "data": items}


@required_env_vars(
    {
        "PROJECTS_DYNAMODB_TABLE": [
            DynamoDBOperation.GET_ITEM,
        ],
    }
)
@validated(op="get")
def get_project(event, context, current_user, name, data):
    project_id = data["data"]["id"]
    project, error = _load_owned_project(project_id, current_user, "read")
    if error:
        return error
    return {"success": True, "message": "Project retrieved.", "data": project}


@required_env_vars(
    {
        "PROJECTS_DYNAMODB_TABLE": [
            DynamoDBOperation.GET_ITEM,
            DynamoDBOperation.UPDATE_ITEM,
        ],
    }
)
@validated(op="update")
def update_project(event, context, current_user, name, data):
    extracted_data = data["data"]
    project_id = extracted_data["id"]

    project, error = _load_owned_project(project_id, current_user, "update")
    if error:
        return error

    updatable_fields = ["name", "description", "instructions", "memoryEnabled", "status", "assistantId", "assistantName"]
    update_expression_parts = []
    expression_attribute_names = {}
    expression_attribute_values = {}

    for field in updatable_fields:
        if field in extracted_data:
            value = extracted_data[field]
            if field == "name" and isinstance(value, str):
                value = value.strip()
                if not value:
                    return {"success": False, "message": "Project name cannot be empty."}
            placeholder = f"#{field}"
            value_placeholder = f":{field}"
            update_expression_parts.append(f"{placeholder} = {value_placeholder}")
            expression_attribute_names[placeholder] = field
            expression_attribute_values[value_placeholder] = value

    if not update_expression_parts:
        return {"success": True, "message": "Nothing to update.", "data": project}

    update_expression_parts.append("#updatedAt = :updatedAt")
    expression_attribute_names["#updatedAt"] = "updatedAt"
    expression_attribute_values[":updatedAt"] = _now_iso()

    try:
        response = _projects_table().update_item(
            Key={"id": project_id},
            UpdateExpression="SET " + ", ".join(update_expression_parts),
            ConditionExpression="attribute_exists(id) AND #createdBy = :owner",
            ExpressionAttributeNames={**expression_attribute_names, "#createdBy": "createdBy"},
            ExpressionAttributeValues={**expression_attribute_values, ":owner": current_user},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"success": False, "message": "Project changed or was deleted; reload and try again."}
        logger.error("Failed to update project %s: %s", project_id, e)
        return {"success": False, "message": "Failed to update project."}
    except Exception as e:
        logger.error("Failed to update project %s: %s", project_id, e)
        return {"success": False, "message": "Failed to update project."}

    return {"success": True, "message": "Project updated.", "data": response.get("Attributes")}


def _delete_project_memories(project_id):
    """Best-effort, retried cleanup of a deleted project's memories.

    Runs *after* the project record is gone: memories are only reachable through
    an owned project, so a failure here leaves unreachable rows rather than a
    half-deleted, still-visible project.
    """
    memories_table = dynamodb.Table(os.environ["PROJECT_MEMORIES_DYNAMODB_TABLE"])
    last_error = None
    for _ in range(3):
        try:
            memories = _query_all(
                memories_table,
                IndexName="ProjectIdIndex",
                KeyConditionExpression=Key("projectId").eq(project_id),
                ProjectionExpression="id",
            )
            if not memories:
                return True
            with memories_table.batch_writer() as batch:
                for memory in memories:
                    batch.delete_item(Key={"id": memory["id"]})
        except Exception as e:  # retry, then report
            last_error = e
    if last_error:
        logger.error("Failed to clean up memories for deleted project %s: %s", project_id, last_error)
        return False
    return True


def _delete_project_file_manifest(project_id):
    """Best-effort removal of a deleted project's file manifest rows.

    Only the manifest is removed here; the underlying files are deleted by the
    frontend through the files service (which owns them).
    """
    table_name = os.environ.get("PROJECT_FILES_DYNAMODB_TABLE")
    if not table_name:
        return True
    table = dynamodb.Table(table_name)
    last_error = None
    for _ in range(3):
        try:
            rows = _query_all(
                table,
                KeyConditionExpression=Key("projectId").eq(project_id),
                ProjectionExpression="projectId, fileId",
            )
            if not rows:
                return True
            with table.batch_writer() as batch:
                for row in rows:
                    batch.delete_item(Key={"projectId": row["projectId"], "fileId": row["fileId"]})
        except Exception as e:
            last_error = e
    if last_error:
        logger.error("Failed to clean up file manifest for deleted project %s: %s", project_id, last_error)
        return False
    return True


@required_env_vars(
    {
        "PROJECTS_DYNAMODB_TABLE": [
            DynamoDBOperation.GET_ITEM,
            DynamoDBOperation.DELETE_ITEM,
        ],
        "PROJECT_MEMORIES_DYNAMODB_TABLE": [
            DynamoDBOperation.QUERY,
            DynamoDBOperation.BATCH_WRITE_ITEM,
        ],
        "PROJECT_FILES_DYNAMODB_TABLE": [
            DynamoDBOperation.QUERY,
            DynamoDBOperation.BATCH_WRITE_ITEM,
        ],
    }
)
@validated(op="delete")
def delete_project(event, context, current_user, name, data):
    """
    Deletes the project record, then its memories. Knowledge-base files and
    conversation references belong to other services; the frontend detaches
    chats before calling this and removes files afterwards.
    """
    project_id = data["data"]["id"]

    project, error = _load_owned_project(project_id, current_user, "delete")
    if error:
        return error

    try:
        _projects_table().delete_item(
            Key={"id": project_id},
            ConditionExpression="attribute_exists(id) AND #createdBy = :owner",
            ExpressionAttributeNames={"#createdBy": "createdBy"},
            ExpressionAttributeValues={":owner": current_user},
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"success": False, "message": "Project changed or was already deleted."}
        logger.error("Failed to delete project %s: %s", project_id, e)
        return {"success": False, "message": "Failed to delete project."}
    except Exception as e:
        logger.error("Failed to delete project %s: %s", project_id, e)
        return {"success": False, "message": "Failed to delete project."}

    memories_cleaned = _delete_project_memories(project_id)
    manifest_cleaned = _delete_project_file_manifest(project_id)
    cleaned = memories_cleaned and manifest_cleaned
    return {
        "success": True,
        "message": "Project deleted." if cleaned else "Project deleted; some cleanup will be retried by support.",
        "data": {"memoriesCleaned": memories_cleaned, "fileManifestCleaned": manifest_cleaned},
    }
