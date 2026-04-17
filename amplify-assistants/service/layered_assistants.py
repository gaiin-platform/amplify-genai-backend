# Copyright (c) 2024 Vanderbilt University
# Layered Assistants Service  CRUD API
#
# assistantId prefixes assigned by the backend:
#   astr/<uuid>   — personal layered assistant (owned by a user)
#   astgr/<uuid>  — group layered assistant (owned by a group system user)

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


def _new_assistant_id(is_group: bool) -> str:
    prefix = LAYERED_AST_GROUP_PREFIX if is_group else LAYERED_AST_PERSONAL_PREFIX
    return f"{prefix}/{str(uuid.uuid4())}"


def _get_by_id(table, assistant_id):
    """
    Fetch the DynamoDB item by assistantId (primary key).
    Returns the item dict or None.
    """
    resp = table.get_item(Key={"assistantId": assistant_id})
    return resp.get("Item")


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


DATA_FIELDS = ("isPublished", "model", "trackConversations", "supportConvAnalysis", "analysisCategories", "astIcon")


def _hoist_data_fields(item: dict) -> dict:
    """
    Hoist the fields stored inside item["data"] back to the top level of the
    returned item so the frontend receives the same shape it always has.
    item["data"] is preserved so the frontend can also read from it.
    """
    item = dict(item)
    nested = item.get("data") or {}
    for field in DATA_FIELDS:
        if field in nested:
            item[field] = nested[field]
    return item


def _enrich_with_ast_paths(items):
    """
    For each LA in `items`, look up the ASSISTANT_LOOKUP_DYNAMODB_TABLE for a
    row where assistantId == la.assistantId and attach the first found astPath.
    Returns a new list of dicts — originals are not mutated.
    """
    lookup_table_name = os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE")
    if not lookup_table_name:
        return items  # env var not set — skip silently

    try:
        from boto3.dynamodb.conditions import Key as _Key
        lookup_table = boto3.resource("dynamodb").Table(lookup_table_name)
        enriched = []
        for item in items:
            assistant_id = item.get("assistantId")
            enriched_item = dict(item)
            if assistant_id:
                try:
                    resp = lookup_table.query(
                        IndexName="AssistantIdIndex",
                        KeyConditionExpression=_Key("assistantId").eq(assistant_id),
                        Limit=1,
                    )
                    path_items = resp.get("Items", [])
                    if path_items:
                        enriched_item["astPath"] = path_items[0].get("astPath")
                        enriched_item["astPathData"] = {
                            "isPublic": path_items[0].get("public", True),
                            "accessTo": path_items[0].get("accessTo", {"amplifyGroups": [], "users": []}),
                        }
                except Exception as inner_exc:
                    logger.warning(
                        "Could not enrich LA %s with astPath: %s", assistant_id, inner_exc
                    )
            enriched.append(enriched_item)
        return enriched
    except Exception as exc:
        logger.error("_enrich_with_ast_paths failed: %s", exc)
        return items  # degrade gracefully


