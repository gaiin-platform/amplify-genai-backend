# Copyright (c) 2024 Vanderbilt University
# Layered Assistants Service  CRUD API
#
# Public-ID prefixes:
#   astr/<uuid>   — personal layered assistant (owned by a user)
#   astgr/<uuid>  — group layered assistant (owned by a group system user)
#
# DB-ID prefix:
#   lastr/<uuid>  — internal DynamoDB record id

import os
import time
import uuid
import boto3
from boto3.dynamodb.conditions import Key
from pycommon.logger import getLogger
from pycommon.const import APIAccessType
from pycommon.api.object_permissions import update_object_permissions
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ASSISTANTS.value])

logger = getLogger("layered_assistants")

LAYERED_AST_PERSONAL_PREFIX = "astr"    # personal — e.g. astr/<uuid>
LAYERED_AST_GROUP_PREFIX     = "astgr"  # group-owned — e.g. astgr/<uuid>
LAYERED_AST_DB_PREFIX        = "lastr"  # internal DynamoDB record id


# ── Internal helpers ──────────────────────────────────────────────────────────

def _table():
    return boto3.resource("dynamodb").Table(
        os.environ["LAYERED_ASSISTANTS_DYNAMODB_TABLE"]
    )


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _is_group_caller(current_user: str) -> bool:
    """
    Group system users are identified by their group_id format (e.g. 'GroupName_<uuid>').
    Real users are identified by their email address (contains '@').
    """
    return "@" not in current_user


def _new_public_id(is_group: bool) -> str:
    prefix = LAYERED_AST_GROUP_PREFIX if is_group else LAYERED_AST_PERSONAL_PREFIX
    return f"{prefix}/{str(uuid.uuid4())}"


def _new_db_id() -> str:
    return f"{LAYERED_AST_DB_PREFIX}/{str(uuid.uuid4())}"


def _get_by_public_id(table, public_id):
    """
    Fetch the DynamoDB item whose publicId == public_id via the PublicIdIndex GSI.
    Returns the item dict or None.
    """
    resp = table.query(
        IndexName="PublicIdIndex",
        KeyConditionExpression=Key("publicId").eq(public_id),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _can_access(item, current_user):
    """
    Access check:
    - Personal LAs: the user who created it (their email == createdBy).
    - Group LAs: the group's system user (group_id == createdBy). All CRUD for
      group LAs is routed through groups.py which obtains the group API key and
      calls this service as the group system user, so current_user == group_id == createdBy.
    """
    if not item:
        return False
    return item.get("createdBy") == current_user


def _delete_permissions(object_id, principal_id):
    """Best-effort removal of object-access rows.  Never raises."""
    try:
        tbl = boto3.resource("dynamodb").Table(
            os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]
        )
        tbl.delete_item(Key={"object_id": object_id, "principal_id": principal_id})
    except Exception as exc:
        logger.error(
            "Failed to delete permissions for %s / %s: %s", object_id, principal_id, exc
        )


# ── create_or_update ──────────────────────────────────────────────────────────

@required_env_vars({
    "LAYERED_ASSISTANTS_DYNAMODB_TABLE": [
        DynamoDBOperation.GET_ITEM,
        DynamoDBOperation.PUT_ITEM,
        DynamoDBOperation.QUERY,
    ],
    "OBJECT_ACCESS_DYNAMODB_TABLE": [
        DynamoDBOperation.PUT_ITEM,
        DynamoDBOperation.GET_ITEM,
    ],
})
@validated(op="create_or_update_layered_assistant")
def create_or_update_layered_assistant(event, context, current_user, name, data):
    """
    Create a new Layered Assistant or update an existing one in-place.

    Request body shape  (inside data["data"]):
    {
        "publicId":    "astr/<uuid>" | "astgr/<uuid>",  # omit / empty → create
        "purpose":     "personal" | "group",             # optional; omit defaults to personal
        "name":        "My Router",
        "description": "What this router does",          # optional
        "rootNode":    { ...LayeredAssistantNode tree... }
    }

    When purpose="group", the request comes through the groups proxy and current_user
    is the group system user. The publicId gets 'astgr/' prefix and groupId is stored.
    When purpose="personal" or omitted, publicId gets 'astr/' prefix (personal assistant).

    Response:
    {
        "success": true,
        "message": "...",
        "data": { "publicId": "astr/..." | "astgr/...", "id": "lastr/...", "updatedAt": "..." }
    }
    """
    access = data.get("allowed_access", [])
    if (
        APIAccessType.ASSISTANTS.value not in access
        and APIAccessType.FULL_ACCESS.value not in access
    ):
        return {
            "success": False,
            "message": "API key does not have access to assistant functionality.",
        }

    access_token = data["access_token"]
    payload      = data["data"]
    la_name      = payload["name"]
    description  = payload.get("description", "")
    root_node    = payload["rootNode"]
    public_id    = (payload.get("publicId") or "").strip()
    # Determine if this is a group LA based on explicit purpose field in request
    is_group     = payload.get("purpose", "").lower() == "group"
    table     = _table()
    now       = _now()

    # ── UPDATE existing ───────────────────────────────────────────────
    if public_id:
        existing = _get_by_public_id(table, public_id)
        if not existing:
            return {
                "success": False,
                "message": f"Layered assistant not found: {public_id}",
            }

        if not _can_access(existing, current_user):
            logger.warning(
                "User %s unauthorised update attempt on layered assistant %s (owner: %s)",
                current_user, public_id, existing.get("createdBy"),
            )
            return {
                "success": False,
                "message": "You are not authorized to update this layered assistant.",
            }

        updated_item = {
            **existing,
            "name":        la_name,
            "description": description,
            "rootNode":    root_node,
            "updatedAt":   now,
        }
        table.put_item(Item=updated_item)

        logger.info("Updated layered assistant %s by %s", public_id, current_user)
        return {
            "success": True,
            "message": "Layered assistant updated successfully.",
            "data": {
                "publicId":  public_id,
                "id":        existing["id"],
                "updatedAt": now,
            },
        }

    # ── CREATE new ────────────────────────────────────────────────────
    public_id = _new_public_id(is_group)
    db_id     = _new_db_id()

    new_item = {
        "id":          db_id,
        "publicId":    public_id,
        "createdBy":   current_user,
        "name":        la_name,
        "description": description,
        "rootNode":    root_node,
        "createdAt":   now,
        "updatedAt":   now,
    }

    # For group LAs store the groupId explicitly — useful for phase-3 access checks.
    # When purpose="group", the request comes via groups proxy and current_user is the group system user.
    if is_group:
        new_item["groupId"] = current_user

    table.put_item(Item=new_item)
    logger.info(
        "Created layered assistant %s (%s) for %s (is_group=%s)",
        public_id, db_id, current_user, is_group,
    )

    # Grant owner-level object permissions so the owner can access it via the
    # shared object-access system.
    if not update_object_permissions(
        access_token,
        [current_user],
        [public_id, db_id],
        "layered_assistant",
        "group" if is_group else "user",
        "owner",
    ):
        logger.error(
            "Failed to set object permissions for layered assistant %s", public_id
        )

    return {
        "success": True,
        "message": "Layered assistant created successfully.",
        "data": {
            "publicId":  public_id,
            "id":        db_id,
            "updatedAt": now,
        },
    }


