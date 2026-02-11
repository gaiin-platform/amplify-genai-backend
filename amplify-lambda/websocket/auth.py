"""
WebSocket Authentication & Authorization

Validates JWT tokens and manages user permissions for WebSocket connections
"""

import json
import os
from typing import Optional, Dict
from pycommon.logger import getLogger

logger = getLogger("websocket_auth")


def authenticate_websocket_connection(event: Dict) -> Optional[Dict]:
    """
    Authenticate WebSocket connection request

    Checks for authentication token in:
    1. Query parameter: ?token=<jwt>
    2. Header: Sec-WebSocket-Protocol with token
    3. Query parameter: ?Authorization=Bearer%20<jwt>

    Args:
        event: Lambda event from API Gateway WebSocket

    Returns:
        Dict with user info if authenticated, None otherwise:
        {
            "user_id": "uuid",
            "email": "user@example.com",
            "groups": ["admin", "user"],
            "exp": 1234567890
        }
    """
    # Extract token from query parameters
    query_params = event.get('queryStringParameters', {}) or {}

    token = query_params.get('token')
    if not token:
        # Try Authorization header format
        auth_header = query_params.get('Authorization') or query_params.get('authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header[7:]  # Remove 'Bearer ' prefix

    # Try Sec-WebSocket-Protocol header (some clients send token here)
    if not token:
        headers = event.get('headers', {}) or {}
        protocol = headers.get('Sec-WebSocket-Protocol') or headers.get('sec-websocket-protocol')
        if protocol and ',' in protocol:
            # Format: "protocol, token"
            parts = [p.strip() for p in protocol.split(',')]
            if len(parts) > 1:
                token = parts[1]
        elif protocol:
            token = protocol

    if not token:
        logger.warning("No authentication token provided")
        return None

    # Verify and decode token
    user_info = verify_jwt_token(token)

    if not user_info:
        logger.warning("Invalid authentication token")
        return None

    logger.info(f"Authenticated user: {user_info.get('user_id')}")

    return user_info


def verify_jwt_token(token: str) -> Optional[Dict]:
    """
    Verify JWT token and extract user information

    Supports:
    1. AWS Cognito tokens (default)
    2. Custom JWT tokens

    Args:
        token: JWT token string

    Returns:
        Dict with user info if valid, None otherwise
    """
    try:
        import jwt
        from jwt import PyJWKClient
        import time

        # Get JWT configuration from environment
        jwt_issuer = os.environ.get('JWT_ISSUER')
        jwt_audience = os.environ.get('JWT_AUDIENCE')
        jwks_url = os.environ.get('JWKS_URL')

        if not jwt_issuer:
            logger.error("JWT_ISSUER not configured")
            return None

        # Cognito format: https://cognito-idp.{region}.amazonaws.com/{user_pool_id}
        if 'cognito' in jwt_issuer:
            return verify_cognito_token(token, jwt_issuer, jwt_audience, jwks_url)
        else:
            return verify_custom_token(token, jwt_issuer, jwt_audience)

    except Exception as e:
        logger.error(f"Token verification failed: {str(e)}")
        return None


def verify_cognito_token(
    token: str,
    issuer: str,
    audience: Optional[str],
    jwks_url: Optional[str]
) -> Optional[Dict]:
    """
    Verify AWS Cognito JWT token

    Args:
        token: JWT token
        issuer: Cognito issuer URL
        audience: Expected audience (client_id)
        jwks_url: JWKS endpoint URL (auto-generated if not provided)

    Returns:
        Dict with user info if valid
    """
    try:
        import jwt
        from jwt import PyJWKClient

        # Auto-generate JWKS URL if not provided
        if not jwks_url:
            jwks_url = f"{issuer}/.well-known/jwks.json"

        # Fetch signing keys
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Decode and verify
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience if audience else None,
            options={"verify_aud": bool(audience)}
        )

        # Extract user info from Cognito claims
        user_info = {
            "user_id": decoded.get('sub'),
            "email": decoded.get('email'),
            "username": decoded.get('cognito:username'),
            "groups": decoded.get('cognito:groups', []),
            "exp": decoded.get('exp'),
            "token_use": decoded.get('token_use')
        }

        # Verify token hasn't expired
        import time
        if user_info['exp'] and user_info['exp'] < time.time():
            logger.warning("Token expired")
            return None

        return user_info

    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Cognito token verification failed: {str(e)}")
        return None


