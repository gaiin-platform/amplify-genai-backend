"""
Standalone MCP API Endpoints - No External Dependencies

This provides all MCP API endpoints needed by the frontend without requiring
any external dependencies like pycommon. Perfect for local development.
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def create_cors_response(status_code, body):
    """Create a standard CORS response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
        },
        "body": json.dumps(body)
    }


def get_all_tools(event, context):
    """GET /dev/mcp/tools - Get all available tools."""
    try:
        tools = [
            {
                "name": "read_file",
                "qualified_name": "filesystem.read_file",
                "server": "filesystem",
                "description": "Read the contents of a file",
                "category": "file_operations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to read"}
                    },
                    "required": ["path"]
                },
                "usage_count": 5,
                "average_execution_time": 150.5,
                "success_rate": 0.95,
                "last_used": "2024-12-21T10:30:00Z"
            },
            {
                "name": "write_file",
                "qualified_name": "filesystem.write_file",
                "server": "filesystem",
                "description": "Write content to a file",
                "category": "file_operations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to write"},
                        "content": {"type": "string", "description": "Content to write to the file"}
                    },
                    "required": ["path", "content"]
                },
                "usage_count": 3,
                "average_execution_time": 89.2,
                "success_rate": 1.0,
                "last_used": "2024-12-21T09:15:00Z"
            },
            {
                "name": "create_entities",
                "qualified_name": "memory.create_entities",
                "server": "memory",
                "description": "Create knowledge entities in memory",
                "category": "memory_management",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entities": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "entityType": {"type": "string"},
                                    "observations": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "required": ["entities"]
                },
                "usage_count": 2,
                "average_execution_time": 234.7,
                "success_rate": 1.0,
                "last_used": "2024-12-21T08:45:00Z"
            }
        ]

        response = {
            "success": True,
            "data": tools,
            "message": f"Discovered {len(tools)} tools"
        }

        return create_cors_response(200, response)

    except Exception as e:
        logger.error(f"Error in get_all_tools: {e}")
        error_response = {
            "success": False,
            "message": f"Tool discovery failed: {str(e)}"
        }
        return create_cors_response(500, error_response)


def get_servers(event, context):
    """GET /dev/mcp/servers - Get all server configurations."""
    try:
        servers = [
            {
                "name": "filesystem",
                "type": "stdio",
                "enabled": True,
                "description": "File operations and document processing",
                "command": "npx",
                "args": ["@modelcontextprotocol/server-filesystem", "/tmp"],
                "env": {}
            },
            {
                "name": "memory",
                "type": "stdio",
                "enabled": True,
                "description": "Persistent knowledge management",
                "command": "npx",
                "args": ["@modelcontextprotocol/server-memory"],
                "env": {}
            }
        ]

        response = {
            "success": True,
            "data": servers,
            "message": f"Retrieved {len(servers)} server configurations"
        }

        return create_cors_response(200, response)

    except Exception as e:
        logger.error(f"Error in get_servers: {e}")
        error_response = {
            "success": False,
            "message": f"Failed to get servers: {str(e)}"
        }
        return create_cors_response(500, error_response)


def get_mcp_status(event, context):
    """GET /dev/mcp/status - Get MCP status."""
    try:
        response = {
            "success": True,
            "data": {
                "mcp_integration_available": True,
                "available_servers": ["filesystem", "memory"],
                "total_servers": 2,
                "total_tools": 3,
                "configuration": {
                    "max_tools_per_request": 20,
                    "cache_ttl": 300,
                    "timeout": 30
                },
                "last_updated": datetime.utcnow().isoformat()
            },
            "message": "MCP integration active with 2 servers"
        }

        return create_cors_response(200, response)

    except Exception as e:
        logger.error(f"Error in get_mcp_status: {e}")
        error_response = {
            "success": False,
            "message": f"Status check failed: {str(e)}",
            "data": {
                "mcp_integration_available": False,
                "available_servers": []
            }
        }
        return create_cors_response(500, error_response)


def add_server(event, context):
    """POST /dev/mcp/servers - Add server configuration."""
    try:
        response = {
            "success": True,
            "message": "Server added successfully (local development mode)"
        }

        return create_cors_response(200, response)

    except Exception as e:
        error_response = {
            "success": False,
            "message": f"Failed to add server: {str(e)}"
        }
        return create_cors_response(500, error_response)


