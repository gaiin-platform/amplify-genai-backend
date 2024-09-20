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


"""
Every service must define a schema each operation here. The schema is applied to the data field of the request
body. You do NOT need to include the top-level "data" key in the schema.
"""
optimize_schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Data Schema",
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string"
        },
        "query": {
            "type": "string"
        },
        "maxPlaceholders": {
            "type": "integer"
        }
    },
    "additionalProperties": True
}


"""
Every service must define the permissions for each operation here. 
The permission is related to a request path and to a specific operation.
"""
validators = {
    "/optimizer/prompt": {
        "optimize": optimize_schema
    },
}



api_validators = {
    "/optimizer/prompt": {
        "optimize": optimize_schema
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
        except jwt.InvalidAudienceError:
            print("Invalid audience.")
            raise Unauthorized("Invalid audience.")
        except jwt.InvalidIssuerError:
            print("Invalid issuer.")
            raise Unauthorized("Invalid issuer.")
        except Exception as e:
            print(f"Error during token validation: {e}")
            raise Unauthorized(f"Error during token validation: {e}")
    else:
        print("No RSA Key Found, likely an invalid OAUTH_ISSUER_BASE_URL")

    raise Unauthorized("No Valid Access Token Found")