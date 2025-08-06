import os
import re
import inspect
from jsonschema import validate
from jsonschema.exceptions import ValidationError

import re
from data.user import CommonData

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata import permissions

setup_validated(rules, permissions.get_permission_checker)
from pycommon.api.ops import api_tool, set_route_data, set_permissions_by_state
from service.routes import route_data

set_route_data(route_data)
set_permissions_by_state(permissions)


USER_DATA_TABLE = os.environ["USER_STORAGE_TABLE"]
table_name = os.getenv("USER_STORAGE_TABLE")
storage = CommonData(table_name)


def has_named_parameter(func, param_name):
    # Get the signature of the function
    sig = inspect.signature(func)
    # Check if the parameter name exists in the signature's parameters
    return param_name in sig.parameters


# Create a missing param exception that has a param name and message
class MissingParamException(Exception):
    def __init__(self, param_name, message):
        self.param_name = param_name
        self.message = message

    # Create a string representation of the exception
    def __str__(self):
        return f"MissingParamException: {self.message}"


def camel_to_snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def common_handler(operation, func_schema, **optional_params):
    def handler(event, context, current_user, name, data):
        try:
            print(f"Function schema: {func_schema}")

            access_token = data["access_token"]

            wrapper_schema = {
                "type": "object",
                "properties": {"data": func_schema},
                "required": ["data"],
            }

            # Validate the data against the schema
            print("Validating request")
            try:
                validate(data, wrapper_schema)
            except ValidationError as e:
                print("Validation error: ", str(e))
                raise ValueError(f"Invalid request: {str(e)}")

            print("Converting parameters to snake case")
            # build a keyword argument dictionary from the data based on the schema
            args = {
                camel_to_snake(param): data["data"].get(
                    param, func_schema["properties"][param].get("default", None)
                )
                for param in func_schema.get("properties", [])
            }

            if has_named_parameter(operation, "current_user"):
                args["current_user"] = current_user

            if has_named_parameter(operation, "access_token"):
                args["access_token"] = access_token

            print("Invoking operation")
            response = operation(**args)

            return {"success": True, "data": response}
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"Unexpected error: {str(e)}")
            return {"success": False, "error": "Unexpected error."}

    return handler


@validated("route", False)
def route(event, context, current_user, name, data):

    try:
        # get the request path from the event and remove the first component...if there aren't enough components
        # then the path is invalid
        target_path_string = event["path"]
        print(f"Route: {target_path_string}")

        print(f"Route data: {route_data}")

        route_info = route_data.get(target_path_string, None)
        if not route_info:
            return {"success": False, "error": "Invalid path"}

        handler_func = route_info["handler"]
        func_schema = route_info["parameters"] or {}

        return common_handler(handler_func, func_schema)(
            event, context, current_user, name, data
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e)}


def _create_hash_key(current_user, app_id):
    """Create a secure hash key combining user and app_id"""
    if not current_user or not app_id:
        raise ValueError("Both current_user and app_id are required")

    if not isinstance(current_user, str) or not isinstance(app_id, str):
        raise ValueError("Both current_user and app_id must be strings")

    # Allow underscore in email part, replace other unsafe chars with dash
    sanitized_user = re.sub(r"[^a-zA-Z0-9@._-]", "-", current_user)
    sanitized_app = re.sub(r"[^a-zA-Z0-9-]", "-", app_id)

    # Use # as delimiter to match DynamoDB convention
    return f"{sanitized_user}#{sanitized_app}"


def _decode_app_id(app_id):
    """Convert the hashed app_id back to its components"""
    return app_id.split("#")[-1] if "#" in app_id else app_id


def _remove_keys(item):
    """Remove PK and SK from the item"""
    item_copy = item.copy()  # Create a copy to avoid modifying the original
    item_copy.pop("PK", None)  # Remove 'PK' if exists
    item_copy.pop("SK", None)  # Remove 'SK' if exists

    # Make UUID lowercase for consistency
    if "UUID" in item_copy:
        item_copy["uuid"] = item_copy["UUID"]
        item_copy.pop("UUID", None)

    return item_copy


