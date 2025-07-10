from typing import List, Any

from agent.components.tool import register_tool, get_tool_metadata, to_openai_tools
from agent.core import ActionContext
from agent.prompt import Prompt
from inspect import signature, Parameter
import functools
import json


@register_tool(tags=["prompts"])
def prompt_llm_with_messages(action_context: ActionContext, prompt: dict):
    """
    Generate a response to a prompt using the LLM model.
    The LLM cannot see the conversation history, so make sure to include all necessary context in the prompt.
    Make sure and include ALL text, data, or other information that the LLM needs
    directly in the prompt. The LLM can't access any external information or context.
    """
    generate_response = action_context.get("llm")
    response = generate_response(Prompt(messages=[*prompt]))
    return response


def prompt_with_retries(
    generate_response, prompt: Prompt, max_retries: int = 3, functions=None
):
    """
    Generate a response to a prompt using the LLM model with retries.
    This function will retry up to `max_retries` times if the response is empty.
    """
    for i in range(max_retries):
        try:
            response = generate_response(prompt)
            if response:
                if prompt.tools:
                    result = json.loads(response)
                    function_name = result["function"]["name"]
                    args = result["function"]["arguments"]
                    args_dict = json.loads(args)

                    if functions and function_name in functions:
                        return functions[function_name](**args_dict)
                    else:
                        return args_dict
                else:
                    return response
        except Exception as e:
            print(f"Error generating response: {e}")
            print("Retrying...")

    return None


def prompt2(template, tools=None):
    """
    A decorator that transforms a function into one that uses an LLM prompt template.
    The decorated function's arguments are used to fill in placeholders in the template.
    Supports:
    - A single string or a list of messages as the prompt.
    - An optional `tools` parameter for integrating tool references.
    The `action_context` parameter is automatically managed and added to the signature.
    """
    tool_meta = [get_tool_metadata(tool) for tool in tools] if tools else []
    tool_lookup = (
        {tool["tool_name"]: tool["function"] for tool in tool_meta} if tools else {}
    )
    tools_metadata = to_openai_tools(tool_meta) if tools else []

    def decorator(func):
        # Get the original function's signature
        orig_sig = signature(func)
        orig_params = list(orig_sig.parameters.values())

        # Add the `action_context` parameter to the function's signature
        action_context_param = Parameter(
            "action_context",
            kind=Parameter.KEYWORD_ONLY,  # Makes it a keyword-only parameter
            default=None,
        )
        new_params = orig_params + [action_context_param]
        new_sig = orig_sig.replace(parameters=new_params)

        @functools.wraps(
            func
        )  # Preserve the original docstring and function attributes
        def wrapper(*args, **kwargs):
            # Extract action_context from kwargs or raise an error if not provided
            action_context = kwargs.pop("action_context", None)
            if action_context is None:
                raise ValueError("action_context is required for the prompt decorator")

            # Map function arguments to their values
            func_args = func.__code__.co_varnames[: func.__code__.co_argcount]
            arg_dict = {**dict(zip(func_args, args)), **kwargs}

            # Format the prompt template
            if isinstance(template, str):
                # Single string prompt: format it with the arguments
                filled_prompt = template.format(**arg_dict)
                messages = [{"role": "user", "content": filled_prompt}]
            elif isinstance(template, list):
                # List of messages: format each message with the arguments
                messages = [
                    {"role": msg["role"], "content": msg["content"].format(**arg_dict)}
                    for msg in template
                ]
            else:
                raise TypeError(
                    "Template must be either a string or a list of messages"
                )

            # Use the LLM in the action_context to generate a response
            generate_response = action_context.get("llm")
            if not generate_response:
                raise ValueError("No LLM available in the action_context")

            response = prompt_with_retries(
                generate_response=generate_response,
                prompt=Prompt(messages=messages, tools=tools_metadata),
                functions=tool_lookup,
                max_retries=3,
            )

            return response

        # Dynamically update the wrapper's signature to include action_context
        wrapper.__signature__ = new_sig
        return wrapper

    return decorator


def prompt(template):
    """
    A decorator that transforms a function into one that uses an LLM prompt template.
    The decorated function's arguments are used to fill in placeholders in the template.
    Supports both a single string as a prompt or a list of messages.
    Automatically manages the `action_context` and ensures it appears in the signature.
    """

    def decorator(func):
        # Get the original function's signature
        orig_sig = signature(func)
        orig_params = list(orig_sig.parameters.values())

        # Add the `action_context` parameter to the function's signature
        action_context_param = Parameter(
            "action_context",
            kind=Parameter.KEYWORD_ONLY,  # Makes it a keyword-only parameter
            default=None,
        )
        new_params = orig_params + [action_context_param]
        new_sig = orig_sig.replace(parameters=new_params)

        @functools.wraps(
            func
        )  # Preserve the original docstring and function attributes
        def wrapper(*args, **kwargs):
            # Extract action_context
            action_context = kwargs.pop("action_context", None)
            if action_context is None:
                raise ValueError("action_context is required for the prompt decorator")

            # Map function arguments to their values
            func_args = func.__code__.co_varnames[: func.__code__.co_argcount]
            arg_dict = {**dict(zip(func_args, args)), **kwargs}

            # Format the prompt template
            if isinstance(template, str):
                # Single string prompt: format it with the arguments
                filled_prompt = template.format(**arg_dict)
                messages = [{"role": "user", "content": filled_prompt}]
            elif isinstance(template, list):
                # List of messages: format each message with the arguments
                messages = [
                    {"role": msg["role"], "content": msg["content"].format(**arg_dict)}
                    for msg in template
                ]
            else:
                raise TypeError(
                    "Template must be either a string or a list of messages"
                )

            # Use the LLM in the action_context to generate a response
            generate_response = action_context.get("llm")
            if not generate_response:
                raise ValueError("No LLM available in the action_context")
            response = generate_response(Prompt(messages=messages))

            return response

        # Dynamically update the wrapper's signature to include action_context
        wrapper.__signature__ = new_sig
        return wrapper

    return decorator


