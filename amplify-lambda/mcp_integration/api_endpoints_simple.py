"""
Simple MCP API Endpoints for local development

This provides basic MCP API endpoints with dummy responses for testing the frontend.
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def get_all_tools(event, context):
    """GET /dev/mcp/tools - Get all available tools."""
    try:
        # Return dummy tools for local development
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

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
            },
            "body": json.dumps(response)
        }

    except Exception as e:
        logger.error(f"Error in get_all_tools: {e}")
        error_response = {
            "success": False,
            "message": f"Tool discovery failed: {str(e)}"
        }
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps(error_response)
        }


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

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
            },
            "body": json.dumps(response)
        }

    except Exception as e:
        logger.error(f"Error in get_servers: {e}")
        error_response = {
            "success": False,
            "message": f"Failed to get servers: {str(e)}"
        }
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps(error_response)
        }


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

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
            },
            "body": json.dumps(response)
        }

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
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps(error_response)
        }


def add_server(event, context):
    """POST /dev/mcp/servers - Add server configuration."""
    try:
        response = {
            "success": True,
            "message": "Server added successfully (local development mode)"
        }

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
            },
            "body": json.dumps(response)
        }

    except Exception as e:
        error_response = {
            "success": False,
            "message": f"Failed to add server: {str(e)}"
        }
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps(error_response)
        }


# Add basic handlers for other endpoints
def update_server(event, context):
    return add_server(event, context)

def delete_server(event, context):
    return add_server(event, context)

def test_server(event, context):
    return add_server(event, context)

def get_server_tools(event, context):
    return get_all_tools(event, context)

def run_mcp_test(event, context):
    return get_mcp_status(event, context)