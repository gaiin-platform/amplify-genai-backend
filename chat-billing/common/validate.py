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

get_mtd_cost_schema = {
    "type": "object",
    "properties": {
        "email": {
            "type": "string",
            "description": "The email you determine month to date cost for.",
        },
    },
    "required": ["email"],
}


validators = {
    "/billing": {"report_generator": report_generator_schema},
    "/usage": {"get_mtd_cost": get_mtd_cost_schema},
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
            data = json.loads(event["body"]) if event.get("body") else {}
        except json.decoder.JSONDecodeError as e:
            raise BadRequest("Unable to parse JSON body.")

    name = event["path"]

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

                claims, token = get_claims(event, context)

                get_email = lambda text: text.split("_", 1)[1] if "_" in text else None
                current_user = get_email(claims["username"])

                print(f"User: {current_user}")

                # current_user = claims['user']['name']

                if current_user is None:
                    raise Unauthorized("User not found.")

                [name, data] = parse_and_validate(
                    current_user, event, op, validate_body
                )

                data["access_token"] = token
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


def get_claims(event, context):
    # https://cognito-idp.<Region>.amazonaws.com/<userPoolId>/.well-known/jwks.json

    oauth_issuer_base_url = os.getenv("OAUTH_ISSUER_BASE_URL")
    oauth_audience = os.getenv("OAUTH_AUDIENCE")

    jwks_url = f"{oauth_issuer_base_url}/.well-known/jwks.json"
    jwks = requests.get(jwks_url).json()

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

    header = jwt.get_unverified_header(token)
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

    if rsa_key:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            audience=oauth_audience,
            issuer=oauth_issuer_base_url,
        )
        return payload, token
    else:
        print("No RSA Key Found, likely an invalid OAUTH_ISSUER_BASE_URL")

    raise Unauthorized("No Valid Access Token Found")
