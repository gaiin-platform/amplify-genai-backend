"""
DirectMCPClient: Direct JSON-RPC communication with MCP servers

This module bypasses the broken MCP Python client library by implementing
direct JSON-RPC communication over subprocess. This workaround was created
after discovering that the official MCP Python client v1.14.1 is fundamentally broken.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
import subprocess
import os

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""
    name: str
    type: str = "stdio"  # stdio, http, websocket
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    auth: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    timeout: int = 30
    enabled: bool = True
    description: str = ""

    def __post_init__(self):
        """Initialize defaults after dataclass creation."""
        if self.args is None:
            self.args = []
        if self.env is None:
            self.env = {}
        if self.command is None and self.type == "stdio":
            raise ValueError("Command is required for stdio servers")
        if self.url is None and self.type in ["http", "websocket"]:
            raise ValueError("URL is required for HTTP/WebSocket servers")


class DirectMCPClient:
    """
    Direct JSON-RPC communication with MCP servers bypassing broken Python client.

    This client communicates directly with MCP servers using JSON-RPC over subprocess,
    providing a reliable alternative to the broken official Python client.
    """

    def __init__(self, server_config: MCPServerConfig):
        self.server_config = server_config
        self.process: Optional[subprocess.Popen] = None
        self.initialized = False
        self.request_id = 0
        self.server_capabilities = {}
        self.available_tools = []
        self.available_resources = []

    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.cleanup()

    async def initialize(self) -> Dict[str, Any]:
        """Initialize connection to MCP server."""
        try:
            logger.info(f"Initializing MCP server: {self.server_config.name}")

            # Start the MCP server process
            env = os.environ.copy()
            env.update(self.server_config.env)

            self.process = subprocess.Popen(
                [self.server_config.command] + self.server_config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self.server_config.cwd,
                text=True,
                bufsize=0
            )

            # Send initialize request
            init_params = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {}
                },
                "clientInfo": {
                    "name": "amplify-mcp-client",
                    "version": "1.0.0"
                }
            }

            result = await self._send_request("initialize", init_params)

            if result and "capabilities" in result:
                self.server_capabilities = result["capabilities"]
                logger.info(f"Server capabilities: {self.server_capabilities}")

            # Send initialized notification
            await self._send_notification("initialized", {})

            # Discover available tools and resources
            await self._discover_capabilities()

            self.initialized = True
            logger.info(f"Successfully initialized MCP server: {self.server_config.name}")

            return {
                "server_name": self.server_config.name,
                "capabilities": self.server_capabilities,
                "tools": self.available_tools,
                "resources": self.available_resources
            }

        except Exception as e:
            logger.error(f"Failed to initialize MCP server {self.server_config.name}: {e}")
            await self.cleanup()
            raise

    async def _discover_capabilities(self):
        """Discover available tools and resources from the server."""
        try:
            # List available tools
            if self.server_capabilities.get("tools"):
                tools_result = await self._send_request("tools/list", {})
                if tools_result and "tools" in tools_result:
                    self.available_tools = tools_result["tools"]
                    logger.info(f"Discovered {len(self.available_tools)} tools")

            # List available resources
            if self.server_capabilities.get("resources"):
                resources_result = await self._send_request("resources/list", {})
                if resources_result and "resources" in resources_result:
                    self.available_resources = resources_result["resources"]
                    logger.info(f"Discovered {len(self.available_resources)} resources")

        except Exception as e:
            logger.warning(f"Failed to discover capabilities: {e}")

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List all available tools from the server."""
        if not self.initialized:
            raise RuntimeError("Client not initialized")

        try:
            result = await self._send_request("tools/list", {})
            if result and "tools" in result:
                self.available_tools = result["tools"]
                return self.available_tools
            return []
        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a specific tool with given arguments."""
        if not self.initialized:
            raise RuntimeError("Client not initialized")

        try:
            params = {
                "name": tool_name,
                "arguments": arguments
            }

            result = await self._send_request("tools/call", params)
            return result or {}

        except Exception as e:
            logger.error(f"Failed to call tool {tool_name}: {e}")
            raise

    async def list_resources(self) -> List[Dict[str, Any]]:
        """List all available resources from the server."""
        if not self.initialized:
            raise RuntimeError("Client not initialized")

        try:
            result = await self._send_request("resources/list", {})
            if result and "resources" in result:
                self.available_resources = result["resources"]
                return self.available_resources
            return []
        except Exception as e:
            logger.error(f"Failed to list resources: {e}")
            return []

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read a specific resource by URI."""
        if not self.initialized:
            raise RuntimeError("Client not initialized")

        try:
            params = {"uri": uri}
            result = await self._send_request("resources/read", params)
            return result or {}

        except Exception as e:
            logger.error(f"Failed to read resource {uri}: {e}")
            raise

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request and return the result."""
        if not self.process:
            raise RuntimeError("MCP server process not started")

        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params
        }

        try:
            # Send request
            request_json = json.dumps(request) + "\n"
            logger.debug(f"Sending request: {request_json.strip()}")

            self.process.stdin.write(request_json)
            self.process.stdin.flush()

            # Read response
            response_line = self.process.stdout.readline()
            if not response_line:
                raise RuntimeError("No response from MCP server")

            logger.debug(f"Received response: {response_line.strip()}")
            response = json.loads(response_line.strip())

            if "error" in response:
                error = response["error"]
                raise RuntimeError(f"MCP server error: {error.get('message', 'Unknown error')}")

            return response.get("result")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            raise RuntimeError(f"Invalid JSON response from MCP server: {e}")
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise

    async def _send_notification(self, method: str, params: Dict[str, Any]):
        """Send a JSON-RPC notification (no response expected)."""
        if not self.process:
            raise RuntimeError("MCP server process not started")

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }

        try:
            notification_json = json.dumps(notification) + "\n"
            logger.debug(f"Sending notification: {notification_json.strip()}")

            self.process.stdin.write(notification_json)
            self.process.stdin.flush()

        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
            raise

    async def cleanup(self):
        """Clean up resources and terminate the MCP server process."""
        try:
            if self.process:
                logger.info(f"Cleaning up MCP server: {self.server_config.name}")

                # Try to terminate gracefully first
                self.process.terminate()

                try:
                    # Wait for process to terminate
                    await asyncio.wait_for(
                        asyncio.create_task(self._wait_for_termination()),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"MCP server {self.server_config.name} did not terminate gracefully, killing...")
                    self.process.kill()

                self.process = None
                self.initialized = False
                logger.info(f"MCP server {self.server_config.name} cleaned up")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    async def _wait_for_termination(self):
        """Wait for the process to terminate."""
        while self.process and self.process.poll() is None:
            await asyncio.sleep(0.1)

    def is_healthy(self) -> bool:
        """Check if the MCP server is healthy and responsive."""
        return (
            self.initialized and
            self.process is not None and
            self.process.poll() is None
        )

    def get_server_info(self) -> Dict[str, Any]:
        """Get information about the connected MCP server."""
        return {
            "name": self.server_config.name,
            "initialized": self.initialized,
            "healthy": self.is_healthy(),
            "capabilities": self.server_capabilities,
            "tools_count": len(self.available_tools),
            "resources_count": len(self.available_resources),
            "tools": [tool.get("name") for tool in self.available_tools],
            "resources": [resource.get("uri") for resource in self.available_resources]
        }