# Copyright (c) 2026 Vanderbilt University
#
# Project file manifest: the list of knowledge-base files that belong to a
# project. The files themselves are uploaded, processed and deleted through the
# existing files service, untouched; this table only records *which* of the
# user's files a project uses, plus a status and token count for display.
#
# Why a manifest instead of filtering the files table by `knowledgeBase`:
#   * a chat needs the project's files on every message — a keyed Query on this
#     table replaces a filtered scan over all of the user's files;
#   * amplify-lambda-js reads it to attach the files server-side, so a slow or
#     failed browser lookup can no longer silently drop the project's knowledge;
#   * the per-project file limit is enforced here, not just in the UI.
#
# Ownership is inherited from the parent project. A file may only be registered
# if its key sits under the caller's own prefix (keys are <user>/<date>/<uuid>).

from datetime import datetime, timezone
import os

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

logger = getLogger("project_files")

MAX_FILES_PER_PROJECT = 100
FILE_STATUS_PROCESSING = "processing"


def _files_table():
    return dynamodb.Table(os.environ["PROJECT_FILES_DYNAMODB_TABLE"])


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _query_all(project_id, **extra):
    items = []
    kwargs = {"KeyConditionExpression": Key("projectId").eq(project_id), **extra}
    while True:
        response = _files_table().query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


@required_env_vars(
    {
        "PROJECT_FILES_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.QUERY],
        "PROJECTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@validated(op="add")
def add_project_file(event, context, current_user, name, data):
    extracted = data["data"]
    project_id = extracted["projectId"]
    file_id = extracted["fileId"]

    # Keys are issued by the files service as <owner>/<date>/<uuid>.json, so a
    # caller can only register files under their own prefix.
    if not file_id.startswith(f"{current_user}/") or ".." in file_id:
        logger.warning("User %s tried to register foreign file key %s", current_user, file_id)
        return {"success": False, "message": "Invalid file."}

    try:
        project, error = _load_owned_project(project_id, current_user, "add a file to", require_active=True)
        if error:
            return error

        existing = _query_all(project_id, ProjectionExpression="fileId")
        if any(item["fileId"] == file_id for item in existing):
            current = _files_table().get_item(Key={"projectId": project_id, "fileId": file_id}, ConsistentRead=True).get("Item")
            return {"success": True, "message": "File already added.", "data": current}
        if len(existing) >= MAX_FILES_PER_PROJECT:
            return {"success": False, "message": f"A project can contain at most {MAX_FILES_PER_PROJECT} files."}

        now = _now_iso()
        item = {
            "projectId": project_id,
            "fileId": file_id,
            "name": extracted["name"],
            "type": extracted.get("type", ""),
            "status": FILE_STATUS_PROCESSING,
            "totalTokens": 0,
            "addedBy": current_user,
            "addedAt": now,
            "updatedAt": now,
        }
        _files_table().put_item(Item=item, ConditionExpression="attribute_not_exists(fileId)")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"success": True, "message": "File already added."}
        logger.error("Failed to add project file: %s", e)
        return {"success": False, "message": "Failed to add file."}
    except Exception as e:
        logger.error("Failed to add project file: %s", e)
        return {"success": False, "message": "Failed to add file."}

    return {"success": True, "message": "File added.", "data": item}


@required_env_vars(
    {
        "PROJECT_FILES_DYNAMODB_TABLE": [DynamoDBOperation.QUERY],
        "PROJECTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@validated(op="list")
def list_project_files(event, context, current_user, name, data):
    project_id = data["data"]["projectId"]
    try:
        _, error = _load_owned_project(project_id, current_user, "list files for")
        if error:
            return error
        items = sorted(_query_all(project_id, ConsistentRead=True), key=lambda i: i.get("addedAt", ""))
    except Exception as e:
        logger.error("Failed to list project files for %s: %s", project_id, e)
        return {"success": False, "message": "Failed to list files."}
    return {"success": True, "message": "Files retrieved.", "data": items}


@required_env_vars(
    {
        "PROJECT_FILES_DYNAMODB_TABLE": [DynamoDBOperation.UPDATE_ITEM],
        "PROJECTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@validated(op="update")
def update_project_file(event, context, current_user, name, data):
    extracted = data["data"]
    project_id = extracted["projectId"]
    file_id = extracted["fileId"]

    sets = ["#updatedAt = :updatedAt"]
    names = {"#updatedAt": "updatedAt", "#addedBy": "addedBy"}
    values = {":updatedAt": _now_iso(), ":owner": current_user}
    if "status" in extracted:
        sets.append("#status = :status")
        names["#status"] = "status"
        values[":status"] = extracted["status"]
    if "totalTokens" in extracted:
        sets.append("#totalTokens = :totalTokens")
        names["#totalTokens"] = "totalTokens"
        values[":totalTokens"] = extracted["totalTokens"]

    try:
        _, error = _load_owned_project(project_id, current_user, "update a file in")
        if error:
            return error
        response = _files_table().update_item(
            Key={"projectId": project_id, "fileId": file_id},
            UpdateExpression="SET " + ", ".join(sets),
            ConditionExpression="attribute_exists(fileId) AND #addedBy = :owner",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"success": False, "message": "File not found."}
        logger.error("Failed to update project file %s: %s", file_id, e)
        return {"success": False, "message": "Failed to update file."}
    except Exception as e:
        logger.error("Failed to update project file %s: %s", file_id, e)
        return {"success": False, "message": "Failed to update file."}

    return {"success": True, "message": "File updated.", "data": response.get("Attributes")}


@required_env_vars(
    {
        "PROJECT_FILES_DYNAMODB_TABLE": [DynamoDBOperation.DELETE_ITEM],
        "PROJECTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@validated(op="remove")
def remove_project_file(event, context, current_user, name, data):
    extracted = data["data"]
    project_id = extracted["projectId"]
    file_id = extracted["fileId"]
    try:
        _, error = _load_owned_project(project_id, current_user, "remove a file from")
        if error:
            return error
        # Idempotent: removing an entry that is already gone is a success.
        _files_table().delete_item(Key={"projectId": project_id, "fileId": file_id})
    except Exception as e:
        logger.error("Failed to remove project file %s: %s", file_id, e)
        return {"success": False, "message": "Failed to remove file."}
    return {"success": True, "message": "File removed."}
