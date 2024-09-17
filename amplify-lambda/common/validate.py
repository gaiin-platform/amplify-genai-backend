
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


export_schema = {
    "type": "object",
    "properties": {
        "version": {"type": "number"},
        "history": {"type": "array"},
        "folders": {"type": "array"},
        "prompts": {"type": "array"},
    },
    "required": ["version", "history", "folders", "prompts"]
}

share_schema = {
    "type": "object",
    "properties": {
        "note": {"type": "string"},
        "sharedWith": {"type": "array", "items": {"type": "string"}},
        "sharedData": export_schema
    },
    "required": ["sharedWith", "sharedData", "note"]
}

share_load_schema = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string"
        }
    },
    "required": ["key"]
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
        "fileKeys": {
            "type": "array",
            "description": "A list of file keys associated with the item",
            "items": {
                "type": "string"
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
    "required": ["name", "description", "tags", "instructions", "fileKeys", "tools"]
}

file_upload_schema = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": ["saveAsData", "createChunks", "ingestRag", "makeDownloadable", "extractText"]
                    },
                    "params": {
                        "type": "object",
                        "additionalProperties": True
                    }
                },
                "required": ["name"],
                "additionalProperties": False
            }
        },
        "type": {
            "type": "string"
        },
        "name": {
            "type": "string"
        },
        "knowledgeBase": {
            "type": "string"
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "data": {
            "type": "object"
        }
    },
    "required": ["actions", "type", "name", "knowledgeBase", "tags", "data"],
}

file_set_tags_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string"
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string"
            },
            "default": []
        }
    },
    "additionalProperties": False
}

user_list_tags_schema = {
    "type": "object",
    "properties": {
    },
    "additionalProperties": False
}

user_delete_tag_schema = {
    "type": "object",
    "properties": {
        "tag": {
            "type": "string"
        },
    },
    "additionalProperties": False
}

file_query_schema = {
    "type": "object",
    "properties": {
        "startDate": {
            "type": "string",
            "format": "date-time",
            "default": "2021-01-01T00:00:00Z"
        },
        "pageSize": {
            "type": "integer",
            "default": 10
        },
        "pageKey": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string"
                },
                "createdAt": {
                    "type": "string"
                },
                "type": {
                    "type": "string"
                }
            }
        },
        "namePrefix": {
            "type": ["string", "null"]
        },
        "createdAtPrefix": {
            "type": ["string", "null"]
        },
        "typePrefix": {
            "type": ["string", "null"]
        },
        "types": {
            "type": "array",
            "items": {
                "type": "string"
            },
            "default": []
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string"
            },
            "default": []
        },
        "pageIndex": {
            "type": "integer",
            "default": 0
        },
        "forwardScan": {
            "type": "boolean",
            "default": True
        },
        "sortIndex": {
            "type": "string",
            "default": "createdAt"
        }
    },
    "additionalProperties": False
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

key_request_schema = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": "Key."
        }
    },
    "required": ["key"]
}

id_and_category_request_schema = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Id."
        },
        "category": {
            "type": "string",
            "description": "Category."
        }
    },
    "required": ["id", "category"]
}

task_and_category_request_schema = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "Id."
        },
        "category": {
            "type": "string",
            "description": "Category."
        }
    },
    "required": ["id", "category"]
}

add_charge = {
    "type": "object",
    "properties": {
        "accountId": {"type": "string"},
        "charge": {"type": "number"},
        "description": {"type": "string"},
        "details": {"type": "object"},
    },
    "required": ["accountId", "charge", "description", "details"]
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
                    }
                },
                "required": ["id", "content"]
            }
        }
    },
    "required": ["id", "fileKeys", "messages"]
}

publish_item_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The name of the item"
        },
        "description": {
            "type": "string",
            "description": "A detailed description of the item"
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string"
            },
            "description": "Tags related to the item"
        },
        "category": {
            "type": "string",
            "description": "The category of the item"
        },
        "content": export_schema,
    },
    "required": ["name", "description", "tags", "category", "content"]
}

save_accounts_schema = {
    "type": "object",
    "properties": {
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "A unique identifier for the account."
                    },
                    "name": {
                        "type": "string",
                        "description": "The name of the account."
                    },
                    "isDefault": {
                        "type": "boolean",
                        "description": "Indicates if this is the default account."
                    }
                },
                "required": ["id", "name"]
            }
        }
    },
    "required": ["accounts"]
}

convert_schema = {
    "type": "object",
    "properties": {
        "format": {
            "type": "string",
            "description": "The format to convert to docx|pptx"
        },
        "conversationHeader": {
            "type": "string",
            "description": "A markdown header to use for each conversation"
        },
        "messageHeader": {
            "type": "string",
            "description": "A markdown header to use for each message"
        },
        "userHeader": {
            "type": "string",
            "description": "A markdown header to use for each user message"
        },
        "assistantHeader": {
            "type": "string",
            "description": "A markdown header to use for each assistant message"
        },
        "content": export_schema,
    },
    "required": ["format", "content"]
}

get_category_schema = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "description": "The category to fetch"
        }
    },
    "required": ["category"]
}

validators = {
    "/state/share": {
        "append": share_schema,
        "create": {}
    },
    "/state/base-prompts/get": {
        "get": {}
    },
    "/state/share/load": {
        "load": share_load_schema
    },
    "/assistant/files/upload": {
        "upload": file_upload_schema
    },
    "/assistant/files/download": {
        "download": key_request_schema
    },
    "/assistant/files/set_tags": {
        "set_tags": file_set_tags_schema
    },
    "/assistant/tags/delete": {
        "delete": user_delete_tag_schema
    },
    "/assistant/tags/list": {
        "list": user_list_tags_schema
    },
    "/assistant/files/query": {
        "query": file_query_schema
    },
    "/assistant/create": {
        "create": create_assistant_schema
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
    "/market/item/publish": {
        "publish_item": publish_item_schema
    },
    "/market/item/delete": {
        "delete_item": id_request_schema
    },
    "/market/ideate": {
        "ideate": task_and_category_request_schema
    },
    "/market/category/get": {
        "get_category": get_category_schema
    },
    "/market/item/get": {
        "get_item": id_request_schema
    },
    "/market/item/examples/get": {
        "get_examples": id_and_category_request_schema
    },
    "/chat/convert": {
        "convert": convert_schema
    },
    "/state/accounts/charge": {
        "create_charge": add_charge
    },
    "/state/accounts/save": {
        "save": save_accounts_schema
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

    name = event['path']

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
    


# Make sure ALGORITHMS is defined somewhere, e.g., ALGORITHMS = ["RS256"]

idpPrefix = os.environ['IDP_PREFIX']

def validated(op, validate_body=True):
    def decorator(f):
        def wrapper(event, context):
            try:
                print("Getting claims...")
                claims, token = get_claims(event, context)

                def get_email(text, idpPrefix):
                    if idpPrefix and text.startswith(idpPrefix + '_'):
                        return text.split(idpPrefix + '_', 1)[1]
                    else:
                        return text

                current_user = get_email(claims['username'], idpPrefix)

                print(f"User: {current_user}")

                if current_user is None:
                    raise Unauthorized("User not found.")

                print("Parsing and validating...")
                [name, data] = parse_and_validate(current_user, event, op, validate_body)
                data['access_token'] = token
                result = f(event, context, current_user, name, data)

                return {
                    "statusCode": 200,
                    "body": json.dumps(result, cls=CombinedEncoder)
                }
            except HTTPException as e:
                print(f"HTTPException occurred: {e}")
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

