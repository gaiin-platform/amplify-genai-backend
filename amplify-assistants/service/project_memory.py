# Copyright (c) 2026 Vanderbilt University
#
# Project-scoped memory: durable facts extracted from chats inside a project,
# isolated to that project (never leaks into other projects or general chats).
# This is a separate store from the pre-existing global "user" memory feature.
#
# Extraction happens client-side (useChatSendService.ts) and always stores
# suggestions as `pending`; only an explicit edit call from the owner promotes
# a memory to `approved`. Approved memories are injected into prompts
# server-side by amplify-lambda-js (projects/projectContext.js). Ownership is
# inherited from the parent project.

from datetime import datetime, timezone
import os
import uuid

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

from service.projects import dynamodb, get_project_by_id, _load_owned_project

setup_validated(rules, get_permission_checker)

logger = getLogger("project_memory")

MAX_MEMORIES_PER_PROJECT = 500
MEMORY_STATUS_PENDING = "pending"


def _memories_table():
    return dynamodb.Table(os.environ["PROJECT_MEMORIES_DYNAMODB_TABLE"])


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _query_all(project_id):
    items = []
    kwargs = {
        "IndexName": "ProjectIdIndex",
        "KeyConditionExpression": Key("projectId").eq(project_id),
        "ScanIndexForward": False,
    }
    while True:
        response = _memories_table().query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def _normalized_content(content):
    return " ".join(content.casefold().split())


def _new_memory_id():
    # Random id: content is editable, so it cannot be part of the key.
    return f"projmem/{uuid.uuid4()}"


def _load_owned_memory(memory_id, current_user, action):
    """Return (memory, error_response). The memory must exist, belong to the
    caller AND belong to a project the caller still owns."""
    try:
        existing = _memories_table().get_item(Key={"id": memory_id}, ConsistentRead=True).get("Item")
        if existing and existing.get("createdBy") == current_user and not _load_owned_project(
            existing.get("projectId", ""), current_user, action
        )[0]:
            existing = None
    except Exception as e:
        logger.error("Failed to load project memory %s: %s", memory_id, e)
        return None, {"success": False, "message": "Failed to load memory."}
    if not existing or existing.get("createdBy") != current_user:
        if existing:
            logger.warning("User %s attempted to %s memory %s they do not own", current_user, action, memory_id)
        return None, {"success": False, "message": "Memory not found."}
    return existing, None


