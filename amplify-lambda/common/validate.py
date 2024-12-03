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
from datetime import datetime
import re
from boto3.dynamodb.conditions import Key

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

chat_input_schema = {
  "type": "object",
  "required": [
    "model",
    "temperature",
    "max_tokens",
    "messages"
  ],
  "properties": {
    "model": {
      "type": "string",
      "enum": [
        "gpt-35-turbo",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-1106-Preview",
        "anthropic.claude-3-haiku-20240307-v1:0",
        "anthropic.claude-3-5-sonnet-20240620-v1:0",
        "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        "us.anthropic.claude-3-5-haiku-20241022-v1:0",
        "us.anthropic.claude-3-opus-20240229-v1:0",
        "anthropic.claude-3-opus-20240229-v1:0",
        "mistral.mistral-7b-instruct-v0:2",
        "mistral.mixtral-8x7b-instruct-v0:1",
        "mistral.mistral-large-2402-v1:0"
      ]
    },
    "temperature": {
      "type": "number"
    },
    "max_tokens": {
      "type": "integer"
    },
    "dataSources": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "messages": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "role",
          "content"
        ],
        "properties": {
          "role": {
            "type": "string",
            "enum": [
              "system",
              "assistant",
              "user"
            ]
          },
          "content": {
            "type": "string"
          },
          "type": {
            "type": "string",
            "enum": [
              "prompt"
            ]
          }
        }
      }
    },
    "options": {
      "type": "object",
      "properties": {
        "dataSourceOptions": {
          "type": "object"
        },
        "ragOnly": {
          "type": "boolean"
        },
        "skipRag": {
          "type": "boolean"
        },
        "assistantId": {
          "type": "string"
        },
        "model": {
          "type": "object",
          "properties": {
            "id": {
              "type": "string",
              "enum": [
                "gpt-35-turbo",
                "gpt-4o",
                "gpt-4-1106-Preview",
                "anthropic.claude-3-haiku-20240307-v1:0",
                "anthropic.claude-3-5-sonnet-20240620-v1:0",
                "anthropic.claude-3-opus-20240229-v1:0",
                "mistral.mistral-7b-instruct-v0:2",
                "mistral.mixtral-8x7b-instruct-v0:1",
                "mistral.mistral-large-2402-v1:0"
              ]
            }
          }
        },
        "prompt": {
          "type": "string"
        }
      }
    }
  }
}

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
                        }
                    },
                    "compressedMessages": {
                        "type": ["array", "null"],
                        "items": {
                            "type": "number"
                        }
                    },
                    "model": {
                        "type": "object"
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
                        "type": "string", 
                        "enum": ["chat", "workflow", "prompt"] 
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
                        "type": ["object", "null"]
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
        },
        "groupId": {
            "type": ["string", "null"]
        },
    },
    "required": ["type", "name", "knowledgeBase", "tags", "data"],
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



key_request_schema = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": "Key."
        },
        "groupId": {
            "type": "string",
            "description": "Group Id."
        }
    },
    "required": ["key"]
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
                    },
                     "rateLimit": {
                        "type": "object",
                        "properties": {
                            "rate": { "type": ["number", "null"] },
                            "period": { "type": "string" } 
                        },
                        "description": "Cost restriction using the API key"
                    },
                },
                "required": ["id", "name", 'rateLimit']
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

save_settings_schema = {
    "type": "object",
    "properties": {
        "settings": {
            "type": "object",
            "properties": {
                "theme": {
                    "type": "string",
                    "enum": ["light", "dark"]
                },
                "modelOptions": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "boolean"
                    }
                },
                "featureOptions": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "boolean"
                    }
                }
            },
            "required": ["theme", "modelOptions", "featureOptions"]
        }
    },
    "required": ["settings"]
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
        "read": {}
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
    "/files/upload": {
        "upload": file_upload_schema
    },
    "/files/download": {
        "download": key_request_schema
    },
    "/files/set_tags": {
        "set_tags": file_set_tags_schema
    },
    "/files/tags/delete": {
        "delete": user_delete_tag_schema
    },
    "/files/tags/create": {
        "create": create_tags_schema
    },
    "/files/tags/list": {
        "list": user_list_tags_schema
    },
    "/files/query": {
        "query": file_query_schema
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
    "/chat": {
        "chat": chat_input_schema
    },
    "/state/settings/save": {
        "save": save_settings_schema
    },
    "/state/settings/get": {
        "get": {}
    },

}

