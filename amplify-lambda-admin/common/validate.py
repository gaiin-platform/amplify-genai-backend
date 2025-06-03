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


update_admin_config_schema = {
    "type": "object",
    "properties": {
        "configurations": {
            "type": "array",
            "items": {
                "oneOf": [
                    {
                        # Configuration for 'admins'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "admins"
                            },
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                },
                                "minItems": 1
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'applicationVariables'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "applicationVariables"
                            },
                            "data": {
                                "type": "object",
                                "additionalProperties": {
                                    "type": "string"
                                }
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'applicationSecrets'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "applicationSecrets"
                            },
                            "data": {
                                "type": "object",
                                "additionalProperties": {
                                    "type": "string"
                                }
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'featureFlags'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "featureFlags"
                            },
                            "data": {
                                "type": "object",
                                "patternProperties": {
                                    "^.*$": {
                                        "type": "object",
                                        "properties": {
                                            "enabled": {
                                                "type": "boolean"
                                            },
                                            "userExceptions": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            },
                                            "amplifyGroupExceptions": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            }
                                        },
                                        "required": ["enabled", "userExceptions"],
                                        "additionalProperties": False
                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'openaiEndpoints'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "openaiEndpoints"
                            },
                            "data": {
                            "type": "object",
                            "properties": {
                                "models": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "patternProperties": {
                                    "^.*$": {
                                        "type": "object",
                                        "properties": {
                                        "endpoints": {
                                            "type": "array",
                                            "items": {
                                            "type": "object",
                                            "properties": {
                                                "url": { "type": "string" },
                                                "key": { "type": "string" }
                                            },
                                            "required": ["url", "key"],
                                            "additionalProperties": False
                                            },
                                            "minItems": 1
                                        }
                                        },
                                        "required": ["endpoints"],
                                        "additionalProperties": False
                                    }
                                    },
                                    "additionalProperties": False
                                }
                                }
                            },
                            "required": ["models"],
                            "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'supportedModels'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "supportedModels"
                            },
                            "data": {
                                "type": "object",
                                "patternProperties": {
                                    "^.*$": {
                                        "type": "object",
                                        "properties": {
                                            "id": {
                                                "type": "string"
                                            },
                                            "name": {
                                                "type": "string"
                                            },
                                            "provider": {
                                                "type": "string"
                                            },
                                            "description": {
                                                "type": "string"
                                            },
                                             "isAvailable": {
                                                "type": "boolean"
                                            },
                                            "isBuiltIn": {
                                                "type": "boolean"
                                            },
                                            "systemPrompt": {
                                                "type": "string"
                                            },
                                            "supportsSystemPrompts": {
                                                "type": "boolean"
                                            },
                                             "supportsImages": {
                                                "type": "boolean"
                                            },
                                            "supportsReasoning": {
                                                "type": "boolean"
                                            },
                                             "inputContextWindow": {
                                                "type": "number"
                                            },
                                             "outputTokenLimit": {
                                                "type": "number"
                                            },
                                            "inputTokenCost": {
                                                "type": "number"
                                            },
                                             "outputTokenCost": {
                                                "type": "number"
                                            },
                                             "cachedTokenCost": {
                                                "type": "number"
                                            },
                                            "exclusiveGroupAvailability": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            }
                                        },
                                        "required": ["id","name", "provider", "description", "isAvailable",  "isBuiltIn",
                                                     "supportsImages", "supportsReasoning", "supportsSystemPrompts", "systemPrompt",
                                                     "inputContextWindow", "outputTokenLimit", "inputTokenCost", "outputTokenCost", 
                                                     "cachedTokenCost", "exclusiveGroupAvailability"],
                                        "additionalProperties": False
                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'defaultModels'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "defaultModels"
                            },
                            "data": {
                                "type": "object",
                                "properties": {
                                    "user": {"type": ["string", "null"]},
                                    "advanced": {"type": ["string", "null"]},
                                    "cheapest": {"type": ["string", "null"]},
                                    "documentCaching": {"type": ["string", "null"]},
                                    "agent": {"type": ["string", "null"]},
                                    "embeddings": {"type": ["string", "null"]},
                                    "qa": {"type": ["string", "null"]}
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'amplifyGroups'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "amplifyGroups"
                            },
                            "data": {
                                "type": "object",
                                "patternProperties": {
                                    "^.*$": {
                                        "type": "object",
                                        "properties": {
                                            "groupName": {
                                                "type": "string"
                                            },
                                            "createdBy": {
                                                "type": "string"
                                            },
                                            "members": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            },
                                            "includeFromOtherGroups": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            }
                                        },
                                        "required": ["groupName", "createdBy", "members", ],
                                        "additionalProperties": False

                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'assistantAdminGroups'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "assistantAdminGroups"
                            },
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "group_id": {"type": "string"},
                                        "amplifyGroups": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "isPublic": {"type": "boolean"},
                                        "supportConvAnalysis": {"type": "boolean"},
                                    },
                                    "required": ["group_id", "amplifyGroups", "isPublic", "supportConvAnalysis"],
                                "additionalProperties": False
                                },
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'powerPointTemplates'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "powerPointTemplates"
                            },
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "isAvailable": {"type": "boolean"},
                                        "amplifyGroups": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        }
                                    },
                                    "required": ["name", "isAvailable", "amplifyGroups"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'promtCostAlert'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "promtCostAlert"
                            },
                            "data": {
                                "type": "object",
                                "properties": {
                                    "isActive": {"type": "boolean"},
                                    "alertMessage": {"type": "string"},
                                    "cost": {"type": "number"},
                                },
                                "required": ["isActive", "alertMessage", "cost"],
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'emailSupport'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "emailSupport"
                            },
                            "data": {
                                "type": "object",
                                "properties": {
                                    "isActive": {"type": "boolean"},
                                    "email": {"type": "string"},
                                },
                                "required": ["isActive", "email"],
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                     {
                        # Configuration for 'defaultConversationStorage'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "defaultConversationStorage"
                            },
                            "data": {
                                "type": "string",
                                "enum": ["future-local", "future-cloud"],
                            },
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                                
                    {
                        # Configuration for 'rateLimit'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "rateLimit"
                            },
                            "data": {
                                "type": "object",
                                "properties": {
                                    "period": {"type": "string"},
                                    "rate": { "type": ["number", "null"] }, 
                                },
                                "required": ["period", "rate"],
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                    {
                        # Configuration for 'integrations'
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "const": "integrations"
                            },
                            "data": {
                                "type": "object",
                                "patternProperties": {
                                    "^(google|microsoft|drive|github|slack)$": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "id": {"type": "string"},
                                                "icon": {"type": "string"},
                                                "description": {"type": "string"},
                                                "isAvailable": {"type": "boolean"},
                                            },
                                            "required": ["name", "id", "icon", "description"],
                                            "additionalProperties": False
                                        },
                                    }
                                },
                                "additionalProperties": False
                            }
                        },
                        "required": ["type", "data"],
                        "additionalProperties": False
                    },
                ]
            }
        }
    },
    "required": ["configurations"],
    "additionalProperties": False
}

