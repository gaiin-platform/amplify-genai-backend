import string
from common.permissions import get_permission_checker
import json
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from common.encoders import CombinedEncoder

import os
import requests
from jose import jwt

from dotenv import load_dotenv

import boto3
import json
from datetime import datetime
from botocore.exceptions import ClientError
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


export_schema = {
    "type": "object",
    "properties": {
        "version": {
            "type": "number"
        },
        "history": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string"
                    },
                    "name": {
                        "type": "string"
                    },
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object"
                            #  You need to define the specific structure of "Message" objects as needed here
                        }
                    },
                    "compressedMessages": {
                        "type": ["array", "null"],
                        "items": {
                            "type": "number"
                        }
                    },
                    "model": {
                        "type": "string"
                        #  If OpenAIModel is an enum of specific strings, use the "enum" constraint
                    },
                    "prompt": {
                        "type": ["string", "null"]
                    },
                    "temperature": {
                        "type": ["number", "null"]
                    },
                    "folderId": {
                        "type": ["string", "null"]
                    },
                    "promptTemplate": {
                        "type": ["object", "null"]
                        #  You need to define the structure of Prompt object if necessary here
                    },
                    "tags": {
                        "type": ["array", "null"],
                        "items": {
                            "type": "string"
                        }
                    },
                    "maxTokens": {
                        "type": ["number", "null"]
                    },
                    "workflowDefinition": {
                        "type": ["object", "null"]
                        #  Define the structure of WorkflowDefinition, if there's a specific schema
                    },
                    "data": {
                        "type": ["object", "null"],
                        "additionalProperties": True
                    },
                    "codeInterpreterAssistantId": {
                        "type": ["string", "null"]
                    },
                    "isLocal": {
                        "type": ["boolean", "null"]
                    }
                },
                "required": ["id", "name", "messages", "model", "folderId"]
            }
        },
        "folders": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string"
                    },
                    "date": {
                        "type": ["string", "null"]
                    },
                    "name": {
                        "type": "string"
                    },
                    "type": {
                        "type": "string", # Assuming FolderType is directly translatable to a string in JSON schema.
                        "enum": ["personal", "shared", "archived"] # Replace with actual folder types if they differ.
                    }
                },
                "required": ["id", "name", "type"]
            }
        },
        "prompts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string"
                    },
                    "name": {
                        "type": "string"
                    },
                    "description": {
                        "type": "string"
                    },
                    "content": {
                        "type": "string"
                    },
                    "model": {
                        "type": ["string", "null"]
                    },
                    "folderId": {
                        "type": ["string", "null"]
                    },
                    "type": {
                        "type": ["string", "null"]
                    },
                    "data": {
                        "type": "object",
                        "properties": {
                            "rootPromptId": {
                                "type": ["string", "null"]
                            },
                            "code": {
                                "type": ["string", "null"]
                            }
                        },
                        "additionalProperties": True
                    }
                },
                "required": ["id", "name", "description", "content", "folderId", "type"]
            },
            "required": ["version", "history", "folders", "prompts"]
        }    
    }
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

create_tags_schema = {
    "type": "object",
    "properties": {
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

conversation_ids_schema = {
    "type": "object",
    "properties": {
        "conversationIds": {
            "type": "array",
            "items": {
                "type": "string",
            }
        }
    },
    "required": ["conversationIds"]
}


compressed_conversation_schema = {
    "type": "object",
    "properties": {
        "conversation": {
            "type": "array"
        },
        "conversationId" : {
            "type": "string",
        },
        "folder": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string"
                        },
                        "date": {
                            "type": "string",
                            "format": "date",
                            "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
                        },
                        "name": {
                            "type": "string"
                        },
                        "type": {
                            "type": "string",
                            "enum": ["chat", "prompt", "workflow"]
                        }
                    },
                    "required": ["id", "name", "type"]
                },
                {
                    "type": "null"
                }
            ]
        }
    },
    "required": ["conversation", "conversationId"]
}

