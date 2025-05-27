import inspect
from common.ops import vop, op
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from common.validate import validated
from agent.components.tool import tools
import re

from service.routes import route_data
from service.handlers import *
from service.workflow_handlers import *
from service.scheduled_task_handlers import *
from service.email_events_handlers import *

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
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()

def common_handler(operation, func_schema, **optional_params):
    def handler(event, context, current_user, name, data):
        try:
            print(f"Function schema: {func_schema}")

            access_token = data['access_token']
            account_id = data['account']

            wrapper_schema = {
                "type": "object",
                "properties": {
                    "data": func_schema
                },
                "required": ["data"]
            }

            # Validate the data against the schema
            print("Validating request")
            try:
                validate(data, wrapper_schema)
                print("Request is valid")
            except ValidationError as e:
                print(f"Validation error: {str(e)}")
                raise ValueError(f"Invalid request: {str(e)}")

            print("Converting parameters to snake case")
            # build a keyword argument dictionary from the data based on the schema
            args = {
                camel_to_snake(param): data['data'].get(
                    param,
                    func_schema['properties'][param].get('default', None)
                )
                for param in func_schema.get('properties',[])
            }

            if has_named_parameter(operation, "current_user"):
                args["current_user"] = current_user

            if has_named_parameter(operation, "access_token"):
                args["access_token"] = access_token
            
            if has_named_parameter(operation, "account_id"):
                args["account_id"] = account_id


            print("Invoking operation")
            response = operation(**args)

            success = response.get('success', True)
            print(f"Returning response success: {success}")
            return {"success": success, "data": response}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return handler


@validated("route")
def route(event, context, current_user, name, data):

    try:
        # get the request path from the event and remove the first component...if there aren't enough components
        # then the path is invalid
        target_path_string = event.get('path', event.get('rawPath', ''))
        print(f"Route: {target_path_string}")

        # print(f"Route data: {route_data}")

        route_info = route_data.get(target_path_string, None)
        if not route_info:
            return {"success": False, "error": "Invalid path"}

        handler_func = route_info['handler']
        func_schema = route_info['schema'] or {}

        return common_handler(handler_func, func_schema)(event, context, current_user, name, data)
    except Exception as e:
        return {"success": False, "error": str(e)}




@op(
    path="/vu-agent/tools",
    tags=["default"],
    method="GET",
    name="agentBuiltInTools",
    description="Get the list of built-in tools.",
    params={},
)
@validated("get")
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
                "tags": tool_metadata["tags"]
            }
        
            serializable_tools[tool_name] = tool_info
        
        return {"success": True, "data": serializable_tools}
    except Exception as e:
        return {"success": False, "error": str(e)} 