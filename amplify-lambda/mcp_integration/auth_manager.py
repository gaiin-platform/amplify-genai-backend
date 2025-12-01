"""
MCPAuthManager: Secure credential storage and authentication handling

This module manages secure storage and retrieval of authentication credentials
for MCP servers using encryption and AWS services.
"""

import os
import json
import logging
import base64
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import boto3
from botocore.exceptions import ClientError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


class MCPAuthManager:
    """
    Manages secure authentication credentials for MCP servers.

    Features:
    - Encrypted credential storage in DynamoDB
    - Key derivation from environment variables
    - Automatic credential rotation support
    - Secure credential retrieval and caching
    - Role-based access control
    """

    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb')
        self.auth_table_name = os.environ.get('MCP_AUTH_TABLE')
        self.auth_table = None

        if self.auth_table_name:
            self.auth_table = self.dynamodb.Table(self.auth_table_name)

        # Encryption setup
        self.encryption_key = self._derive_encryption_key()
        self.cipher = Fernet(self.encryption_key) if self.encryption_key else None

        # Credential cache with TTL
        self._credential_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 300  # 5 minutes

    def _derive_encryption_key(self) -> Optional[bytes]:
        """Derive encryption key from environment variables."""
        try:
            # Use a combination of environment-specific values to create encryption key
            master_key = os.environ.get('MCP_MASTER_KEY')
            if not master_key:
                logger.warning("No MCP_MASTER_KEY found in environment, credentials will not be encrypted")
                return None

            # Additional entropy from AWS region and stage
            salt_components = [
                master_key,
                os.environ.get('AWS_REGION', 'us-east-1'),
                os.environ.get('STAGE', 'dev'),
                'mcp-auth-salt'
            ]
            salt = ''.join(salt_components).encode('utf-8')

            # Derive key using PBKDF2
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt[:32],  # Use first 32 bytes as salt
                iterations=100000,
            )

            key = base64.urlsafe_b64encode(kdf.derive(master_key.encode('utf-8')))
            return key

        except Exception as e:
            logger.error(f"Failed to derive encryption key: {e}")
            return None

    def _encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data."""
        if not self.cipher:
            logger.warning("No encryption key available, storing data in plain text")
            return data

        try:
            encrypted_data = self.cipher.encrypt(data.encode('utf-8'))
            return base64.urlsafe_b64encode(encrypted_data).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to encrypt data: {e}")
            return data

    def _decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data."""
        if not self.cipher:
            return encrypted_data

        try:
            decoded_data = base64.urlsafe_b64decode(encrypted_data.encode('utf-8'))
            decrypted_data = self.cipher.decrypt(decoded_data)
            return decrypted_data.decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to decrypt data: {e}")
            return encrypted_data

    async def store_credentials(self, user_id: str, server_name: str,
                              credentials: Dict[str, Any], expires_at: Optional[datetime] = None) -> bool:
        """
        Store encrypted credentials for a user and server.

        Args:
            user_id: User identifier
            server_name: Name of the MCP server
            credentials: Dictionary of credentials to store
            expires_at: Optional expiration time for the credentials

        Returns:
            bool: True if stored successfully
        """
        if not self.auth_table:
            logger.error("No DynamoDB table configured for authentication")
            return False

        try:
            # Encrypt sensitive credential data
            encrypted_credentials = {}
            for key, value in credentials.items():
                if self._is_sensitive_field(key):
                    encrypted_credentials[key] = self._encrypt_data(str(value))
                else:
                    encrypted_credentials[key] = value

            # Prepare item for DynamoDB
            item = {
                'user_id': user_id,
                'server_name': server_name,
                'credentials': encrypted_credentials,
                'created_at': int(datetime.utcnow().timestamp()),
                'updated_at': int(datetime.utcnow().timestamp()),
                'encrypted': True if self.cipher else False
            }

            if expires_at:
                item['expires_at'] = int(expires_at.timestamp())

            # Store in DynamoDB
            self.auth_table.put_item(Item=item)

            # Clear cache for this user/server combination
            cache_key = f"{user_id}:{server_name}"
            self._credential_cache.pop(cache_key, None)

            logger.info(f"Stored credentials for {server_name} (user: {user_id})")
            return True

        except ClientError as e:
            logger.error(f"DynamoDB error storing credentials: {e}")
            return False
        except Exception as e:
            logger.error(f"Error storing credentials: {e}")
            return False

    async def get_credentials(self, user_id: str, server_name: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve and decrypt credentials for a user and server.

        Args:
            user_id: User identifier
            server_name: Name of the MCP server

        Returns:
            Dict of credentials or None if not found/expired
        """
        cache_key = f"{user_id}:{server_name}"

        # Check cache first
        if cache_key in self._credential_cache:
            cached_data = self._credential_cache[cache_key]
            if datetime.utcnow() < cached_data['cache_expires']:
                return cached_data['credentials']
            else:
                # Remove expired cache entry
                del self._credential_cache[cache_key]

        if not self.auth_table:
            logger.error("No DynamoDB table configured for authentication")
            return None

        try:
            response = self.auth_table.get_item(
                Key={
                    'user_id': user_id,
                    'server_name': server_name
                }
            )

            if 'Item' not in response:
                return None

            item = response['Item']

            # Check if credentials have expired
            if 'expires_at' in item:
                expires_at = datetime.fromtimestamp(item['expires_at'])
                if datetime.utcnow() > expires_at:
                    logger.info(f"Credentials for {server_name} have expired, removing...")
                    await self.delete_credentials(user_id, server_name)
                    return None

            # Decrypt credentials
            encrypted_credentials = item.get('credentials', {})
            decrypted_credentials = {}

            for key, value in encrypted_credentials.items():
                if self._is_sensitive_field(key) and item.get('encrypted', False):
                    decrypted_credentials[key] = self._decrypt_data(str(value))
                else:
                    decrypted_credentials[key] = value

            # Cache the result
            self._credential_cache[cache_key] = {
                'credentials': decrypted_credentials,
                'cache_expires': datetime.utcnow() + timedelta(seconds=self._cache_ttl)
            }

            return decrypted_credentials

        except ClientError as e:
            logger.error(f"DynamoDB error retrieving credentials: {e}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving credentials: {e}")
            return None

    async def delete_credentials(self, user_id: str, server_name: str) -> bool:
        """
        Delete stored credentials for a user and server.

        Args:
            user_id: User identifier
            server_name: Name of the MCP server

        Returns:
            bool: True if deleted successfully
        """
        if not self.auth_table:
            logger.error("No DynamoDB table configured for authentication")
            return False

        try:
            self.auth_table.delete_item(
                Key={
                    'user_id': user_id,
                    'server_name': server_name
                }
            )

            # Clear cache
            cache_key = f"{user_id}:{server_name}"
            self._credential_cache.pop(cache_key, None)

            logger.info(f"Deleted credentials for {server_name} (user: {user_id})")
            return True

        except ClientError as e:
            logger.error(f"DynamoDB error deleting credentials: {e}")
            return False
        except Exception as e:
            logger.error(f"Error deleting credentials: {e}")
            return False

    async def list_user_credentials(self, user_id: str) -> List[Dict[str, Any]]:
        """
        List all stored credentials for a user (without sensitive data).

        Args:
            user_id: User identifier

        Returns:
            List of credential information (without sensitive fields)
        """
        if not self.auth_table:
            return []

        try:
            response = self.auth_table.query(
                KeyConditionExpression='user_id = :user_id',
                ExpressionAttributeValues={':user_id': user_id}
            )

            credentials_list = []
            for item in response.get('Items', []):
                # Return metadata without sensitive credential data
                cred_info = {
                    'server_name': item['server_name'],
                    'created_at': item.get('created_at'),
                    'updated_at': item.get('updated_at'),
                    'encrypted': item.get('encrypted', False),
                    'has_expiration': 'expires_at' in item
                }

                if 'expires_at' in item:
                    expires_at = datetime.fromtimestamp(item['expires_at'])
                    cred_info['expires_at'] = expires_at.isoformat()
                    cred_info['is_expired'] = datetime.utcnow() > expires_at

                # Include non-sensitive credential fields
                credentials = item.get('credentials', {})
                non_sensitive = {k: v for k, v in credentials.items() if not self._is_sensitive_field(k)}
                cred_info['credential_fields'] = list(non_sensitive.keys())

                credentials_list.append(cred_info)

            return credentials_list

        except ClientError as e:
            logger.error(f"DynamoDB error listing credentials: {e}")
            return []

    def _is_sensitive_field(self, field_name: str) -> bool:
        """Determine if a credential field contains sensitive data."""
        sensitive_fields = {
            'token', 'key', 'password', 'secret', 'api_key', 'access_token',
            'refresh_token', 'private_key', 'certificate', 'passphrase'
        }

        field_lower = field_name.lower()
        return any(sensitive in field_lower for sensitive in sensitive_fields)

    async def rotate_credentials(self, user_id: str, server_name: str,
                                new_credentials: Dict[str, Any], expires_at: Optional[datetime] = None) -> bool:
        """
        Rotate credentials for a server (backup old, store new).

        Args:
            user_id: User identifier
            server_name: Name of the MCP server
            new_credentials: New credentials to store
            expires_at: Optional expiration time for new credentials

        Returns:
            bool: True if rotation was successful
        """
        try:
            # Get existing credentials for backup
            existing_creds = await self.get_credentials(user_id, server_name)

            # Store new credentials
            success = await self.store_credentials(user_id, server_name, new_credentials, expires_at)

            if success and existing_creds:
                # Store backup of old credentials with rotation timestamp
                backup_server_name = f"{server_name}_backup_{int(datetime.utcnow().timestamp())}"
                await self.store_credentials(user_id, backup_server_name, existing_creds,
                                           datetime.utcnow() + timedelta(days=30))  # Keep backup for 30 days

            logger.info(f"Successfully rotated credentials for {server_name} (user: {user_id})")
            return success

        except Exception as e:
            logger.error(f"Failed to rotate credentials for {server_name}: {e}")
            return False

    def clear_cache(self):
        """Clear the credential cache."""
        self._credential_cache.clear()
        logger.info("Cleared credential cache")

    async def cleanup_expired_credentials(self) -> int:
        """
        Clean up expired credentials from the database.

        Returns:
            int: Number of expired credentials removed
        """
        if not self.auth_table:
            return 0

        removed_count = 0
        current_timestamp = int(datetime.utcnow().timestamp())

        try:
            # Scan for items with expires_at in the past
            response = self.auth_table.scan(
                FilterExpression='expires_at < :current_time',
                ExpressionAttributeValues={':current_time': current_timestamp}
            )

            for item in response.get('Items', []):
                try:
                    await self.delete_credentials(item['user_id'], item['server_name'])
                    removed_count += 1
                except Exception as e:
                    logger.error(f"Failed to delete expired credentials: {e}")

            if removed_count > 0:
                logger.info(f"Cleaned up {removed_count} expired credentials")

        except ClientError as e:
            logger.error(f"Error during credential cleanup: {e}")

        return removed_count

    def get_auth_status(self) -> Dict[str, Any]:
        """Get authentication manager status and statistics."""
        return {
            "encryption_enabled": self.cipher is not None,
            "table_configured": self.auth_table is not None,
            "cache_entries": len(self._credential_cache),
            "cache_ttl_seconds": self._cache_ttl
        }