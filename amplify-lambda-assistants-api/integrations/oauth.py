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

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata import permissions

setup_validated(rules, permissions.get_permission_checker)
from pycommon.api.ops import api_tool, set_permissions_by_state

set_permissions_by_state(permissions)


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


def create_oauth_client(integration, client_config, scopes, origin=None):
    """
    Creates an OAuth client for either Google or Microsoft integrations.
    Returns a tuple of (client, is_google_flow) where is_google_flow is used to determine
    how to handle the client in other functions.
    Args:
        integration: The integration type
        client_config: OAuth client configuration
        scopes: OAuth scopes
        origin: Request origin for dynamic redirect URI selection
    """
    match provider_case(integration):
        case IntegrationType.GOOGLE:
            flow = Flow.from_client_config(client_config, scopes=scopes)
            redirect_uris = client_config.get("web", {}).get("redirect_uris", [])
            if len(redirect_uris) == 1:
                flow.redirect_uri = redirect_uris[0]
            else:
                flow.redirect_uri = build_redirect_uri(origin)
            return flow, scopes
        case IntegrationType.MICROSOFT:
            client_config = client_config.get("web")
            client_id = client_config.get("client_id")
            client_secret = client_config.get("client_secret")
            authority = (
                f"{client_config.get('auth_uri')}{client_config.get('tenant_id')}"
            )
            
            # Log the redirect URI that will be used
            redirect_uri = build_redirect_uri(origin)
            print(f"Microsoft OAuth - Redirect URI being used: {redirect_uri}")
            print(f"Microsoft OAuth - Client config redirect_uris: {client_config.get('redirect_uris', [])}")
            
            app = ConfidentialClientApplication(
                client_id=client_id,
                client_credential=client_secret,
                authority=authority,
            )
            return app, scopes
    raise ValueError(f"Unsupported integration type: {integration}")


def get_authorization_url_and_state(integration, client, scopes=None, retry_with_consent=False):
    """
    Gets authorization URL and state for either Google or Microsoft clients.
    Args:
        integration: The integration type
        client: OAuth client
        scopes: OAuth scopes
        retry_with_consent: If True, forces prompt=consent for Microsoft OAuth
    """
    match provider_case(integration):
        case IntegrationType.GOOGLE:
            authorization_url, state = client.authorization_url(prompt="consent")
        case IntegrationType.MICROSOFT:
            state = str(uuid.uuid4())  # Generate a random state
            # Only use prompt=consent if explicitly requested (for retry scenarios)
            auth_params = {"scopes": scopes, "state": state}
            if retry_with_consent:
                auth_params["prompt"] = "consent"
                
            # For Microsoft, we need to explicitly pass the redirect_uri
            # The redirect_uri should be determined from the current environment
            redirect_uri = build_redirect_uri()
            auth_params["redirect_uri"] = redirect_uri
            
            print(f"Microsoft OAuth authorization URL params: {auth_params}")
            authorization_url = client.get_authorization_request_url(**auth_params)

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
            # For Microsoft, we need to pass the redirect_uri that was used in authorization
            redirect_uri = build_redirect_uri()
            print(f"Microsoft token acquisition - using redirect_uri: {redirect_uri}")
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
                    print("Error parsing Google expiry date:", e)
                    raise e
            else:
                raise Exception("Google credentials missing 'expiry' field")
        case IntegrationType.MICROSOFT:
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

    print(
        f"Retrieving credentials for user {current_user} and integration {integration} using key {item_key}"
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
        print(f"Error retrieving credentials from DynamoDB: {str(e)}")
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
    print(f"Getting OAuth client for integration: /oauth/{parameter_name}")
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


def get_oauth_client_for_integration(integration, origin=None):
    client_config, scopes = get_oauth_integration_parameter(integration)
    return create_oauth_client(integration, client_config, scopes, origin)


def detect_request_origin(event):
    """
    Detects the origin of the OAuth request from event headers.
    Returns the origin URL or None if not detectable.
    """
    headers = event.get("headers", {})
    
    # Print all headers for debugging
    print(f"Available headers: {list(headers.keys())}")
    
    # Check various header formats (case-insensitive)
    origin = None
    for key, value in headers.items():
        key_lower = key.lower()
        if key_lower in ["origin", "referer", "host"]:
            print(f"Found header {key}: {value}")
            
        if key_lower == "origin":
            origin = value
            break
        elif key_lower == "referer":
            # Extract origin from referer URL
            import urllib.parse
            parsed = urllib.parse.urlparse(value)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            break
        elif key_lower == "host" and not origin:
            # Fallback to host header if available
            # Determine protocol based on environment
            protocol = "https" if "amazonaws.com" in value or "dev-amplify" in value else "http"
            origin = f"{protocol}://{value}"
    
    # If still no origin detected and we're in local dev, assume localhost frontend
    if not origin:
        # Check if API_BASE_URL suggests local development
        api_base = os.environ.get("API_BASE_URL", "")
        if "localhost" in api_base:
            origin = "http://localhost:3000"  # Common frontend port
            print(f"No origin detected, assuming local frontend: {origin}")
    
    print(f"Detected request origin: {origin}")
    return origin


@validated("start_oauth")
def start_auth(event, context, current_user, name, data):

    integration = data["data"]["integration"]
    print(f"Starting OAuth flow for integration: {integration}")

    # Detect request origin for dynamic callback URL selection
    origin = detect_request_origin(event)
    
    auth_client, scopes = get_oauth_client_for_integration(integration, origin)

    print("Obtained client.")
    print("Creating client redirect url...")
    
    # For Microsoft, try without consent first (will retry with consent if needed)
    retry_with_consent = False
    authorization_url, state = get_authorization_url_and_state(
        integration, auth_client, scopes, retry_with_consent
    )

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["OAUTH_STATE_TABLE"])

    try:
        table.put_item(
            Item={
                "state": state,
                "integration": integration,
                "user": current_user,
                "timestamp": int(time.time()),
                "origin": origin,  # Store origin for callback handling
                "retry_with_consent": retry_with_consent,
            }
        )
    except ClientError as e:
        print(f"Error storing state in DynamoDB: {e}")
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

    print(f"Storing token in DynamoDB under key: {item_key}")

    integration_map = {}
    try:
        # Try to retrieve an existing record
        response = oauth_user_table.get_item(Key={"user_integration": item_key})
        record = response.get("Item")
        if record:
            integration_map = record.get("integrations", {})
            print("Found existing integrations map:", integration_map)
        else:
            print("No record found; initializing a new integrations map.")
    except Exception as e:
        print(f"Error retrieving item {item_key} from DynamoDB: {e}")
        return {
            "success": False,
            "message": f"Error retrieving existing OAuth credentials",
        }

    # Update the integrations map for this integration.
    integration_map[integration] = encrypt_oauth_data(credentials_data)
    print("Updated integrations map:", integration_map)
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
        print(f"Credentials successfully stored in DynamoDB under key {item_key}")
        return {"success": True}
    except Exception as e:
        print(f"Error storing token in DynamoDB: {e}")
        return {"success": False, "message": f"Error storing OAuth credentials."}