def verify_custom_token(
    token: str,
    issuer: str,
    audience: Optional[str]
) -> Optional[Dict]:
    """
    Verify custom JWT token

    Args:
        token: JWT token
        issuer: Expected issuer
        audience: Expected audience

    Returns:
        Dict with user info if valid
    """
    try:
        import jwt

        # Get secret key from environment or AWS Secrets Manager
        secret_key = os.environ.get('JWT_SECRET_KEY')

        if not secret_key:
            logger.error("JWT_SECRET_KEY not configured")
            return None

        # Decode and verify
        decoded = jwt.decode(
            token,
            secret_key,
            algorithms=["HS256", "RS256"],
            issuer=issuer,
            audience=audience if audience else None,
            options={"verify_aud": bool(audience)}
        )

        # Extract user info
        user_info = {
            "user_id": decoded.get('sub') or decoded.get('user_id'),
            "email": decoded.get('email'),
            "username": decoded.get('username'),
            "groups": decoded.get('groups', []),
            "exp": decoded.get('exp')
        }

        # Verify token hasn't expired
        import time
        if user_info['exp'] and user_info['exp'] < time.time():
            logger.warning("Token expired")
            return None

        return user_info

    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Custom token verification failed: {str(e)}")
        return None


def authorize_document_access(user_id: str, document_id: str) -> bool:
    """
    Check if user has access to document

    Args:
        user_id: User UUID
        document_id: Document UUID

    Returns:
        True if authorized, False otherwise
    """
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("RAG_POSTGRES_DB_READ_ENDPOINT"),
            database=os.environ.get("RAG_POSTGRES_DB_NAME"),
            user=os.environ.get("RAG_POSTGRES_DB_USERNAME"),
            password=os.environ.get("RAG_POSTGRES_DB_SECRET")
        )

        cursor = conn.cursor()

        # Check document ownership or shared access
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM documents
            WHERE id = %s
              AND (user_id = %s OR %s = ANY(shared_with))
            """,
            (document_id, user_id, user_id)
        )

        count = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        return count > 0

    except Exception as e:
        logger.error(f"Authorization check failed: {str(e)}")
        return False


def generate_policy(effect: str, resource: str, principal_id: str, context: Dict = None) -> Dict:
    """
    Generate IAM policy for API Gateway

    Args:
        effect: "Allow" or "Deny"
        resource: Resource ARN (e.g., WebSocket route ARN)
        principal_id: User identifier
        context: Additional context to pass to integration

    Returns:
        IAM policy document
    """
    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': effect,
                    'Resource': resource
                }
            ]
        }
    }

    if context:
        policy['context'] = context

    return policy


def websocket_authorizer(event, context):
    """
    Custom authorizer for WebSocket API

    Called by API Gateway before $connect

    Args:
        event: Authorizer event with methodArn and queryStringParameters

    Returns:
        IAM policy allowing or denying connection
    """
    try:
        logger.info("WebSocket authorizer invoked")

        # Authenticate
        user_info = authenticate_websocket_connection(event)

        if not user_info:
            logger.warning("Authentication failed")
            raise Exception('Unauthorized')

        user_id = user_info['user_id']

        # Generate allow policy
        method_arn = event['methodArn']

        # Allow connection with user context
        policy = generate_policy(
            'Allow',
            method_arn,
            user_id,
            context={
                'user_id': user_id,
                'email': user_info.get('email', ''),
                'groups': ','.join(user_info.get('groups', []))
            }
        )

        logger.info(f"Authorized user: {user_id}")

        return policy

    except Exception as e:
        logger.error(f"Authorization failed: {str(e)}")
        raise Exception('Unauthorized')


# Rate limiting
class RateLimiter:
    """
    Simple in-memory rate limiter for WebSocket connections

    In production, use Redis or DynamoDB for distributed rate limiting
    """

    def __init__(self, max_connections_per_user: int = 5):
        self.max_connections = max_connections_per_user
        self.connections = {}  # {user_id: [connection_id, ...]}

    def check_limit(self, user_id: str) -> bool:
        """
        Check if user is within rate limit

        Args:
            user_id: User UUID

        Returns:
            True if within limit, False if exceeded
        """
        user_connections = self.connections.get(user_id, [])
        return len(user_connections) < self.max_connections

    def add_connection(self, user_id: str, connection_id: str):
        """Add connection for user"""
        if user_id not in self.connections:
            self.connections[user_id] = []
        self.connections[user_id].append(connection_id)

    def remove_connection(self, user_id: str, connection_id: str):
        """Remove connection for user"""
        if user_id in self.connections:
            self.connections[user_id] = [
                conn_id for conn_id in self.connections[user_id]
                if conn_id != connection_id
            ]
            if not self.connections[user_id]:
                del self.connections[user_id]


# Global rate limiter instance (in production, use Redis)
rate_limiter = RateLimiter(max_connections_per_user=10)