@register_tool()
def qa_check(
    action_context: ActionContext, qa_criteria: str, thing_to_check: Any
) -> bool:
    """
    Check if the provided thing meets the specified QA criteria.

    Parameters:
        qa_criteria (str): The QA criteria to check against.
        thing_to_check (Any): The thing to check.

    Returns:
        Bool: True if the thing meets the QA criteria, False otherwise.
    """
    generate_response = action_context.get("llm")
    response = generate_response(
        Prompt(
            messages=[
                {
                    "role": "system",
                    "content": "Be extremely thorough in your quality assurance check.",
                },
                {
                    "role": "user",
                    "content": "Check if the following meets the specified QA criteria."
                    "\n\nCriteria:\n---------\n" + qa_criteria + "\n---------\n"
                    "\nWhat to Check:\n---------\n"
                    + json.dumps(thing_to_check)
                    + "\n---------\n",
                },
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "return_qa_check_result",
                        # Include up to 1024 characters of the description
                        "description": "Report the result of a quality assurance check as true|false.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "passed": {
                                    "type": "boolean",
                                    "description": "True if the quality assurance check passed.",
                                }
                            },
                            "required": ["passed"],
                        },
                    },
                }
            ],
        )
    )

    try:
        result = json.loads(response)
        args = result["function"]["arguments"]
        args_dict = json.loads(args)
        return args_dict["passed"]
    except Exception as e:
        print(f"Error parsing QA check result: {e}")
        return False


@register_tool(tags=["prompts"])
def prompt_llm(action_context: ActionContext, prompt: str):
    """
    Generate a response to a prompt using the LLM model.
    The LLM cannot see the conversation history, so make sure to include all necessary context in the prompt.
    Make sure and include ALL text, data, or other information that the LLM needs
    directly in the prompt. The LLM can't access any external information or context.
    """
    generate_response = action_context.get("llm")
    response = generate_response(Prompt(messages=[{"role": "user", "content": prompt}]))
    return response


@register_tool(tags=["prompts"])
def prompt_llm_with_info(
    action_context: ActionContext, prompt: str, result_references: List[str] = None
):
    """
    Generate a response to a prompt using the LLM model.
    The LLM cannot see the conversation history, so make sure to include all necessary context in the prompt.
    Make sure and include ALL text, data, or other information that the LLM needs
    directly in the prompt. The LLM can't access any external information or context.
    """
    generate_response = action_context.get("llm")
    result_references = result_references or []
    result_info = "\n".join(str(result_references))

    response = generate_response(
        Prompt(
            messages=[
                {
                    "role": "user",
                    "content": "<info>\n" + result_info + "\n</info>\n" + prompt,
                }
            ]
        )
    )
    return response


@register_tool(tags=["prompts"])
def prompt_llm_for_json(action_context: ActionContext, schema: dict, prompt: str):
    """
    Have the LLM generate JSON in response to a prompt. Always use this tool when you need structured data out of the LLM.
    This function takes a JSON schema that specifies the structure of the expected JSON response.
    """
    generate_response = action_context.get("llm")
    for i in range(3):
        try:
            response = generate_response(
                Prompt(
                    messages=[
                        {
                            "role": "system",
                            "content": f"You MUST produce output that adheres to the following JSON schema:\n\n{json.dumps(schema, indent=4)}",
                        },
                        {"role": "user", "content": prompt},
                    ]
                )
            )

            # Check if the response has the json inside of a markdown code block
            if "```json" in response:
                # Search from the front and then the back
                start = response.find("```json")
                end = response.rfind("```")
                response = response[start + 7 : end].strip()

            # Parse the JSON response
            response = json.loads(response)

            return response
        except Exception as e:
            if i == 2:
                raise e
            print(f"Error generating response: {e}")
            print("Retrying...")


@register_tool(tags=["prompts"])
def prompt_expert(
    action_context: ActionContext, description_of_expert: str, prompt: str
):
    """
    Generate a response to a prompt using the LLM model, acting as the provided expert.
    You should provide a detailed description of the expert to act as. Explain the expert's background, knowledge, and expertise.
    Provide a rich and highly detailed prompt for the expert that considers the most important frameworks, methodologies,
    analyses, techniques, etc. of relevance.
    """
    generate_response = action_context.get("llm")
    response = generate_response(
        Prompt(
            messages=[
                {
                    "role": "system",
                    "content": f"Act as the following expert and respond accordingly: {description_of_expert}",
                },
                {"role": "user", "content": prompt},
            ]
        )
    )
    return response
