import inspect
import traceback
from typing import get_type_hints, List

tools = {}
tools_by_tag = {}


def to_openai_tools(tools_metadata: List[dict]):
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["tool_name"],
                # Include up to 1024 characters of the description
                "description": t.get("description", "")[:1024],
                "parameters": t.get("parameters", {}),
            },
        }
        for t in tools_metadata
    ]
    return openai_tools


def get_tool_metadata(
    func,
    tool_name=None,
    description=None,
    parameters_override=None,
    terminal=False,
    tags=None,
):
    """
    Extracts metadata for a function to use in tool registration.

    Parameters:
        func (function): The function to extract metadata from.
        tool_name (str, optional): The name of the tool. Defaults to the function name.
        description (str, optional): Description of the tool. Defaults to the function's docstring.
        parameters_override (dict, optional): Override for the argument schema. Defaults to dynamically inferred schema.
        terminal (bool, optional): Whether the tool is terminal. Defaults to False.
        tags (List[str], optional): List of tags to associate with the tool.

    Returns:
        dict: A dictionary containing metadata about the tool, including description, args schema, and the function.
    """
    # Default tool_name to the function name if not provided
    tool_name = tool_name or func.__name__

    # Default description to the function's docstring if not provided
    description = description or (
        func.__doc__.strip() if func.__doc__ else "No description provided."
    )

    # Discover the function's signature and type hints if no args_override is provided
    if parameters_override is None:
        signature = inspect.signature(func)
        type_hints = get_type_hints(func)

        # Build the arguments schema dynamically
        args_schema = {"type": "object", "properties": {}, "required": []}
        for param_name, param in signature.parameters.items():

            if param_name in [
                "action_context",
                "action_agent",
            ] or param_name.startswith("_"):
                continue  # Skip these parameters

            def get_json_type(param_type):
                if param_type == str:
                    return "string"
                elif param_type == int:
                    return "integer"
                elif param_type == float:
                    return "number"
                elif param_type == bool:
                    return "boolean"
                elif param_type == list:
                    return "array"
                elif param_type == dict:
                    return "object"
                else:
                    return "string"

            # Add parameter details
            param_type = type_hints.get(
                param_name, str
            )  # Default to string if type is not annotated
            param_schema = {
                "type": get_json_type(param_type)
            }  # Convert Python types to JSON schema types

            args_schema["properties"][param_name] = param_schema

            # Add to required if not defaulted
            if param.default == inspect.Parameter.empty:
                args_schema["required"].append(param_name)
    else:
        args_schema = parameters_override

    # Return the metadata as a dictionary
    return {
        "tool_name": tool_name,
        "description": description,
        "parameters": args_schema,
        "function": func,
        "terminal": terminal,
        "tags": tags or [],
    }


import functools
import inspect


def register_tool(
    tool_name=None,
    description=None,
    parameters_override=None,
    terminal=False,
    tags=None,
    status=None,
    resultStatus=None,
    errorStatus=None,
):
    """
    A decorator to dynamically register a function in the tools dictionary with its parameters, schema, and docstring.

    Parameters:
        tool_name (str, optional): The name of the tool to register. Defaults to the function name.
        description (str, optional): Override for the tool's description. Defaults to the function's docstring.
        parameters_override (dict, optional): Override for the argument schema. Defaults to dynamically inferred schema.
        terminal (bool, optional): Whether the tool is terminal. Defaults to False.
        tags (List[str], optional): List of tags to associate with the tool.
        status (str, optional): If provided, adds `action_context` as a parameter.

    Returns:
        function: The wrapped function.
    """

    def decorator(func):
        # Modify function signature to include action_context if status is provided

        # Get the original function signature
        sig = inspect.signature(func)
        parameters = list(sig.parameters.values())

        # Check if 'action_context' is already a parameter
        if status and "action_context" not in sig.parameters:
            parameters.append(
                inspect.Parameter(
                    "action_context", inspect.Parameter.KEYWORD_ONLY, default=None
                )
            )

        new_sig = sig.replace(parameters=parameters)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            action_context = kwargs.get("action_context")
            send_event = action_context.incremental_event() if action_context else None
            try:
                pre_call_action(
                    send_event, func.__name__, status, action_context, kwargs
                )
            except Exception as e:
                pass

            result = None
            try:
                # Call the original function
                result = func(*args, **kwargs)
            except Exception as e:
                traceback_str = traceback.format_exc()
                try:
                    error_call_action(
                        send_event,
                        func.__name__,
                        errorStatus,
                        e,
                        traceback_str,
                        action_context,
                        kwargs,
                    )
                except Exception as e:
                    pass

            try:
                # Post-call actions
                post_call_action(
                    send_event,
                    func.__name__,
                    resultStatus,
                    result,
                    action_context,
                    kwargs,
                )
            except Exception as e:
                pass

            return result

        # Apply modified signature to the wrapper
        wrapper.__signature__ = new_sig

        # Use the reusable function to extract metadata
        metadata = get_tool_metadata(
            func=wrapper,
            tool_name=tool_name,
            description=description,
            parameters_override=parameters_override,
            terminal=terminal,
            tags=tags,
        )

        # Register the tool in the global dictionary
        tools[metadata["tool_name"]] = metadata

        for tag in metadata["tags"]:
            if tag not in tools_by_tag:
                tools_by_tag[tag] = []
            tools_by_tag[tag].append(metadata)

        return wrapper

    return decorator


# Placeholder pre/post-call action functions
def pre_call_action(send_event, function_name, status, action_context, args):
    if action_context and send_event:
        # Remove action_context and all of its keys from args
        logged_args = {
            k: v
            for k, v in args.items()
            if k != "action_context" and k not in action_context.properties
        }
        send_event("tools/" + function_name + "/start", logged_args)
        if status:
            status = status.format(logged_args)
            send_event("agent/status", {"status": status})


def post_call_action(
    send_event, function_name, result_status, result, action_context, args
):
    if action_context and send_event:
        # Remove action_context and all of its keys from args
        logged_args = {
            k: v
            for k, v in args.items()
            if k != "action_context" and k not in action_context.properties
        }
        send_event("tools/" + function_name + "/end", {**logged_args, "result": result})
        if result_status:
            status = result_status.format({**logged_args, "result": result})
            send_event("agent/status", {"status": status})


def error_call_action(
    send_event, function_name, errorStatus, ex, traceback_str, action_context, args
):
    if action_context and send_event:
        # Remove action_context and all of its keys from args
        logged_args = {
            k: v
            for k, v in args.items()
            if k != "action_context" and k not in action_context.properties
        }
        send_event(
            "tools/" + function_name + "/error",
            {**logged_args, "exception": ex, "traceback": traceback_str},
        )
        if errorStatus:
            status = errorStatus.format(
                {**logged_args, "exception": ex, "traceback": traceback_str}
            )
            send_event("agent/status", {"status": status})