@api_tool(
    path="/user-data/put",
    tags=["default", "user-data"],
    name="putUserData",
    description="Stores user data in DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType", "itemId", "data"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity being stored",
            },
            "itemId": {
                "type": "string",
                "description": "The unique identifier for the item",
            },
            "data": {
                "type": "object",
                "description": "The data to store",
                "additionalProperties": True,
            },
            "rangeKey": {
                "type": "string",
                "description": "Optional range key for the item",
            },
        },
    },
    output={
        "type": "object",
        "properties": {
            "uuid": {"type": "string", "description": "The UUID of the stored item"}
        },
        "required": ["uuid"],
    },
)
def handle_put_item(current_user, app_id, entity_type, item_id, data, range_key=None):
    """Handler to store an item in DynamoDB"""
    hash_key = _create_hash_key(current_user, app_id)
    return {"uuid": storage.put_item(hash_key, entity_type, item_id, data, range_key)}


@api_tool(
    path="/user-data/get",
    tags=["default", "user-data"],
    name="getUserData",
    description="Retrieves user data from DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType", "itemId"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity to retrieve",
            },
            "itemId": {
                "type": "string",
                "description": "The unique identifier for the item",
            },
            "rangeKey": {
                "type": "string",
                "description": "Optional range key for the item",
            },
        },
    },
    output={
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "appId": {
                        "type": "string",
                        "description": "The application identifier",
                    },
                    "entityType": {
                        "type": "string",
                        "description": "The type of entity",
                    },
                    "itemId": {
                        "type": "string",
                        "description": "The unique identifier for the item",
                    },
                    "data": {
                        "type": "object",
                        "description": "The stored data",
                        "additionalProperties": True,
                    },
                    "uuid": {"type": "string", "description": "The UUID of the item"},
                    "rangeKey": {
                        "type": "string",
                        "description": "The range key if present",
                    },
                },
                "required": ["appId", "entityType", "itemId", "data", "uuid"],
            },
            {"type": "null", "description": "Returns null if item not found"},
        ]
    },
)
def handle_get_item(current_user, app_id, entity_type, item_id, range_key=None):
    """Handler to retrieve an item from DynamoDB"""
    hash_key = _create_hash_key(current_user, app_id)
    item = storage.get_item(hash_key, entity_type, item_id, range_key)

    # Convert the app_id and remove keys before returning
    if item:
        item["appId"] = _decode_app_id(item["appId"])
        return _remove_keys(item)
    return None


@api_tool(
    path="/user-data/query-range",
    tags=["default", "user-data"],
    name="queryUserDataByRange",
    description="Queries user data by range in DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity to query",
            },
            "rangeStart": {"type": "string", "description": "Optional start of range"},
            "rangeEnd": {"type": "string", "description": "Optional end of range"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": 100,
                "description": "Optional maximum number of items to return (default is 100)",
            },
        },
    },
    output={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "appId": {
                    "type": "string",
                    "description": "The application identifier",
                },
                "entityType": {"type": "string", "description": "The type of entity"},
                "itemId": {
                    "type": "string",
                    "description": "The unique identifier for the item",
                },
                "data": {
                    "type": "object",
                    "description": "The stored data",
                    "additionalProperties": True,
                },
                "uuid": {"type": "string", "description": "The UUID of the item"},
                "rangeKey": {
                    "type": "string",
                    "description": "The range key if present",
                },
            },
            "required": ["appId", "entityType", "itemId", "data", "uuid"],
        },
        "description": "Array of items matching the range query",
    },
)
def handle_query_by_range(
    current_user, app_id, entity_type, range_start=None, range_end=None, limit=100
):
    """Handler to query items by range"""
    limit = limit if limit and limit <= 100 else 100
    hash_key = _create_hash_key(current_user, app_id)
    items = storage.query_by_range(hash_key, entity_type, range_start, range_end, limit)

    # Convert app_id and remove keys for all items
    return [
        _remove_keys({**item, "appId": _decode_app_id(item["appId"])}) for item in items
    ]