def handle_oauth_error_retry(current_user, integration, origin, error_description):
    """
    Handles OAuth errors by retrying with consent prompt if needed.
    Returns a redirect URL for retry or None if retry is not appropriate.
    """
    # Check if this is an approval required error for Microsoft
    if provider_case(integration) == IntegrationType.MICROSOFT:
        if "approval" in error_description.lower() or "consent" in error_description.lower():
            print(f"Approval required error detected, retrying with consent prompt")
            
            # Create new OAuth client with consent prompt
            auth_client, scopes = get_oauth_client_for_integration(integration, origin)
            authorization_url, state = get_authorization_url_and_state(
                integration, auth_client, scopes, retry_with_consent=True
            )
            
            # Store retry state
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(os.environ["OAUTH_STATE_TABLE"])
            
            try:
                table.put_item(
                    Item={
                        "state": state,
                        "integration": integration,
                        "user": current_user,
                        "timestamp": int(time.time()),
                        "origin": origin,
                        "retry_with_consent": True,
                        "is_retry": True,
                    }
                )
                return authorization_url
            except ClientError as e:
                print(f"Error storing retry state in DynamoDB: {e}")
    
    return None


def auth_callback(event, context):
    try:
        print(f"OAuth callback received - Event: {json.dumps(event, default=str, indent=2)}")
        
        query_params = event.get("queryStringParameters", {})
        if not query_params:
            print("No query parameters found in callback")
            return return_html_failed_auth("No parameters received in OAuth callback.")
        
        print(f"Query parameters: {query_params}")
        
        # Check for OAuth errors first
        error = query_params.get("error")
        error_description = query_params.get("error_description", "")
        
        if error:
            print(f"OAuth error received: {error} - {error_description}")
            
            # Try to get state to retrieve user and integration info for retry
            state = query_params.get("state")
            if state:
                dynamodb = boto3.resource("dynamodb")
                table = dynamodb.Table(os.environ["OAUTH_STATE_TABLE"])
                
                try:
                    response = table.get_item(Key={"state": state})
                    item = response.get("Item")
                    
                    if item and not item.get("is_retry", False):  # Avoid infinite retry loops
                        current_user = item["user"]
                        integration = item["integration"]
                        origin = item.get("origin")
                        
                        # Attempt retry with consent
                        retry_url = handle_oauth_error_retry(current_user, integration, origin, error_description)
                        if retry_url:
                            return {
                                "statusCode": 302,
                                "headers": {"Location": retry_url},
                                "body": {"Location": retry_url},
                            }
                except ClientError as e:
                    print(f"Error retrieving state for retry: {e}")
            
            # If retry is not possible or appropriate, return error
            return return_html_failed_auth(f"OAuth error: {error_description or error}")
    except Exception as e:
        print(f"Unexpected error in auth_callback: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return return_html_failed_auth(f"Internal server error: {str(e)}")

    state = query_params.get("state")
    if not state:
        return return_html_failed_auth("Missing state parameter.")
        
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["OAUTH_STATE_TABLE"])

    try:
        response = table.get_item(Key={"state": state})
        item = response.get("Item")
        if item:
            current_user = item["user"]
            integration = item["integration"]
            origin = item.get("origin")
        else:
            raise ValueError("Invalid OAuth callback.")
    except ClientError as e:
        print(f"Error retrieving state from DynamoDB: {e}")
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

    print("Current user:", current_user)
    print("Integration:", integration)
    authorization_code = query_params.get("code")
    
    if not authorization_code:
        return return_html_failed_auth("Missing authorization code.")

    client, scopes = get_oauth_client_for_integration(integration, origin)
    credentials = acquire_token_from_code(
        integration, client, scopes, authorization_code
    )

    print("State found:", state is not None)
    print("Credentials found:", credentials is not None)

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
@validated("list_integrations")
def list_connected_integrations(event, context, current_user, name, data):
    supported_integrations = get_available_integrations()
    if not supported_integrations:
        return {
            "success": False,
            "message": f"Error retrieving user integrations from Admin Table",
        }

    connected = list_user_integrations(supported_integrations, current_user)
    print(False if connected is None else True)

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
            print(
                f"Error retrieving record for integration {provider} for user {current_user}: {str(e)}"
            )
            continue
    return connected_list


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
                print(f"Integration {integration} found in the record {item_key}")
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
                print(
                    f"Successfully updated record for user {current_user} after deleting integration {integration}"
                )
                return True
            else:
                print(f"Integration {integration} not found in record {item_key}")
        else:
            print(
                f"No record found in DynamoDB for user {current_user} and integration provider {integration_provider}"
            )
    except Exception as e:
        print(f"Error deleting credentials from DynamoDB: {str(e)}")
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
            print("Integrations found in DynamoDB")
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
        print(f"Error retrieving user integrations: {str(e)}")
        return None


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
            print(f"Credentials stored in Parameter Store {param_name}")
            return {"success": True}
    except ClientError as e:
        print(f"Error storing token in Parameter Store: {e}")
    return {"success": False}


