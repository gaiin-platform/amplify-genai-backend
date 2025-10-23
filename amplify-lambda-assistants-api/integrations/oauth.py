from datetime import datetime, timedelta, timezone
import json
import os
import time
import uuid
from msal import ConfidentialClientApplication
from google_auth_oauthlib.flow import Flow
from enum import Enum

import requests
from pycommon.api.secrets import store_secret_parameter
import boto3
from botocore.exceptions import ClientError
from integrations.scopes import scopes
from integrations.oauth_encryption import (
    decrypt_oauth_data,
    encrypt_oauth_data,
    verify_oauth_encryption_parameter,
)
from pycommon.api.auth_admin import verify_user_as_admin

from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation, SSMOperation
)
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata import permissions

setup_validated(rules, permissions.get_permission_checker)
from pycommon.api.ops import api_tool, set_permissions_by_state

set_permissions_by_state(permissions)

from pycommon.logger import getLogger
logger = getLogger("oauth")

# Define a custom error for missing credentials
class MissingCredentialsError(Exception):
    pass


################## Handle different oauth providers #####################


class IntegrationType(Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"


def provider_case(integration):
    for provider in IntegrationType:
        if integration.startswith(provider.value):
            return provider
    raise ValueError(f"Unsupported integration type: {integration}")


def create_oauth_client(integration, client_config, scopes):
    """
    Creates an OAuth client for either Google or Microsoft integrations.
    Returns a tuple of (client, is_google_flow) where is_google_flow is used to determine
    how to handle the client in other functions.
    """
    match provider_case(integration):
        case IntegrationType.GOOGLE:
            flow = Flow.from_client_config(client_config, scopes=scopes)
            redirect_uris = client_config.get("web", {}).get("redirect_uris", [])
            if len(redirect_uris) == 1:
                flow.redirect_uri = redirect_uris[0]
            else:
                flow.redirect_uri = build_redirect_uri()
            return flow, scopes
        case IntegrationType.MICROSOFT:
            client_config = client_config.get("web")
            client_id = client_config.get("client_id")
            client_secret = client_config.get("client_secret")
            authority = (
                f"{client_config.get('auth_uri')}{client_config.get('tenant_id')}"
            )
            app = ConfidentialClientApplication(
                client_id=client_id,
                client_credential=client_secret,
                authority=authority,
            )
            return app, scopes
    raise ValueError(f"Unsupported integration type: {integration}")


def get_authorization_url_and_state(integration, client, scopes=None):
    """
    Gets authorization URL and state for either Google or Microsoft clients.
    """
    match provider_case(integration):
        case IntegrationType.GOOGLE:
            authorization_url, state = client.authorization_url(prompt="consent")
        case IntegrationType.MICROSOFT:
            state = str(uuid.uuid4())  # Generate a random state
            redirect_uri = build_redirect_uri()
            authorization_url = client.get_authorization_request_url(
                scopes=scopes, state=state, redirect_uri=redirect_uri, prompt="consent"
            )

    return authorization_url, state


def acquire_token_from_code(integration, client, scopes, authorization_code):
    """
    Acquires token from authorization code for either Google or Microsoft clients.
    Args:
        integration: The integration type (google/microsoft)
        client: The OAuth client (either Flow or ConfidentialClientApplication)
        scopes: The scopes for the token request
        authorization_code: The authorization code from the callback
    Returns:
        credentials: The token credentials (format may differ between providers)
    """
    match provider_case(integration):
        case IntegrationType.GOOGLE:
            client.fetch_token(code=authorization_code)
            return client.credentials
        case IntegrationType.MICROSOFT:
            redirect_uri = build_redirect_uri()
            result = client.acquire_token_by_authorization_code(
                code=authorization_code, scopes=scopes, redirect_uri=redirect_uri
            )
            return result
    raise ValueError(f"Unsupported integration type: {integration}")


def serialize_credentials(integration, credentials):
    """
    Serializes and encrypts the credentials returning a consistent JSON that includes an 'expires_at' timestamp.
    """

    match provider_case(integration):
        case IntegrationType.GOOGLE:
            credentials_dict = json.loads(credentials.to_json())
            if "expiry" in credentials_dict:
                try:
                    # Google typically provides expiry in ISO format (e.g., "2023-10-10T12:34:56Z").
                    # Replace 'Z' with '+00:00' if necessary for ISO parsing.
                    expiry_str = credentials_dict["expiry"].replace("Z", "+00:00")
                    dt = datetime.fromisoformat(expiry_str)
                    credentials_dict["expires_at"] = int(dt.timestamp())
                except Exception as e:
                    logger.error("Error parsing Google expiry date: %s", e)
                    raise e
            else:
                raise Exception("Google credentials missing required fields:", credentials_dict)
        case IntegrationType.MICROSOFT:
            if ("error" in credentials or "error_description" in credentials):
                logger.error("Error serializing Microsoft credentials: %s", credentials)
                raise Exception(f"Error serializing Microsoft credentials: {credentials}")
                
            credentials_dict = {
                "token": credentials.get("access_token"),
                "expires_in": credentials.get("expires_in"),
                "refresh_token": credentials.get("refresh_token"),
            }
            credentials_dict["expires_at"] = get_expiration_time(
                credentials_dict["expires_in"]
            )
        case _:
            raise ValueError(f"Unsupported integration type: {integration}")
    return credentials_dict


def extract_refresh_response(integration, response_data, credentials):
    if (
        "expires_in" not in response_data
    ):  # applied to both google and microsoft, if we add more this may need to move
        raise ValueError(
            f"Missing 'expires_in' in response data for integration {integration}"
        )

    credentials["expires_at"] = get_expiration_time(response_data["expires_in"])

    match provider_case(integration):
        case IntegrationType.GOOGLE:
            credentials["token"] = response_data["access_token"]

        case IntegrationType.MICROSOFT:
            credentials["token"] = response_data["access_token"]
            if "refresh_token" in response_data:
                credentials["refresh_token"] = response_data["refresh_token"]
        case _:
            raise ValueError(f"Unsupported integration type: {integration}")

    return credentials


########################################################


def get_user_credentials(current_user, integration):
    oauth_user_table = get_oauth_user_table()

    integration_provider = provider_case(integration).value
    item_key = f"{current_user}/{integration_provider}"

    logger.info(
        "Retrieving credentials for user %s and integration %s using key %s", current_user, integration, item_key
    )
    try:
        response = oauth_user_table.get_item(Key={"user_integration": item_key})
        record = response.get("Item")

        if record and "integrations" in record:
            integration_map = record["integrations"]
            credentials = integration_map.get(integration)
            if credentials:
                return decrypt_oauth_data(credentials)
        raise MissingCredentialsError(
            f"No credentials found for user {current_user} and integration {integration}"
        )
    except Exception as e:
        logger.error("Error retrieving credentials from DynamoDB: %s", str(e))
        raise e


def get_oauth_client_credentials(integration):
    """
    Gets OA
    uth client credentials for either Google or Microsoft clients.
    """
    config, _ = get_oauth_integration_parameter(integration)
    client_config = config["web"]
    client_id = (client_config["client_id"],)
    client_secret = client_config["client_secret"]
    tenant_id = client_config.get("tenant_id", client_config.get("project_id", ""))
    token_uri = client_config.get("token_uri", None)
    return client_id, client_secret, tenant_id, token_uri


def get_oauth_integration_parameter(integration):
    stage = os.environ.get("INTEGRATION_STAGE")
    if not stage:
        raise ValueError("INTEGRATION_STAGE environment variable is not set")

    integration_provider = provider_case(integration).value
    ssm = boto3.client("ssm")
    parameter_name = build_integration_parameter_name(integration_provider)
    logger.info("Getting OAuth client for integration: /oauth/%s", parameter_name)
    try:
        response = ssm.get_parameter(
            Name=f"/oauth/{parameter_name}", WithDecryption=True
        )
        config = json.loads(response["Parameter"]["Value"])

        client_config = config["client_config"]
        scopes = config["scopes"][integration]
        return client_config, scopes

    except ssm.exceptions.ParameterNotFound:
        raise ValueError(
            f"No configuration found for integration '{integration}' in stage '{stage}'"
        )
    except KeyError:
        raise ValueError(
            f"Invalid configuration format for integration '{integration}' in stage '{stage}'"
        )


def get_oauth_client_for_integration(integration):
    client_config, scopes = get_oauth_integration_parameter(integration)
    return create_oauth_client(integration, client_config, scopes)


@required_env_vars({
    "OAUTH_STATE_TABLE": [DynamoDBOperation.PUT_ITEM],
    "INTEGRATION_STAGE": [SSMOperation.GET_PARAMETER],
    "API_BASE_URL": [],
    "OAUTH_AUDIENCE": [],
})
@validated("start_oauth")
def start_auth(event, context, current_user, name, data):

    integration = data["data"]["integration"]
    logger.info("Starting OAuth flow for integration: %s", integration)

    auth_client, scopes = get_oauth_client_for_integration(integration)

    logger.debug("Obtained client.")
    logger.debug("Creating client redirect url...")
    authorization_url, state = get_authorization_url_and_state(
        integration, auth_client, scopes
    )

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["OAUTH_STATE_TABLE"])

    try:
        current_timestamp = int(time.time())
        table.put_item(
            Item={
                "state": state,
                "integration": integration,
                "user": current_user,
                "timestamp": current_timestamp,
                "ttl": current_timestamp + 3600,  # Expire in 1 hour (3600 seconds)
            }
        )
    except ClientError as e:
        logger.error("Error storing state in DynamoDB: %s", e)
        raise

    return {
        "statusCode": 302,
        "headers": {"Location": authorization_url},
        "body": {"Location": authorization_url},
    }


