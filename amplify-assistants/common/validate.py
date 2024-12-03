
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from common.permissions import get_permission_checker
import json
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from common.encoders import CombinedEncoder
from boto3.dynamodb.conditions import Key

import os
import requests
from jose import jwt

from dotenv import load_dotenv

import boto3
from datetime import datetime
import re

load_dotenv(dotenv_path=".env.local")

ALGORITHMS = ["RS256"]


class HTTPException(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class BadRequest(HTTPException):
    def __init__(self, message="Bad Request"):
        super().__init__(400, message)


class Unauthorized(HTTPException):
    def __init__(self, message="Unauthorized"):
        super().__init__(401, message)


class NotFound(HTTPException):
    def __init__(self, message="Not Found"):
        super().__init__(404, message)


delete_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "The public id of the assistant",
        },
        "removePermsForUsers": {
            "type": "array",
            "description": "A list of user who have access to this ast",
            "items": {"type": "string"},
        },
    },
    "required": ["assistantId"],
}

create_assistant_schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The name of the item"},
        "description": {
            "type": "string",
            "description": "A brief description of the item",
        },
        "assistantId": {
            "type": "string",
            "description": "The public id of the assistant",
        },
        "tags": {
            "type": "array",
            "description": "A list of tags associated with the item",
            "items": {"type": "string"},
        },
        "instructions": {
            "type": "string",
            "description": "Instructions related to the item",
        },
        "disclaimer": {
            "type": "string",
            "description": "Appended assistant response disclaimer related to the item",
        },
        "uri": {
            "oneOf": [
                {
                    "type": "string",
                    "description": "The endpoint that receives requests for the assistant",
                },
                {"type": "null"},
            ]
        },
        "dataSources": {
            "type": "array",
            "description": "A list of data sources",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "The key of the data source",
                    }
                },
            },
        },
    },
    "required": ["name", "description", "tags", "instructions", "dataSources"],
}


create_code_interpreter_assistant_schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The name of the item"},
        "description": {
            "type": "string",
            "description": "A brief description of the item",
        },
        "tags": {
            "type": "array",
            "description": "A list of tags associated with the item",
            "items": {"type": "string"},
        },
        "instructions": {
            "type": "string",
            "description": "Instructions related to the item",
        },
        "dataSources": {
            "type": "array",
            "description": "A list of data sources keys",
            "items": {"type": "string"},
        },
    },
    "required": ["name", "description", "tags", "instructions", "dataSources"],
}

share_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "Code interpreter Assistant Id",
        },
        "recipientUsers": {"type": "array", "items": {"type": "string"}},
        "accessType": {"type": "string"},
        "policy": {"type": "string", "default": ""},
        "note": {"type": "string"},
        "shareToS3": {"type": "boolean"},
    },
    "required": ["assistantId", "recipientUsers"],
    "additionalProperties": False,
}


chat_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {"type": "string"},
        "accountId": {"type": "string"},
        "requestId": {"type": "string"},
        "threadId": {"type": ["string", "null"]},
        "messages": {
            "anyOf": [
                {  # Messages through amplify
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "role": {"type": "string"},
                            "type": {"type": "string"},
                            "data": {"type": "object", "additionalProperties": True},
                            "codeInterpreterMessageData": {
                                "type": "object",
                                "properties": {
                                    "threadId": {"type": "string"},
                                    "role": {"type": "string"},
                                    "textContent": {"type": "string"},
                                    "content": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "type": {
                                                    "enum": [
                                                        "image_file",
                                                        "file",
                                                        "application/pdf",
                                                        "text/csv",
                                                        "image/png",
                                                    ]
                                                },
                                                "values": {
                                                    "type": "object",
                                                    "properties": {
                                                        "file_key": {"type": "string"},
                                                        "presigned_url": {
                                                            "type": "string"
                                                        },
                                                        "file_key_low_res": {
                                                            "type": "string"
                                                        },
                                                        "presigned_url_low_res": {
                                                            "type": "string"
                                                        },
                                                        "file_size": {
                                                            "type": "integer"
                                                        },
                                                    },
                                                    "required": [
                                                        "file_key",
                                                        "presigned_url",
                                                    ],
                                                    "additionalProperties": False,
                                                },
                                            },
                                            "required": ["type", "values"],
                                        },
                                    },
                                },
                                "required": [],
                            },
                        },
                        "required": ["id", "content", "role"],
                    },
                },
                {  # messages from API
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "role": {"type": "string", "enum": ["user", "assistant"]},
                            "dataSourceIds": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["content", "role"],
                    },
                },
            ]
        },
    },
    "required": ["assistantId", "messages"],
}


download_ci_files_schema = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "Key."},
        "fileName": {"type": "string", "description": "optional file name"},
    },
    "required": ["key"],
}