api_validators = {
    "/state/share": {
        "read": {}
    },
    "/state/share/load": {
        "load": share_load_schema
    },
    "/files/upload": {
        "upload": file_upload_schema
    },
    "/files/set_tags": {
        "set_tags": file_set_tags_schema
    },
    "/files/tags/delete": {
        "delete": user_delete_tag_schema
    },
    "/files/tags/create": {
        "create": create_tags_schema
    },
    "/files/tags/list": {
        "list": user_list_tags_schema
    },
    "/files/query": {
        "query": file_query_schema
    },
    "/chat": {
        "chat": chat_input_schema
    },
    "/files/download": {
        "download": key_request_schema
    },
}

def validate_data(name, op, data, api_accessed):
    # print(f"Name: {name} and Op: {op} and Data: {data}")
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



# Make sure ALGORITHMS is defined somewhere, e.g., ALGORITHMS = ["RS256"]

idpPrefix = os.environ['IDP_PREFIX']

def validated(op, validate_body=True):
    def decorator(f):
        def wrapper(event, context):
            try:

                token = parseToken(event)
                api_accessed = token[:4] == 'amp-'

                claims = api_claims(event, context, token) if (api_accessed) else get_claims(event, context, token)


                idp_prefix = os.getenv('IDP_PREFIX')
                get_email = lambda text: text.split(idp_prefix + '_', 1)[1] if idp_prefix and text.startswith(idp_prefix + '_') else text
                current_user = get_email(claims['username'])

                print(f"User: {current_user}")
                if current_user is None:
                    raise Unauthorized("User not found.")

                [name, data] = parse_and_validate(current_user, event, op, api_accessed, validate_body)
                
                data['access_token'] = token
                data['account'] = claims['account']
                data['api_accessed'] = api_accessed
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

        idp_prefix = os.getenv('IDP_PREFIX')
        
        get_email = lambda text: text.split(idp_prefix + '_', 1)[1] if idp_prefix and text.startswith(idp_prefix + '_') else text

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
                    
        except Exception as e:
            print(f"Error retrieving default account: {e}")

        if (not account):
            print("setting account to general_account")
            account = 'general_account'   

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
        if ('file_upload' not in access and 'share' not in access  and
            'chat' not in access and 'full_access' not in access):
            print("API key doesn't have access to the functionality")
            raise PermissionError("API key does not have access to the required functionality")
        
        # Determine API user
        current_user = determine_api_user(item)
        
        rate_limit = item['rateLimit']
        if is_rate_limited(current_user, rate_limit):
                    rate = float(rate_limit['rate'])
                    period = rate_limit['period']
                    print(f"You have exceeded your rate limit of ${rate:.2f}/{period}")
                    raise Unauthorized(f"You have exceeded your rate limit of ${rate:.2f}/{period}")

        # Update last accessed
        table.update_item(
            Key={'api_owner_id': item['api_owner_id']},
            UpdateExpression="SET lastAccessed = :now",
            ExpressionAttributeValues={':now': datetime.now().isoformat()}
        )
        print("Last Access updated")

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
    


def is_rate_limited(current_user, rate_limit): 
    print(rate_limit)
    if rate_limit['period'] == 'Unlimited': return False
    
    cost_calc_table = os.getenv('COST_CALCULATIONS_DYNAMO_TABLE')
    if not cost_calc_table:
        raise ValueError("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.")

    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(cost_calc_table)

    try:
        print("Query cost calculation table")
        response = table.query(
            KeyConditionExpression=Key('id').eq(current_user) 
        )
        items = response['Items']
        if not items:
            print("Table entry does not exist. Cannot verify if rate limited.")
            return False

        rate_data = items[0] 

        period = rate_limit['period']
        col_name = f"{period.lower()}Cost"

        spent = rate_data[col_name]
        if (period == 'Hourly'): spent = spent[datetime.now().hour] # Get the current hour as a number from 0 to 23
        print(f"Amount spent {spent}")
        return spent >= rate_limit['rate']

    except Exception as error:
        print(f"Error during rate limit DynamoDB operation: {error}")
        return False