auth_as_admin_schema = {
    "type": "object",
    "properties": {
        "purpose": {
            "type": "string"
        }
    },
    "required": ["purpose"]
}

upload_pptx_schema = {
    "type": "object",
    "properties": {
        "fileName": {
            "type": "string"
        },
        "isAvailable" : {
            "type": "boolean"
        },
        "amplifyGroups": {
            "type": "array",
            "items": {"type": "string"}
        },
        "contentType" : {
            "type": "string"
        },
        "md5" : {
            "type": "string"
        }
    },
    "required": ["fileName", "isAvailable",  "amplifyGroups", "contentType", "md5"]
}

verify_in_amp_group_schema = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {"type": "string"}
        }
    },
    "required": ["groups"]
}

add_user_access_ast_admin = {
    "type": "object",
    "properties": {
        "users": {
            "type": "array",
            "items": {"type": "string"}
        }
    },
    "required": ["users"]
}


validators = {
    "/amplifymin/configs": {
        "read": {} #get
    },
    "/amplifymin/configs/update": {
        "update": update_admin_config_schema
    },
    "/amplifymin/feature_flags": {
        "read": {} 
    },
    "/amplifymin/auth": {
        "read": auth_as_admin_schema
    },
    "/amplifymin/pptx_templates": {
        "read": {} 
    },
    "/amplifymin/pptx_templates/delete": {
        "delete": {} 
    },
    "/amplifymin/pptx_templates/upload": {
        "upload": upload_pptx_schema
    },
    "/amplifymin/verify_amp_member" : {
        "read": verify_in_amp_group_schema
    },
    "/amplifymin/amplify_groups/list" : {
        "read": {}
    },
    "/amplifymin/user_app_configs": {
        "read": {}
    }
    
}

api_validators = {
    "/amplifymin/auth": {
        "read": auth_as_admin_schema
    },
    "/amplifymin/verify_amp_member" : {
        "read": verify_in_amp_group_schema
    },
}


def validate_data(name, op, data, api_accessed):
    print(f" data : {data} ")

    print(f" data or path: {name} - op:{op} ")
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
        
        idp_prefix: str = os.getenv('IDP_PREFIX') or ''
        idp_prefix = idp_prefix.lower()
        print(f"IDP_PREFIX from env: {idp_prefix}")
        print(f"Original username: {payload['username']}")

        def get_email(text: str):
            print(f"Input text: {text}")
            print(f"Checking if text starts with: {idp_prefix + '_'}")

            if len(idp_prefix) > 0 and text.startswith(idp_prefix + '_'):
                result = text.split(idp_prefix + '_', 1)[1]
                print(f"Text matched pattern, returning: {result}")
                return result
            
            print(f"Text did not match pattern, returning original: {text}")
            return text

        user = get_email(payload['username'])
        print(f"Final user value: {user}")
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
        # if ('admin' not in access):
        #     # and 'full_access' not in access
        #     print("API doesn't have access to api functionality")
        #     raise PermissionError("API key does not have access to api functionality")
        
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

        return {'username': current_user, 'account': item['account'], 'allowed_access': access}

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
