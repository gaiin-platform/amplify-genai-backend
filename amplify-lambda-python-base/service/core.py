from pycommon.authz import validated, setup_validated
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.logger import getLogger
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

logger = getLogger("sample_service")

# Initialize validation system (once per module)
setup_validated(rules, get_permission_checker)

# Add API access restrictions if needed:
# from pycommon.authz import add_api_access_types
# from pycommon.const import APIAccessType
# add_api_access_types([APIAccessType.ASSISTANTS.value, ...])


@required_env_vars({
    "SAMPLE_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("sample")
def sample(event, context, current_user, name, data):
    """
    Sample REST endpoint with validation.

    The @validated decorator:
    - Validates request against schema (from schemata/)
    - Checks user permissions (from schemata/)
    - Automatically tracks usage metrics
    - Injects: current_user, name, data parameters

    The @required_env_vars decorator:
    - Declares environment variables and AWS operations
    - Automatically resolves env vars (Lambda env → Parameter Store → Error)
    - Tracks env var usage for auditing
    - Documents IAM requirements
    """
    data = data['data']

    logger.info(f"User {current_user} requested {data}")

    # Your business logic here
    # Environment variables are automatically resolved
    import os
    table_name = os.environ["SAMPLE_DYNAMODB_TABLE"]

    return {
        "success": True,
        "message": "Sample response",
        "data": {"msg": data.get("msg", "Sample response")}
    }
