"""
MCP API Endpoints

Comprehensive REST API for MCP server and tool management.
This provides all the endpoints that the frontend expects.
"""

import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

# Import fallbacks for local development
try:
    from pycommon.authz import validated
    from pycommon.api.ops import api_tool
except ImportError:
    # Fallback decorators for local development
    def validated(func):
        def wrapper(event, context, current_user=None, name=None, data=None):
            # Extract data from event for local testing
            if not current_user:
                current_user = {"sub": "test-user"}
            if not data:
                try:
                    data = json.loads(event.get("body", "{}"))
                except:
                    data = {}
            return func(event, context, current_user, name, data)
        return wrapper

    def api_tool(func):
        return func  # Simple passthrough for local development

try:
    from .client_manager import MCPClientManager
    from .server_config import MCPServerConfigManager, MCPServerConfig
    from .tool_registry import MCPToolRegistry
    from .auth_manager import MCPAuthManager
except ImportError as e:
    logger.error(f"Failed to import MCP components: {e}")
    # Create dummy classes for local development
    class MCPClientManager:
        def __init__(self): pass
        async def check_server_health(self, name): return False
        async def add_server(self, config): return True
        async def remove_server(self, name): return True

    class MCPServerConfigManager:
        def __init__(self): pass
        def get_default_server_names(self): return []
        async def get_server_config(self, name, user_id=None): return None
        async def save_server_config(self, config, user_id=None): return True
        async def delete_server_config(self, name, user_id=None): return True

    class MCPToolRegistry:
        def __init__(self): pass
        async def discover_tools(self, manager): pass
        def get_all_tools(self): return []
        def get_tools_by_server(self, name): return []

    class MCPAuthManager:
        def __init__(self): pass

    class MCPServerConfig:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

logger = logging.getLogger(__name__)

# Global MCP instances
mcp_client_manager = None
mcp_config_manager = None
mcp_tool_registry = None
mcp_auth_manager = None


async def initialize_mcp_components(current_user=None):
    """Initialize MCP components if not already initialized."""
    global mcp_client_manager, mcp_config_manager, mcp_tool_registry, mcp_auth_manager

    if not mcp_client_manager:
        mcp_config_manager = MCPServerConfigManager()
        mcp_auth_manager = MCPAuthManager()
        mcp_tool_registry = MCPToolRegistry()
        mcp_client_manager = MCPClientManager()

        logger.info("MCP components initialized")

    return True


def handle_mcp_error(func):
    """Decorator to handle MCP errors consistently."""
    def wrapper(*args, **kwargs):
        try:
            if asyncio.iscoroutinefunction(func):
                return asyncio.run(func(*args, **kwargs))
            else:
                return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"MCP API error in {func.__name__}: {e}")
            return {
                "success": False,
                "message": f"MCP operation failed: {str(e)}",
                "error": str(e)
            }
    return wrapper


# ============================================================================
# Server Management Endpoints
# ============================================================================

@api_tool
@validated
@handle_mcp_error
async def get_servers(event, context, current_user, name, data):
    """GET /dev/mcp/servers - Get all available server configurations."""
    await initialize_mcp_components(current_user)

    user_id = current_user.get('sub') if current_user else None
    server_names = mcp_config_manager.get_default_server_names()
    servers = []

    for server_name in server_names:
        try:
            config = await mcp_config_manager.get_server_config(server_name, user_id)
            if config:
                # Convert to dict and add status info
                server_dict = {
                    "name": config.name,
                    "type": config.type,
                    "enabled": config.enabled,
                    "description": config.description,
                }

                # Add type-specific fields
                if config.command:
                    server_dict["command"] = config.command
                if config.args:
                    server_dict["args"] = config.args
                if config.url:
                    server_dict["url"] = config.url
                if config.env:
                    server_dict["env"] = config.env

                servers.append(server_dict)
        except Exception as e:
            logger.warning(f"Error loading server {server_name}: {e}")

    return {
        "success": True,
        "data": servers,
        "message": f"Retrieved {len(servers)} server configurations"
    }


@api_tool
@validated
@handle_mcp_error
async def add_server(event, context, current_user, name, data):
    """POST /dev/mcp/servers - Add a new server configuration."""
    await initialize_mcp_components(current_user)

    server_data = data.get("server")
    if not server_data:
        return {"success": False, "message": "Server configuration required"}

    try:
        # Create server config
        config = MCPServerConfig(
            name=server_data["name"],
            type=server_data["type"],
            enabled=server_data.get("enabled", True),
            description=server_data.get("description", ""),
            command=server_data.get("command"),
            args=server_data.get("args"),
            url=server_data.get("url"),
            env=server_data.get("env", {}),
            auth=server_data.get("auth")
        )

        # Save configuration
        user_id = current_user.get('sub') if current_user else None
        success = await mcp_config_manager.save_server_config(config, user_id)

        if success:
            # Try to initialize the server
            try:
                await mcp_client_manager.add_server(config)
                logger.info(f"Server {config.name} added and initialized")
            except Exception as e:
                logger.warning(f"Server {config.name} added but failed to initialize: {e}")

            return {
                "success": True,
                "message": f"Server '{config.name}' added successfully"
            }
        else:
            return {"success": False, "message": "Failed to save server configuration"}

    except Exception as e:
        logger.error(f"Error adding server: {e}")
        return {"success": False, "message": f"Error adding server: {str(e)}"}


