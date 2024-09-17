
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from common.permissions import get_permission_checker
import json
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from common.encoders import CombinedEncoder

import os
import requests
from jose import jwt

from dotenv import load_dotenv

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
            "description": "The public id of the assistant"
        },
    },
    "required": ["assistantId"]
}

create_assistant_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The name of the item"
        },
        "description": {
            "type": "string",
            "description": "A brief description of the item"
        },
        "assistantId": {
            "type": "string",
            "description": "The public id of the assistant"
        },
        "tags": {
            "type": "array",
            "description": "A list of tags associated with the item",
            "items": {
                "type": "string"
            }
        },
        "instructions": {
            "type": "string",
            "description": "Instructions related to the item"
        },
        "uri": {
            "oneOf": [
                {
                    "type": "string",
                    "description": "The endpoint that receives requests for the assistant"
                },
                {
                    "type": "null"
                }
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
                        "description": "The key of the data source"
                    }
                }
            }
        },
        "tools": {
            "type": "array",
            "description": "A list of tools associated with the item",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "The type of tool"
                    }
                }
            }
        }
    },
    "required": ["name", "description", "tags", "instructions", "dataSources", "tools"]
}

share_assistant_schema = {
    "type": "object",
    "properties": {
        "assistantId": {"type": "string"},
        "recipientUsers": {"type": "array", "items": {"type": "string"}},
        "accessType": {"type": "string"},
        "dataSources": {"type": "array", "items": {"type": "string"}},
        "policy": {"type": "string", "default": ""}
    },
    "required": ["assistantId", "recipientUsers", "accessType"],
    "additionalProperties": False
}

add_message_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "A unique identifier for the object."
        },
        "role": {
            "type": "string",
            "description": "The role of the user or assistant in the conversation."
        },
        "fileKeys": {
            "type": "array",
            "description": "A list of keys associated with files.",
            "items": {
                "type": "string"
            }
        },
        "content": {
            "type": "string",
            "description": "The textual content of the message."
        },
        "messageId": {
            "type": "string",
            "description": "The ID of the message."
        },
        "data": {
            "type": "object",
            "description": "Optional data as a dictionary with string keys and string values.",
            "additionalProperties": {
                "type": "string"
            }
        }
    },
    "required": ["id", "role", "content", "messageId"],
}

chat_assistant_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string"
        },
        "fileKeys": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string"
                    },
                    "content": {
                        "type": "string"
                    },
                    "role": {
                        "type": "string"
                    },
                    "type": {
                        "type": "string"
                    },
                    "data": {
                        "type": "object",
                        "additionalProperties": True
                    },
                    "codeInterpreterMessageData": {
                        "type": "object",
                        "properties": {
                            "threadId": {"type": "string"},
                            "role": {"type": "string"},
                            "textContent": {"type": "string"},
                            "content": {
                                "type": "array",
                                "items": {
                                    "oneOf": [
                                        {
                                            "type": "object",
                                            "properties": {
                                                "type": {"const": "image_file"},
                                                "value": {
                                                    "type": "object",
                                                    "properties": {
                                                        "file_key": {"type": "string"}
                                                    },
                                                    "required": ["file_key"]
                                                }
                                            },
                                            "required": ["type", "value"]
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "type": {"const": "file"},
                                                "value": {
                                                    "type": "object",
                                                    "properties": {
                                                        "file_key": {"type": "string"}
                                                    },
                                                    "required": ["file_key"]
                                                }
                                            },
                                            "required": ["type", "value"]
                                        },
                                    ]
                                }
                            }
                        },
                        "required": []
                    }
                },
                "required": ["id", "content", "role"]
            }
        }
    },
    "required": ["id", "fileKeys", "messages"]
}


run_thread_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "The identifier of the thread."
        },
        "assistantId": {
            "type": "string",
            "description": "The identifier of the assistant."
        },
        "instructions": {
            "type": "string",
            "description": "Instructions for the assistant (optional).",
            "default": "",
            "minLength": 0
        }
    },
    "required": ["id", "assistantId"]
}

id_request_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Id."
        }
    },
    "required": ["id"]
}