def _release_layered_ast_paths(assistant_id, current_user):
    """
    Best-effort release of all standalone-path lookup entries that point to this LA.
    Uses the AssistantIdIndex GSI on ASSISTANT_LOOKUP_DYNAMODB_TABLE.
    Never raises — a failure here must not block the delete.
    """
    try:
        from boto3.dynamodb.conditions import Key as _Key
        from datetime import datetime as _dt

        lookup_table = boto3.resource("dynamodb").Table(
            os.environ["ASSISTANT_LOOKUP_DYNAMODB_TABLE"]
        )
        resp = lookup_table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=_Key("assistantId").eq(assistant_id),
        )
        for item in resp.get("Items", []):
            ast_path = item["astPath"]
            path_history = item.get("pathHistory", [])
            path_history.append({
                "path":         ast_path,
                "assistant_id": None,
                "changedAt":    _dt.now().isoformat(),
                "changedBy":    current_user,
            })
            lookup_table.update_item(
                Key={"astPath": ast_path},
                UpdateExpression="REMOVE assistantId SET pathHistory = :history",
                ExpressionAttributeValues={":history": path_history},
            )
            logger.info(
                "Released path '%s' from deleted layered assistant %s", ast_path, assistant_id
            )
    except Exception as exc:
        logger.error(
            "Failed to release paths for layered assistant %s: %s", assistant_id, exc
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
        "assistantId": "astr/<uuid>" | "astgr/<uuid>",  # omit / empty → create
        "purpose":     "personal" | "group",             # optional; omit defaults to personal
        "name":        "My Router",
        "description": "What this router does",          # optional
        "rootNode":    { ...LayeredAssistantNode tree... }
    }

    When purpose="group", the request comes through the groups proxy and current_user
    is the group system user. The assistantId gets 'astgr/' prefix and groupId is stored.
    When purpose="personal" or omitted, assistantId gets 'astr/' prefix (personal assistant).

    Response:
    {
        "success": true,
        "message": "...",
        "data": { "assistantId": "astr/..." | "astgr/...", "updatedAt": "..." }
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
    assistant_id = (payload.get("assistantId") or "").strip()
    # Determine if this is a group LA based on explicit purpose field in request
    is_group     = payload.get("purpose", "").lower() == "group"
    table     = _table()
    now       = _now()

    # ── UPDATE existing ───────────────────────────────────────────────
    if assistant_id:
        existing = _get_by_id(table, assistant_id)
        if not existing:
            return {
                "success": False,
                "message": f"Layered assistant not found: {assistant_id}",
            }

        if not _can_access(existing, current_user):
            logger.warning(
                "User %s unauthorised update attempt on layered assistant %s (owner: %s)",
                current_user, assistant_id, existing.get("createdBy"),
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

        # Persist optional config fields inside the nested `data` map
        existing_data = dict(existing.get("data") or {})
        payload_data  = payload.get("data") or {}
        for field in DATA_FIELDS:
            # Accept values sent either at payload top-level (legacy) or inside payload["data"]
            if field in payload_data:
                existing_data[field] = payload_data[field]
            elif field in payload:
                existing_data[field] = payload[field]
            else:
                # Field explicitly omitted — clear it
                existing_data.pop(field, None)
        updated_item["data"] = existing_data
        # Remove any old top-level copies of these fields
        for field in DATA_FIELDS:
            updated_item.pop(field, None)

        table.put_item(Item=updated_item)

        logger.info("Updated layered assistant %s by %s", assistant_id, current_user)
        return {
            "success": True,
            "message": "Layered assistant updated successfully.",
            "data": {
                "assistantId": assistant_id,
                "updatedAt":   now,
            },
        }

    # ── CREATE new ────────────────────────────────────────────────────
    assistant_id = _new_assistant_id(is_group)

    payload_data = payload.get("data") or {}
    item_data = {}
    for field in DATA_FIELDS:
        # Accept values sent either at payload top-level (legacy) or inside payload["data"]
        if field in payload_data:
            item_data[field] = payload_data[field]
        elif field in payload:
            item_data[field] = payload[field]

    new_item = {
        "assistantId": assistant_id,
        "createdBy":   current_user,
        "name":        la_name,
        "description": description,
        "rootNode":    root_node,
        "createdAt":   now,
        "updatedAt":   now,
        "data":        item_data,
    }

    # For group LAs store the groupId explicitly — useful for phase-3 access checks.
    if is_group:
        new_item["groupId"] = current_user

    table.put_item(Item=new_item)
    logger.info(
        "Created layered assistant %s for %s (is_group=%s)",
        assistant_id, current_user, is_group,
    )

    # Grant owner-level object permissions so the owner can access it via the
    # shared object-access system.
    if not update_object_permissions(
        access_token,
        [current_user],
        [assistant_id],
        "layered_assistant",
        "group" if is_group else "user",
        "owner",
    ):
        logger.error(
            "Failed to set object permissions for layered assistant %s", assistant_id
        )

    return {
        "success": True,
        "message": "Layered assistant created successfully.",
        "data": {
            "assistantId": assistant_id,
            "updatedAt":   now,
        },
    }


# ── list ──────────────────────────────────────────────────────────────────────

@required_env_vars({
    "LAYERED_ASSISTANTS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY],
    "ASSISTANT_LOOKUP_DYNAMODB_TABLE": [DynamoDBOperation.QUERY],
})
@validated(op="list_layered_assistants")
def list_layered_assistants(event, context, current_user, name, data):
    """
    Return every Layered Assistant owned by the current user/group.

    Response:
    {
        "success": true,
        "data": [ { "assistantId": "astr/..." | "astgr/...",
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

    # Hoist nested data fields back to top-level for frontend compatibility
    items = [_hoist_data_fields(item) for item in items]

    # Enrich each LA with its published astPath (if any)
    enriched = _enrich_with_ast_paths(items)

    return {
        "success": True,
        "message": "Layered assistants retrieved successfully.",
        "data": enriched,
    }


# ── delete ────────────────────────────────────────────────────────────────────

@required_env_vars({
    "LAYERED_ASSISTANTS_DYNAMODB_TABLE": [
        DynamoDBOperation.GET_ITEM,
        DynamoDBOperation.DELETE_ITEM,
    ],
    "OBJECT_ACCESS_DYNAMODB_TABLE": [DynamoDBOperation.DELETE_ITEM],
    "ASSISTANT_LOOKUP_DYNAMODB_TABLE": [
        DynamoDBOperation.QUERY,
        DynamoDBOperation.GET_ITEM,
        DynamoDBOperation.UPDATE_ITEM,
    ],
})
@validated(op="delete_layered_assistant")
def delete_layered_assistant(event, context, current_user, name, data):
    """
    Permanently delete a Layered Assistant.

    Request body (inside data["data"]):
    {
        "assistantId": "astr/<uuid>" | "astgr/<uuid>"
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

    assistant_id = (data["data"].get("assistantId") or "").strip()
    if not assistant_id:
        return {"success": False, "message": "assistantId is required."}

    table    = _table()
    existing = _get_by_id(table, assistant_id)

    if not existing:
        return {"success": False, "message": f"Layered assistant not found: {assistant_id}"}

    if not _can_access(existing, current_user):
        logger.warning(
            "User %s unauthorised delete attempt on layered assistant %s (owner: %s)",
            current_user, assistant_id, existing.get("createdBy"),
        )
        return {
            "success": False,
            "message": "You are not authorized to delete this layered assistant.",
        }

    table.delete_item(Key={"assistantId": assistant_id})

    # Release any published standalone paths pointing to this LA
    _release_layered_ast_paths(assistant_id, current_user)

    # Clean up from object-access table
    _delete_permissions(assistant_id, current_user)

    logger.info("Deleted layered assistant %s by %s", assistant_id, current_user)
    return {"success": True, "message": "Layered assistant deleted successfully."}
