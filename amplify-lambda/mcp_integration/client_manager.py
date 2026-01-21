"""
MCPClientManager: Manages connections to multiple MCP servers

This module provides connection pooling, lifecycle management, and a unified interface
for interacting with multiple MCP servers simultaneously.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta
import json

from .direct_client import DirectMCPClient, MCPServerConfig

logger = logging.getLogger(__name__)


class MCPClientManager:
    """
    Manages connections to multiple MCP servers with connection pooling and lifecycle management.

    Features:
    - Connection pooling and reuse
    - Automatic health checking and reconnection
    - Unified tool and resource discovery
    - Load balancing across servers
    - Graceful error handling and fallback
    """

    def __init__(self, max_connections_per_server: int = 3, health_check_interval: int = 60):
        self.clients: Dict[str, List[DirectMCPClient]] = {}
        self.server_configs: Dict[str, MCPServerConfig] = {}
        self.max_connections_per_server = max_connections_per_server
        self.health_check_interval = health_check_interval
        self.last_health_check = datetime.now()
        self.initialized_servers: Set[str] = set()
        self.failed_servers: Set[str] = set()

    async def add_server(self, server_config: MCPServerConfig) -> bool:
        """
        Add a new MCP server to the manager.

        Args:
            server_config: Configuration for the MCP server

        Returns:
            bool: True if server was added successfully, False otherwise
        """
        try:
            logger.info(f"Adding MCP server: {server_config.name}")

            # Store server configuration
            self.server_configs[server_config.name] = server_config

            # Initialize connection pool for this server
            self.clients[server_config.name] = []

            # Create initial connection
            client = DirectMCPClient(server_config)
            await client.initialize()

            self.clients[server_config.name].append(client)
            self.initialized_servers.add(server_config.name)
            self.failed_servers.discard(server_config.name)

            logger.info(f"Successfully added MCP server: {server_config.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to add MCP server {server_config.name}: {e}")
            self.failed_servers.add(server_config.name)
            return False

    async def remove_server(self, server_name: str) -> bool:
        """
        Remove an MCP server and clean up all its connections.

        Args:
            server_name: Name of the server to remove

        Returns:
            bool: True if server was removed successfully
        """
        try:
            logger.info(f"Removing MCP server: {server_name}")

            if server_name in self.clients:
                # Clean up all connections for this server
                for client in self.clients[server_name]:
                    await client.cleanup()

                del self.clients[server_name]

            if server_name in self.server_configs:
                del self.server_configs[server_name]

            self.initialized_servers.discard(server_name)
            self.failed_servers.discard(server_name)

            logger.info(f"Successfully removed MCP server: {server_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to remove MCP server {server_name}: {e}")
            return False

    async def get_client(self, server_name: str) -> Optional[DirectMCPClient]:
        """
        Get a healthy client for the specified server.

        Args:
            server_name: Name of the MCP server

        Returns:
            DirectMCPClient: A healthy client or None if unavailable
        """
        if server_name not in self.clients or server_name in self.failed_servers:
            return None

        clients = self.clients[server_name]

        # Find a healthy client
        for client in clients:
            if client.is_healthy():
                return client

        # If no healthy clients, try to create a new one
        if len(clients) < self.max_connections_per_server:
            try:
                server_config = self.server_configs[server_name]
                new_client = DirectMCPClient(server_config)
                await new_client.initialize()
                clients.append(new_client)
                return new_client
            except Exception as e:
                logger.error(f"Failed to create new client for {server_name}: {e}")
                self.failed_servers.add(server_name)

        return None

    async def discover_all_tools(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Discover all available tools from all connected MCP servers.

        Returns:
            Dict mapping server names to their available tools
        """
        all_tools = {}

        for server_name in self.initialized_servers:
            if server_name in self.failed_servers:
                continue

            try:
                client = await self.get_client(server_name)
                if client:
                    tools = await client.list_tools()
                    all_tools[server_name] = tools
                    logger.debug(f"Discovered {len(tools)} tools from {server_name}")

            except Exception as e:
                logger.error(f"Failed to discover tools from {server_name}: {e}")
                self.failed_servers.add(server_name)

        return all_tools

    async def discover_all_resources(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Discover all available resources from all connected MCP servers.

        Returns:
            Dict mapping server names to their available resources
        """
        all_resources = {}

        for server_name in self.initialized_servers:
            if server_name in self.failed_servers:
                continue

            try:
                client = await self.get_client(server_name)
                if client:
                    resources = await client.list_resources()
                    all_resources[server_name] = resources
                    logger.debug(f"Discovered {len(resources)} resources from {server_name}")

            except Exception as e:
                logger.error(f"Failed to discover resources from {server_name}: {e}")
                self.failed_servers.add(server_name)

        return all_resources

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call a specific tool on a specific server.

        Args:
            server_name: Name of the MCP server
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            Dict containing the tool execution result

        Raises:
            RuntimeError: If the server is unavailable or tool call fails
        """
        client = await self.get_client(server_name)
        if not client:
            raise RuntimeError(f"MCP server {server_name} is not available")

        try:
            logger.info(f"Calling tool {tool_name} on server {server_name}")
            result = await client.call_tool(tool_name, arguments)
            logger.debug(f"Tool {tool_name} executed successfully")
            return result

        except Exception as e:
            logger.error(f"Failed to call tool {tool_name} on {server_name}: {e}")
            # Mark server as failed if the error is severe
            if "not initialized" in str(e).lower() or "process" in str(e).lower():
                self.failed_servers.add(server_name)
            raise

    async def read_resource(self, server_name: str, uri: str) -> Dict[str, Any]:
        """
        Read a specific resource from a specific server.

        Args:
            server_name: Name of the MCP server
            uri: URI of the resource to read

        Returns:
            Dict containing the resource content

        Raises:
            RuntimeError: If the server is unavailable or resource read fails
        """
        client = await self.get_client(server_name)
        if not client:
            raise RuntimeError(f"MCP server {server_name} is not available")

        try:
            logger.info(f"Reading resource {uri} from server {server_name}")
            result = await client.read_resource(uri)
            logger.debug(f"Resource {uri} read successfully")
            return result

        except Exception as e:
            logger.error(f"Failed to read resource {uri} from {server_name}: {e}")
            # Mark server as failed if the error is severe
            if "not initialized" in str(e).lower() or "process" in str(e).lower():
                self.failed_servers.add(server_name)
            raise

    async def health_check(self) -> Dict[str, Dict[str, Any]]:
        """
        Perform health check on all servers and return status information.

        Returns:
            Dict mapping server names to their health status
        """
        now = datetime.now()
        if now - self.last_health_check < timedelta(seconds=self.health_check_interval):
            # Skip health check if too recent
            return self.get_server_status()

        logger.info("Performing health check on all MCP servers")
        self.last_health_check = now

        health_status = {}

        for server_name in self.server_configs.keys():
            try:
                client = await self.get_client(server_name)
                if client and client.is_healthy():
                    health_status[server_name] = {
                        "status": "healthy",
                        "info": client.get_server_info()
                    }
                    self.failed_servers.discard(server_name)
                else:
                    health_status[server_name] = {
                        "status": "unhealthy",
                        "info": {"error": "Client unavailable or unhealthy"}
                    }
                    self.failed_servers.add(server_name)

            except Exception as e:
                logger.error(f"Health check failed for {server_name}: {e}")
                health_status[server_name] = {
                    "status": "failed",
                    "info": {"error": str(e)}
                }
                self.failed_servers.add(server_name)

        return health_status

    def get_server_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get current status of all servers without performing health checks.

        Returns:
            Dict mapping server names to their current status
        """
        status = {}

        for server_name, config in self.server_configs.items():
            server_status = {
                "name": server_name,
                "initialized": server_name in self.initialized_servers,
                "failed": server_name in self.failed_servers,
                "connection_count": len(self.clients.get(server_name, [])),
                "healthy_connections": 0
            }

            # Count healthy connections
            if server_name in self.clients:
                for client in self.clients[server_name]:
                    if client.is_healthy():
                        server_status["healthy_connections"] += 1

            status[server_name] = server_status

        return status

    async def cleanup_all(self):
        """Clean up all connections and resources."""
        logger.info("Cleaning up all MCP connections")

        for server_name in list(self.clients.keys()):
            await self.remove_server(server_name)

        self.clients.clear()
        self.server_configs.clear()
        self.initialized_servers.clear()
        self.failed_servers.clear()

        logger.info("All MCP connections cleaned up")

    def get_available_tools_summary(self) -> Dict[str, int]:
        """
        Get a summary of available tools across all servers.

        Returns:
            Dict mapping server names to tool counts
        """
        summary = {}
        for server_name in self.initialized_servers:
            if server_name not in self.failed_servers and server_name in self.clients:
                clients = self.clients[server_name]
                if clients:
                    summary[server_name] = len(clients[0].available_tools)
                else:
                    summary[server_name] = 0
        return summary

    async def get_unified_tool_list(self) -> List[Dict[str, Any]]:
        """
        Get a unified list of all available tools from all servers.

        Returns:
            List of tool definitions with server information
        """
        unified_tools = []
        all_tools = await self.discover_all_tools()

        for server_name, tools in all_tools.items():
            for tool in tools:
                unified_tool = tool.copy()
                unified_tool["server"] = server_name
                unified_tool["qualified_name"] = f"{server_name}.{tool.get('name', 'unknown')}"
                unified_tools.append(unified_tool)

        return unified_tools

    async def check_server_health(self, server_name: str) -> bool:
        """
        Check if a server is healthy and responding.

        Args:
            server_name: Name of the MCP server

        Returns:
            bool: True if server is healthy
        """
        if server_name in self.failed_servers:
            return False

        try:
            client = await self.get_client(server_name)
            if not client:
                return False

            # Try to get tools as a health check
            tools = await client.list_tools()
            return tools is not None

        except Exception as e:
            logger.warning(f"Health check failed for {server_name}: {e}")
            return False

    async def remove_server(self, server_name: str) -> bool:
        """
        Remove a server from the client manager.

        Args:
            server_name: Name of the MCP server to remove

        Returns:
            bool: True if server was removed
        """
        try:
            # Cleanup clients
            if server_name in self.clients:
                clients = self.clients[server_name]
                for client in clients:
                    try:
                        await client.cleanup()
                    except Exception as e:
                        logger.warning(f"Error cleaning up client for {server_name}: {e}")

                del self.clients[server_name]

            # Remove from tracking sets
            self.initialized_servers.discard(server_name)
            self.failed_servers.discard(server_name)

            logger.info(f"Removed server {server_name}")
            return True

        except Exception as e:
            logger.error(f"Error removing server {server_name}: {e}")
            return False