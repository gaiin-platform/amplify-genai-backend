"""Admin API for the RAG Migration tab (see rag/migration_service.py for the logic)."""

from pycommon.api.auth_admin import verify_user_as_admin
from pycommon.authz import validated, setup_validated
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, SQSOperation
from pycommon.logger import getLogger
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

from rag import migration as m
from rag import migration_service as service

setup_validated(rules, get_permission_checker)
logger = getLogger("rag_migration_api")

PURPOSE = "RAG Migration"
ENV = {
    "RAG_MIGRATION_TABLE": [
        DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM,
        DynamoDBOperation.DELETE_ITEM, DynamoDBOperation.QUERY, DynamoDBOperation.SCAN,
    ],
    "EMBEDDING_PROGRESS_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.SCAN],
    "HASH_FILES_DYNAMO_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.SCAN, DynamoDBOperation.UPDATE_ITEM, DynamoDBOperation.GET_ITEM],
}


def _admin_context(data):
    """(migrator, error). The caller must be an admin."""
    if not verify_user_as_admin(data["access_token"], PURPOSE):
        return None, {"success": False, "error": "Unable to authenticate user as admin"}
    try:
        return service.build_migrator(), None
    except Exception as error:
        logger.error("RAG migration is unavailable: %s", error)
        return None, {"success": False, "error": str(error)}


@required_env_vars(ENV)
@validated("read")
def get_status(event, context, current_user, name, data):
    migrator, error = _admin_context(data)
    if error:
        return error
    return {"success": True, "data": service.status_payload(migrator)}


@required_env_vars(ENV)
@validated("update")
def update_settings(event, context, current_user, name, data):
    migrator, error = _admin_context(data)
    if error:
        return error
    try:
        service.update_settings(migrator, data["data"], current_user)
        return {"success": True}
    except ValueError as invalid:
        return {"success": False, "error": str(invalid)}


@required_env_vars({
    **ENV,
    "OBJECT_ACCESS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.PUT_ITEM],
    "RAG_PROCESS_DOCUMENT_QUEUE_URL": [SQSOperation.SEND_MESSAGE],
})
@validated("action", support_polling=True)    # refresh_inventory can outlast the API gateway's 29 s limit
def run_action(event, context, current_user, name, data):
    migrator, error = _admin_context(data)
    if error:
        return error
    request = data["data"]
    logger.info("RAG migration action %s by %s", request["action"], current_user)
    try:
        result = service.run_action(migrator, request["action"], request, current_user, data["access_token"])
        return {"success": True, "data": m.decimal_safe(result)}       # the polled-request record cannot hold floats
    except (ValueError, RuntimeError) as refused:
        return {"success": False, "error": str(refused)}


# Scheduled entry point (every minute; a lock in the store prevents overlapping runs).
tick = service.tick_handler