def update_server(event, context):
    """PUT /dev/mcp/servers/{serverName} - Update server configuration."""
    try:
        server_name = event.get("pathParameters", {}).get("serverName", "unknown")
        response = {
            "success": True,
            "message": f"Server '{server_name}' updated successfully (local development mode)"
        }

        return create_cors_response(200, response)

    except Exception as e:
        error_response = {
            "success": False,
            "message": f"Failed to update server: {str(e)}"
        }
        return create_cors_response(500, error_response)


def delete_server(event, context):
    """DELETE /dev/mcp/servers/{serverName} - Delete server configuration."""
    try:
        server_name = event.get("pathParameters", {}).get("serverName", "unknown")
        response = {
            "success": True,
            "message": f"Server '{server_name}' deleted successfully (local development mode)"
        }

        return create_cors_response(200, response)

    except Exception as e:
        error_response = {
            "success": False,
            "message": f"Failed to delete server: {str(e)}"
        }
        return create_cors_response(500, error_response)


def test_server(event, context):
    """POST /dev/mcp/servers/{serverName}/test - Test server connection."""
    try:
        server_name = event.get("pathParameters", {}).get("serverName", "unknown")
        response = {
            "success": True,
            "message": f"Server '{server_name}' is healthy (simulated)",
            "server": server_name,
            "healthy": True
        }

        return create_cors_response(200, response)

    except Exception as e:
        error_response = {
            "success": False,
            "message": f"Server test failed: {str(e)}",
            "server": event.get("pathParameters", {}).get("serverName", "unknown"),
            "healthy": False
        }
        return create_cors_response(500, error_response)


def get_server_tools(event, context):
    """GET /dev/mcp/servers/{serverName}/tools - Get tools from specific server."""
    try:
        server_name = event.get("pathParameters", {}).get("serverName", "unknown")

        # Filter tools by server
        all_tools = [
            {
                "name": "read_file",
                "qualified_name": "filesystem.read_file",
                "server": "filesystem",
                "description": "Read the contents of a file",
                "category": "file_operations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to read"}
                    },
                    "required": ["path"]
                },
                "usage_count": 5,
                "average_execution_time": 150.5,
                "success_rate": 0.95,
                "last_used": "2024-12-21T10:30:00Z"
            },
            {
                "name": "write_file",
                "qualified_name": "filesystem.write_file",
                "server": "filesystem",
                "description": "Write content to a file",
                "category": "file_operations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to write"},
                        "content": {"type": "string", "description": "Content to write to the file"}
                    },
                    "required": ["path", "content"]
                },
                "usage_count": 3,
                "average_execution_time": 89.2,
                "success_rate": 1.0,
                "last_used": "2024-12-21T09:15:00Z"
            }
        ]

        server_tools = [tool for tool in all_tools if tool["server"] == server_name]

        response = {
            "success": True,
            "data": server_tools,
            "message": f"Found {len(server_tools)} tools for server '{server_name}'"
        }

        return create_cors_response(200, response)

    except Exception as e:
        logger.error(f"Error getting tools for server: {e}")
        error_response = {
            "success": False,
            "message": f"Failed to get tools: {str(e)}"
        }
        return create_cors_response(500, error_response)


def run_mcp_test(event, context):
    """POST /dev/mcp/run - Run comprehensive MCP test."""
    try:
        response = {
            "success": True,
            "data": {
                "test_summary": {
                    "servers_initialized": 2,
                    "total_servers": 2,
                    "total_tools_discovered": 3,
                    "healthy_servers": ["filesystem", "memory"]
                },
                "servers": ["filesystem", "memory"],
                "tools_count": 3
            },
            "message": "Test completed: 2/2 servers healthy, 3 tools discovered"
        }

        return create_cors_response(200, response)

    except Exception as e:
        logger.error(f"MCP test failed: {e}")
        error_response = {
            "success": False,
            "data": {
                "test_summary": {
                    "servers_initialized": 0,
                    "total_servers": 0,
                    "total_tools_discovered": 0,
                    "healthy_servers": []
                },
                "servers": [],
                "tools_count": 0
            },
            "message": f"Test failed: {str(e)}"
        }
        return create_cors_response(500, error_response)


def test_mcp_endpoint(event, context):
    """GET /dev/mcp/test - Simple MCP test endpoint."""
    try:
        response = {
            "success": True,
            "message": "MCP integration is working",
            "data": {
                "servers_available": 2,
                "tools_available": 3,
                "timestamp": datetime.utcnow().isoformat()
            }
        }

        return create_cors_response(200, response)

    except Exception as e:
        logger.error(f"MCP test endpoint failed: {e}")
        error_response = {
            "success": False,
            "message": f"MCP test failed: {str(e)}"
        }
        return create_cors_response(500, error_response)