@required_env_vars(
    {
        "PROJECT_MEMORIES_DYNAMODB_TABLE": [
            DynamoDBOperation.PUT_ITEM,
            DynamoDBOperation.QUERY,
        ],
        "PROJECTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@validated(op="add")
def add_project_memory(event, context, current_user, name, data):
    extracted_data = data["data"]
    project_id = extracted_data["projectId"]
    content = extracted_data["content"].strip()

    if not content:
        return {"success": False, "message": "Memory content is required."}

    try:
        if not _load_owned_project(project_id, current_user, "add memory to", require_active=True)[0]:
            return {"success": False, "message": "Project not found."}

        existing_memories = _query_all(project_id)
        normalized = _normalized_content(content)
        duplicate = next(
            (m for m in existing_memories if _normalized_content(m.get("content", "")) == normalized),
            None,
        )
        if duplicate:
            return {"success": True, "message": "Memory already exists.", "data": duplicate}
        if len(existing_memories) >= MAX_MEMORIES_PER_PROJECT:
            return {"success": False, "message": f"Project memory limit reached ({MAX_MEMORIES_PER_PROJECT})."}

        # Only record ids that are real, existing memories of this same project —
        # never trust the caller's list blindly, since it is resolved against the
        # real project owner's records only when this memory is later approved.
        existing_ids = {m.get("id") for m in existing_memories}
        requested_supersedes = extracted_data.get("supersedesIds") or []
        supersedes_ids = [mid for mid in requested_supersedes if mid in existing_ids]

        now = _now_iso()
        item = {
            "id": _new_memory_id(),
            "projectId": project_id,
            "content": content,
            "sourceConversationId": extracted_data.get("sourceConversationId", ""),
            "createdBy": current_user,
            "createdAt": now,
            "updatedAt": now,
            # Always pending: approval is a separate, explicit owner action
            # (edit_project_memory) so extraction can never self-approve.
            "status": MEMORY_STATUS_PENDING,
            # The records this one corrects/replaces, if any. Left untouched
            # until (and unless) the owner approves THIS memory — see
            # edit_project_memory — so a rejected suggestion never costs the
            # owner an already-approved fact.
            "supersedesIds": supersedes_ids,
        }
        _memories_table().put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
    except Exception as e:
        logger.error("Failed to add project memory: %s", e)
        return {"success": False, "message": "Failed to save memory."}

    return {"success": True, "message": "Memory saved.", "data": item}


@required_env_vars(
    {
        "PROJECT_MEMORIES_DYNAMODB_TABLE": [
            DynamoDBOperation.QUERY,
        ],
        "PROJECTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@validated(op="list")
def list_project_memories(event, context, current_user, name, data):
    project_id = data["data"]["projectId"]

    try:
        if not _load_owned_project(project_id, current_user, "list memories for")[0]:
            return {"success": False, "message": "Project not found."}
        items = _query_all(project_id)
    except Exception as e:
        logger.error("Failed to list project memories for %s: %s", project_id, e)
        return {"success": False, "message": "Failed to list memories."}

    return {"success": True, "message": "Memories retrieved.", "data": items}


@required_env_vars(
    {
        "PROJECT_MEMORIES_DYNAMODB_TABLE": [
            DynamoDBOperation.GET_ITEM,
            DynamoDBOperation.QUERY,
            DynamoDBOperation.UPDATE_ITEM,
            # Approving a memory that supersedes another one retires that old
            # record here -- see the approved-status branch below.
            DynamoDBOperation.DELETE_ITEM,
        ],
        "PROJECTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@validated(op="edit")
def edit_project_memory(event, context, current_user, name, data):
    extracted_data = data["data"]
    memory_id = extracted_data["id"]
    content = extracted_data["content"].strip()
    status = extracted_data.get("status")

    if not content:
        return {"success": False, "message": "Memory content is required."}

    existing, error = _load_owned_memory(memory_id, current_user, "edit")
    if error:
        return error

    try:
        normalized = _normalized_content(content)
        project_memories = _query_all(existing["projectId"])
        if any(
            memory.get("id") != memory_id
            and _normalized_content(memory.get("content", "")) == normalized
            for memory in project_memories
        ):
            return {"success": False, "message": "An identical project memory already exists."}

        update_parts = ["#content = :content", "#updatedAt = :updatedAt"]
        names = {"#content": "content", "#updatedAt": "updatedAt", "#createdBy": "createdBy"}
        values = {":content": content, ":updatedAt": _now_iso(), ":owner": current_user}
        if status is not None:
            update_parts.append("#status = :status")
            names["#status"] = "status"
            values[":status"] = status
        response = _memories_table().update_item(
            Key={"id": memory_id},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(id) AND #createdBy = :owner",
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"success": False, "message": "Memory changed or was deleted; reload and try again."}
        logger.error("Failed to edit project memory %s: %s", memory_id, e)
        return {"success": False, "message": "Failed to update memory."}
    except Exception as e:
        logger.error("Failed to edit project memory %s: %s", memory_id, e)
        return {"success": False, "message": "Failed to update memory."}

    updated = response.get("Attributes") or {}

    # Only now — the owner just explicitly approved this memory — retire the
    # records it corrects/replaces. A rejected or still-pending suggestion
    # never touches them, so declining "Justin" can never cost you "Harsha".
    if status == "approved":
        for superseded_id in existing.get("supersedesIds") or []:
            try:
                old_record, lookup_error = _load_owned_memory(superseded_id, current_user, "retire")
                if lookup_error or not old_record:
                    continue
                if old_record["projectId"] != existing["projectId"]:
                    continue
                _memories_table().delete_item(
                    Key={"id": superseded_id},
                    ConditionExpression="attribute_exists(id) AND createdBy = :owner",
                    ExpressionAttributeValues={":owner": current_user},
                )
            except Exception as e:
                logger.error("Failed to retire superseded memory %s: %s", superseded_id, e)

    return {"success": True, "message": "Memory updated.", "data": updated}


@required_env_vars(
    {
        "PROJECT_MEMORIES_DYNAMODB_TABLE": [
            DynamoDBOperation.GET_ITEM,
            DynamoDBOperation.DELETE_ITEM,
        ],
        "PROJECTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@validated(op="delete")
def delete_project_memory(event, context, current_user, name, data):
    memory_id = data["data"]["id"]

    existing, error = _load_owned_memory(memory_id, current_user, "delete")
    if error:
        return error

    try:
        _memories_table().delete_item(
            Key={"id": memory_id},
            ConditionExpression="#createdBy = :owner",
            ExpressionAttributeNames={"#createdBy": "createdBy"},
            ExpressionAttributeValues={":owner": current_user},
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"success": False, "message": "Memory changed or was already deleted."}
        logger.error("Failed to delete project memory %s: %s", memory_id, e)
        return {"success": False, "message": "Failed to delete memory."}
    except Exception as e:
        logger.error("Failed to delete project memory %s: %s", memory_id, e)
        return {"success": False, "message": "Failed to delete memory."}

    return {"success": True, "message": "Memory deleted."}
