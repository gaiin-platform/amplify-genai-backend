"""
MCP (Model Context Protocol) Server Configuration

Manages user MCP server configurations stored in DynamoDB.
Users can add, remove, and configure their own MCP servers.
"""

from datetime import datetime, timezone
import json
import os
import traceback
import uuid

import boto3
from botocore.exceptions import ClientError

from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata import permissions
from pycommon.api.ops import api_tool, set_permissions_by_state
from pycommon.logger import getLogger

setup_validated(rules, permissions.get_permission_checker)
set_permissions_by_state(permissions)

logger = getLogger("mcp_servers")

# App ID for MCP server storage (matches JS backend)
MCP_APP_ID = "amplify-mcp"
MCP_ENTITY_TYPE = "mcp_servers"


def get_idp_prefix():
    """Get IDP prefix from environment"""
    return (os.environ.get("IDP_PREFIX") or "").lower()


def create_hash_key(user_id: str, app_id: str) -> str:
    """Create hash key matching storage format"""
    import re
    sanitized_user = re.sub(r'[^a-zA-Z0-9@._-]', '-', user_id)
    sanitized_app = re.sub(r'[^a-zA-Z0-9-]', '-', app_id)
    return f"{sanitized_user}#{sanitized_app}"


def get_full_user_id(user_id: str) -> str:
    """Get full user ID with IDP prefix"""
    idp_prefix = get_idp_prefix()
    return f"{idp_prefix}_{user_id}" if idp_prefix else user_id


def generate_server_id() -> str:
    """Generate unique server ID"""
    return f"mcp_{int(datetime.now().timestamp() * 1000)}_{uuid.uuid4().hex[:9]}"


@api_tool(
    path="/integrations/mcp/servers",
    name="listMCPServers",
    method="GET",
    tags=["MCP"],
    description="List all MCP servers configured for the current user",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "data": {"type": "array"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "USER_STORAGE_TABLE": [DynamoDBOperation.QUERY],
})
@validated("list_mcp_servers")
def list_mcp_servers(event, context, current_user, name, data):
    """List all MCP servers configured for the current user"""

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    try:
        full_user_id = get_full_user_id(current_user)
        hash_key = create_hash_key(full_user_id, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        logger.info(f"Querying MCP servers for user: {full_user_id}, PK: {pk}")

        response = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": pk}
        )

        servers = []
        for item in response.get("Items", []):
            server_data = item.get("data", {})
            servers.append({
                "id": item.get("SK"),
                "name": server_data.get("name"),
                "url": server_data.get("url"),
                "transport": server_data.get("transport", "http"),
                "enabled": server_data.get("enabled", True),
                "tools": server_data.get("tools", []),
                "status": server_data.get("status", "disconnected"),
                "lastConnected": server_data.get("lastConnected"),
                "lastError": server_data.get("lastError"),
                "createdAt": server_data.get("createdAt"),
                "updatedAt": server_data.get("updatedAt"),
            })

        logger.info(f"Retrieved {len(servers)} MCP servers for user")
        return {"success": True, "data": servers}

    except Exception as e:
        logger.error(f"Error listing MCP servers: {str(e)}")
        return {"success": False, "message": f"Error listing servers: {str(e)}"}


