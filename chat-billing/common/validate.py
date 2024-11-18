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


report_generator_schema = {
    "type": "object",
    "properties": {
        "emails": {
            "type": "array",
            "items": {"type": "string", "format": "email"},
            "description": "These are the emails you will collect usage data for.",
        },
    },
    "required": ["emails"],
}


validators = {
    "/billing": {"report_generator": report_generator_schema},
}

api_validators = {
    "/billing": {"report_generator": report_generator_schema},
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
        if ('billing' not in access):
            # and 'full_access' not in access
            print("API doesn't have access to api key functionality")
            raise PermissionError("API key does not have access to api key functionality")
        
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