# ── list ──────────────────────────────────────────────────────────────────────

@required_env_vars({
    "LAYERED_ASSISTANTS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY],
})
@validated(op="list_layered_assistants")
def list_layered_assistants(event, context, current_user, name, data):
    """
    Return every Layered Assistant owned by the current user/group.

    When called with a personal token → returns the user's personal LAs.
    When called with a group API key → returns the group's LAs (createdBy = group_id).

    Response:
    {
        "success": true,
        "data": [ { "id": "lastr/...", "publicId": "astr/..." | "astgr/...",
                    "name": "...", "description": "...",
                    "groupId": "...",   # only present for group LAs
                    "rootNode": {...}, "createdAt": "...", "updatedAt": "..." }, ... ]
    }
    """
    access = data.get("allowed_access", [])
    if (
        APIAccessType.ASSISTANTS.value not in access
        and APIAccessType.FULL_ACCESS.value not in access
    ):
        return {
            "success": False,
            "message": "API key does not have access to assistant functionality.",
        }

    table    = _table()
    items    = []
    last_key = None

    while True:
        kwargs = {
            "IndexName": "CreatedByIndex",
            "KeyConditionExpression": Key("createdBy").eq(current_user),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    logger.info("Listed %d layered assistants for %s", len(items), current_user)
    return {
        "success": True,
        "message": "Layered assistants retrieved successfully.",
        "data": items,
    }


# ── delete ────────────────────────────────────────────────────────────────────

@required_env_vars({
    "LAYERED_ASSISTANTS_DYNAMODB_TABLE": [
        DynamoDBOperation.QUERY,
        DynamoDBOperation.DELETE_ITEM,
    ],
    "OBJECT_ACCESS_DYNAMODB_TABLE": [DynamoDBOperation.DELETE_ITEM],
})
@validated(op="delete_layered_assistant")
def delete_layered_assistant(event, context, current_user, name, data):
    """
    Permanently delete a Layered Assistant.

    Request body (inside data["data"]):
    {
        "publicId": "astr/<uuid>" | "astgr/<uuid>"
    }
    """
    access = data.get("allowed_access", [])
    if (
        APIAccessType.ASSISTANTS.value not in access
        and APIAccessType.FULL_ACCESS.value not in access
    ):
        return {
            "success": False,
            "message": "API key does not have access to assistant functionality.",
        }

    public_id = (data["data"].get("publicId") or "").strip()
    if not public_id:
        return {"success": False, "message": "publicId is required."}

    table    = _table()
    existing = _get_by_public_id(table, public_id)

    if not existing:
        return {"success": False, "message": f"Layered assistant not found: {public_id}"}

    if not _can_access(existing, current_user):
        logger.warning(
            "User %s unauthorised delete attempt on layered assistant %s (owner: %s)",
            current_user, public_id, existing.get("createdBy"),
        )
        return {
            "success": False,
            "message": "You are not authorized to delete this layered assistant.",
        }

    db_id = existing["id"]
    table.delete_item(Key={"id": db_id})

    # Clean up both identifiers from object-access table
    _delete_permissions(public_id, current_user)
    _delete_permissions(db_id,     current_user)

    logger.info("Deleted layered assistant %s by %s", public_id, current_user)
    return {"success": True, "message": "Layered assistant deleted successfully."}
