"""
MCPServerConfigManager: Manages MCP server configurations

This module handles server configuration storage and retrieval from both
environment variables and DynamoDB, providing a unified configuration interface.
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import boto3
from botocore.exceptions import ClientError

from .direct_client import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPServerConfigManager:
    """
    Manages MCP server configurations from multiple sources.

    Sources:
    - Environment variables for default configurations
    - DynamoDB for user-specific configurations
    - Runtime configurations for dynamic servers
    """

    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb')
        self.configs_table_name = os.environ.get('MCP_SERVERS_CONFIG_TABLE')
        self.configs_table = None

        if self.configs_table_name:
            self.configs_table = self.dynamodb.Table(self.configs_table_name)

        # Cache for frequently accessed configurations
        self._config_cache: Dict[str, MCPServerConfig] = {}
        self._default_configs: Dict[str, MCPServerConfig] = {}

        # Load default configurations
        self._load_default_configs()

    def _load_default_configs(self):
        """Load default MCP server configurations from environment variables."""
        try:
            # Default server configurations
            default_servers = {
                "filesystem": {
                    "command": "npx",
                    "args": ["@modelcontextprotocol/server-filesystem", "/tmp"],
                    "env": {},
                    "description": "File operations and document processing"
                },
                "memory": {
                    "command": "npx",
                    "args": ["@modelcontextprotocol/server-memory"],
                    "env": {},
                    "description": "Persistent knowledge management"
                },
                "github": {
                    "command": "npx",
                    "args": ["@modelcontextprotocol/server-github"],
                    "env": {
                        "GITHUB_PERSONAL_ACCESS_TOKEN": os.environ.get("MCP_GITHUB_TOKEN", "")
                    },
                    "description": "Repository operations and code analysis",
                    "requires_auth": True
                },
                "sqlite": {
                    "command": "npx",
                    "args": ["@modelcontextprotocol/server-sqlite", "--db-path",
                            os.environ.get("MCP_SQLITE_PATH", "/tmp/mcp_test.db")],
                    "env": {},
                    "description": "Database query operations",
                    "requires_auth": False
                },
                "brave-search": {
                    "command": "npx",
                    "args": ["@modelcontextprotocol/server-brave-search"],
                    "env": {
                        "BRAVE_API_KEY": os.environ.get("MCP_BRAVE_API_KEY", "")
                    },
                    "description": "Web search capabilities",
                    "requires_auth": True
                }
            }

            for name, config in default_servers.items():
                # Only include servers that have required authentication
                if config.get("requires_auth", False):
                    # Check if required environment variables are set
                    required_env_set = all(
                        value for value in config["env"].values() if value
                    )
                    if not required_env_set:
                        logger.warning(f"Skipping {name} server - missing required authentication")
                        continue

                server_config = MCPServerConfig(
                    name=name,
                    command=config["command"],
                    args=config["args"],
                    env=config["env"],
                    cwd=None,
                    timeout=30
                )

                self._default_configs[name] = server_config
                logger.info(f"Loaded default config for {name} server")

        except Exception as e:
            logger.error(f"Failed to load default configurations: {e}")

    async def get_server_config(self, server_name: str, user_id: Optional[str] = None) -> Optional[MCPServerConfig]:
        """
        Get server configuration by name, checking user-specific configs first.

        Args:
            server_name: Name of the MCP server
            user_id: Optional user ID for user-specific configurations

        Returns:
            MCPServerConfig or None if not found
        """
        cache_key = f"{user_id or 'default'}:{server_name}"

        # Check cache first
        if cache_key in self._config_cache:
            return self._config_cache[cache_key]

        try:
            # Check user-specific configuration first
            if user_id and self.configs_table:
                user_config = await self._get_user_server_config(user_id, server_name)
                if user_config:
                    self._config_cache[cache_key] = user_config
                    return user_config

            # Fall back to default configuration
            if server_name in self._default_configs:
                config = self._default_configs[server_name]
                self._config_cache[cache_key] = config
                return config

            logger.warning(f"No configuration found for server: {server_name}")
            return None

        except Exception as e:
            logger.error(f"Failed to get server config for {server_name}: {e}")
            return None

    async def _get_user_server_config(self, user_id: str, server_name: str) -> Optional[MCPServerConfig]:
        """Get user-specific server configuration from DynamoDB."""
        try:
            response = self.configs_table.get_item(
                Key={
                    'user_id': user_id,
                    'server_name': server_name
                }
            )

            if 'Item' not in response:
                return None

            item = response['Item']

            # Check if server is enabled
            if not item.get('enabled', True):
                return None

            server_config_data = item.get('server_config', {})

            return MCPServerConfig(
                name=server_name,
                command=server_config_data.get('command', ''),
                args=server_config_data.get('args', []),
                env=server_config_data.get('env', {}),
                cwd=server_config_data.get('cwd'),
                timeout=server_config_data.get('timeout', 30)
            )

        except ClientError as e:
            logger.error(f"DynamoDB error getting user server config: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting user server config: {e}")
            return None

    async def save_user_server_config(self, user_id: str, server_config: MCPServerConfig,
                                    enabled: bool = True, auth_info: Optional[Dict[str, Any]] = None) -> bool:
        """
        Save user-specific server configuration to DynamoDB.

        Args:
            user_id: User identifier
            server_config: Server configuration to save
            enabled: Whether the server is enabled for this user
            auth_info: Optional authentication information

        Returns:
            bool: True if saved successfully
        """
        if not self.configs_table:
            logger.error("No DynamoDB table configured for server configs")
            return False

        try:
            item = {
                'user_id': user_id,
                'server_name': server_config.name,
                'server_config': {
                    'command': server_config.command,
                    'args': server_config.args,
                    'env': server_config.env,
                    'cwd': server_config.cwd,
                    'timeout': server_config.timeout
                },
                'enabled': enabled,
                'created_at': int(datetime.utcnow().timestamp()),
                'updated_at': int(datetime.utcnow().timestamp())
            }

            if auth_info:
                item['auth_info'] = auth_info

            self.configs_table.put_item(Item=item)

            # Clear cache for this user/server combination
            cache_key = f"{user_id}:{server_config.name}"
            self._config_cache.pop(cache_key, None)

            logger.info(f"Saved server config for {server_config.name} (user: {user_id})")
            return True

        except ClientError as e:
            logger.error(f"DynamoDB error saving user server config: {e}")
            return False
        except Exception as e:
            logger.error(f"Error saving user server config: {e}")
            return False

    async def delete_user_server_config(self, user_id: str, server_name: str) -> bool:
        """
        Delete user-specific server configuration.

        Args:
            user_id: User identifier
            server_name: Name of the server to delete

        Returns:
            bool: True if deleted successfully
        """
        if not self.configs_table:
            logger.error("No DynamoDB table configured for server configs")
            return False

        try:
            self.configs_table.delete_item(
                Key={
                    'user_id': user_id,
                    'server_name': server_name
                }
            )

            # Clear cache
            cache_key = f"{user_id}:{server_name}"
            self._config_cache.pop(cache_key, None)

            logger.info(f"Deleted server config for {server_name} (user: {user_id})")
            return True

        except ClientError as e:
            logger.error(f"DynamoDB error deleting user server config: {e}")
            return False
        except Exception as e:
            logger.error(f"Error deleting user server config: {e}")
            return False

    async def list_available_servers(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all available MCP servers for a user.

        Args:
            user_id: Optional user ID for user-specific configurations

        Returns:
            List of server information dictionaries
        """
        servers = []

        # Add default servers
        for name, config in self._default_configs.items():
            server_info = {
                "name": name,
                "type": "default",
                "command": config.command,
                "description": self._get_server_description(name),
                "enabled": True
            }
            servers.append(server_info)

        # Add user-specific servers if user_id provided
        if user_id and self.configs_table:
            try:
                user_servers = await self._list_user_servers(user_id)
                servers.extend(user_servers)
            except Exception as e:
                logger.error(f"Failed to list user servers: {e}")

        return servers

    async def _list_user_servers(self, user_id: str) -> List[Dict[str, Any]]:
        """List user-specific servers from DynamoDB."""
        try:
            response = self.configs_table.query(
                KeyConditionExpression='user_id = :user_id',
                ExpressionAttributeValues={':user_id': user_id}
            )

            user_servers = []
            for item in response.get('Items', []):
                server_info = {
                    "name": item['server_name'],
                    "type": "user",
                    "enabled": item.get('enabled', True),
                    "created_at": item.get('created_at'),
                    "updated_at": item.get('updated_at')
                }

                server_config = item.get('server_config', {})
                server_info["command"] = server_config.get('command', '')

                user_servers.append(server_info)

            return user_servers

        except ClientError as e:
            logger.error(f"DynamoDB error listing user servers: {e}")
            return []

    def _get_server_description(self, server_name: str) -> str:
        """Get description for a server."""
        descriptions = {
            "filesystem": "File operations and document processing",
            "memory": "Persistent knowledge management and context storage",
            "github": "Repository operations, code analysis, and GitHub integration",
            "sqlite": "Database query operations and data analysis",
            "brave-search": "Web search capabilities and content retrieval"
        }
        return descriptions.get(server_name, f"MCP server: {server_name}")

    def get_default_server_names(self) -> List[str]:
        """Get list of default server names."""
        return list(self._default_configs.keys())

    def clear_cache(self):
        """Clear the configuration cache."""
        self._config_cache.clear()
        logger.info("Cleared server configuration cache")

    async def validate_server_config(self, server_config: MCPServerConfig) -> Dict[str, Any]:
        """
        Validate a server configuration by attempting to create a client.

        Args:
            server_config: Configuration to validate

        Returns:
            Dict with validation results
        """
        from .direct_client import DirectMCPClient

        try:
            # Create a temporary client to test the configuration
            client = DirectMCPClient(server_config)
            result = await client.initialize()
            await client.cleanup()

            return {
                "valid": True,
                "message": "Configuration is valid",
                "server_info": result
            }

        except Exception as e:
            return {
                "valid": False,
                "message": f"Configuration validation failed: {str(e)}",
                "error": str(e)
            }

    async def save_server_config(self, config: MCPServerConfig, user_id: Optional[str] = None) -> bool:
        """Save server configuration for user (API wrapper)."""
        if user_id:
            return await self.save_user_server_config(user_id, config)
        else:
            # For default configs, just return True as they're read-only
            logger.info(f"Save server config called for {config.name} (default user)")
            return True

    async def delete_server_config(self, server_name: str, user_id: Optional[str] = None) -> bool:
        """Delete server configuration for user (API wrapper)."""
        if user_id:
            return await self.delete_user_server_config(user_id, server_name)
        else:
            # Cannot delete default configs
            logger.warning(f"Cannot delete default server config: {server_name}")
            return False