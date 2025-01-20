import inspect
import uuid

from common.ops import vop
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from common.validate import validated
import re
from service.routes import route_data
from service.handlers import *

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
            except ValidationError as e:
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


            print(f"Args: {args}")
            print("Invoking operation")
            response = operation(**args)

            print("Returning response")
            return {"success": True, "data": response}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return handler


@validated("route")
def route(event, context, current_user, name, data):

    try:
        # get the request path from the event and remove the first component...if there aren't enough components
        # then the path is invalid
        print(f"Event: {event}")
        print(f"Data: {data}")

        target_path_string = event.get('path', event.get('rawPath', ''))
        print(f"Route: {target_path_string}")

        print(f"Route data: {route_data}")

        route_info = route_data.get(target_path_string, None)
        if not route_info:
            return {"success": False, "error": "Invalid path"}

        handler_func = route_info['handler']
        func_schema = route_info['schema'] or {}

        return common_handler(handler_func, func_schema)(event, context, current_user, name, data)
    except Exception as e:
        return {"success": False, "error": str(e)}


