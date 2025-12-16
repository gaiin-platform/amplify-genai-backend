"""
Admin Web Search Configuration

Manages admin-level API keys for web search tools.
Keys are stored server-side in SSM Parameter Store for all users to share.
"""

from datetime import datetime, timezone
import json
import os
import traceback

import boto3
from botocore.exceptions import ClientError

from pycommon.api.auth_admin import verify_user_as_admin
from pycommon.api.secrets import store_secret_parameter, get_secret_parameter
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation, SSMOperation
)
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata import permissions
from pycommon.api.ops import api_tool, set_permissions_by_state
from pycommon.logger import getLogger
from pycommon.api.critical_logging import log_critical_error, SEVERITY_HIGH

setup_validated(rules, permissions.get_permission_checker)
set_permissions_by_state(permissions)

logger = getLogger("admin_web_search")

# Supported web search providers
SUPPORTED_PROVIDERS = ["brave_search", "tavily", "serper", "serpapi"]

# SSM parameter path prefix for web search keys
WEB_SEARCH_SSM_PREFIX = "/tools/web_search"


def build_web_search_parameter_name(provider: str) -> str:
    """Build the SSM parameter name for a web search provider"""
    stage = os.environ.get("INTEGRATION_STAGE", os.environ.get("STAGE", "dev"))
    return f"{WEB_SEARCH_SSM_PREFIX}/{provider}/{stage}"


def mask_api_key(key: str) -> str:
    """Mask an API key for display, showing only first 4 and last 4 chars"""
    if not key or len(key) < 12:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


@api_tool(
    path="/integrations/web-search/admin/config",
    name="getAdminWebSearchConfig",
    method="GET",
    tags=["WebSearch"],
    description="Get the current admin web search configuration",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "data": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "isEnabled": {"type": "boolean"},
                    "maskedKey": {"type": "string"},
                    "lastUpdated": {"type": "string"},
                },
            },
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "INTEGRATION_STAGE": [SSMOperation.GET_PARAMETER],
})
@validated("get_admin_web_search_config")
def get_admin_web_search_config(event, context, current_user, name, data):
    """Get the current admin web search configuration"""

    dynamodb = boto3.resource("dynamodb")
    admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])

    try:
        response = admin_table.get_item(Key={"config_id": "web_search_config"})

        if "Item" not in response:
            return {"success": True, "data": None}

        config = response["Item"].get("data", {})
        provider = config.get("provider")

        if not provider:
            return {"success": True, "data": None}

        # Try to get the actual key to verify it exists
        ssm = boto3.client("ssm")
        param_name = build_web_search_parameter_name(provider)

        try:
            ssm_response = ssm.get_parameter(Name=param_name, WithDecryption=True)
            api_key = ssm_response["Parameter"]["Value"]

            return {
                "success": True,
                "data": {
                    "provider": provider,
                    "isEnabled": True,
                    "maskedKey": mask_api_key(api_key),
                    "lastUpdated": config.get("lastUpdated"),
                },
            }
        except ssm.exceptions.ParameterNotFound:
            # Config exists but key was deleted from SSM
            return {"success": True, "data": None}

    except Exception as e:
        logger.error("Error getting admin web search config: %s", str(e))
        return {"success": False, "message": f"Error retrieving config: {str(e)}"}


@required_env_vars({
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
    "INTEGRATION_STAGE": [SSMOperation.PUT_PARAMETER],
})
@validated("register_admin_web_search_key")
def register_admin_web_search_key(event, context, current_user, name, data):
    """Register or update an admin web search API key"""

    # Verify user is admin
    access_token = data.get("access_token")
    if not verify_user_as_admin(access_token, "Register Admin Web Search Key"):
        return {"success": False, "error": "Unauthorized: Admin access required"}

    request_data = data.get("data", {})
    provider = request_data.get("provider")
    api_key = request_data.get("api_key")

    # Validate provider
    if provider not in SUPPORTED_PROVIDERS:
        return {
            "success": False,
            "error": f"Unsupported provider: {provider}. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        }

    # Validate API key
    if not api_key or len(api_key.strip()) < 10:
        return {"success": False, "error": "Invalid API key"}

    try:
        # Store API key in SSM Parameter Store
        param_name = build_web_search_parameter_name(provider)

        ssm = boto3.client("ssm")
        ssm.put_parameter(
            Name=param_name,
            Value=api_key.strip(),
            Type="SecureString",
            Overwrite=True,
            Description=f"Admin web search API key for {provider}"
        )

        logger.info("Stored web search API key in SSM: %s", param_name)

        # Store config in DynamoDB admin table
        dynamodb = boto3.resource("dynamodb")
        admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])

        timestamp = datetime.now(timezone.utc).isoformat()

        admin_table.put_item(
            Item={
                "config_id": "web_search_config",
                "data": {
                    "provider": provider,
                    "lastUpdated": timestamp,
                    "updatedBy": current_user,
                },
            }
        )

        logger.info("Updated web search config in admin table for provider: %s", provider)

        return {"success": True}

    except ClientError as e:
        logger.error("Error storing web search API key: %s", str(e))

        log_critical_error(
            function_name="register_admin_web_search_key",
            error_type="WebSearchKeyStorageFailure",
            error_message=f"Failed to store web search API key: {str(e)}",
            current_user=current_user,
            severity=SEVERITY_HIGH,
            stack_trace=traceback.format_exc(),
            context={"provider": provider}
        )

        return {"success": False, "error": "Failed to store API key"}
    except Exception as e:
        logger.error("Unexpected error registering web search key: %s", str(e))
        return {"success": False, "error": f"Unexpected error: {str(e)}"}