def update_oauth_user_credentials(current_user, integration, credentials_data):
    oauth_user_table = get_oauth_user_table()

    integration_provider = provider_case(integration).value
    item_key = f"{current_user}/{integration_provider}"

    logger.info("Storing token in DynamoDB under key: %s", item_key)

    integration_map = {}
    try:
        # Try to retrieve an existing record
        response = oauth_user_table.get_item(Key={"user_integration": item_key})
        record = response.get("Item")
        if record:
            integration_map = record.get("integrations", {})
            logger.debug("Found existing integrations map: %s", integration_map)
        else:
            logger.debug("No record found; initializing a new integrations map.")
    except Exception as e:
        logger.error("Error retrieving item %s from DynamoDB: %s", item_key, e)
        return {
            "success": False,
            "message": f"Error retrieving existing OAuth credentials",
        }

    # Update the integrations map for this integration.
    integration_map[integration] = encrypt_oauth_data(credentials_data)
    logger.debug("Updated integrations map: %s", integration_map)
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        # Store (or update) the new record in DynamoDB.
        oauth_user_table.put_item(
            Item={
                "user_integration": item_key,
                "integrations": integration_map,
                "last_updated": timestamp,
            }
        )
        logger.info("Credentials successfully stored in DynamoDB under key %s", item_key)
        return {"success": True}
    except Exception as e:
        logger.error("Error storing token in DynamoDB: %s", e)
        return {"success": False, "message": f"Error storing OAuth credentials."}