@api_tool(
    path="/integrations/mcp/server/get",
    name="getMCPServer",
    method="POST",
    tags=["MCP"],
    description="Get a single MCP server configuration by ID",
    parameters={
        "type": "object",
        "properties": {
            "serverId": {"type": "string"},
        },
        "required": ["serverId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "data": {"type": "object"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "USER_STORAGE_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("get_mcp_server")
def get_mcp_server(event, context, current_user, name, data):
    """Get a single MCP server configuration by ID"""

    request_data = data.get("data", {})
    server_id = request_data.get("serverId")

    if not server_id:
        return {"success": False, "message": "Server ID is required"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    try:
        full_user_id = get_full_user_id(current_user)
        hash_key = create_hash_key(full_user_id, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        response = table.get_item(Key={"PK": pk, "SK": server_id})

        if "Item" not in response:
            return {"success": False, "message": "MCP server not found"}

        item = response["Item"]
        server_data = item.get("data", {})

        return {
            "success": True,
            "data": {
                "id": item.get("SK"),
                "name": server_data.get("name"),
                "url": server_data.get("url"),
                "transport": server_data.get("transport", "http"),
                "enabled": server_data.get("enabled", True),
                "tools": server_data.get("tools", []),
                "status": server_data.get("status", "disconnected"),
                "lastConnected": server_data.get("lastConnected"),
                "lastError": server_data.get("lastError"),
                "createdAt": server_data.get("createdAt"),
                "updatedAt": server_data.get("updatedAt"),
            }
        }

    except Exception as e:
        logger.error(f"Error getting MCP server: {str(e)}")
        return {"success": False, "message": f"Error getting server: {str(e)}"}


@api_tool(
    path="/integrations/mcp/servers",
    name="addMCPServer",
    method="POST",
    tags=["MCP"],
    description="Add a new MCP server configuration",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "url": {"type": "string"},
            "transport": {"type": "string"},
        },
        "required": ["name", "url"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "data": {"type": "object"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "USER_STORAGE_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated("add_mcp_server")
def add_mcp_server(event, context, current_user, name, data):
    """Add a new MCP server configuration"""

    request_data = data.get("data", {})
    server_name = request_data.get("name")
    server_url = request_data.get("url")
    transport = request_data.get("transport", "http")

    # Validate required fields
    if not server_name or not server_name.strip():
        return {"success": False, "message": "Server name is required"}

    if not server_url or not server_url.strip():
        return {"success": False, "message": "Server URL is required"}

    # Validate URL format
    if not server_url.startswith("http://") and not server_url.startswith("https://"):
        return {"success": False, "message": "Server URL must start with http:// or https://"}

    # Validate transport
    if transport not in ["http", "sse"]:
        return {"success": False, "message": "Transport must be 'http' or 'sse'"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    try:
        full_user_id = get_full_user_id(current_user)
        hash_key = create_hash_key(full_user_id, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"
        server_id = generate_server_id()
        now = datetime.now(timezone.utc).isoformat()

        server_data = {
            "name": server_name.strip(),
            "url": server_url.strip(),
            "transport": transport,
            "enabled": True,
            "tools": [],
            "status": "disconnected",
            "createdAt": now,
            "updatedAt": now,
        }

        table.put_item(
            Item={
                "PK": pk,
                "SK": server_id,
                "data": server_data,
            }
        )

        logger.info(f"Added MCP server '{server_name}' for user {current_user}")

        return {
            "success": True,
            "data": {
                "id": server_id,
                **server_data,
            }
        }

    except Exception as e:
        logger.error(f"Error adding MCP server: {str(e)}")
        return {"success": False, "message": f"Error adding server: {str(e)}"}


@api_tool(
    path="/integrations/mcp/server/update",
    name="updateMCPServer",
    method="POST",
    tags=["MCP"],
    description="Update an existing MCP server configuration",
    parameters={
        "type": "object",
        "properties": {
            "serverId": {"type": "string"},
            "name": {"type": "string"},
            "url": {"type": "string"},
            "transport": {"type": "string"},
            "enabled": {"type": "boolean"},
        },
        "required": ["serverId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "data": {"type": "object"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "USER_STORAGE_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("update_mcp_server")
def update_mcp_server(event, context, current_user, name, data):
    """Update an existing MCP server configuration"""

    request_data = data.get("data", {})
    server_id = request_data.get("serverId")

    if not server_id:
        return {"success": False, "message": "Server ID is required"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    try:
        full_user_id = get_full_user_id(current_user)
        hash_key = create_hash_key(full_user_id, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        # Get existing item
        response = table.get_item(Key={"PK": pk, "SK": server_id})

        if "Item" not in response:
            return {"success": False, "message": "MCP server not found"}

        existing_data = response["Item"].get("data", {})

        # Update only provided fields
        if "name" in request_data and request_data["name"]:
            existing_data["name"] = request_data["name"].strip()
        if "url" in request_data and request_data["url"]:
            url = request_data["url"].strip()
            if not url.startswith("http://") and not url.startswith("https://"):
                return {"success": False, "message": "Server URL must start with http:// or https://"}
            existing_data["url"] = url
        if "transport" in request_data:
            if request_data["transport"] not in ["http", "sse"]:
                return {"success": False, "message": "Transport must be 'http' or 'sse'"}
            existing_data["transport"] = request_data["transport"]
        if "enabled" in request_data:
            existing_data["enabled"] = request_data["enabled"]
        if "tools" in request_data:
            existing_data["tools"] = request_data["tools"]
        if "status" in request_data:
            existing_data["status"] = request_data["status"]
        if "lastConnected" in request_data:
            existing_data["lastConnected"] = request_data["lastConnected"]
        if "lastError" in request_data:
            existing_data["lastError"] = request_data["lastError"]

        existing_data["updatedAt"] = datetime.now(timezone.utc).isoformat()

        table.put_item(
            Item={
                "PK": pk,
                "SK": server_id,
                "data": existing_data,
            }
        )

        logger.info(f"Updated MCP server {server_id} for user {current_user}")

        return {
            "success": True,
            "data": {
                "id": server_id,
                **existing_data,
            }
        }

    except Exception as e:
        logger.error(f"Error updating MCP server: {str(e)}")
        return {"success": False, "message": f"Error updating server: {str(e)}"}


@api_tool(
    path="/integrations/mcp/server/delete",
    name="deleteMCPServer",
    method="POST",
    tags=["MCP"],
    description="Delete an MCP server configuration",
    parameters={
        "type": "object",
        "properties": {
            "serverId": {"type": "string"},
        },
        "required": ["serverId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "USER_STORAGE_TABLE": [DynamoDBOperation.DELETE_ITEM],
})
@validated("delete_mcp_server")
def delete_mcp_server(event, context, current_user, name, data):
    """Delete an MCP server configuration"""

    request_data = data.get("data", {})
    server_id = request_data.get("serverId")

    if not server_id:
        return {"success": False, "message": "Server ID is required"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    try:
        full_user_id = get_full_user_id(current_user)
        hash_key = create_hash_key(full_user_id, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        table.delete_item(Key={"PK": pk, "SK": server_id})

        logger.info(f"Deleted MCP server {server_id} for user {current_user}")

        return {"success": True}

    except Exception as e:
        logger.error(f"Error deleting MCP server: {str(e)}")
        return {"success": False, "message": f"Error deleting server: {str(e)}"}


def _test_mcp_connection_by_url(server_url):
    """
    Internal helper function to test MCP connection by URL.
    Can be called directly without going through decorator validation.
    """
    import requests

    if not server_url:
        return {"success": False, "error": "Server URL is required"}

    # Perform MCP initialize handshake
    try:
        # Send initialize request (JSON-RPC 2.0)
        init_request = {
            "jsonrpc": "2.0",
            "id": f"test_{int(datetime.now().timestamp() * 1000)}",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "resources": {},
                    "prompts": {}
                },
                "clientInfo": {
                    "name": "amplify-genai-test",
                    "version": "1.0.0"
                }
            }
        }

        response = requests.post(
            server_url,
            json=init_request,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            timeout=10
        )

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:200]}"
            }

        init_result = response.json()

        if "error" in init_result:
            return {
                "success": False,
                "error": init_result["error"].get("message", "Unknown error")
            }

        server_info = init_result.get("result", {}).get("serverInfo", {})

        # Send initialized notification
        requests.post(
            server_url,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {}
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            timeout=5
        )

        # Get tools list
        tools_request = {
            "jsonrpc": "2.0",
            "id": f"tools_{int(datetime.now().timestamp() * 1000)}",
            "method": "tools/list",
            "params": {}
        }

        tools_response = requests.post(
            server_url,
            json=tools_request,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            timeout=10
        )

        tools = []
        if tools_response.status_code == 200:
            tools_result = tools_response.json()
            if "result" in tools_result and "tools" in tools_result["result"]:
                tools = [
                    {
                        "name": t.get("name"),
                        "description": t.get("description"),
                        "inputSchema": t.get("inputSchema", {})
                    }
                    for t in tools_result["result"]["tools"]
                ]

        return {
            "success": True,
            "data": {
                "serverInfo": server_info,
                "tools": tools
            }
        }

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Connection timed out"}
    except requests.exceptions.ConnectionError as e:
        return {"success": False, "error": f"Connection failed: Could not connect to server"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"Request failed: {str(e)}"}
    except json.JSONDecodeError:
        return {"success": False, "error": "Invalid JSON response from server"}
    except Exception as e:
        logger.error(f"Error testing MCP connection: {str(e)}")
        return {"success": False, "error": f"Test failed: {str(e)}"}


@api_tool(
    path="/integrations/mcp/servers/test",
    name="testMCPConnection",
    method="POST",
    tags=["MCP"],
    description="Test connection to an MCP server",
    parameters={
        "type": "object",
        "properties": {
            "serverId": {"type": "string"},
            "name": {"type": "string"},
            "url": {"type": "string"},
            "transport": {"type": "string"},
        },
        "required": [],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "data": {"type": "object"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@validated("test_mcp_connection")
def test_mcp_connection(event, context, current_user, name, data):
    """Test connection to an MCP server"""

    request_data = data.get("data", {})
    server_url = request_data.get("url")

    # If serverId provided, get URL from stored config
    server_id = request_data.get("serverId")
    if server_id and not server_url:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ.get("USER_STORAGE_TABLE", ""))

        full_user_id = get_full_user_id(current_user)
        hash_key = create_hash_key(full_user_id, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        response = table.get_item(Key={"PK": pk, "SK": server_id})
        if "Item" not in response:
            return {"success": False, "error": "MCP server not found"}

        server_url = response["Item"].get("data", {}).get("url")

    # Use the internal helper function
    return _test_mcp_connection_by_url(server_url)


@api_tool(
    path="/integrations/mcp/server/tools",
    name="getMCPServerTools",
    method="POST",
    tags=["MCP"],
    description="Get tools from an MCP server",
    parameters={
        "type": "object",
        "properties": {
            "serverId": {"type": "string"},
        },
        "required": ["serverId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "data": {"type": "array"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "USER_STORAGE_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("get_mcp_server_tools")
def get_mcp_server_tools(event, context, current_user, name, data):
    """Get tools from an MCP server"""

    request_data = data.get("data", {})
    server_id = request_data.get("serverId")

    if not server_id:
        return {"success": False, "message": "Server ID is required"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    try:
        full_user_id = get_full_user_id(current_user)
        hash_key = create_hash_key(full_user_id, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        response = table.get_item(Key={"PK": pk, "SK": server_id})

        if "Item" not in response:
            return {"success": False, "message": "MCP server not found"}

        server_data = response["Item"].get("data", {})
        tools = server_data.get("tools", [])

        return {"success": True, "data": tools}

    except Exception as e:
        logger.error(f"Error getting MCP server tools: {str(e)}")
        return {"success": False, "message": f"Error getting tools: {str(e)}"}


@api_tool(
    path="/integrations/mcp/server/refresh",
    name="refreshMCPServerTools",
    method="POST",
    tags=["MCP"],
    description="Refresh tools from an MCP server",
    parameters={
        "type": "object",
        "properties": {
            "serverId": {"type": "string"},
        },
        "required": ["serverId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "data": {"type": "object"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "USER_STORAGE_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("refresh_mcp_server_tools")
def refresh_mcp_server_tools(event, context, current_user, name, data):
    """Refresh tools from an MCP server (reconnect and discover)"""

    request_data = data.get("data", {})
    server_id = request_data.get("serverId")

    if not server_id:
        return {"success": False, "message": "Server ID is required"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    try:
        full_user_id = get_full_user_id(current_user)
        hash_key = create_hash_key(full_user_id, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        response = table.get_item(Key={"PK": pk, "SK": server_id})

        if "Item" not in response:
            return {"success": False, "message": "MCP server not found"}

        server_data = response["Item"].get("data", {})
        server_url = server_data.get("url")

        if not server_url:
            return {"success": False, "message": "Server URL not configured"}

        # Test connection and get tools using internal helper
        test_result = _test_mcp_connection_by_url(server_url)

        if not test_result.get("success"):
            # Update status to error
            server_data["status"] = "error"
            server_data["lastError"] = test_result.get("error", "Connection failed")
            server_data["updatedAt"] = datetime.now(timezone.utc).isoformat()

            table.put_item(Item={"PK": pk, "SK": server_id, "data": server_data})

            return test_result

        # Update server with new tools and status
        result_data = test_result.get("data", {})
        server_data["tools"] = result_data.get("tools", [])
        server_data["status"] = "connected"
        server_data["lastConnected"] = datetime.now(timezone.utc).isoformat()
        server_data["lastError"] = None
        server_data["updatedAt"] = datetime.now(timezone.utc).isoformat()

        table.put_item(Item={"PK": pk, "SK": server_id, "data": server_data})

        logger.info(f"Refreshed tools for MCP server {server_id}")

        return {
            "success": True,
            "data": {
                "tools": server_data["tools"]
            }
        }

    except Exception as e:
        logger.error(f"Error refreshing MCP server tools: {str(e)}")
        return {"success": False, "message": f"Error refreshing tools: {str(e)}"}