@required_env_vars({
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.DELETE_ITEM],
    "INTEGRATION_STAGE": [SSMOperation.DELETE_PARAMETER],
})
@validated("delete_admin_web_search_key")
def delete_admin_web_search_key(event, context, current_user, name, data):
    """Delete the admin web search API key"""

    # Verify user is admin
    access_token = data.get("access_token")
    if not verify_user_as_admin(access_token, "Delete Admin Web Search Key"):
        return {"success": False, "error": "Unauthorized: Admin access required"}

    request_data = data.get("data", {})
    provider = request_data.get("provider")

    if not provider:
        return {"success": False, "error": "Provider is required"}

    try:
        # Delete from SSM Parameter Store
        param_name = build_web_search_parameter_name(provider)

        ssm = boto3.client("ssm")
        try:
            ssm.delete_parameter(Name=param_name)
            logger.info("Deleted web search API key from SSM: %s", param_name)
        except ssm.exceptions.ParameterNotFound:
            logger.warning("Web search API key not found in SSM: %s", param_name)

        # Delete config from DynamoDB admin table
        dynamodb = boto3.resource("dynamodb")
        admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])

        admin_table.delete_item(Key={"config_id": "web_search_config"})

        logger.info("Deleted web search config from admin table")

        return {"success": True}

    except Exception as e:
        logger.error("Error deleting web search API key: %s", str(e))
        return {"success": False, "error": f"Failed to delete API key: {str(e)}"}


@validated("test_admin_web_search_key")
def test_admin_web_search_key(event, context, current_user, name, data):
    """Test an admin web search API key by making a simple query"""

    import requests

    request_data = data.get("data", {})
    provider = request_data.get("provider")
    api_key = request_data.get("api_key")

    if provider not in SUPPORTED_PROVIDERS:
        return {"success": False, "error": f"Unsupported provider: {provider}"}

    if not api_key:
        return {"success": False, "error": "API key is required"}

    test_query = "test"

    try:
        if provider == "brave_search":
            response = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": test_query, "count": 1},
                headers={"X-Subscription-Token": api_key},
                timeout=10
            )
            if response.status_code == 200:
                return {"success": True}
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid API key"}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        elif provider == "tavily":
            response = requests.post(
                "https://api.tavily.com/search",
                json={"query": test_query, "api_key": api_key, "max_results": 1},
                timeout=10
            )
            if response.status_code == 200:
                return {"success": True}
            elif response.status_code == 401 or response.status_code == 403:
                return {"success": False, "error": "Invalid API key"}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        elif provider == "serper":
            response = requests.post(
                "https://google.serper.dev/search",
                json={"q": test_query, "num": 1},
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                timeout=10
            )
            if response.status_code == 200:
                return {"success": True}
            elif response.status_code == 401 or response.status_code == 403:
                return {"success": False, "error": "Invalid API key"}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        elif provider == "serpapi":
            response = requests.get(
                "https://serpapi.com/search",
                params={"q": test_query, "api_key": api_key, "engine": "google", "num": 1},
                timeout=10
            )
            if response.status_code == 200:
                return {"success": True}
            elif response.status_code == 401 or response.status_code == 403:
                return {"success": False, "error": "Invalid API key"}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        return {"success": False, "error": "Unknown provider"}

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timed out"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"Request failed: {str(e)}"}
    except Exception as e:
        logger.error("Error testing web search key: %s", str(e))
        return {"success": False, "error": f"Test failed: {str(e)}"}


def get_admin_web_search_api_key() -> dict:
    """
    Get the admin-configured web search API key.
    Returns dict with 'provider' and 'api_key' if configured, None otherwise.
    This function is used by the web search tool to get the shared API key.
    """

    dynamodb = boto3.resource("dynamodb")
    admin_table_name = os.environ.get("AMPLIFY_ADMIN_DYNAMODB_TABLE")

    if not admin_table_name:
        return None

    admin_table = dynamodb.Table(admin_table_name)

    try:
        response = admin_table.get_item(Key={"config_id": "web_search_config"})

        if "Item" not in response:
            return None

        config = response["Item"].get("data", {})
        provider = config.get("provider")

        if not provider:
            return None

        # Get the API key from SSM
        ssm = boto3.client("ssm")
        param_name = build_web_search_parameter_name(provider)

        try:
            ssm_response = ssm.get_parameter(Name=param_name, WithDecryption=True)
            api_key = ssm_response["Parameter"]["Value"]

            return {
                "provider": provider,
                "api_key": api_key
            }
        except ssm.exceptions.ParameterNotFound:
            logger.warning("Web search API key not found in SSM: %s", param_name)
            return None

    except Exception as e:
        logger.error("Error getting admin web search API key: %s", str(e))
        return None