remove_astp_perms_schema = {
    "type": "object",
    "properties": {
        "assistant_public_id": {"type": "string", "description": "astp assistantId."},
        "users": {
            "type": "array",
            "description": "Remove astp permissions for each user",
            "items": {"type": "string"},
        },
    },
    "required": ["assistant_public_id", "users"],
}

get_group_assistant_conversations_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "The id of the assistant",
        }
    },
    "required": ["assistantId"],
}

get_group_assistant_dashboards_schema = {
    "type": "object",
    "properties": {
        "assistantId": {
            "type": "string",
            "description": "The id of the assistant",
        },
        "startDate": {
            "type": "string",
            "format": "date-time",
            "description": "Optional start date for filtering conversations",
        },
        "endDate": {
            "type": "string",
            "format": "date-time",
            "description": "Optional end date for filtering conversations",
        },
        "includeConversationData": {
            "type": "boolean",
            "description": "Whether to include conversation data in CSV format",
        },
        "includeConversationContent": {
            "type": "boolean",
            "description": "Whether to include full conversation content",
        },
    },
    "required": ["assistantId"],
}

save_user_rating_schema = {
    "type": "object",
    "properties": {
        "conversationId": {
            "type": "string",
            "description": "The id of the conversation",
        },
        "userRating": {
            "type": "number",
            "description": "The user's rating",
        },
        "userFeedback": {
            "type": "string",
            "description": "Optional user feedback on the conversation",
        },
    },
    "required": ["conversationId", "userRating"],
}

get_group_conversations_data_schema = {
    "type": "object",
    "properties": {
        "conversationId": {
            "type": "string",
            "description": "The id of the conversation",
        },
        "assistantId": {
            "type": "string",
            "description": "The id of the assistant",
        },
    },
    "required": ["conversationId", "assistantId"],
}

"""
Every service must define the permissions for each operation here. 
The permission is related to a request path and to a specific operation.
"""
validators = {
    "/assistant/create": {"create": create_assistant_schema},
    "/assistant/delete": {"delete": delete_assistant_schema},
    "/assistant/share": {"share_assistant": share_assistant_schema},
    "/assistant/list": {"list": {}},  # Get
    "/assistant/chat/codeinterpreter": {"chat": chat_assistant_schema},
    "/assistant/create/codeinterpreter": {
        "create": create_code_interpreter_assistant_schema
    },
    "/assistant/files/download/codeinterpreter": {"download": download_ci_files_schema},
    "/assistant/openai/thread/delete": {"delete": {}},
    "/assistant/openai/delete": {"delete": {}},
    "/assistant/remove_astp_permissions": {
        "remove_astp_permissions": remove_astp_perms_schema
    },
    "/assistant/get_group_assistant_conversations": {
        "get_group_assistant_conversations": get_group_assistant_conversations_schema
    },
    "/assistant/get_group_assistant_dashboards": {
        "get_group_assistant_dashboards": get_group_assistant_dashboards_schema
    },
    "/assistant/save_user_rating": {
        "save_user_rating": save_user_rating_schema
    },
    "/assistant/get_group_conversations_data": {
        "get_group_conversations_data": get_group_conversations_data_schema
    }
}

api_validators = {
    "/assistant/create": {"create": create_assistant_schema},
    "/assistant/delete": {"delete": delete_assistant_schema},
    "/assistant/share": {"share_assistant": share_assistant_schema},
    "/assistant/list": {"list": {}},  # Get
    "/assistant/chat_with_code_interpreter": {"chat": chat_assistant_schema},
    # "/": {
    #     "chat": chat_assistant_schema
    # },
    "/assistant/create/codeinterpreter": {
        "create": create_code_interpreter_assistant_schema
    },
    "/assistant/files/download/codeinterpreter": {"download": download_ci_files_schema},
    "/assistant/openai/thread/delete": {"delete": {}},
    "/assistant/openai/delete": {"delete": {}},
    "/assistant/remove_astp_permissions": {
        "remove_astp_permissions": remove_astp_perms_schema
    },
    "/assistant/get/system_user": {"get": {}},
    "/assistant/get_group_assistant_conversations": {
        "get_group_assistant_conversations": get_group_assistant_conversations_schema
    },
    "/assistant/get_group_assistant_dashboards": {
        "get_group_assistant_dashboards": get_group_assistant_dashboards_schema
    },
    "/assistant/save_user_rating": {
        "save_user_rating": save_user_rating_schema
    },
    "/assistant/get_group_conversations_data": {
        "get_group_conversations_data": get_group_conversations_data_schema
    }
}


