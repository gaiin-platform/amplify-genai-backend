import json
import os
import ast
import traceback
from typing import Dict

from agent.components.tool import register_tool
from agent.core import ActionContext
import ast
import importlib
import sys


@register_tool(tags=["code_exec"])
def get_installed_python_modules():
    """
    Returns a list of installed Python packages with versions.
    Falls back to sys.modules if pkg_resources is not available.
    Returns an empty string if any errors occur.
    """
    try:
        # First try using pkg_resources (preferred method)
        try:
            import pkg_resources

            installed_packages = sorted(
                [
                    f"{dist.project_name}=={dist.version}"
                    for dist in pkg_resources.working_set
                ]
            )
            return "\n".join(installed_packages)
        except (ImportError, Exception):
            # Fall back to sys.modules if pkg_resources is not available
            import sys
            import os

            result = []

            # Get modules that are currently imported
            for module_name, module in sys.modules.items():
                # Skip internal/private modules
                if not module_name or module_name.startswith("_") or "." in module_name:
                    continue

                # Try to get the version
                try:
                    version = getattr(module, "__version__", "unknown")
                    result.append(f"{module_name}=={version}")
                except Exception:
                    pass

            # Try to get additional packages from site-packages directories
            try:
                import site
                from importlib.metadata import distribution, distributions

                # Get packages using importlib.metadata (Python 3.8+)
                for dist in distributions():
                    try:
                        name = dist.metadata["Name"]
                        version = dist.version
                        result.append(f"{name}=={version}")
                    except Exception:
                        pass
            except (ImportError, Exception):
                pass

            return "\n".join(sorted(list(set(result))))

    except Exception:
        # Return empty string if anything goes wrong
        return ""


def get_imports_from_code(code_string):
    """Extract modules and specific imports from Python code."""
    required_imports = {}

    try:
        tree = ast.parse(code_string)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    required_imports.setdefault(alias.name, []).append(None)
            elif isinstance(node, ast.ImportFrom) and node.module:
                required_imports.setdefault(node.module, []).extend(
                    [alias.name for alias in node.names]
                )
    except SyntaxError:
        print("Failed to parse code")
        return {}

    return required_imports


def prepare_exec_globals(code_string, context_dict):
    """Prepare an execution environment that mimics running the script normally."""
    imports = get_imports_from_code(code_string)
    exec_globals = {
        "__name__": "__main__",  # Mimics running as a script
        "__file__": "<executed_code>",  # Simulates a script name
        "__builtins__": __builtins__,  # Ensures built-in functions are available
    }

    # Import modules and handle 'from x import y'
    for module, names in imports.items():
        try:
            mod = importlib.import_module(module)
            exec_globals[module] = mod  # Store full module

            # Add specific imports from 'from module import name'
            for name in names:
                if name:
                    exec_globals[name] = getattr(
                        mod, name, None
                    )  # Store attribute directly
        except ImportError as e:
            print(f"Warning: Could not import {module}: {e}")

    exec_globals.update(context_dict)  # Merge additional execution context
    return exec_globals


@register_tool(tags=["code_exec"])
def exec_code(action_context: ActionContext, code: str):
    """
    Executes the provided Python code and returns the value of the 'result' variable if defined.
    IMPORTANT!!! Make sure the code arg is a valid JSON attribute with all line breaks, quotes, etc. escaped like this:

    "args": {
        "code": "# This code generates 10 random numbers between 1 and 100 and converts them to JSON format.\nimport random\nimport json\n\n# Generate 10 random numbers\nrandom_numbers = [random.randint(1, 100) for _ in range(10)]\n# Convert the list of random numbers to JSON format\nresult = json.dumps(random_numbers)"
    }

    Parameters:
        code (str): The Python code to execute. Make sure it is a valid JSON attribute with all line breaks, quotes, etc. escaped.


    Ideally, the start of your code should be a long set of comments explaining what it does and the variables
    that are in scope at the start of its execution.

    Write a very robust python code that catches all exceptions and returns a dictionary with the key 'error' and
    the value as the error message if there is a problem.

    Avoid using any libraries that are not built-in unless you are told they are available. If you need some libraries,
    you can use the get_installed_python_modules() tool to get a list of installed packages that are available to you.

    This is an AWS lambda environment, so only attempt to write to directory returned from the get_writeable_directory()
    tool.

    ALWAYS HAVE THE LAST LINE OF THE CODE assign the result to the variable 'result'. There MUST be a result.

    Returns:
        dict: A dictionary containing the status ('result' or 'error') and the 'result' or 'error_message'.
    """
    context_dict = action_context.properties.get("code_exec_context", {})

    result_mode = "result_only"
    var_list = []
    options = {}

    # Prepare the execution environment
    # exec_globals = context_dict
    exec_globals = prepare_exec_globals(code, context_dict)
    # exec_globals = globals().copy()
    # exec_globals.update({'context': context_dict})

    send_event = action_context.incremental_event()
    exec_locals = {}
    # Execute the code
    try:
        send_event("tools/code_exec/execute/start", {"code": code})
        exec(code, exec_globals, exec_locals)
        send_event("tools/code_exec/execute/result_received", {"code": code})

        # Combine all serializable variables from exec_globals and exec_locals
        def get_serializable_vars(var_dict):
            serializable_vars = {}
            for key, value in var_dict.items():

                # if we are in output_prefix mode, only include variables that start with 'output_'
                if result_mode == "output_prefix" and not key.startswith("output_"):
                    continue

                # if we are in include_list mode, only include variables that are in the var_list
                if result_mode == "include_list" and key not in var_list:
                    continue

                # if we are in exclude_list mode, only include variables that are not in the var_list
                if result_mode == "exclude_list" and key in var_list:
                    continue

                try:
                    # Try to serialize the variable
                    json.dumps(value)
                    serializable_vars[key] = value
                except (TypeError, OverflowError):
                    # Skip non-serializable variables
                    pass
            return serializable_vars

        all_serializable_vars = {}
        # Skip if result_mode is 'result_only'
        if result_mode != "result_only":
            serializable_globals = get_serializable_vars(exec_globals)
            serializable_locals = get_serializable_vars(exec_locals)

            # Merge globals and locals, with locals overriding globals for any key overlap
            all_serializable_vars = {**serializable_globals, **serializable_locals}
        # Assume the last expression in the code is the result
        result = exec_locals.get("result", exec_globals.get("result"))
        send_event("tools/code_exec/execute/end", {"result": result})

        return result

    except Exception as e:
        traceback_str = traceback.format_exc()
        send_event(
            "tools/code_exec/execute/error",
            {"error": str(e), "traceback": traceback_str},
        )
        return {"error": str(e), "traceback": traceback_str}
