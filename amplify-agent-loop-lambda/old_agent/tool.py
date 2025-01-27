import inspect
from functools import wraps
from typing import get_type_hints

tools = {}

def register_tool(tool_name=None, description=None, args_override=None, terminal=False):
    """
    A decorator to dynamically register a function in the tools dictionary with its parameters, schema, and docstring.

    Parameters:
        tool_name (str, optional): The name of the tool to register. Defaults to the function name.
        description (str, optional): Override for the tool's description. Defaults to the function's docstring.
        args_override (dict, optional): Override for the argument schema. Defaults to dynamically inferred schema.

    Returns:
        function: The wrapped function.
    """
    def decorator(func):
        nonlocal tool_name, description, args_override

        # Default tool_name to the function name if not provided
        tool_name = tool_name or func.__name__

        # Default description to the function's docstring if not provided
        description = description or (func.__doc__.strip() if func.__doc__ else "No description provided.")

        # Discover the function's signature and type hints if no args_override is provided
        if args_override is None:
            signature = inspect.signature(func)
            type_hints = get_type_hints(func)

            # Build the arguments schema dynamically
            args_schema = {
                "type": "object",
                "properties": {},
                "required": []
            }
            for param_name, param in signature.parameters.items():

                if param_name == "action_context":
                    continue
                elif param_name == "action_agent":
                    continue

                # Add parameter details
                param_type = type_hints.get(param_name, str)  # Default to string if type is not annotated
                param_schema = {"type": param_type.__name__.lower()}  # Convert Python types to JSON schema types

                args_schema["properties"][param_name] = param_schema

                # Add to required if not defaulted
                if param.default == inspect.Parameter.empty:
                    args_schema["required"].append(param_name)
        else:
            args_schema = args_override

        # Register the tool in the global dictionary
        tools[tool_name] = {
            "description": description,
            "args": args_schema,
            "function": func,
            "terminal": terminal
        }
        return func
    return decorator
