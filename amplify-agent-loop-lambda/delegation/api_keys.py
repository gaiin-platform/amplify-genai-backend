from datetime import datetime, timezone
from typing import Union, Dict
import os
import json
import uuid
import requests
import boto3

dynamodb = boto3.resource("dynamodb")

# Define period types
PERIOD_TYPE = ["Unlimited", "Daily", "Hourly", "Monthly"]
UNLIMITED = "Unlimited"

# Define the default rate limit for unlimited access
NO_RATE_LIMIT = {"period": UNLIMITED, "rate": None}


def format_rate_limit(limits: Dict[str, Union[str, float, None]]) -> str:
    """
    Formats the rate limit as a string.

    Args:
        limits (dict): A dictionary containing 'period' and 'rate'.

    Returns:
        str: Formatted rate limit string.
    """
    if not limits.get("rate"):
        return NO_RATE_LIMIT["period"]
    return f"{limits['rate']:.2f}/{limits['period']}"


def rate_limit_obj(
    period: str, rate: Union[str, None]
) -> Dict[str, Union[str, float, None]]:
    """
    Creates a rate limit object based on the given period and rate.

    Args:
        period (str): The rate limit period ("Unlimited", "Daily", "Hourly", or "Monthly").
        rate (str or None): The rate as a string, potentially with a dollar sign.

    Returns:
        dict: A rate limit object with 'period' and 'rate'.
    """
    if period == UNLIMITED:
        return NO_RATE_LIMIT
    return {"period": period, "rate": float(rate.replace("$", "")) if rate else None}

def pascalCase(input_str):
    return "".join(x for x in input_str.title() if not x.isspace() and x.isalnum())


def create_api_key(
    token: str,
    user: str,
    selected_account: dict,
    delegate_input: str | None,
    app_name: str,
    app_description: str,
    rate_limit_period: str,
    rate_limit_rate: Union[str, None],
    include_expiration: bool,
    selected_date: Union[str, None],
    full_access: bool,
    options: Dict[str, bool],
    purpose: str,
) -> Union[Dict, None]:
    """
    Calls the apiKeys/keys/create endpoint to generate a new API key.

    Args:
        token (str): Authorization token.
        user (str): Owner of the API key.
        selected_account (str): Account associated with the key.
        delegate_input (str): Delegate input (if any).
        app_name (str): Application name.
        app_description (str): Application description.
        rate_limit_period (str): Rate limit period.
        rate_limit_rate (str or None): Rate limit rate.
        include_expiration (bool): Whether to include an expiration date.
        selected_date (str or None): Expiration date (if applicable).
        full_access (bool): Whether the key has full access.
        options (dict): Dictionary defining access options.
        system_use (bool): Whether the key is for system use.

    Returns:
        dict or None: API response or None if an error occurs.
    """
    name = pascalCase(app_name)

    print("create api key for agent")
    api_keys_table_name = os.environ["API_KEYS_DYNAMODB_TABLE"]
    api_table = dynamodb.Table(api_keys_table_name)

    api_owner_id = f"{name}/ownerKey/{str(uuid.uuid4())}"
    timestamp = datetime.now(timezone.utc).isoformat()
    apiKey = 'amp-' + str(uuid.uuid4())
    try:
        print("Put entry in api keys table")
        # Put (or update) the item for the specified user in the DynamoDB table
        response = api_table.put_item(
            Item={
                'api_owner_id': api_owner_id,
                'owner': user,
                "delegate": delegate_input if delegate_input else None,
                'apiKey': apiKey,
                'active': True,
                'createdAt': timestamp, 
                'expirationDate' : selected_date if include_expiration else None,
                'accessTypes': (["full_access"] if full_access
                                else [key for key, value in options.items() if value]),
                'account': selected_account,
                'rateLimit': rate_limit_obj(rate_limit_period, rate_limit_rate),
                'purpose': purpose,
                'applicationDescription' : app_description,
                'applicationName' : app_name
            }
        )

        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
            print(f"API key for created successfully")
            return {"success": True,
                    "data": {"id": api_owner_id, "apiKey": apiKey},
                    "message": "API key created successfully",
                    }
        else:
            print(f"Failed to create API key")
            return {"success": False, "message": "Failed to create API key"}
    except Exception as e:
        print(f"An error occurred while saving API key: {e}")
        return {
            "success": False,
            "message": f"An error occurred while saving API key: {str(e)}",
        }


