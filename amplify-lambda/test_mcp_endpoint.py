"""
MCP Test Endpoint for debugging and testing MCP integration

This endpoint provides authentication-free testing of MCP functionality
for development and debugging purposes.
"""

import json
import logging
import asyncio
from pycommon.api.ops import api_tool

from . import (
    MCPClientManager,
    MCPServerConfigManager,
    MCPToolRegistry,
    MCPAuthManager,
    DirectMCPClient,
    MCPServerConfig
)

logger = logging.getLogger(__name__)


@api_tool(
    path="/mcp/test",
    name="testMCPIntegration",
    method="GET",
    tags=["mcp", "testing"],
    description="Test MCP integration functionality without authentication",
    parameters={},
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "message": {"type": "string"},
            "data": {"type": "object"}
        }
    }
)
def test_mcp_endpoint(event, context):
    """Test endpoint for MCP integration."""
    return asyncio.run(async_test_mcp_endpoint(event, context))


async def async_test_mcp_endpoint(event, context):
    """Async test endpoint implementation."""
    try:
        logger.info("Starting MCP integration test")

        test_results = {
            "timestamp": json.dumps({"timestamp": "now"}, default=str),
            "servers_tested": [],
            "tools_discovered": [],
            "test_summary": {},
            "errors": []
        }

        # Initialize MCP components
        config_manager = MCPServerConfigManager()
        auth_manager = MCPAuthManager()
        tool_registry = MCPToolRegistry()
        client_manager = MCPClientManager()

        try:
            # Test 1: List available default servers
            logger.info("Test 1: Listing available default servers")
            default_servers = config_manager.get_default_server_names()
            test_results["test_summary"]["available_default_servers"] = len(default_servers)
            test_results["available_servers"] = default_servers

            # Test 2: Initialize MCP servers
            logger.info("Test 2: Initializing MCP servers")
            initialized_servers = []

            for server_name in default_servers:
                try:
                    server_config = await config_manager.get_server_config(server_name)

                    if server_config:
                        success = await client_manager.add_server(server_config)

                        server_test_result = {
                            "name": server_name,
                            "initialized": success,
                            "config": {
                                "command": server_config.command,
                                "args": server_config.args,
                                "has_env": bool(server_config.env)
                            }
                        }

                        if success:
                            initialized_servers.append(server_name)
                            logger.info(f"Successfully initialized server: {server_name}")
                        else:
                            server_test_result["error"] = "Initialization failed"
                            logger.warning(f"Failed to initialize server: {server_name}")

                        test_results["servers_tested"].append(server_test_result)

                except Exception as e:
                    error_msg = f"Error testing server {server_name}: {str(e)}"
                    logger.error(error_msg)
                    test_results["errors"].append(error_msg)
                    test_results["servers_tested"].append({
                        "name": server_name,
                        "initialized": False,
                        "error": str(e)
                    })

            test_results["test_summary"]["servers_initialized"] = len(initialized_servers)

            # Test 3: Discover tools
            logger.info("Test 3: Discovering tools from initialized servers")

            try:
                discovered_tools = await tool_registry.discover_tools(client_manager)

                total_tools = 0
                for server_name, tools in discovered_tools.items():
                    total_tools += len(tools)
                    test_results["tools_discovered"].append({
                        "server": server_name,
                        "tool_count": len(tools),
                        "tools": [tool.get("name", "unknown") for tool in tools]
                    })

                test_results["test_summary"]["total_tools_discovered"] = total_tools

            except Exception as e:
                error_msg = f"Error discovering tools: {str(e)}"
                logger.error(error_msg)
                test_results["errors"].append(error_msg)

            # Test 4: Get tools formatted for AI
            logger.info("Test 4: Formatting tools for AI model")

            try:
                ai_tools = tool_registry.get_tools_for_ai_model(limit=10)
                test_results["test_summary"]["ai_formatted_tools"] = len(ai_tools)
                test_results["sample_ai_tools"] = [
                    {
                        "name": tool["function"]["name"],
                        "description": tool["function"]["description"]
                    }
                    for tool in ai_tools[:5]  # Show first 5 as sample
                ]

            except Exception as e:
                error_msg = f"Error formatting tools for AI: {str(e)}"
                logger.error(error_msg)
                test_results["errors"].append(error_msg)

            # Test 5: Server health check
            logger.info("Test 5: Performing health check")

            try:
                health_status = await client_manager.health_check()
                test_results["health_check"] = health_status

                healthy_servers = sum(1 for status in health_status.values()
                                    if status.get("status") == "healthy")
                test_results["test_summary"]["healthy_servers"] = healthy_servers

            except Exception as e:
                error_msg = f"Error during health check: {str(e)}"
                logger.error(error_msg)
                test_results["errors"].append(error_msg)

            # Test 6: Tool registry statistics
            logger.info("Test 6: Getting tool registry statistics")

            try:
                tool_stats = tool_registry.get_tool_statistics()
                test_results["tool_statistics"] = tool_stats

            except Exception as e:
                error_msg = f"Error getting tool statistics: {str(e)}"
                logger.error(error_msg)
                test_results["errors"].append(error_msg)

        finally:
            # Clean up resources
            try:
                await client_manager.cleanup_all()
                logger.info("MCP test cleanup completed")
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")

        # Determine overall test success
        overall_success = (
            test_results["test_summary"].get("servers_initialized", 0) > 0 and
            test_results["test_summary"].get("total_tools_discovered", 0) > 0 and
            len(test_results["errors"]) == 0
        )

        return {
            "success": overall_success,
            "message": f"MCP integration test completed. Initialized {test_results['test_summary'].get('servers_initialized', 0)} servers, discovered {test_results['test_summary'].get('total_tools_discovered', 0)} tools.",
            "data": test_results
        }

    except Exception as e:
        logger.error(f"MCP test endpoint error: {e}")
        return {
            "success": False,
            "message": f"MCP test failed: {str(e)}",
            "data": {"error": str(e)}
        }


@api_tool(
    path="/mcp/status",
    name="getMCPStatus",
    method="GET",
    tags=["mcp", "status"],
    description="Get current MCP integration status and configuration",
    parameters={},
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "message": {"type": "string"},
            "data": {"type": "object"}
        }
    }
)
def get_mcp_status(event, context):
    """Get MCP integration status."""
    try:
        config_manager = MCPServerConfigManager()
        auth_manager = MCPAuthManager()

        status = {
            "mcp_integration_available": True,
            "available_servers": config_manager.get_default_server_names(),
            "auth_manager_status": auth_manager.get_auth_status(),
            "configuration": {
                "cache_ttl": 300,  # From environment or default
                "max_tools_per_request": 20
            }
        }

        return {
            "success": True,
            "message": "MCP status retrieved successfully",
            "data": status
        }

    except Exception as e:
        logger.error(f"Error getting MCP status: {e}")
        return {
            "success": False,
            "message": f"Error retrieving MCP status: {str(e)}",
            "data": {"error": str(e)}
        }