@api_tool(
    path="/user-data/query-prefix",
    tags=["default", "user-data"],
    name="queryUserDataByPrefix",
    description="Queries user data by prefix in DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType", "prefix"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity to query",
            },
            "prefix": {"type": "string", "description": "The prefix to search for"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": 100,
                "description": "Optional maximum number of items to return (default is 100)",
            },
        },
    },
    output={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "appId": {
                    "type": "string",
                    "description": "The application identifier",
                },
                "entityType": {"type": "string", "description": "The type of entity"},
                "itemId": {
                    "type": "string",
                    "description": "The unique identifier for the item",
                },
                "data": {
                    "type": "object",
                    "description": "The stored data",
                    "additionalProperties": True,
                },
                "uuid": {"type": "string", "description": "The UUID of the item"},
                "rangeKey": {
                    "type": "string",
                    "description": "The range key if present",
                },
            },
            "required": ["appId", "entityType", "itemId", "data", "uuid"],
        },
        "description": "Array of items matching the prefix query",
    },
)
def handle_query_by_prefix(current_user, app_id, entity_type, prefix, limit=100):
    """Handler to query items by prefix"""
    limit = limit if limit and limit <= 100 else 100
    hash_key = _create_hash_key(current_user, app_id)
    items = storage.query_by_prefix(hash_key, entity_type, prefix, limit)

    # Convert app_id and remove keys for all items
    return [
        _remove_keys({**item, "appId": _decode_app_id(item["appId"])}) for item in items
    ]


@api_tool(
    path="/user-data/query-type",
    tags=["default", "user-data"],
    name="queryUserDataByType",
    description="Queries all user data of a specific type in DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity to query",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": 100,
                "description": "Optional maximum number of items to return (default is 100)",
            },
        },
    },
    output={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "appId": {
                    "type": "string",
                    "description": "The application identifier",
                },
                "entityType": {"type": "string", "description": "The type of entity"},
                "itemId": {
                    "type": "string",
                    "description": "The unique identifier for the item",
                },
                "data": {
                    "type": "object",
                    "description": "The stored data",
                    "additionalProperties": True,
                },
                "uuid": {"type": "string", "description": "The UUID of the item"},
                "rangeKey": {
                    "type": "string",
                    "description": "The range key if present",
                },
            },
            "required": ["appId", "entityType", "itemId", "data", "uuid"],
        },
        "description": "Array of items of the specified entity type",
    },
)
def handle_query_by_type(current_user, app_id, entity_type, limit=100):
    """Handler to query items by entity type"""
    limit = limit if limit and limit <= 100 else 100
    hash_key = _create_hash_key(current_user, app_id)
    items = storage.query_by_type(hash_key, entity_type, limit)

    # Convert app_id and remove keys for all items
    return [
        _remove_keys({**item, "appId": _decode_app_id(item["appId"])}) for item in items
    ]


@api_tool(
    path="/user-data/batch-get",
    tags=["default", "user-data"],
    name="batchGetUserData",
    description="Retrieves multiple user data items from DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType", "itemIds"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity to retrieve",
            },
            "itemIds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["itemId"],
                    "properties": {
                        "itemId": {
                            "type": "string",
                            "description": "The unique identifier for the item",
                        },
                        "rangeKey": {
                            "type": "string",
                            "description": "Optional range key for the item",
                        },
                    },
                },
                "description": "Array of item IDs and optional range keys to retrieve",
            },
        },
    },
    output={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "appId": {
                    "type": "string",
                    "description": "The application identifier",
                },
                "entityType": {"type": "string", "description": "The type of entity"},
                "itemId": {
                    "type": "string",
                    "description": "The unique identifier for the item",
                },
                "data": {
                    "type": "object",
                    "description": "The stored data",
                    "additionalProperties": True,
                },
                "uuid": {"type": "string", "description": "The UUID of the item"},
                "rangeKey": {
                    "type": "string",
                    "description": "The range key if present",
                },
            },
            "required": ["appId", "entityType", "itemId", "data", "uuid"],
        },
        "description": "Array of retrieved items that belong to the user",
    },
)
def handle_batch_get_items(current_user, app_id, entity_type, item_ids):
    """Handler to batch retrieve items from DynamoDB"""
    hash_key = _create_hash_key(current_user, app_id)
    items = storage.batch_get_items(hash_key, entity_type, item_ids)

    print(f"items: {items}")

    # Filter items to ensure they belong to this user and convert app_id
    return [
        _remove_keys({**item, "appId": _decode_app_id(item["appId"])})
        for item in items
        if item.get("appId") == hash_key
    ]