def auth_callback(event, context):

    state = event["queryStringParameters"]["state"]
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["OAUTH_STATE_TABLE"])

    try:
        response = table.get_item(Key={"state": state})
        item = response.get("Item")
        if item:
            current_user = item["user"]
            integration = item["integration"]
        else:
            raise ValueError("Invalid OAuth callback.")
    except ClientError as e:
        logger.error("Error retrieving state from DynamoDB: %s", e)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "text/html"},
            "body": """
                <html>
                <head>
                    <title>Authentication Error</title>
                    <style>
                        body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f0f0f0; }
                        .container { text-align: center; padding: 2rem; background-color: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
                        h1 { color: #e74c3c; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Authentication Error</h1>
                        <p>An error occurred while processing your request.</p>
                    </div>
                </body>
                </html>
            """,
        }

    logger.debug("Current user: %s", current_user)
    logger.debug("Integration: %s", integration)
    authorization_code = event["queryStringParameters"]["code"]

    client, scopes = get_oauth_client_for_integration(integration)
    credentials = acquire_token_from_code(
        integration, client, scopes, authorization_code
    )

    logger.debug("State found: %s", state is not None)
    logger.debug("Credentials found: %s", credentials is not None)

    if state is None or credentials is None:
        return return_html_failed_auth("Invalid OAuth callback, missing parameters.")

    credentials_data = serialize_credentials(integration, credentials)
    update_res = update_oauth_user_credentials(
        current_user, integration, credentials_data
    )
    if not update_res["success"]:
        return return_html_failed_auth(update_res["message"])

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": """
        <html>
        <head>
            <title>Authentication Successful</title>
            <style>
                body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f0f0f0; }
                .container { text-align: center; padding: 2rem; background-color: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
                h1 { color: #2ecc71; }
                .close-button { margin-top: 1rem; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Authentication Successful</h1>
                <p>You can now close this window and return to the application.</p>
                <button class="close-button" onclick="window.close()">Close</button>
            </div>
        </body>
        </html>
    """,
    }


def return_html_failed_auth(message):
    return {
        "statusCode": 400,
        "headers": {"Content-Type": "text/html"},
        "body": f"""
                <html>
                <head>
                    <title>Authentication Failed</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f0f0f0; }}
                        .container {{ text-align: center; padding: 2rem; background-color: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }}
                        h1 {{ color: #e74c3c; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Authentication Failed</h1>
                        <p>{message}</p>
                    </div>
                </body>
                </html>
            """,
    }


@api_tool(
    path="/integrations/oauth/user/list",
    tags=["default"],
    method="GET",
    name="listUserIntegrations",
    description="Takes a list of 3rd party services and returns a list of the 3rd party services that the user has connected to, such as Office 365, Google Sheets, Google Drive, etc.",
    parameters={
        "type": "object",
        "properties": {
            "integrations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "JSON list of string IDs of integrations to check, such as google_sheets, google_drive, google_gmail, google_forms",
            }
        },
        "required": ["integrations"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of integration IDs that the user has connected to",
            },
            "message": {
                "type": "string",
                "description": "Error message if operation failed",
            },
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "OAUTH_USER_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("list_integrations")
def list_connected_integrations(event, context, current_user, name, data):
    supported_integrations = get_available_integrations()
    if not supported_integrations:
        return {
            "success": False,
            "message": f"Error retrieving user integrations from Admin Table",
        }

    connected = list_user_integrations(supported_integrations, current_user)
    logger.debug("Connected status: %s", False if connected is None else True)

    return {"success": False if connected is None else True, "data": connected}


def list_user_integrations(supported_integrations, current_user):
    oauth_user_table = get_oauth_user_table()
    integration_providers = supported_integrations.keys()
    connected_list = []
    for provider in integration_providers:
        item_key = f"{current_user}/{provider}"
        try:
            response = oauth_user_table.get_item(Key={"user_integration": item_key})
            record = response.get("Item")
            if record and "integrations" in record:
                available_integrations = [
                    integration["id"]
                    for integration in supported_integrations[provider]
                    if "id" in integration
                ]

                integration_map = record["integrations"]
                integration_ids = list(integration_map.keys())
                # only keep those that are still made available
                filtered_ids = [
                    id for id in integration_ids if id in available_integrations
                ]
                connected_list.extend(filtered_ids)
        except Exception as e:
            logger.error(
                "Error retrieving record for integration %s for user %s: %s", provider, current_user, str(e)
            )
            continue
    return connected_list


@required_env_vars({
    "OAUTH_USER_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("delete_integration")
def handle_delete_integration(event, context, current_user, name, data):
    integration = data["data"]["integration"]
    success = delete_integration(current_user, integration)
    return {
        "success": success,
        "message": f"Integration {integration} {'deleted' if success else 'not found'} for user {current_user}",
    }


def delete_integration(current_user, integration):
    oauth_user_table = get_oauth_user_table()

    integration_provider = provider_case(integration).value
    item_key = f"{current_user}/{integration_provider}"

    try:
        response = oauth_user_table.get_item(Key={"user_integration": item_key})
        record = response.get("Item")
        if record:
            integration_map = record.get("integrations", {})
            if integration in integration_map:
                logger.info("Integration %s found in the record %s", integration, item_key)
                del integration_map[integration]
                timestamp = datetime.now(timezone.utc).isoformat()
                # Update the record with the new integrations map.
                oauth_user_table.put_item(
                    Item={
                        "user_integration": item_key,
                        "integrations": integration_map,
                        "last_updated": timestamp,
                    }
                )
                logger.info(
                    "Successfully updated record for user %s after deleting integration %s", current_user, integration
                )
                return True
            else:
                logger.warning("Integration %s not found in record %s", integration, item_key)
        else:
            logger.warning(
                "No record found in DynamoDB for user %s and integration provider %s", current_user, integration_provider
            )
    except Exception as e:
        logger.error("Error deleting credentials from DynamoDB: %s", str(e))
    return False


@api_tool(
    path="/integrations/list_supported",
    name="getSupportedIntegrations",
    method="GET",
    tags=["SupportedIntegrations"],
    description="Get a list of the supported integrations.",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "object",
                "description": "Dictionary containing supported integrations organized by provider",
                "additionalProperties": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Integration identifier",
                            },
                            "isAvailable": {
                                "type": "boolean",
                                "description": "Whether the integration is available",
                            },
                        },
                    },
                },
            },
            "message": {
                "type": "string",
                "description": "Error message if operation failed",
            },
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("list_integrations")
def get_supported_integrations(event, context, current_user, name, data):
    supported_integrations = get_available_integrations()
    if supported_integrations:
        return {"success": True, "data": supported_integrations}
    else:
        return {
            "success": False,
            "message": f"Error retrieving user integrations from Admin Table",
        }


def get_available_integrations():
    INTEGRATIONS = "integrations"
    dynamodb = boto3.resource("dynamodb")
    admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])
    # Retrieve available integrations list from DynamoDB
    try:
        response = admin_table.get_item(Key={"config_id": INTEGRATIONS})
        data = {}
        if "Item" in response:
            logger.debug("Integrations found in DynamoDB")
            integrations_map = response["Item"].get("data", {})
            # keep only available integrations
            for provider, integrations in integrations_map.items():
                # For each integration entry, only keep those with isAvailable set to True
                filtered_list = [
                    integration
                    for integration in integrations
                    if integration.get("isAvailable", False)
                ]
                if filtered_list:
                    data[provider] = filtered_list

        return data

    except Exception as e:
        logger.error("Error retrieving user integrations: %s", str(e))
        return None


@required_env_vars({
    "INTEGRATION_STAGE": [SSMOperation.PUT_PARAMETER],
    "API_BASE_URL": [],
    "OAUTH_AUDIENCE": [],
})
@validated("register_secret")
def regiser_secret(event, context, current_user, name, data):
    integration_provider = data["data"]["integration"]
    if not verify_user_as_admin(
        data["access_token"], f"Register Integration Secrets for {integration_provider}"
    ):
        return {"success": False, "error": "Unable to authenticate user as admin"}

    verify_oauth_encryption_parameter()

    data = data["data"]
    client_id = data["client_id"]
    client_secret = data["client_secret"]
    tenant_id = data.get("tenant_id", None)
    if not tenant_id:
        tenant_id = "amplifygenai"

    param_name = build_integration_parameter_name(integration_provider)

    try:
        configuration = format_integration_param(
            integration_provider,
            client_id,
            client_secret,
            scopes[integration_provider],
            tenant_id,
        )
        response = store_secret_parameter(
            param_name, json.dumps(configuration), "/oauth"
        )
        if response:
            logger.info("Credentials stored in Parameter Store %s", param_name)
            return {"success": True}
    except ClientError as e:
        logger.error("Error storing token in Parameter Store: %s", e)
    return {"success": False}


def build_redirect_uri():
    API_BASE_URL = os.environ.get("API_BASE_URL")
    return f"{API_BASE_URL}/integrations/oauth/callback"


def format_integration_param(
    integration_provider, client_id, client_secret, integration_scopes, tenant_id
):
    OAUTH_AUDIENCE = os.environ.get("OAUTH_AUDIENCE")

    param_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": [build_redirect_uri()],
        "javascript_origins": [OAUTH_AUDIENCE],
    }

    match integration_provider:
        case IntegrationType.GOOGLE.value:
            param_data["auth_uri"] = "https://accounts.google.com/o/oauth2/auth"
            param_data["token_uri"] = "https://oauth2.googleapis.com/token"
            param_data["auth_provider_x509_cert_url"] = (
                "https://www.googleapis.com/oauth2/v1/certs"
            )
            param_data["project_id"] = tenant_id
        case IntegrationType.MICROSOFT.value:
            param_data["auth_uri"] = "https://login.microsoftonline.com/"
            param_data["tenant_id"] = tenant_id
            param_data["token_uri"] = (
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
            )

    configuration = {"client_config": {"web": param_data}, "scopes": integration_scopes}
    return configuration


def build_integration_parameter_name(integration):
    stage = os.environ.get("INTEGRATION_STAGE")
    return f"integrations/{integration}/{stage}"


def get_oauth_user_table():
    dynamodb = boto3.resource("dynamodb")
    oauth_user_table_name = os.environ.get("OAUTH_USER_TABLE")
    if not oauth_user_table_name:
        raise ValueError("OAUTH_USER_TABLE environment variable is not set")
    return dynamodb.Table(oauth_user_table_name)


@required_env_vars({
    "OAUTH_USER_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
    "INTEGRATION_STAGE": [SSMOperation.GET_PARAMETER],
})
@validated("refresh_token")
def refresh_integration_tokens(event, context, current_user, name, data):
    integration = data["data"]["integration"]
    credentials = get_user_credentials(current_user, integration)
    # ultimately returns only success or failure, no credentials are returned
    return refresh_credentials(current_user, integration, credentials)


def refresh_credentials(current_user, integration, credentials):
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        logger.warning("No refresh token available for refreshing credentials.")
        return {
            "success": False,
            "message": "No refresh token available for refreshing credentials.",
        }

    client_id, client_secret, _, token_uri = get_oauth_client_credentials(integration)

    # Data to post to the token endpoint
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    logger.info("Refreshing token for integration: %s", integration)
    response = requests.post(token_uri, data=data)
    if response.status_code != 200:
        logger.error("Failed to refresh token: %s", response.text)
        return {
            "success": False,
            "message": f"Failed to refresh token: {response.text}",
        }

    logger.debug("Extracting refresh token response")

    updated_credentials = extract_refresh_response(
        integration, response.json(), credentials
    )

    return update_oauth_user_credentials(current_user, integration, updated_credentials)


def get_expiration_time(expires_in):
    # Handle None or invalid expires_in values by defaulting to 1 hour (3600 seconds)
    if expires_in is None or not isinstance(expires_in, (int, float)) or expires_in <= 0:
        logger.warning("Invalid expires_in value: %s, defaulting to 3600 seconds (1 hour)", expires_in)
        expires_in = 3600
    
    return int((datetime.now(timezone.utc) + timedelta(seconds=expires_in)).timestamp())
