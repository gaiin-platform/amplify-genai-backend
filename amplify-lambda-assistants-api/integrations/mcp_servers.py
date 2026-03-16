"""
MCP (Model Context Protocol) Server Configuration

Manages user MCP server configurations stored in DynamoDB.
Users can add, remove, and configure their own MCP servers.
"""

from datetime import datetime, timezone
import json
import os
import uuid

import boto3

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

# Configurable timeout for MCP connections (in seconds)
MCP_CONNECTION_TIMEOUT = int(os.environ.get("MCP_CONNECTION_TIMEOUT", "10"))
MCP_NOTIFICATION_TIMEOUT = int(os.environ.get("MCP_NOTIFICATION_TIMEOUT", "5"))
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


def _sanitize_headers_for_response(headers):
    """Return headers safe for API responses (redacts sensitive auth values)."""
    if not isinstance(headers, dict):
        return {}

    redacted = {}
    for key, value in headers.items():
        if str(key).lower() == "authorization":
            continue
        redacted[key] = value
    return redacted


def _default_mcp_oauth_callback_url() -> str:
    """Canonical callback URL for provider redirects (backend endpoint)."""
    api_base = os.environ.get("API_BASE_URL", "").rstrip("/")
    return f"{api_base}/integrations/mcp/server/oauth/callback" if api_base else ""




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
        hash_key = create_hash_key(current_user, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        logger.info(f"Querying MCP servers for user: {current_user}, PK: {pk}")

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
                "headers": _sanitize_headers_for_response(server_data.get("headers", {})),
                "oauthConnected": server_data.get("oauthConnected", False),
                "oauthDiscoverable": server_data.get("oauthDiscoverable", False),
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
        hash_key = create_hash_key(current_user, MCP_APP_ID)
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
                "headers": _sanitize_headers_for_response(server_data.get("headers", {})),
                "oauthConnected": server_data.get("oauthConnected", False),
                "oauthDiscoverable": server_data.get("oauthDiscoverable", False),
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
    headers = request_data.get("headers", {})
    if not isinstance(headers, dict):
        headers = {}

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
        
        hash_key = create_hash_key(current_user, MCP_APP_ID)
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
            "headers": headers,
            "createdAt": now,
            "updatedAt": now,
        }

        # Eagerly probe for OAuth2 discovery so the UI can show the right
        # "Sign In" button without the user having to click Refresh first.
        discovery = _discover_mcp_oauth(server_url.strip())
        if discovery:
            server_data["oauthDiscoverable"] = True

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
                **{
                    **server_data,
                    "headers": _sanitize_headers_for_response(server_data.get("headers", {})),
                },
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
        hash_key = create_hash_key(current_user, MCP_APP_ID)
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
        if "headers" in request_data:
            v = request_data["headers"]
            existing_data["headers"] = v if isinstance(v, dict) else {}

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
                **{
                    **existing_data,
                    "headers": _sanitize_headers_for_response(existing_data.get("headers", {})),
                },
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
        hash_key = create_hash_key(current_user, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        table.delete_item(Key={"PK": pk, "SK": server_id})

        logger.info(f"Deleted MCP server {server_id} for user {current_user}")

        return {"success": True}

    except Exception as e:
        logger.error(f"Error deleting MCP server: {str(e)}")
        return {"success": False, "message": f"Error deleting server: {str(e)}"}


def _test_mcp_connection_by_url(server_url, custom_headers=None):
    """
    Internal helper function to test MCP connection by URL.
    Can be called directly without going through decorator validation.
    custom_headers: optional dict of HTTP headers to include (e.g. Authorization).
    """
    import requests

    if not server_url:
        return {"success": False, "error": "Server URL is required"}

    # Build base headers, then merge any caller-supplied headers
    _base_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }
    if custom_headers and isinstance(custom_headers, dict):
        _base_headers.update(custom_headers)

    def _parse_mcp_response(response):
        """Parse MCP response supporting JSON and text/event-stream payloads."""
        content_type = (response.headers.get("Content-Type") or "").lower()

        try:
            return response.json()
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError):
            body = (response.text or "").strip()

            if not body:
                raise ValueError("Empty response body from MCP server")

            if "text/event-stream" in content_type or "data:" in body:
                for line in body.splitlines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue

                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue

                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError:
                        continue

            preview = body[:200]
            raise ValueError(
                f"Non-JSON MCP response (content-type: {content_type or 'unknown'}): {preview}"
            )

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
            headers=_base_headers,
            timeout=MCP_CONNECTION_TIMEOUT
        )

        if response.status_code == 401:
            # Try to discover OAuth2 endpoints advertised by the server
            discovered = _discover_mcp_oauth(server_url, response)
            return {
                "success": False,
                "requiresAuth": True,
                "oauthDiscoverable": bool(discovered),
                "oauthMeta": discovered,
                "error": "Server requires authentication. Connect via OAuth2 or provide an Authorization header.",
            }

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:200]}"
            }

        init_result = _parse_mcp_response(response)

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
            headers=_base_headers,
            timeout=MCP_NOTIFICATION_TIMEOUT
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
            headers=_base_headers,
            timeout=MCP_CONNECTION_TIMEOUT
        )

        tools = []
        if tools_response.status_code == 200:
            tools_result = _parse_mcp_response(tools_response)
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
    except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Invalid MCP response: {str(e)}")
        return {
            "success": False,
            "error": (
                "Server did not return a valid MCP JSON-RPC response. "
                f"Details: {str(e)}"
            )
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"MCP request failed: {str(e)}")
        return {"success": False, "error": "Request failed"}
    except Exception as e:
        logger.error(f"Error testing MCP connection: {str(e)}")
        return {"success": False, "error": "Test failed"}


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
    inline_headers = request_data.get("headers")
    if not isinstance(inline_headers, dict):
        inline_headers = {}

    # If serverId provided, get URL (and stored headers) from stored config
    server_id = request_data.get("serverId")
    if server_id and not server_url:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ.get("USER_STORAGE_TABLE", ""))


        hash_key = create_hash_key(current_user, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        response = table.get_item(Key={"PK": pk, "SK": server_id})
        if "Item" not in response:
            return {"success": False, "error": "MCP server not found"}

        stored = response["Item"].get("data", {})
        server_url = stored.get("url")
        # Use stored headers when no inline headers provided
        if not inline_headers:
            stored_headers = stored.get("headers", {})
            inline_headers = stored_headers if isinstance(stored_headers, dict) else {}

    # Use the internal helper function
    return _test_mcp_connection_by_url(server_url, inline_headers)


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
        hash_key = create_hash_key(current_user, MCP_APP_ID)
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
        hash_key = create_hash_key(current_user, MCP_APP_ID)
        pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

        response = table.get_item(Key={"PK": pk, "SK": server_id})

        if "Item" not in response:
            return {"success": False, "message": "MCP server not found"}

        server_data = response["Item"].get("data", {})
        server_url = server_data.get("url")
        server_headers = server_data.get("headers", {})
        if not isinstance(server_headers, dict):
            server_headers = {}

        if not server_url:
            return {"success": False, "message": "Server URL not configured"}

        # Test connection and get tools using internal helper
        test_result = _test_mcp_connection_by_url(server_url, server_headers)

        if not test_result.get("success"):
            # Update status to error; persist oauthDiscoverable so the UI can
            # show the right button without needing another round-trip.
            server_data["status"] = "error"
            server_data["lastError"] = test_result.get("error", "Connection failed")
            server_data["updatedAt"] = datetime.now(timezone.utc).isoformat()
            if test_result.get("oauthDiscoverable"):
                server_data["oauthDiscoverable"] = True

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


# ---------------------------------------------------------------------------
# Generic OAuth2 support for MCP servers
#
# Flow:
#   1. Frontend calls POST /integrations/mcp/server/oauth/start with
#      { serverId, clientId, clientSecret, authorizationUrl, tokenUrl, scopes }
#   2. Backend stores those settings in short-lived OAuth state (TTL), then
#      returns a redirect URL that the frontend opens in a popup/tab.
#   3. User authenticates; provider redirects to /integrations/mcp/server/oauth/callback
#   4. Backend exchanges the code for a token and writes it as
#      headers.Authorization = "Bearer <access_token>" on the server record,
#      then renders a "close this window" HTML page.
#   5. All existing MCP connection code already reads headers — done.
# ---------------------------------------------------------------------------

def _get_mcp_oauth_state_table():
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(os.environ["OAUTH_STATE_TABLE"])


def _discover_mcp_oauth(server_url: str, auth_response=None) -> dict | None:
    """
    Attempt to discover OAuth2 metadata from an MCP server following the
    MCP Authorization spec (2025-03-26).

    Steps:
      1. Parse WWW-Authenticate header for resource_metadata URL.
      2. Fetch /.well-known/oauth-protected-resource to get auth server issuer.
      3. Fetch /.well-known/oauth-authorization-server from auth server.
      4. Return a dict with authorizationUrl, tokenUrl, registrationUrl, scopes.
    Returns None if discovery fails.
    """
    import re
    import urllib.parse
    import requests as _req

    try:
        # Step 1: Parse WWW-Authenticate from the 401 response (if provided),
        #         otherwise probe the server directly.
        resource_metadata_url = None

        if auth_response is not None:
            www_auth = auth_response.headers.get("WWW-Authenticate", "")
        else:
            probe = _req.post(
                server_url,
                json={"jsonrpc": "2.0", "id": "probe", "method": "initialize",
                      "params": {"protocolVersion": "2024-11-05",
                                 "capabilities": {},
                                 "clientInfo": {"name": "amplify", "version": "1.0"}}},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            www_auth = probe.headers.get("WWW-Authenticate", "")

        # Extract resource_metadata="..." from the header value
        m = re.search(r'resource_metadata="([^"]+)"', www_auth, re.I)
        if m:
            resource_metadata_url = m.group(1)
        else:
            # Fall back: try the standard path relative to server origin
            parsed = urllib.parse.urlparse(server_url)
            resource_metadata_url = f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource"

        # Step 2: Fetch protected resource metadata
        pr_resp = _req.get(resource_metadata_url, timeout=10)
        pr_resp.raise_for_status()
        pr_meta = pr_resp.json()

        auth_servers = pr_meta.get("authorization_servers", [])
        if not auth_servers:
            return None
        as_issuer = auth_servers[0].rstrip("/")

        # Step 3: Fetch authorization server metadata
        as_resp = _req.get(f"{as_issuer}/.well-known/oauth-authorization-server", timeout=10)
        as_resp.raise_for_status()
        as_meta = as_resp.json()

        # Step 4: Return the useful bits
        return {
            "authorizationUrl": as_meta.get("authorization_endpoint", ""),
            "tokenUrl": as_meta.get("token_endpoint", ""),
            "registrationUrl": as_meta.get("registration_endpoint", ""),
            "scopes": " ".join(pr_meta.get("scopes_supported", [])),
            "pkceRequired": "S256" in as_meta.get("code_challenge_methods_supported", []),
            "publicClient": "none" in as_meta.get("token_endpoint_auth_methods_supported", []),
        }
    except Exception as e:
        logger.debug(f"MCP OAuth discovery failed for {server_url}: {e}")
        return None


@api_tool(
    path="/integrations/mcp/server/oauth/start",
    name="startMCPOAuth",
    method="POST",
    tags=["MCP"],
    description="Begin OAuth2 authorization flow for an MCP server",
    parameters={
        "type": "object",
        "properties": {
            "serverId": {"type": "string"},
            "clientId": {"type": "string"},
            "clientSecret": {"type": "string"},
            "authorizationUrl": {"type": "string"},
            "tokenUrl": {"type": "string"},
            "scopes": {"type": "string"},
        },
        "required": ["serverId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "authorizationUrl": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "USER_STORAGE_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
    "OAUTH_STATE_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated("start_mcp_oauth")
def start_mcp_oauth(event, context, current_user, name, data):
    """Start OAuth2 flow for an MCP server."""
    import time as _time
    import urllib.parse

    import base64 as _b64
    import hashlib as _hashlib
    import secrets as _secrets

    req = data.get("data", {})
    server_id = req.get("serverId")
    client_id = req.get("clientId", "").strip()
    client_secret = req.get("clientSecret", "").strip()
    authorization_url = req.get("authorizationUrl", "").strip()
    token_url = req.get("tokenUrl", "").strip()
    scopes = req.get("scopes", "").strip()
    # Optional client override for local dev or custom frontends.
    redirect_uri_from_client = req.get("redirectUri", "").strip()
    redirect_uri = redirect_uri_from_client or _default_mcp_oauth_callback_url()

    if not redirect_uri:
        return {
            "success": False,
            "message": "Could not determine redirect URI. Set API_BASE_URL or provide redirectUri.",
        }

    if not server_id:
        return {"success": False, "message": "serverId is required"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    hash_key = create_hash_key(current_user, MCP_APP_ID)
    pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

    response = table.get_item(Key={"PK": pk, "SK": server_id})
    if "Item" not in response:
        return {"success": False, "message": "MCP server not found"}

    server_data = response["Item"].get("data", {})
    server_url = server_data.get("url", "")

    # ------------------------------------------------------------------ #
    # Auto-discovery: if caller did not supply OAuth2 endpoints, discover  #
    # them from the server's /.well-known metadata + dynamic registration. #
    # ------------------------------------------------------------------ #
    code_verifier = None  # Only set when server requires PKCE
    public_client = False

    if not authorization_url or not token_url:
        meta = _discover_mcp_oauth(server_url)
        if not meta:
            return {"success": False,
                    "message": "Could not discover OAuth2 config. Please enter it manually."}
        authorization_url = meta["authorizationUrl"]
        token_url = meta["tokenUrl"]
        registration_url = meta.get("registrationUrl", "")
        discovered_scopes = meta.get("scopes", "")
        public_client = meta.get("publicClient", False)

        if not scopes and discovered_scopes:
            scopes = discovered_scopes

        # Dynamic client registration (RFC 7591) — get a fresh client_id
        if not client_id and registration_url:
            import requests as _requests_reg
            try:
                reg_redirect_uri = redirect_uri
                # Use a unique client_name per redirect-URI origin so that
                # OAuth servers (e.g. scite.ai) don't deduplicate registrations
                # across environments, which would cause redirect_uri mismatches
                # during token exchange.
                _reg_origin = urllib.parse.urlparse(reg_redirect_uri).netloc or "amplify"
                reg_payload = {
                    "client_name": f"Amplify GenAI ({_reg_origin})",
                    "redirect_uris": [reg_redirect_uri],
                    "grant_types": ["authorization_code"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                }
                reg_resp = _requests_reg.post(
                    registration_url,
                    json=reg_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                reg_resp.raise_for_status()
                reg_data = reg_resp.json()
                client_id = reg_data.get("client_id", "")
                client_secret = reg_data.get("client_secret", "")
                logger.info(f"Dynamic client registration succeeded: client_id={client_id}")
            except Exception as e:
                logger.warning(f"Dynamic client registration failed: {e}")

        if not client_id:
            return {"success": False,
                    "message": "Could not register OAuth2 client automatically. Please enter Client ID manually."}

        # Generate PKCE code_verifier + challenge
        if meta.get("pkceRequired"):
            code_verifier = _secrets.token_urlsafe(64)
    else:
        # Manual config supplied — PKCE off unless explicitly requested
        pass

    if not authorization_url or not token_url:
        return {"success": False, "message": "clientId, authorizationUrl and tokenUrl are required"}

    # Store state in OAUTH_STATE_TABLE so callback can find user + server.
    # The oauth2 config goes here too (short-lived, TTL 1h) to avoid
    # needing the OAUTH_ENCRYPTION_PARAMETER env var on the server record.
    state = str(uuid.uuid4())
    state_table = _get_mcp_oauth_state_table()
    current_timestamp = int(_time.time())
    state_item = {
        "state": state,
        "integration": f"mcp_{server_id}",
        "user": current_user,
        "server_id": server_id,
        "timestamp": current_timestamp,
        "ttl": current_timestamp + 3600,
        # OAuth2 config stored here — no encryption needed, TTL cleans it up
        "oauth2Config": {
            "clientId": client_id,
            "clientSecret": client_secret,
            "tokenUrl": token_url,
            "scopes": scopes,
            "publicClient": public_client,
        },
    }
    # Store code_verifier (PKCE) so callback can use it in token exchange
    if code_verifier:
        state_item["code_verifier"] = code_verifier
    # Persist redirect URI used for authorization so token exchange uses the exact same value.
    state_item["redirect_uri"] = redirect_uri
    state_table.put_item(Item=state_item)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    if scopes:
        params["scope"] = scopes
    # Add PKCE params
    if code_verifier:
        code_challenge = _b64.urlsafe_b64encode(
            _hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"

    full_auth_url = authorization_url + ("&" if "?" in authorization_url else "?") + urllib.parse.urlencode(params)

    logger.info(f"Started MCP OAuth2 flow for server {server_id}, user {current_user}")
    return {"success": True, "authorizationUrl": full_auth_url}


def mcp_oauth_callback(event, context):
    """
    OAuth2 callback for MCP servers. Exchanges the authorization code for a
    token and stores it as headers.Authorization on the server record.
    This is a plain Lambda handler (no @validated decorator) because the
    provider redirects here without our JWT.
    """
    import urllib.parse

    def _html_ok():
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": """<html><head><title>Connected</title>
            <style>body{font-family:Arial,sans-serif;display:flex;justify-content:center;
            align-items:center;height:100vh;margin:0;background:#f0f0f0;}
            .box{text-align:center;padding:2rem;background:#fff;border-radius:8px;
            box-shadow:0 4px 6px rgba(0,0,0,.1);}h1{color:#2ecc71;}</style></head>
            <body><div class="box"><h1>&#10003; Connected</h1>
            <p>MCP server authenticated. You can close this window.</p>
            <button onclick="window.close()">Close</button></div></body></html>""",
        }

    def _html_err(msg):
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "text/html"},
            "body": f"""<html><head><title>Error</title>
            <style>body{{font-family:Arial,sans-serif;display:flex;justify-content:center;
            align-items:center;height:100vh;margin:0;background:#f0f0f0;}}
            .box{{text-align:center;padding:2rem;background:#fff;border-radius:8px;
            box-shadow:0 4px 6px rgba(0,0,0,.1);}}h1{{color:#e74c3c;}}</style></head>
            <body><div class="box"><h1>Authentication Failed</h1><p>{msg}</p>
            </div></body></html>""",
        }

    query = event.get("queryStringParameters") or {}
    error = query.get("error")
    if error:
        return _html_err(f"OAuth error: {query.get('error_description', error)}")

    code = query.get("code")
    state = query.get("state")
    if not code or not state:
        return _html_err("Missing code or state parameter.")

    # Delegate to shared exchange logic (no user check since no JWT here)
    result = _do_mcp_token_exchange(code, state, current_user=None)
    if result.get("success"):
        return _html_ok()
    return _html_err(result.get("message", "Token exchange failed."))


@api_tool(
    path="/integrations/mcp/server/oauth/disconnect",
    name="disconnectMCPOAuth",
    method="POST",
    tags=["MCP"],
    description="Remove OAuth2 token and config from an MCP server",
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
    "USER_STORAGE_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("disconnect_mcp_oauth")
def disconnect_mcp_oauth(event, context, current_user, name, data):
    """Remove the stored OAuth2 token (and config) from an MCP server record."""
    req = data.get("data", {})
    server_id = req.get("serverId")

    if not server_id:
        return {"success": False, "message": "serverId is required"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["USER_STORAGE_TABLE"])

    hash_key = create_hash_key(current_user, MCP_APP_ID)
    pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

    response = table.get_item(Key={"PK": pk, "SK": server_id})
    if "Item" not in response:
        return {"success": False, "message": "MCP server not found"}

    server_data = response["Item"].get("data", {})

    # Remove the auth header and oauth config
    headers = server_data.get("headers", {})
    if isinstance(headers, dict):
        headers.pop("Authorization", None)
    server_data["headers"] = headers
    server_data.pop("oauth2Config", None)
    server_data["oauthConnected"] = False
    server_data["updatedAt"] = datetime.now(timezone.utc).isoformat()

    table.put_item(Item={"PK": pk, "SK": server_id, "data": server_data})

    logger.info(f"Disconnected OAuth for MCP server {server_id}, user {current_user}")
    return {"success": True}


def _do_mcp_token_exchange(code: str, state: str, current_user: str) -> dict:
    """
    Shared logic: look up the pending state, do the PKCE token exchange, and
    write the access_token as headers.Authorization on the server record.
    Called by both mcp_oauth_callback (unauthenticated) and
    mcp_oauth_exchange (authenticated, called from the frontend callback page).
    Returns a dict with at minimum {"success": bool} and optionally "message".
    """
    state_table = _get_mcp_oauth_state_table()
    try:
        item = state_table.get_item(Key={"state": state}).get("Item")
    except Exception as e:
        logger.error(f"MCP token exchange: state lookup failed: {e}")
        return {"success": False, "message": "Internal error (state lookup failed)."}

    if not item:
        return {"success": False, "message": "Invalid or expired OAuth state."}

    # Security: ensure the authenticated user matches who started the flow
    if current_user and item.get("user") and item["user"] != current_user:
        logger.warning(
            f"MCP OAuth state user mismatch: expected {current_user}, got {item['user']}"
        )
        return {"success": False, "message": "OAuth state does not belong to the current user."}

    user = item["user"]
    server_id = item["server_id"]

    cfg = item.get("oauth2Config")
    if not cfg:
        return {"success": False, "message": "OAuth2 config missing from state. Please restart the auth flow."}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ.get("USER_STORAGE_TABLE", ""))

    hash_key = create_hash_key(user, MCP_APP_ID)
    pk = f"{hash_key}#{MCP_ENTITY_TYPE}"

    server_resp = table.get_item(Key={"PK": pk, "SK": server_id})
    if "Item" not in server_resp:
        return {"success": False, "message": "MCP server record not found."}

    server_data = server_resp["Item"].get("data", {})

    # Use the redirect_uri stored during start_mcp_oauth.
    redirect_uri = item.get("redirect_uri") or _default_mcp_oauth_callback_url()

    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": cfg["clientId"],
    }
    if cfg.get("clientSecret") and not cfg.get("publicClient"):
        token_payload["client_secret"] = cfg["clientSecret"]
    if item.get("code_verifier"):
        token_payload["code_verifier"] = item["code_verifier"]

    # Use stdlib http.client instead of requests to avoid urllib3 quirks
    # (e.g. auto-injected Authorization headers from proxy env-vars, or
    # incorrect parsing of WWW-Authenticate: Bearer challenge responses).
    import http.client as _http_client
    import urllib.parse as _urllib_parse
    import json as _json_mod

    _parsed_token_url = _urllib_parse.urlparse(cfg["tokenUrl"])
    _conn_cls = _http_client.HTTPSConnection if _parsed_token_url.scheme == "https" else _http_client.HTTPConnection
    _conn = _conn_cls(_parsed_token_url.netloc, timeout=15)
    _req_path = _parsed_token_url.path + (f"?{_parsed_token_url.query}" if _parsed_token_url.query else "")
    _body_encoded = _urllib_parse.urlencode(token_payload).encode("utf-8")

    try:
        _conn.request(
            "POST", _req_path, body=_body_encoded,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        _resp = _conn.getresponse()
        _resp_text = _resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.error(f"MCP OAuth token exchange network error: {e}")
        return {"success": False, "message": f"Token exchange failed: {str(e)[:150]}"}
    finally:
        try:
            _conn.close()
        except Exception:
            pass

    if _resp.status not in (200, 201):
        logger.error(f"MCP OAuth token exchange HTTP {_resp.status} from {cfg.get('tokenUrl', '?')}: {_resp_text[:500]}")
        try:
            _err_data = _json_mod.loads(_resp_text)
            _err_desc = _err_data.get("error_description") or ""
            _err_code = _err_data.get("error") or ""
            _err_msg = _err_desc or _err_code or f"HTTP {_resp.status}"
        except Exception:
            _err_msg = _resp_text[:100] or f"HTTP {_resp.status}"
        return {"success": False, "message": f"[HTTP {_resp.status}] Token exchange failed: {_err_msg[:200]}"}

    try:
        token_data = _json_mod.loads(_resp_text)
    except Exception:
        return {"success": False, "message": "Token exchange: invalid JSON response from provider."}

    access_token = token_data.get("access_token")
    if not access_token:
        logger.error(f"MCP OAuth: no access_token in response: {token_data}")
        return {"success": False, "message": "No access_token returned by provider."}

    existing_headers = server_data.get("headers", {})
    if not isinstance(existing_headers, dict):
        existing_headers = {}
    existing_headers["Authorization"] = f"Bearer {access_token}"
    server_data["headers"] = existing_headers
    server_data["oauthConnected"] = True
    server_data["updatedAt"] = datetime.now(timezone.utc).isoformat()

    table.put_item(Item={"PK": pk, "SK": server_id, "data": server_data})

    # Clean up state record
    try:
        state_table.delete_item(Key={"state": state})
    except Exception:
        pass

    logger.info(f"MCP OAuth token stored for server {server_id}, user {user}")
    return {"success": True}


@api_tool(
    path="/integrations/mcp/server/oauth/exchange",
    name="mcpOAuthExchange",
    method="POST",
    tags=["MCP"],
    description="Complete OAuth2 code exchange for an MCP server (called from the frontend callback page)",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "state": {"type": "string"},
        },
        "required": ["code", "state"],
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
    "USER_STORAGE_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
    "OAUTH_STATE_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.DELETE_ITEM],
})
@validated("mcp_oauth_exchange")
def mcp_oauth_exchange(event, context, current_user, name, data):
    """Complete the OAuth2 code exchange. Used by frontend-managed callback flows with a valid JWT."""
    req = data.get("data", {})
    code = req.get("code", "").strip()
    state = req.get("state", "").strip()

    if not code or not state:
        return {"success": False, "message": "code and state are required"}

    return _do_mcp_token_exchange(code, state, current_user)
