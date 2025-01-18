import inspect
from functools import wraps
from typing import get_type_hints, Dict, Any, List

from attr import dataclass

tools = {}
tools_by_tags = {}


@dataclass(frozen=True)
class SideEffect:
    name: str
    description: str


@dataclass(frozen=True)
class ExampleInvocation:
    input: Dict
    output: Any
    description: str


class ActionSpec:
    def __init__(self,
                 name: str,
                 function: callable,
                 description: str,
                 args: Dict,
                 tags: List[str],
                 output: Dict,
                 examples: List[ExampleInvocation],
                 side_effects: List[SideEffect],
                 terminal: bool = False):
        self.name = name
        self.function = function
        self.description = description
        self.tags = tags or []
        self.terminal = terminal
        self.args = args
        self.output = output
        self.examples = examples or []
        self.side_effects = side_effects or []

    def execute(self, **args) -> Any:
        """Execute the action's function"""
        return self.function(**args)


def register_tool(tool_name: str = None,
                  description: str = None,
                  args: dict = None,
                  tags: List[str] = None,
                  terminal=False,
                  examples: List[ExampleInvocation] = None,
                  output: Any = None,
                  side_effects: List[SideEffect] = None):
    """
    A decorator to dynamically register a function in the tools dictionary with its parameters, schema, and docstring.

    Parameters:
        tool_name (str, optional): The name of the tool to register. Defaults to the function name.
        description (str, optional): Override for the tool's description. Defaults to the function's docstring.
        tags (List[str], optional): List of tags for organizing the tools into groups.
        args (dict, optional): Override for the argument schema. Defaults to dynamically inferred schema from function signature.
        terminal (bool, optional): Whether the tool is terminal. Defaults to False.
        examples (list, optional): List of examples of inputs to the tool and outputs
        output (dict, optional): Output schema for the tool.
        side_effects (list, optional): List of side effects of the tool.

    Returns:
        function: The wrapped function.
    """

    def decorator(func):
        nonlocal tool_name, description, args

        # Default tool_name to the function name if not provided
        tool_name = tool_name or func.__name__

        # Default description to the function's docstring if not provided
        description = description or (func.__doc__.strip() if func.__doc__ else "No description provided.")

        # Discover the function's signature and type hints if no args_override is provided
        if args is None:
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
            args_schema = args

        spec = {
            "description": description,
            "args": args_schema,
            "function": func,
            "terminal": terminal,
            "examples": examples,
            "output": output,
            "side_effects": side_effects,
            "tags": tags
        }

        # Register the tool in the global dictionary
        tools[tool_name] = {
            "description": description,
            "args": args_schema,
            "function": func,
            "terminal": terminal,
            "examples": examples,
            "output": output,
            "side_effects": side_effects,
            "tags": tags
        }

        if tags:
            # Register with all tags
            for tag in tags:
                if tag not in tools_by_tags:
                    tools_by_tags[tag] = []
                tools_by_tags[tag].append(tool_name)

        return func

    return decorator