def build_redirect_uri(origin=None):
    """
    Builds redirect URI based on request origin or falls back to API_BASE_URL.
    Args:
        origin: The origin URL from the request (e.g., https://dev-amplify.com, http://localhost:3000)
    Returns:
        Appropriate callback URL for the OAuth flow
    """
    # Always use the API_BASE_URL for the callback, not the frontend origin
    api_base_url = os.environ.get("API_BASE_URL")
    
    if not api_base_url:
        raise ValueError("API_BASE_URL environment variable is not set")
    
    callback_url = f"{api_base_url}/integrations/oauth/callback"
    
    print(f"Building redirect URI - Origin: {origin}, API_BASE_URL: {api_base_url}, Callback: {callback_url}")
    
    return callback_url


def format_integration_param(
    integration_provider, client_id, client_secret, integration_scopes, tenant_id
):
    OAUTH_AUDIENCE = os.environ.get("OAUTH_AUDIENCE")

    # Build multiple redirect URIs for different environments
    redirect_uris = [build_redirect_uri()]  # Default/primary callback URL
    
    # Always add localhost callback for local development
    localhost_callback = "http://localhost:3015/integrations/oauth/callback"
    if localhost_callback not in redirect_uris:
        redirect_uris.append(localhost_callback)
    
    # Add additional callback URLs for different environments if OAUTH_AUDIENCE is available
    if OAUTH_AUDIENCE:
        # Add dev environment callback if not already primary
        if 'dev' not in OAUTH_AUDIENCE and 'localhost' not in OAUTH_AUDIENCE:
            dev_callback = OAUTH_AUDIENCE.replace('https://', 'https://dev-')
            dev_callback_url = f"{dev_callback}/integrations/oauth/callback"
            if dev_callback_url not in redirect_uris:
                redirect_uris.append(dev_callback_url)
    
    print(f"Configured redirect URIs for {integration_provider}: {redirect_uris}")

    param_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": redirect_uris,
        "javascript_origins": [OAUTH_AUDIENCE] if OAUTH_AUDIENCE else [],
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


@validated("refresh_token")
def refresh_integration_tokens(event, context, current_user, name, data):
    integration = data["data"]["integration"]
    credentials = get_user_credentials(current_user, integration)
    # ultimately returns only success or failure, no credentials are returned
    return refresh_credentials(current_user, integration, credentials)


def refresh_credentials(current_user, integration, credentials):
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        print("No refresh token available for refreshing credentials.")
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
    print("Refreshing token for integration: ", integration)
    response = requests.post(token_uri, data=data)
    if response.status_code != 200:
        print(f"Failed to refresh token: {response.text}")
        return {
            "success": False,
            "message": f"Failed to refresh token: {response.text}",
        }

    print("Extracting refresh token response")

    updated_credentials = extract_refresh_response(
        integration, response.json(), credentials
    )

    return update_oauth_user_credentials(current_user, integration, updated_credentials)


def get_expiration_time(expires_in):
    return int((datetime.now(timezone.utc) + timedelta(seconds=expires_in)).timestamp())
