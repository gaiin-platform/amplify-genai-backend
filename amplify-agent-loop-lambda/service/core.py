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
from pycommon.api.critical_logging import log_critical_error, SEVERITY_HIGH
from pycommon.authz import validated

from pycommon.logger import getLogger
logger = getLogger("route")


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
        # Initialize usage tracker for agent operations
        from pycommon.metrics import get_usage_tracker
        tracker = get_usage_tracker()
        op_tracking_context = {}

        try:
            logger.debug(f"Function schema: {func_schema}")

            wrapper_schema = {
                "type": "object",
                "properties": {"data": func_schema},
                "required": ["data"],
            }

            # Validate the data against the schema
            logger.debug("Validating request")
            logger.debug(f"Data: {data}")
            logger.debug(f"Wrapper schema: {wrapper_schema}")
            try:
                validate(data, wrapper_schema)
                logger.debug("Request is valid")
            except ValidationError as e:
                logger.error(f"Validation error: {str(e)}")
                raise ValueError(f"Invalid request: {str(e)}")

            logger.debug("Converting parameters to snake case")
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

            # Start tracking agent operation
            op_tracking_context = tracker.start_tracking(
                user=current_user,
                operation=operation.__name__ if hasattr(operation, '__name__') else 'unknown_operation',
                endpoint=name,
                api_accessed=data.get("api_accessed", False),
                context=context,
            )

            logger.debug("Invoking operation")
            response = operation(**args)

            success = response.get("success", True)
            logger.debug(f"Returning response success: {success}")

            # End tracking and record metrics
            result_dict = {"statusCode": 200 if success else 500}
            metrics = tracker.end_tracking(
                tracking_context=op_tracking_context,
                result=result_dict,
                claims={
                    "account": data.get("account"),
                    "username": current_user,
                    "api_key_id": data.get("api_key_id"),
                },
                error_type=None if success else "OperationReturnedFailure",
            )
            tracker.record_metrics(metrics)
            
            # CRITICAL: Agent operation returned success=False - workflow failed
            if not success:
                
                import traceback
                log_critical_error(
                    function_name="common_handler_operation_failed",
                    error_type="AgentOperationReturnedFailure",
                    error_message=f"Agent operation returned success=False: {response.get('error', 'No error message')}",
                    current_user=current_user,
                    severity=SEVERITY_HIGH,
                    stack_trace="",
                    context={
                        "operation_name": operation.__name__ if hasattr(operation, '__name__') else 'unknown',
                        "response_error": response.get('error', 'No error message'),
                        "response_message": response.get('message', 'No message'),
                        "response_keys": list(response.keys())
                    }
                )
            
            return {"success": success, "data": response}
        except Exception as e:
            # Track failed operation
            if op_tracking_context:
                result_dict = {"statusCode": 500}
                metrics = tracker.end_tracking(
                    tracking_context=op_tracking_context,
                    result=result_dict,
                    claims={
                        "account": data.get("account", "unknown") if 'data' in locals() else "unknown",
                        "username": current_user if 'current_user' in locals() else "unknown",
                    },
                    error_type=type(e).__name__,
                )
                tracker.record_metrics(metrics)

            # CRITICAL: Agent operation failure = user workflow blocked
            
            import traceback
            log_critical_error(
                function_name="common_handler",
                error_type="AgentOperationFailure",
                error_message=f"Agent operation failed: {str(e)}",
                current_user=current_user,
                severity=SEVERITY_HIGH,
                stack_trace=traceback.format_exc(),
                context={
                    "operation_name": operation.__name__ if hasattr(operation, '__name__') else 'unknown',
                    "error_details": str(e)
                }
            )
            return {"success": False, "error": str(e)}

    return handler


@validated("route", False)
def route(event, context, current_user, name, data):

    try:
        # get the request path from the event and remove the first component...if there aren't enough components
        # then the path is invalid
        target_path_string = event.get("path", event.get("rawPath", ""))
        logger.debug(f"Route: {target_path_string}")

        logger.debug(f"Route data: {route_data.keys()}")

        route_info = route_data.get(target_path_string, None)
        if not route_info:
            logger.warning(f"Invalid path: {target_path_string}")
            return {"success": False, "error": "Invalid path"}
        logger.debug(f"Route info: {route_info}")
        handler_func = route_info["handler"]
        func_schema = route_info["parameters"] or {}

        return common_handler(handler_func, func_schema)(
            event, context, current_user, name, data
        )
    except Exception as e:
        # CRITICAL: Route handler exception - agent routing failed
        
        import traceback
        log_critical_error(
            function_name="route_handler",
            error_type="AgentRouteHandlerFailure",
            error_message=f"Agent route handler failed: {str(e)}",
            current_user=current_user if 'current_user' in locals() else 'unknown',
            severity=SEVERITY_HIGH,
            stack_trace=traceback.format_exc(),
            context={
                "target_path": event.get("path", event.get("rawPath", "unknown")),
                "error_details": str(e)
            }
        )
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
        # CRITICAL: Get builtin tools failed
        
        import traceback
        log_critical_error(
            function_name="get_builtin_tools",
            error_type="GetBuiltinToolsFailure",
            error_message=f"Failed to get builtin tools: {str(e)}",
            current_user=current_user,
            severity=SEVERITY_HIGH,
            stack_trace=traceback.format_exc(),
            context={
                "tools_count": len(tools) if 'tools' in locals() else 0
            }
        )
        return {"success": False, "error": str(e)}