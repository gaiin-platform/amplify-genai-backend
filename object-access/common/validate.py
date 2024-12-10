
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from common.permissions import get_permission_checker
import json
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from common.encoders import CombinedEncoder
import logging

import os
import requests
from jose import jwt

from dotenv import load_dotenv
import boto3
from datetime import datetime
import re
from boto3.dynamodb.conditions import Key

load_dotenv(dotenv_path=".env.local")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

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

update_object_permissions = {
    "type": "object",
    "properties": {
        "emailList": {
            "type": "array",
            "description": "An array of userids to update permissions for."
        },
        "dataSources": {
            "type": "array",
            "description": "A list of data sources to for permission updates."
        },
        "permissionLevel": {
            "type": "string",
            "description": "The permission level to set for the users."
        },
        "principalType": {
            "type": "string",
            "description": "The principal type to set for the users."
        },
        "objectType": {
            "type": "string",
            "description": "The object type to set for the object."
        },
        "policy": {
            "type": "string",
            "description": "Placehold for future fine grained policy map"
        }

    },
    "required": ["dataSources", "emailList", "permissionLevel"]
}

check_object_permissions = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "dataSources": {
            "type": "object",
            "additionalProperties": {
                "type": "string"
            }
        }
    },
    "required": ["dataSources"]
}

simulate_access_to_objects = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "objects": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {
                    "type": "string"
                }
            }
        }
    },
    "required": ["objects"]
}

in_amp_cogn_group_schema ={
    "type": "object",
    "properties": {
        "amplifyGroups": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "cognitoGroups": {
            "type": "array",
            "items": {
                "type": "string"
            }
        }
    },
    "anyOf": [
        {"required": ["amplifyGroups"]},
        {"required": ["cognitoGroups"]}
    ]
}

create_cognito_group_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "groupName": {
            "type": "string",
            "description": "The name of the group to create."
            },
        "groupDescription": {
            "type": "string",
            "description": "The description of the group to create."
        }
    },
    "required": ["groupName", "groupDescription"]
}

members_schema = {
    "type": "object",
    "patternProperties": {
        ".*": {  # This regex matches any string as the property name
            "type": "string",
            "enum": ["write", "read", "admin"]
        }
    }
}



update_group_type_schema = {
  "type": "object",
  "properties": {
    "group_id": {
      "type": "string",
      "description": "The ID of the group."
    },
    "types": {
        "type": "array",
        "items": {
            "type": "string"
        }
    }
  },
  "required": ["group_id", "types"]
}



create_admin_group_schema = {
    "type": "object",
    "properties": {
        "group_name": {
            "type": "string",
            "description": "The name of the group to be created."
        },
        "members": members_schema,
        },
        "types": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
    "required": ["group_name", "members"]
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
        "disclaimer": {
            "type": "string",
            "description": "Appended assistant response disclaimer related to the item"
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
    },
    "required": ["name", "description", "tags", "instructions", "dataSources"]
}

update_ast_schema = {
    "type": "object",
    "properties": {
        "group_id": {
            "type": "string",
            "description": "The ID of the group."
        },
        "update_type": {
            "type": "string",
            "enum": ["ADD", "REMOVE", "UPDATE"],
            "description": "Type of update to perform on assistants."
        },
        "assistants": {
            "oneOf": [
                {
                    "type": "array",
                    "items": create_assistant_schema
                },
                {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "description": "astp assistantId for REMOVE"
                    }
                }
            ]
        }
    },
    "required": ["group_id", "update_type", "assistants"]
} 


update_members_schema = {
    "type": "object",
    "properties": {
        "group_id": {
            "type": "string",
            "description": "The ID of the group."
        },
        "update_type": {
            "type": "string",
            "enum": ["ADD", "REMOVE"],
            "description": "Type of update to perform on members."
        },
        "members": {
            "anyOf": [
                members_schema,
                {
                   "type": "array",
                    "items": {
                        "type": "string",
                        "description": "member emails to  REMOVE "
                    } 
                }
            ]
        }
             
    },
    "required": ["group_id", "update_type", "members"]
}

update_members_perms_schema = {
    "type": "object",
    "properties": {
        "group_id": {
            "type": "string",
            "description": "The ID of the group."
        },
        "affected_members": members_schema,
    },
    "required": ["group_id", "affected_members"]
}

validators = {
    "/utilities/update_object_permissions": {
        "update_object_permissions": update_object_permissions
    },
    "/utilities/can_access_objects": {
        "can_access_objects": check_object_permissions
    },
    "/utilities/simulate_access_to_objects": {
        "simulate_access_to_objects": simulate_access_to_objects
    },
    "/utilities/create_cognito_group": {
        "create_cognito_group": create_cognito_group_schema
    },
    "/utilities/get_user_groups": {
        "read": {}
    },
    "/utilities/in_cognito_amp_groups" : {
        "in_group" : in_amp_cogn_group_schema
    },
    "/utilities/emails": {
        "read": {}
    },
    "/groups/create" : {
        'create': create_admin_group_schema
    },
    "/groups/members/update" : {
        "update": update_members_schema
    },
    "/groups/members/update_permissions" : {
        "update": update_members_perms_schema
    },
    "/groups/assistants/update" : {
        "update": update_ast_schema
    },
    "/groups/types/update": {
        'update' : update_group_type_schema
    },
    "/groups/delete" : {
        "delete": {}
    },
    "/groups/list" : {
        'list': {}
    },
    "/groups/members/list" : {
        'list': {}
    },
}


api_validators = {
     "/utilities/update_object_permissions": {
        "update_object_permissions": update_object_permissions
    },
    "/utilities/can_access_objects": {
        "can_access_objects": check_object_permissions
    },
    "/utilities/simulate_access_to_objects": {
        "simulate_access_to_objects": simulate_access_to_objects
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
            logger.error("JSON Decode Error: %s", e)
            raise BadRequest("Unable to parse JSON body.")

    name = event['path']
    logger.info("Validating data for user: %s, event path: %s, operation: %s", current_user, name, op)
    
    if not name:
        logger.error("Invalid request, no event path provided")
        raise BadRequest("Unable to perform the operation, invalid request.")

    try:
        if validate_body:
            validate_data(name, op, data, api_accessed)
    except ValidationError as e:
        logger.error("Validation error: %s", e)
        raise BadRequest(e.message)

    permission_checker = get_permission_checker(current_user, name, op, data)

    if not permission_checker(current_user, data):
        logger.warning("User: %s does not have permission for operation: %s", current_user, op)
        raise Unauthorized("User does not have permission to perform the operation.")

    logger.info("User: %s has permission for operation: %s", current_user, op)
    return [name, data]



def validated(op, validate_body=True):  # Note the added argument
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
                # Updated get_email function to incorporate idpPrefix
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
        
        payload['full_username'] = payload['username']

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
        # this call is coming from other lambdas 

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
