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


def get_claims(event, context):

    oauth_issuer_base_url = os.getenv('OAUTH_ISSUER_BASE_URL')
    oauth_audience = os.getenv('OAUTH_AUDIENCE')

    jwks_url = f'{oauth_issuer_base_url}/.well-known/jwks.json'
    jwks = requests.get(jwks_url).json()

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
        return payload
    else:
        print("No RSA Key Found, likely an invalid OAUTH_ISSUER_BASE_URL")

    raise Unauthorized("No Valid Access Token Found")