"""
Every service must define the permissions for each operation here. 
The permission is related to a request path and to a specific operation.
"""
validators = {
    "/assistant/create": {
        "create": create_assistant_schema
    },
    "/assistant/delete": {
        "delete": delete_assistant_schema
    },
    "/assistant/share": {
        "share_assistant": share_assistant_schema
    },
    "/assistant/thread/create": {
        "create": {}
    },
    "/assistant/thread/message/create": {
        "add_message": add_message_schema
    },
    "/assistant/thread/message/list": {
        "get_messages": id_request_schema
    },
    "/assistant/thread/run": {
        "run": run_thread_schema
    },
    "/assistant/thread/run/status": {
        "run_status": id_request_schema
    },
    "/assistant/chat": {
        "chat": chat_assistant_schema
    },
    "/assistant/chat_with_code_interpreter": {
        "chat": chat_assistant_schema
    },
    "/": {
        "chat": chat_assistant_schema
    },
}


def validate_data(name, op, data):
    if name in validators and op in validators[name]:
        schema = validators[name][op]
        try:
            validate(instance=data.get("data"), schema=schema)
        except ValidationError as e:
            print(e)
            raise ValidationError(f"Invalid data: {e.message}")
        print("Data validated")


def parse_and_validate(current_user, event, op, validate_body=True):
    data = {}
    if validate_body:
        try:
            data = json.loads(event['body']) if event.get('body') else {}
        except json.decoder.JSONDecodeError as e:
            raise BadRequest("Unable to parse JSON body.")

    name = event['path'] if event.get('path') else '/'

    if not name:
        raise BadRequest("Unable to perform the operation, invalid request.")

    try:
        if validate_body:
            validate_data(name, op, data)
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
                # Retrieve IDP_PREFIX from environment variables
                idp_prefix = os.getenv('IDP_PREFIX')

                # Extract claims and token
                claims, token = get_claims(event, context)

                # Updated get_email function to incorporate idpPrefix
                get_email = lambda text: text.split(idp_prefix + '_', 1)[1] if idp_prefix and text.startswith(idp_prefix + '_') else text
                current_user = get_email(claims['username'])

                print(f"User: {current_user}")

                if current_user is None:
                    raise Unauthorized("User not found.")

                # Parse and validate the event data
                [name, data] = parse_and_validate(current_user, event, op, validate_body)

                # Add access_token to data
                data['access_token'] = token
                result = f(event, context, current_user, name, data)

                return {
                    "statusCode": 200,
                    "body": json.dumps(result, cls=CombinedEncoder)
                }
            except HTTPException as e:
                return {
                    "statusCode": e.status_code,
                    "body": json.dumps({
                        "error": f"Error: {e.status_code} - {e}"
                    })
                }

        return wrapper

    return decorator

def get_claims(event, context):
    print("Extracting OAUTH_ISSUER_BASE_URL and OAUTH_AUDIENCE...")
    oauth_issuer_base_url = os.getenv('OAUTH_ISSUER_BASE_URL')
    oauth_audience = os.getenv('OAUTH_AUDIENCE')
    print(f"OAUTH_ISSUER_BASE_URL: {oauth_issuer_base_url}\nOAUTH_AUDIENCE: {oauth_audience}")

    jwks_url = f'{oauth_issuer_base_url}/.well-known/jwks.json'
    print(f"Retrieving JWKS from URL: {jwks_url}")
    jwks = requests.get(jwks_url).json()

    print("JWKS Fetch successful. Processing...")
    token = None
    normalized_headers = {k.lower(): v for k, v in event['headers'].items()}
    authorization_key = 'authorization'

    if authorization_key in normalized_headers:
        parts = normalized_headers[authorization_key].split()

        if len(parts) == 2:
            scheme, token = parts
            if scheme.lower() != 'bearer':
                token = None

    if not token:
        print("No Access Token Found")
        raise Unauthorized("No Access Token Found")

    print("Access Token Found, decoding...")
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
                "e": key["e"]
            }
            break

    if rsa_key:
        print("RSA Key Found, validating...")
        try:
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=ALGORITHMS,
                audience=oauth_audience,
                issuer=oauth_issuer_base_url
            )
            print("Token successfully validated.")
            print("Payload", payload )
            return payload, token
        except jwt.ExpiredSignatureError:
            print("Token has expired.")
            raise Unauthorized("Token has expired.")
        except jwt.JWTClaimsError as e:
            print(f"JWT Claims Error: {e}")
            raise Unauthorized(str(e)) 
        except Exception as e:
            print(f"Error during token validation: {e}")
            raise Unauthorized(f"Error during token validation: {e}")
    else:
        print("No RSA Key Found, likely an invalid OAUTH_ISSUER_BASE_URL")

    raise Unauthorized("No Valid Access Token Found")