def create_agent_event_api_key(
    user: str,
    token: str,
    agent_event_name: str,
    account: str,
    description: str,
    purpose: str,
) -> Union[Dict, None]:
    """
    Creates an API key with unlimited rate limits for an agent event.

    Args:
        user (str): Owner of the API key.
        token (str): Authorization token.
        agent_event_name (str): Maps to app_name.
        account (str): The account associated with the API key.
        description (str): Custom description for the API key.

    Returns:
        dict or None: API response or None if an error occurs.
    """

    delegate_input = None  # No delegate provided
    app_name = agent_event_name  # Mapping agent_event_name to app_name
    app_description = description  # Use provided description
    rate_limit_period = UNLIMITED  # No rate limit
    rate_limit_rate = None  # Ensuring consistency in rate handling
    include_expiration = False  # No expiration date
    selected_date = None  # Not used since expiration is disabled
    full_access = True  # Grant full access
    options = {}  # Not used since full_access is True

    return create_api_key(
        token=token,
        user=user,
        selected_account={"id": account, "name": "agent_" + agent_event_name},
        delegate_input=delegate_input,
        app_name=app_name,
        app_description=app_description,
        rate_limit_period=rate_limit_period,
        rate_limit_rate=rate_limit_rate,
        include_expiration=include_expiration,
        selected_date=selected_date,
        full_access=full_access,
        options=options,
        purpose=purpose,
    )


# Requires a valid user token
def get_api_key_by_id(token: str, api_key_id: str) -> Union[Dict, None]:
    """
    Fetches a specific API key by its ID.

    Args:
        token (str): Authorization token.
        api_key_id (str): The unique ID of the API key.

    Returns:
        dict or None: API response containing the API key details, or None if an error occurs.
    """

    api_base = os.environ.get("API_BASE_URL", None)
    if not api_base:
        print("API_BASE_URL is not set.")
        return None

    if not api_key_id:
        print("API Key ID is required.")
        return None

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Ensure API Key ID is correctly passed as a query parameter
    url = f"{api_base}/apiKeys/key/get?apiKeyId={api_key_id}"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        result = response.json()

        print(f"Retrieved API Key: {result}")
        return result["data"]
    except Exception as e:
        print(f"Failed to retrieve API key: {str(e)}")
        return None


def get_api_keys(token: str) -> Union[Dict, None]:
    """
    Retrieves all API keys for the authenticated user.

    Args:
        token (str): Authorization token.

    Returns:
        dict or None: API response containing API keys, or None if an error occurs.
    """

    api_base = os.environ.get("API_BASE_URL", None)
    if not api_base:
        print("API_BASE_URL is not set.")
        return None

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        response = requests.get(f"{api_base}/apiKeys/keys/get", headers=headers)
        response.raise_for_status()
        result = response.json()

        print(f"Retrieved API Keys: {result}")
        return result
    except Exception as e:
        print(f"Failed to retrieve API keys: {str(e)}")
        return None


# direct access to the api key table
def get_api_key_directly_by_id(api_owner_id):
    dynamodb = boto3.resource("dynamodb")
    api_keys_table_name = os.getenv("API_KEYS_DYNAMODB_TABLE")
    if not api_keys_table_name:
        raise ValueError("API_KEYS_DYNAMODB_TABLE is not provided.")

    # retrieve api key
    try:
        api_keys_table = dynamodb.Table(api_keys_table_name)
        api_response = api_keys_table.get_item(Key={"api_owner_id": api_owner_id})
        api_item = api_response.get("Item")

        if not api_item:
            return {
                "success": False,
                "message": f"No API key found for api id: {api_owner_id}",
            }

        return {"success": True, "apiKey": api_item["apiKey"]}
    except Exception as e:
        return {
            "success": False,
            "message": f"Error retrieving API key for {api_owner_id}: {str(e)}",
        }