def validate_data(name, op, data, api_accessed):
    validator = api_validators if api_accessed else validators
    if name in validator and op in validator[name]:
        schema = validator[name][op]
        try:
            validate(instance=data.get("data"), schema=schema)
        except ValidationError as e:
            print(e)
            raise ValidationError(f"Invalid data: {e.message}")
        print("Data validated")
    else:
        print(f"Invalid data or path: {name} - op:{op} - data: {data}")
        raise Exception("Invalid data or path")


def parse_and_validate(current_user, event, op, api_accessed, validate_body=True):
    data = {}
    if validate_body:
        try:
            data = json.loads(event["body"]) if event.get("body") else {}
        except json.decoder.JSONDecodeError as e:
            raise BadRequest("Unable to parse JSON body.")

    name = event["path"] if event.get("path") else "/"

    if not name:
        raise BadRequest("Unable to perform the operation, invalid request.")

    try:
        if validate_body:
            validate_data(name, op, data, api_accessed)
    except ValidationError as e:
        raise BadRequest(e.message)

    permission_checker = get_permission_checker(current_user, name, op, data)

    if not permission_checker(current_user, data):
        # Return a 403 Forbidden if the user does not have permission to append data to this item
        raise Unauthorized("User does not have permission to perform the operation.")

    return [name, data]


def validated(op, validate_body=True):
    def decorator(f):
        def wrapper(event, context):
            try:
                token = parseToken(event)
                api_accessed = token[:4] == "amp-"

                claims = (
                    api_claims(event, context, token)
                    if (api_accessed)
                    else get_claims(event, context, token)
                )
                idp_prefix = os.getenv('IDP_PREFIX')
                get_email = lambda text: text.split(idp_prefix + '_', 1)[1] if idp_prefix and text.startswith(idp_prefix + '_') else text
                current_user = get_email(claims['username'])

                current_user = claims["username"]
                print(f"User: {current_user}")
                if current_user is None:
                    raise Unauthorized("User not found.")

                [name, data] = parse_and_validate(
                    current_user, event, op, api_accessed, validate_body
                )

                data["access_token"] = token
                data["account"] = claims["account"]
                data["allowed_access"] = claims["allowed_access"]
                data["api_accessed"] = api_accessed

                # additional validator change from other lambdas
                data["is_group_sys_user"] = claims.get("is_group_sys_user", False)
                ###

                result = f(event, context, current_user, name, data)

                return {
                    "statusCode": 200,
                    "body": json.dumps(result, cls=CombinedEncoder),
                }
            except HTTPException as e:
                return {
                    "statusCode": e.status_code,
                    "body": json.dumps({"error": f"Error: {e.status_code} - {e}"}),
                }

        return wrapper

    return decorator


def get_claims(event, context, token):
    # https://cognito-idp.<Region>.amazonaws.com/<userPoolId>/.well-known/jwks.json

    oauth_issuer_base_url = os.getenv("OAUTH_ISSUER_BASE_URL")
    oauth_audience = os.getenv("OAUTH_AUDIENCE")

    jwks_url = f"{oauth_issuer_base_url}/.well-known/jwks.json"
    jwks = requests.get(jwks_url).json()

    header = jwt.get_unverified_header(token)
    print (token)
    rsa_key = {}
    for key in jwks["keys"]:
        if key["kid"] == header["kid"]:
            rsa_key = {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key["use"],
                "n": key["n"],
                "e": key["e"],
            }
            break

    if rsa_key:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            audience=oauth_audience,
            issuer=oauth_issuer_base_url,
        )

        idp_prefix = os.getenv('IDP_PREFIX')
        get_email = lambda text: text.split(idp_prefix + '_', 1)[1] if idp_prefix and text.startswith(idp_prefix + '_') else text

        user = get_email(payload['username'])

        # grab deafault account from accounts table
        dynamodb = boto3.resource("dynamodb")
        accounts_table_name = os.getenv("ACCOUNTS_DYNAMO_TABLE")
        if not accounts_table_name:
            raise ValueError("ACCOUNTS_DYNAMO_TABLE is not provided.")

        table = dynamodb.Table(accounts_table_name)
        account = None
        try:
            response = table.get_item(Key={"user": user})
            if "Item" not in response:
                raise ValueError(f"No item found for user: {user}")

            accounts = response["Item"].get("accounts", [])
            for acct in accounts:
                if acct["isDefault"]:
                    account = acct["id"]

        except Exception as e:
            print(f"Error retrieving default account: {e}")

        if not account:
            print("setting account to general_account")
            account = "general_account"

        payload["account"] = account
        payload["username"] = user
        # Here we can established the allowed access according to the feature flags in the future
        # For now it is set to full_access, which says they can do the operation upon entry of the validated function
        # current access types include: asssistants, share, dual_embedding, chat, file_upload
        payload["allowed_access"] = ["full_access"]
        return payload
    else:
        print("No RSA Key Found, likely an invalid OAUTH_ISSUER_BASE_URL")

    raise Unauthorized("No Valid Access Token Found")


