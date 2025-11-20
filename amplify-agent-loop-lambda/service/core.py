import inspect
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from agent.components.tool import tools
import re

from service.routes import route_data
from pycommon.api.ops import api_tool, set_route_data
# Note: order of setting route data matters, all file importing route_data must go AFTER we set the route data
set_route_data(route_data)

from service.handlers import *
from service.workflow_handlers import *
from service.scheduled_task_handlers import *
from service.email_events_handlers import *

from pycommon.authz import validated


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

            wrapper_schema = {
                "type": "object",
                "properties": {"data": func_schema},
                "required": ["data"],
            }

            # Validate the data against the schema
            print("Validating request")
            print(f"Data: {data}")
            print(f"Wrapper schema: {wrapper_schema}")
            try:
                validate(data, wrapper_schema)
                print("Request is valid")
            except ValidationError as e:
                print(f"Validation error: {str(e)}")
                raise ValueError(f"Invalid request: {str(e)}")

            print("Converting parameters to snake case")
            # build a keyword argument dictionary from the data based on the schema
            args = {
                camel_to_snake(param): data["data"].get(
                    param, func_schema["properties"][param].get("default", None)
                )
                for param in func_schema.get("properties", [])
            }

            data_params = {
                "current_user": current_user,
                "access_token": data["access_token"],
                "account_id": data["account"],
                "api_key_id": data.get("api_key_id"),
                "rate_limit": data.get("rate_limit"),
            }

            for param, value in data_params.items():
                if has_named_parameter(operation, param):
                    args[param] = value

            print("Invoking operation")
            response = operation(**args)

            success = response.get("success", True)
            print(f"Returning response success: {success}")
            return {"success": success, "data": response}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return handler


@validated("route", False)
def route(event, context, current_user, name, data):

    try:
        # get the request path from the event and remove the first component...if there aren't enough components
        # then the path is invalid
        target_path_string = event.get("path", event.get("rawPath", ""))
        print(f"Route: {target_path_string}")

        print(f"Route data: {route_data.keys()}")

        route_info = route_data.get(target_path_string, None)
        if not route_info:
            print(f"Invalid path: {target_path_string}")
            return {"success": False, "error": "Invalid path"}
        print(f"Route info: {route_info}")
        handler_func = route_info["handler"]
        func_schema = route_info["parameters"] or {}

        return common_handler(handler_func, func_schema)(
            event, context, current_user, name, data
        )
    except Exception as e:
        return {"success": False, "error": str(e)}


@api_tool(
    path="/vu-agent/tools",
    tags=["default"],
    method="GET",
    name="agentBuiltInTools",
    description="Get the list of built-in tools.",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {
                "type": "object",
                "description": "Dictionary containing all available tools with their metadata",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "The name of the tool",
                        },
                        "description": {
                            "type": "string",
                            "description": "Detailed description of what the tool does",
                        },
                        "parameters": {
                            "type": "object",
                            "description": "JSON schema defining the tool's input parameters",
                        },
                        "terminal": {
                            "type": "boolean",
                            "description": "Whether this tool requires terminal access",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of tags categorizing the tool",
                        },
                    },
                    "required": [
                        "tool_name",
                        "description",
                        "parameters",
                        "terminal",
                        "tags",
                    ],
                },
            },
        },
        "required": ["success"],
    },
)
@validated("get", False)
def get_builtin_tools(event, context, current_user, name, data):
    """
    Returns a list of all available tools.

    Returns:
        dict: A dictionary containing the tools, with success flag
    """
    try:
        # Convert tools to a serializable format by removing the function reference
        serializable_tools = {}
        # print(f"Tools: {tools}")
        for tool_name, tool_metadata in tools.items():
            # Create a copy without the function reference
            tool_info = {
                "tool_name": tool_metadata["tool_name"],
                "description": tool_metadata["description"],
                "parameters": tool_metadata["parameters"],
                "terminal": tool_metadata["terminal"],
                "tags": tool_metadata["tags"],
            }

            serializable_tools[tool_name] = tool_info

        return {"success": True, "data": serializable_tools}
    except Exception as e:
        return {"success": False, "error": str(e)}