@api_tool(
    path="/user-data/get-by-uuid",
    tags=["default", "user-data"],
    name="getUserDataByUuid",
    description="Retrieves user data from DynamoDB using UUID",
    parameters={
        "type": "object",
        "required": ["uuid"],
        "properties": {
            "uuid": {
                "type": "string",
                "format": "uuid",
                "description": "The UUID of the item to retrieve",
            }
        },
    },
    output={
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "appId": {
                        "type": "string",
                        "description": "The application identifier",
                    },
                    "entityType": {
                        "type": "string",
                        "description": "The type of entity",
                    },
                    "itemId": {
                        "type": "string",
                        "description": "The unique identifier for the item",
                    },
                    "data": {
                        "type": "object",
                        "description": "The stored data",
                        "additionalProperties": True,
                    },
                    "uuid": {"type": "string", "description": "The UUID of the item"},
                    "rangeKey": {
                        "type": "string",
                        "description": "The range key if present",
                    },
                },
                "required": ["appId", "entityType", "itemId", "data", "uuid"],
            },
            {
                "type": "null",
                "description": "Returns null if item not found or user doesn't have permission",
            },
        ]
    },
)
def handle_get_by_uuid(current_user, uuid):
    """Handler to retrieve an item by UUID without requiring app_id"""
    item = storage.get_by_uuid(uuid)

    # Extract the email prefix from the current_user (before the '@')
    email_prefix = current_user

    # Verify the item belongs to this user by checking if the app_id starts with the email prefix
    if item and item.get("appId").startswith(email_prefix):
        item["appId"] = _decode_app_id(item["appId"])
        return _remove_keys(item)
    return None


@api_tool(
    path="/user-data/delete",
    tags=["default", "user-data"],
    name="deleteUserData",
    description="Deletes user data from DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType", "itemId"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity to delete",
            },
            "itemId": {
                "type": "string",
                "description": "The unique identifier for the item",
            },
            "rangeKey": {
                "type": "string",
                "description": "Optional range key for the item",
            },
        },
    },
    output={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Confirmation message about the deletion",
            }
        },
        "required": ["message"],
    },
)
def handle_delete_item(current_user, app_id, entity_type, item_id, range_key=None):
    """Handler to delete an item"""
    hash_key = _create_hash_key(current_user, app_id)
    storage.delete_item(hash_key, entity_type, item_id, range_key)
    return {"message": "Item deleted successfully"}


@api_tool(
    path="/user-data/batch-put",
    tags=["default", "user-data"],
    name="batchPutUserData",
    description="Stores multiple user data items in DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType", "items"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity being stored",
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["itemId", "data"],
                    "properties": {
                        "itemId": {
                            "type": "string",
                            "description": "The unique identifier for the item",
                        },
                        "rangeKey": {
                            "type": "string",
                            "description": "Optional range key for the item",
                        },
                        "data": {
                            "type": "object",
                            "description": "The data to store",
                            "additionalProperties": True,
                        },
                    },
                },
                "description": "Array of items to store",
            },
        },
    },
    output={
        "type": "object",
        "properties": {
            "uuids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of UUIDs for the stored items",
            }
        },
        "required": ["uuids"],
    },
)
def handle_batch_put_items(current_user, app_id, entity_type, items):
    """Handler to batch store items in DynamoDB"""

    hash_key = _create_hash_key(current_user, app_id)
    return {"uuids": storage.batch_put_items(hash_key, entity_type, items)}


