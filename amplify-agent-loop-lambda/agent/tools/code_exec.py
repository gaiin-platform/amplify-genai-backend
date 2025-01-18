import json
import ast
import traceback
from uuid import uuid4

from agent.core import AgentContext
from agent.tools.python_tool import register_tool


def get_imports_from_code(code_string):
    """Parse Python code and return a list of required imports."""
    required_imports = set()

    additional = """
from typing import Dict, Any
import requests
import os
import json
    """

    code_string = additional + code_string

    try:
        tree = ast.parse(code_string)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    required_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module
                for alias in node.names:
                    required_imports.add(alias.name)
                    if module:  # handle 'from x import y'
                        required_imports.add(module)

    except SyntaxError:
        print("Failed to parse code")
        return []

    return list(required_imports)

def prepare_exec_globals(code_string, context_dict):
    imports = get_imports_from_code(code_string)
    exec_globals = globals().copy()

    # Add all required imports
    for imp in imports:
        try:
            # Try to import the module or object
            module = __import__(imp) if '.' not in imp else __import__(imp.split('.')[0])
            exec_globals[imp] = module
        except ImportError as e:
            print(f"Warning: Could not import {imp}: {e}")

    exec_globals.update(context_dict)
    return exec_globals


@register_tool()
def exec_code(agent_context: AgentContext, code: str):
    """
    Executes the provided Python code and returns the value of the 'result' variable if defined.

    Parameters:
        code (str): The Python code to execute.

    Ideally, the start of your code should be a long set of comments explaining what it does and the variables
    that are in scope at the start of its execution.

    Write a very robust python code that catches all exceptions and returns a dictionary with the key 'error' and
    the value as the error message if there is a problem.

    Avoid using any libraries that are not built-in unless you are told they are available.

    ALWAYS HAVE THE LAST LINE OF THE CODE assign the result to the variable 'result'. There MUST be a result.

    Returns:
        dict: A dictionary containing the status ('result' or 'error') and the 'result' or 'error_message'.
    """
    context_dict = agent_context.get("code_exec_context", {})

    result_mode = "result_only"
    var_list=[]
    options={}

    # Prepare the execution environment
    # exec_globals = context_dict
    exec_globals = prepare_exec_globals(code, context_dict)
    # exec_globals = globals().copy()
    # exec_globals.update({'context': context_dict})

    session_id = str(uuid4())
    session_data = {"code": code, "session_id": session_id}
    exec_locals = {}

    # Execute the code
    try:
        agent_context.emit("tools/code_exec/execute/start", session_data)
        exec(code, exec_globals, exec_locals)
        agent_context.emit("tools/code_exec/execute/result_received", session_data)

        # Combine all serializable variables from exec_globals and exec_locals
        def get_serializable_vars(var_dict):
            serializable_vars = {}
            for key, value in var_dict.items():

                # if we are in output_prefix mode, only include variables that start with 'output_'
                if result_mode == 'output_prefix' and not key.startswith('output_'):
                    continue

                # if we are in include_list mode, only include variables that are in the var_list
                if result_mode == 'include_list' and key not in var_list:
                    continue

                # if we are in exclude_list mode, only include variables that are not in the var_list
                if result_mode == 'exclude_list' and key in var_list:
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
        if result_mode != 'result_only':
            serializable_globals = get_serializable_vars(exec_globals)
            serializable_locals = get_serializable_vars(exec_locals)

            # Merge globals and locals, with locals overriding globals for any key overlap
            all_serializable_vars = {**serializable_globals, **serializable_locals}
        # Assume the last expression in the code is the result
        result = exec_locals.get('result', exec_globals.get('result'))
        agent_context.emit("tools/code_exec/execute/end", {**session_data, "result": result})

        return result

    except Exception as e:
        traceback_str = traceback.format_exc()
        agent_context.emit("tools/code_exec/execute/error", {**session_data, "error": str(e), "traceback": traceback_str})
        return {"error": str(e), "traceback": traceback_str}