def parseToken(event):
    token = None
    normalized_headers = {k.lower(): v for k, v in event["headers"].items()}
    authorization_key = "authorization"

    if authorization_key in normalized_headers:
        parts = normalized_headers[authorization_key].split()

        if len(parts) == 2:
            scheme, token = parts
            if scheme.lower() != "bearer":
                token = None

    if not token:
        raise Unauthorized("No Access Token Found")

    return token


def api_claims(event, context, token):
    print("API route was taken")

    # Set up DynamoDB connection
    dynamodb = boto3.resource("dynamodb")
    api_keys_table_name = os.getenv("API_KEYS_DYNAMODB_TABLE")
    if not api_keys_table_name:
        raise ValueError("API_KEYS_DYNAMODB_TABLE is not provided.")

    table = dynamodb.Table(api_keys_table_name)

    try:
        # Retrieve item from DynamoDB
        response = table.query(
            IndexName="ApiKeyIndex",
            KeyConditionExpression="apiKey = :apiKeyVal",
            ExpressionAttributeValues={":apiKeyVal": token},
        )
        items = response["Items"]

        if not items:
            print("API key does not exist.")
            raise LookupError("API key not found.")

        item = items[0]

        # Check if the API key is active
        if not item.get("active", False):
            print("API key is inactive.")
            raise PermissionError("API key is inactive.")

        # Optionally check the expiration date if applicable
        if (
            item.get("expirationDate")
            and datetime.strptime(item["expirationDate"], "%Y-%m-%d") <= datetime.now()
        ):
            print("API key has expired.")
            raise PermissionError("API key has expired.")

        # Check for access rights
        access = item.get("accessTypes", [])
        if (
            "assistants" not in access
            and "share" not in access
            and "full_access" not in access
        ):
            print("API doesn't have access to assistants")
            raise PermissionError(
                "API key does not have access to assistants functionality"
            )

        # Determine API user
        current_user = determine_api_user(item)

        rate_limit = item["rateLimit"]
        if is_rate_limited(current_user, rate_limit):
            rate = float(rate_limit["rate"])
            period = rate_limit["period"]
            print(f"You have exceeded your rate limit of ${rate:.2f}/{period}")
            raise Unauthorized(
                f"You have exceeded your rate limit of ${rate:.2f}/{period}"
            )

        # Update last accessed
        table.update_item(
            Key={"api_owner_id": item["api_owner_id"]},
            UpdateExpression="SET lastAccessed = :now",
            ExpressionAttributeValues={":now": datetime.now().isoformat()},
        )
        print("Last Access updated")

        # additional validator change from other lambdas
        is_group_sys_user = item.get("purpose", "") == "group"
        ###
        return {
            "username": current_user,
            "account": item["account"]["id"],
            "allowed_access": access,
            "is_group_sys_user": is_group_sys_user,
        }

    except Exception as e:
        print("Error during DynamoDB operation:", str(e))
        raise RuntimeError("Internal server error occurred: ", e)


def determine_api_user(data):
    key_type_pattern = r"/(.*?)Key/"
    match = re.search(key_type_pattern, data["api_owner_id"])
    key_type = match.group(1) if match else None

    if key_type == "owner":
        return data.get("owner")
    elif key_type == "delegate":
        return data.get("delegate")
    elif key_type == "system":
        return data.get("systemId")
    else:
        print("Unknown or missing key type in api_owner_id:", key_type)
        raise Exception("Invalid or unrecognized key type.")


def get_groups(user, token):
    return ["Amplify_Dev_Api"]
    # amplify_groups = get_user_cognito_amplify_groups(token)
    # return amplify_groups


def is_rate_limited(current_user, rate_limit):
    print(rate_limit)
    if rate_limit["period"] == "Unlimited":
        return False
    cost_calc_table = os.getenv("COST_CALCULATIONS_DYNAMO_TABLE")
    if not cost_calc_table:
        raise ValueError(
            "COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables."
        )

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(cost_calc_table)

    try:
        print("Query cost calculation table")
        response = table.query(KeyConditionExpression=Key("id").eq(current_user))
        items = response["Items"]
        if not items:
            print("Table entry does not exist. Cannot verify if rate limited.")
            return False

        rate_data = items[0]

        period = rate_limit["period"]
        col_name = f"{period.lower()}Cost"

        spent = rate_data[col_name]
        if period == "Hourly":
            spent = spent[
                datetime.now().hour
            ]  # Get the current hour as a number from 0 to 23
        print(f"Amount spent {spent}")
        return spent >= rate_limit["rate"]

    except Exception as error:
        print(f"Error during rate limit DynamoDB operation: {error}")
        return False