@api_tool(
    path="/user-data/batch-delete",
    tags=["default", "user-data"],
    name="batchDeleteUserData",
    description="Deletes multiple user data items from DynamoDB",
    parameters={
        "type": "object",
        "required": ["appId", "entityType", "itemIds"],
        "properties": {
            "appId": {"type": "string", "description": "The application identifier"},
            "entityType": {
                "type": "string",
                "description": "The type of entity to delete",
            },
            "itemIds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["itemId"],
                    "properties": {
                        "itemId": {
                            "type": "string",
                            "description": "The unique identifier for the item",
                        },
                        "rangeKey": {
                            "type": "string",
                            "description": "Optional range key for the item",
                        },
                    },
                },
                "description": "Array of item IDs and optional range keys to delete",
            },
        },
    },
    output={
        "type": "boolean",
        "description": "Returns true when deletion is successful",
    },
)
def handle_batch_delete_items(current_user, app_id, entity_type, item_ids):
    """Handler to batch delete items from DynamoDB"""

    hash_key = _create_hash_key(current_user, app_id)
    storage.batch_delete_items(hash_key, entity_type, item_ids)
    return True


@api_tool(
    path="/user-data/delete-by-uuid",
    tags=["default", "user-data"],
    name="deleteUserDataByUuid",
    description="Deletes user data from DynamoDB by UUID",
    parameters={
        "type": "object",
        "required": ["uuid"],
        "properties": {
            "uuid": {"type": "string", "description": "The UUID of the item to delete"}
        },
    },
    output={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["success", "failure"],
                "description": "Status of the deletion operation",
            },
            "message": {
                "type": "string",
                "description": "Descriptive message about the operation result",
            },
        },
        "required": ["status", "message"],
    },
)
def handle_delete_by_uuid(current_user, uuid):
    """Handler to delete an item by UUID ensuring the user owns the data"""

    # First, retrieve the item by UUID
    item = storage.get_by_uuid(uuid)

    # Extract the email prefix from the current_user (before the '@')
    email_prefix = current_user

    # Verify the item belongs to this user by checking if the app_id starts with the email prefix
    if item and item.get("appId").startswith(email_prefix):
        # User owns the item; proceed to delete
        result = storage.delete_by_uuid(uuid)
        if result:
            return {"status": "success", "message": "Item deleted successfully."}
        else:
            return {"status": "failure", "message": "Item not found."}
    else:
        return {
            "status": "failure",
            "message": "You do not have permission to delete this item.",
        }


@api_tool(
    path="/user-data/list-apps",
    tags=["default", "user-data"],
    name="listUserApps",
    description="Lists all application IDs belonging to the current user",
    parameters={
        "type": "object",
        "properties": {
            "prefix": {
                "type": "string",
                "description": "Optional prefix to filter app IDs",
            }
        },
    },
    output={
        "type": "object",
        "properties": {
            "appIds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of application IDs belonging to the user",
            }
        },
        "required": ["appIds"],
    },
)
def handle_list_user_apps(current_user, prefix=None):
    """Handler to list all app IDs belonging to the current user"""

    # Extract the email prefix from the current_user (before the '@')
    email_prefix = current_user

    # Get all matching app IDs using the storage method
    if prefix:
        search_prefix = f"{email_prefix}#{prefix}"
    else:
        search_prefix = f"{email_prefix}"

    app_ids = storage.list_app_ids(prefix=search_prefix)

    print(f"AppIds {app_ids}")

    # Decode the app IDs to remove the user prefix
    decoded_app_ids = [_decode_app_id(app_id) for app_id in app_ids]

    return {"appIds": decoded_app_ids}