@api_tool
@validated
@handle_mcp_error
async def update_server(event, context, current_user, name, data):
    """PUT /dev/mcp/servers/{serverName} - Update server configuration."""
    await initialize_mcp_components(current_user)

    server_name = event.get("pathParameters", {}).get("serverName")
    server_data = data.get("server")

    if not server_name or not server_data:
        return {"success": False, "message": "Server name and configuration required"}

    try:
        # Create updated server config
        config = MCPServerConfig(
            name=server_data["name"],
            type=server_data["type"],
            enabled=server_data.get("enabled", True),
            description=server_data.get("description", ""),
            command=server_data.get("command"),
            args=server_data.get("args"),
            url=server_data.get("url"),
            env=server_data.get("env", {}),
            auth=server_data.get("auth")
        )

        # Save configuration
        user_id = current_user.get('sub') if current_user else None
        success = await mcp_config_manager.save_server_config(config, user_id)

        if success:
            # Remove old server and add updated one
            try:
                await mcp_client_manager.remove_server(server_name)
                await mcp_client_manager.add_server(config)
                logger.info(f"Server {config.name} updated and reinitialized")
            except Exception as e:
                logger.warning(f"Server {config.name} updated but failed to reinitialize: {e}")

            return {
                "success": True,
                "message": f"Server '{config.name}' updated successfully"
            }
        else:
            return {"success": False, "message": "Failed to update server configuration"}

    except Exception as e:
        logger.error(f"Error updating server: {e}")
        return {"success": False, "message": f"Error updating server: {str(e)}"}


@api_tool
@validated
@handle_mcp_error
async def delete_server(event, context, current_user, name, data):
    """DELETE /dev/mcp/servers/{serverName} - Delete server configuration."""
    await initialize_mcp_components(current_user)

    server_name = event.get("pathParameters", {}).get("serverName")
    if not server_name:
        return {"success": False, "message": "Server name required"}

    try:
        # Remove from client manager
        await mcp_client_manager.remove_server(server_name)

        # Delete configuration
        user_id = current_user.get('sub') if current_user else None
        success = await mcp_config_manager.delete_server_config(server_name, user_id)

        return {
            "success": success,
            "message": f"Server '{server_name}' {'deleted' if success else 'not found'}"
        }

    except Exception as e:
        logger.error(f"Error deleting server: {e}")
        return {"success": False, "message": f"Error deleting server: {str(e)}"}


@api_tool
@validated
@handle_mcp_error
async def test_server(event, context, current_user, name, data):
    """POST /dev/mcp/servers/{serverName}/test - Test server connection."""
    await initialize_mcp_components(current_user)

    server_name = event.get("pathParameters", {}).get("serverName")
    if not server_name:
        return {"success": False, "message": "Server name required"}

    try:
        # Test server connection
        is_healthy = await mcp_client_manager.check_server_health(server_name)

        return {
            "success": is_healthy,
            "message": f"Server '{server_name}' is {'healthy' if is_healthy else 'unhealthy'}",
            "server": server_name,
            "healthy": is_healthy
        }

    except Exception as e:
        logger.error(f"Error testing server {server_name}: {e}")
        return {
            "success": False,
            "message": f"Server test failed: {str(e)}",
            "server": server_name,
            "healthy": False
        }


# ============================================================================
# Tool Discovery Endpoints
# ============================================================================

@api_tool
@validated
@handle_mcp_error
async def get_all_tools(event, context, current_user, name, data):
    """GET /dev/mcp/tools - Discover all available tools."""
    await initialize_mcp_components(current_user)

    try:
        # Discover tools from all connected servers
        await mcp_tool_registry.discover_tools(mcp_client_manager)

        # Get all tools
        tools = mcp_tool_registry.get_all_tools()

        # Convert to frontend format
        tool_list = []
        for tool in tools:
            tool_dict = {
                "name": tool.name,
                "qualified_name": tool.qualified_name,
                "server": tool.server,
                "description": tool.description,
                "category": tool.category,
                "parameters": tool.parameters,
                "usage_count": tool.usage_count,
                "average_execution_time": tool.average_execution_time,
                "success_rate": tool.success_rate,
                "last_used": tool.last_used.isoformat() if tool.last_used else None
            }
            tool_list.append(tool_dict)

        return {
            "success": True,
            "data": tool_list,
            "message": f"Discovered {len(tool_list)} tools"
        }

    except Exception as e:
        logger.error(f"Error discovering tools: {e}")
        return {"success": False, "message": f"Tool discovery failed: {str(e)}"}


