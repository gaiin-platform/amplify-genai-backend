"""Admin-only endpoint: point one assistant's saved document ids at the new embeddings (or back).

Called by the RAG migration (amplify-lambda) with the admin's own token for every assistant that stores a document that was
just moved. It updates the assistant's latest version in place, the same way a website rescan does, so no new version, alias
or permission is created and the owner sees no edit. Old versions are left as they are.
"""

import os

import boto3
from boto3.dynamodb.conditions import Key

from pycommon.api.auth_admin import verify_user_as_admin
from pycommon.authz import validated, setup_validated
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.logger import getLogger
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

from service.reference_swap import swap_data_source_ids

setup_validated(rules, get_permission_checker)
logger = getLogger("migrate_assistant_references")

PURPOSE = "Migrate Assistant References"


def latest_version(table, assistant_public_id):
    """The newest saved version of an assistant (the one chat uses), or None."""
    response = table.query(
        IndexName="AssistantIdIndex",
        KeyConditionExpression=Key("assistantId").eq(assistant_public_id),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def migrate_assistant_references(table, assistant_public_id, replacements, admin):
    """Swap ids in the latest version of one assistant. Never overwrites an edit made in the meantime."""
    latest = latest_version(table, assistant_public_id)
    if not latest:
        return {"success": False, "message": "Assistant not found"}
    current = latest.get("dataSources") or []
    updated, swapped = swap_data_source_ids(current, replacements)
    if not swapped:
        return {"success": True, "message": "Nothing to change", "data": {"changed": False, "replaced": 0, "id": latest["id"]}}
    try:
        table.update_item(
            Key={"id": latest["id"]},
            UpdateExpression="SET dataSources = :new",
            ConditionExpression="dataSources = :old",          # the record must still be exactly what we read
            ExpressionAttributeValues={":new": updated, ":old": current},
        )
    except Exception as error:
        if "ConditionalCheckFailed" in str(error):
            return {"success": False, "message": "The assistant changed while it was being updated; try again"}
        raise
    logger.warning(
        "ASSISTANT_REFERENCES admin=%s assistant=%s record=%s replaced=%s %s", admin, assistant_public_id, latest["id"], swapped,
        {old: new for old, new in replacements.items() if any(s.get("id") == new for s in updated)},
    )
    return {"success": True, "message": "Updated", "data": {"changed": True, "replaced": swapped, "id": latest["id"]}}


@required_env_vars({
    "ASSISTANTS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.UPDATE_ITEM],
})
@validated(op="migrate_references")
def migrate_references(event, context, current_user, name, data):
    if not verify_user_as_admin(data["access_token"], PURPOSE):
        return {"success": False, "message": "Unable to authenticate user as admin"}
    request = data["data"]
    table = boto3.resource("dynamodb").Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
    try:
        return migrate_assistant_references(table, request["assistantId"], request["replacements"], current_user)
    except Exception as error:
        logger.error("Could not update the references of %s: %s", request.get("assistantId"), error)
        return {"success": False, "message": f"Could not update the assistant: {error}"}
