import inspect
from functools import wraps
from typing import get_type_hints, List

tools = {}



def to_openai_tools(tools_metadata: List[dict]):
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t['tool_name'],
                # Include up to 1024 characters of the description
                "description": t.get('description',"")[:1024],
                "parameters": t.get('args',{}),
            },
        } for t in tools_metadata
    ]
    return openai_tools

def get_tool_metadata(func, tool_name=None, description=None, args_override=None, terminal=False):
    """
    Extracts metadata for a function to use in tool registration.

    Parameters:
        func (function): The function to extract metadata from.
        tool_name (str, optional): The name of the tool. Defaults to the function name.
        description (str, optional): Description of the tool. Defaults to the function's docstring.
        args_override (dict, optional): Override for the argument schema. Defaults to dynamically inferred schema.
        terminal (bool, optional): Whether the tool is terminal. Defaults to False.

    Returns:
        dict: A dictionary containing metadata about the tool, including description, args schema, and the function.
    """
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

            if param_name in ["action_context", "action_agent"]:
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
            param_type = type_hints.get(param_name, str)  # Default to string if type is not annotated
            param_schema = {"type": get_json_type(param_type)}  # Convert Python types to JSON schema types

            args_schema["properties"][param_name] = param_schema

            # Add to required if not defaulted
            if param.default == inspect.Parameter.empty:
                args_schema["required"].append(param_name)
    else:
        args_schema = args_override

    # Return the metadata as a dictionary
    return {
        "tool_name": tool_name,
        "description": description,
        "args": args_schema,
        "function": func,
        "terminal": terminal
    }


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
        # Use the reusable function to extract metadata
        metadata = get_tool_metadata(
            func=func,
            tool_name=tool_name,
            description=description,
            args_override=args_override,
            terminal=terminal
        )

        # Register the tool in the global dictionary
        tools[metadata["tool_name"]] = {
            "description": metadata["description"],
            "args": metadata["args"],
            "function": metadata["function"],
            "terminal": metadata["terminal"]
        }
        return func
    return decorator