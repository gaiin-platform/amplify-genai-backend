from datetime import datetime, timezone
import json
import os
import boto3
from .integrationsList import integrations_list
from auth.oauth_encryption import decrypt_oauth_data
from auth.oauth import refresh_integration_token, get_user_integrations
from pycommon.api.secrets import get_secret_parameter
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

PROVIDER = "google"
MAX_RETRIES = 2


# Define a custom error for missing credentials
class MissingCredentialsError(Exception):
    pass


def get_user_credentials(
    current_user, integration, access_token, retry_num=0, available_integrations=None
):
    if retry_num > MAX_RETRIES:
        raise Exception(
            f"Failed to refresh credentials for user {current_user} and integration {integration} after {MAX_RETRIES} retries"
        )

    oauth_user_table_name = os.environ.get("OAUTH_USER_TABLE")

    if not oauth_user_table_name:
        raise ValueError("OAUTH_USER_TABLE environment variable is not set")

    if not available_integrations:
        available_integrations = get_user_integrations(access_token)
        if not available_integrations:
            print(f"Failed to retrieve supported integrations for user {current_user}")
            raise Exception(
                f"Failed to retrieve supported integrations for user {current_user}"
            )
        elif integration not in available_integrations:
            print(f"Integration {integration} is not currently available")
            raise Exception(f"Integration {integration} is not currently available")

    dynamodb = boto3.resource("dynamodb")
    oauth_user_table = dynamodb.Table(oauth_user_table_name)

    item_key = f"{current_user}/{PROVIDER}"

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
                credentials = decrypt_oauth_data(credentials)

                if check_credentials_expired(credentials.get("expires_at")):
                    print(
                        f"Credentials for user {current_user} and integration {integration} are expired"
                    )
                    result = refresh_integration_token(access_token, integration)
                    if not result:
                        raise Exception(
                            f"Failed to refresh credentials for user {current_user} and integration {integration}"
                        )

                    return get_user_credentials(
                        current_user,
                        integration,
                        access_token,
                        retry_num + 1,
                        available_integrations,
                    )

                return credentials
        raise MissingCredentialsError(
            f"No credentials found for user {current_user} and integration {integration}"
        )
    except Exception as e:
        print(f"Error retrieving credentials from DynamoDB: {str(e)}")
        raise e


def check_credentials_expired(expires_at: int) -> bool:
    if not expires_at:
        raise Exception(f"No expiration timestamp found for user")
    """
    Checks if the given expiration timestamp is in the past.
    
    :param expires_at: An integer Unix timestamp representing when the credentials expire.
    :return: True if the current time is equal to or later than expires_at, False otherwise.
    """
    current_ts = int(datetime.now(timezone.utc).timestamp())
    return current_ts >= expires_at


@validated("get")
def get_integrations(event, context, current_user, name, data):
    stage = os.environ.get("INTEGRATION_STAGE")
    secret_param = f"integrations/{PROVIDER}/{stage}"

    secrets_value = None
    try:
        secrets_value = get_secret_parameter(secret_param, "/oauth")
    except Exception as e:
        print(f"Error retrieving secrets: {str(e)}")
        print(f"Setting secrets to empty values")

    secrets = {"client_id": "", "client_secret": "", "tenant_id": ""}
    if secrets_value:
        secrets_json = json.loads(secrets_value)
        secrets_data = secrets_json["client_config"]["web"]
        secrets["client_id"] = secrets_data["client_id"]
        secrets["client_secret"] = secrets_data["client_secret"]
        secrets["tenant_id"] = secrets_data["project_id"]

    # get secrets from param store
    return {
        "success": True,
        "data": {"integrations": integrations_list, "secrets": secrets},
    }