set_metdata_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "The unique id for the datasource item."
        },
        "name": {
            "type": "string",
            "description": "The name of the data item."
        },
        "type": {
            "type": "string",
            "description": "The type of the data item."
        },
        "knowledge_base": {
            "type": "string",
            "description": "The knowledge base, default is 'default'.",
            "default": "default"
        },
        "data": {
            "type": "object",
            "description": "Additional properties for the data item.",
            "default": {}
        },
        "tags": {
            "type": "array",
            "description": "A list of tags associated with the data item.",
            "items": {
                "type": "string"
            },
            "default": []
        }
    },
    "required": ["id", "name", "type"]
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
    "/datasource/metadata/set": {
        "set": set_metdata_schema
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
    "/assistant/tags/create": {
        "create": create_tags_schema
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
    "/assistant/delete": {
    "delete": {}
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
    "/market/category/list" : {
    "list_categories": {}
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
    "/state/accounts/get": {
    "get": {}
    },
    "/state/conversation/upload": {   
        "conversation_upload": compressed_conversation_schema
    },
    "/state/conversation/get_multiple": {   
        "get_multiple_conversations": conversation_ids_schema
    },
    "/state/conversation/get": {
        "read" : {}
    },
    "/state/conversation/get_all": {
        "read" : {}
    },
    "/state/conversation/delete_multiple": {   
        "delete_multiple_conversations": conversation_ids_schema
    },
    "/state/conversation/delete": {
        "delete" : {}
    },
}

api_validators = {
    "/state/share": {
        "append": share_schema,
        "create": {}
    },
    "/state/share/load": {
        "load": share_load_schema
    },
    "/assistant/files/upload": {
        "upload": file_upload_schema
    },
    "/assistant/files/set_tags": {
        "set_tags": file_set_tags_schema
    },
    "/assistant/tags/delete": {
        "delete": user_delete_tag_schema
    },
    "/assistant/tags/create": {
        "create": create_tags_schema
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
            data = json.loads(event['body']) if event.get('body') else {}
        except json.decoder.JSONDecodeError as e:
            raise BadRequest("Unable to parse JSON body.")

    name = event['path']

    if not name:
        raise BadRequest("Unable to perform the operation, invalid request.")

    try:
        if validate_body:
            validate_data(name, op, data, api_accessed)
    except ValidationError as e:
        raise BadRequest(e.message)

    permission_checker = get_permission_checker(current_user, name, op, data)

    if not permission_checker(current_user, data):
        print("User does not have permission to perform the operation.")
        # Return a 403 Forbidden if the user does not have permission to append data to this item
        raise Unauthorized("User does not have permission to perform the operation.")

    return [name, data]


def validated(op, validate_body=True):
    def decorator(f):
        def wrapper(event, context):
            try:

                token = parseToken(event)
                api_accessed = token[:4] == 'amp-'

                claims = api_claims(event, context, token) if (api_accessed) else get_claims(event, context, token)

                current_user = claims['username']
                print(f"User: {current_user}")
                if current_user is None:
                    raise Unauthorized("User not found.")

                [name, data] = parse_and_validate(current_user, event, op, api_accessed, validate_body)
                
                data['access_token'] = token
                data['account'] = claims['account']
                data['allowed_access'] = claims['allowed_access']

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


def get_claims(event, context, token):
    # https://cognito-idp.<Region>.amazonaws.com/<userPoolId>/.well-known/jwks.json

    oauth_issuer_base_url = os.getenv('OAUTH_ISSUER_BASE_URL')
    oauth_audience = os.getenv('OAUTH_AUDIENCE')

    jwks_url = f'{oauth_issuer_base_url}/.well-known/jwks.json'
    jwks = requests.get(jwks_url).json()

    header = jwt.get_unverified_header(token)
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

    if rsa_key:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            audience=oauth_audience,
            issuer=oauth_issuer_base_url
        )

        get_email = lambda text: text.split('_', 1)[1] if '_' in text else None

        user = get_email(payload['username'])

        # grab deafault account from accounts table 
        dynamodb = boto3.resource('dynamodb')
        accounts_table_name = os.getenv('ACCOUNTS_DYNAMO_TABLE')
        if not accounts_table_name:
            raise ValueError("ACCOUNTS_DYNAMO_TABLE is not provided.")

        table = dynamodb.Table(accounts_table_name)
        account = None
        try:
            response = table.get_item(Key={'user': user})
            if 'Item' not in response:
                raise ValueError(f"No item found for user: {user}")

            accounts = response['Item'].get('accounts', [])
            for acct in accounts:
                if acct['isDefault']:
                    account = acct['id']
                    
            if (not account):
                account = 'general_account'
        except Exception as e:
            print(f"Error retrieving default account: {e}")
            raise Exception('Error retrieving default account')

        payload['account'] = account
        payload['username'] = user
        # Here we can established the allowed access according to the feature flags in the future
        # For now it is set to full_access, which says they can do the operation upon entry of the validated function
        # current access types include: asssistants, share, dual_embedding, chat, file_upload
        payload['allowed_access'] =  ['full_access']
        return payload
    else:
        print("No RSA Key Found, likely an invalid OAUTH_ISSUER_BASE_URL")

    raise Unauthorized("No Valid Access Token Found")


def parseToken(event):
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
        raise Unauthorized("No Access Token Found")
    
    return token


def api_claims(event, context, token):
    print("API route was taken")

    # Set up DynamoDB connection
    dynamodb = boto3.resource('dynamodb')
    api_keys_table_name = os.getenv('API_KEYS_DYNAMODB_TABLE')
    if not api_keys_table_name:
        raise ValueError("API_KEYS_DYNAMODB_TABLE is not provided.")

    table = dynamodb.Table(api_keys_table_name)

    try:
        # Retrieve item from DynamoDB
        response = table.query(
            IndexName='ApiKeyIndex',
            KeyConditionExpression='apiKey = :apiKeyVal',
            ExpressionAttributeValues={
                ':apiKeyVal': token
            }
        )
        items = response['Items']


        if not items:
            print("API key does not exist.")
            raise LookupError("API key not found.")
        
        item = items[0]

        # Check if the API key is active
        if (not item.get('active', False)):
            print("API key is inactive.")
            raise PermissionError("API key is inactive.")

        # Optionally check the expiration date if applicable
        if (item.get('expirationDate') and datetime.strptime(item['expirationDate'], "%Y-%m-%d") <= datetime.now()):
            print("API key has expired.")
            raise PermissionError("API key has expired.")

        # Check for access rights
        access = item.get('accessTypes', [])
        if ('file_upload' not in access and 'share' not in access
                                        and 'full_access' not in access):
            print("API key doesn't have access to the functionality")
            raise PermissionError("API key does not have access to the required functionality")

        # Update last accessed
        table.update_item(
            Key={'api_owner_id': item['api_owner_id']},
            UpdateExpression="SET lastAccessed = :now",
            ExpressionAttributeValues={':now': datetime.now().isoformat()}
        )
        print("Last Access updated")

        # Determine API user
        current_user = determine_api_user(item)

        return {'username': current_user, 'account': item['account']['id'], 'allowed_access': access}

    except Exception as e:
        print("Error during DynamoDB operation:", str(e))
        raise RuntimeError("Internal server error occurred: ", e)

def determine_api_user(data):
    key_type_pattern = r"/(.*?)Key/"
    match = re.search(key_type_pattern, data['api_owner_id'])
    key_type = match.group(1) if match else None

    if key_type == 'owner':
        return data.get('owner')
    elif key_type == 'delegate':
        return data.get('delegate')
    elif key_type == 'system':
        return data.get('systemId')
    else:
        print("Unknown or missing key type in api_owner_id:", key_type)
        raise Exception("Invalid or unrecognized key type.")
    

    