@api_tool
@validated
@handle_mcp_error
async def get_server_tools(event, context, current_user, name, data):
    """GET /dev/mcp/servers/{serverName}/tools - Get tools from specific server."""
    await initialize_mcp_components(current_user)

    server_name = event.get("pathParameters", {}).get("serverName")
    if not server_name:
        return {"success": False, "message": "Server name required"}

    try:
        # Get tools for specific server
        tools = mcp_tool_registry.get_tools_by_server(server_name)

        # Convert to frontend format
        tool_list = []
        for tool in tools:
            tool_dict = {
                "name": tool.name,
                "qualified_name": tool.qualified_name,
                "server": tool.server,
                "description": tool.description,
                "category": tool.category,
                "parameters": tool.parameters,
                "usage_count": tool.usage_count,
                "average_execution_time": tool.average_execution_time,
                "success_rate": tool.success_rate,
                "last_used": tool.last_used.isoformat() if tool.last_used else None
            }
            tool_list.append(tool_dict)

        return {
            "success": True,
            "data": tool_list,
            "message": f"Found {len(tool_list)} tools for server '{server_name}'"
        }

    except Exception as e:
        logger.error(f"Error getting tools for server {server_name}: {e}")
        return {"success": False, "message": f"Failed to get tools: {str(e)}"}


# ============================================================================
# Status and Health Endpoints
# ============================================================================

@api_tool
@validated
@handle_mcp_error
async def get_mcp_status(event, context, current_user, name, data):
    """GET /dev/mcp/status - Get comprehensive MCP status."""
    await initialize_mcp_components(current_user)

    try:
        # Get available servers
        available_servers = []
        server_names = mcp_config_manager.get_default_server_names()

        for server_name in server_names:
            try:
                is_healthy = await mcp_client_manager.check_server_health(server_name)
                if is_healthy:
                    available_servers.append(server_name)
            except Exception as e:
                logger.warning(f"Health check failed for {server_name}: {e}")

        # Get tool count
        tool_count = len(mcp_tool_registry.get_all_tools()) if mcp_tool_registry else 0

        return {
            "success": True,
            "data": {
                "mcp_integration_available": True,
                "available_servers": available_servers,
                "total_servers": len(server_names),
                "total_tools": tool_count,
                "configuration": {
                    "max_tools_per_request": 20,
                    "cache_ttl": 300,
                    "timeout": 30
                },
                "last_updated": datetime.utcnow().isoformat()
            },
            "message": f"MCP integration active with {len(available_servers)} servers"
        }

    except Exception as e:
        logger.error(f"Error getting MCP status: {e}")
        return {
            "success": False,
            "message": f"Status check failed: {str(e)}",
            "data": {
                "mcp_integration_available": False,
                "available_servers": [],
                "total_servers": 0,
                "total_tools": 0
            }
        }


# ============================================================================
# Tool Execution Endpoint
# ============================================================================

@api_tool
@validated
@handle_mcp_error
async def run_mcp_test(event, context, current_user, name, data):
    """POST /dev/mcp/run - Run comprehensive MCP test."""
    await initialize_mcp_components(current_user)

    try:
        # Initialize MCP integration
        from chat.service import initialize_mcp_integration
        success = await initialize_mcp_integration(current_user)

        if not success:
            return {
                "success": False,
                "message": "Failed to initialize MCP integration"
            }

        # Discover tools
        await mcp_tool_registry.discover_tools(mcp_client_manager)

        # Get status
        server_names = mcp_config_manager.get_default_server_names()
        healthy_servers = []

        for server_name in server_names:
            try:
                is_healthy = await mcp_client_manager.check_server_health(server_name)
                if is_healthy:
                    healthy_servers.append(server_name)
            except Exception as e:
                logger.warning(f"Server {server_name} unhealthy: {e}")

        tools = mcp_tool_registry.get_all_tools()

        return {
            "success": True,
            "data": {
                "test_summary": {
                    "servers_initialized": len(healthy_servers),
                    "total_servers": len(server_names),
                    "total_tools_discovered": len(tools),
                    "healthy_servers": healthy_servers
                },
                "servers": healthy_servers,
                "tools_count": len(tools)
            },
            "message": f"Test completed: {len(healthy_servers)}/{len(server_names)} servers healthy, {len(tools)} tools discovered"
        }

    except Exception as e:
        logger.error(f"MCP test failed: {e}")
        return {
            "success": False,
            "message": f"Test failed: {str(e)}"
        }