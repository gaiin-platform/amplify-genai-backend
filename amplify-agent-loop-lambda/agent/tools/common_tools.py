import json
from typing import Dict, List

import requests

from agent.game.action import ActionContext
from agent.prompt import generate_response
from agent.tool import register_tool


@register_tool()
def get_user_input(message: str) -> str:
    """
    Get user input based on the provided message.

    Parameters:
        message (str): The prompt to display to the user.

    Returns:
        str: The user's input.
    """
    return input(message)


@register_tool()
def prompt_llm(prompt: str):
    """
    Generate a response to a prompt using the LLM model.
    The LLM cannot see the conversation history, so make sure to include all necessary context in the prompt.
    Make sure and include ALL text, data, or other information that the LLM needs
    directly in the prompt. The LLM can't access any external information or context.
    """
    response = generate_response([
        {"role": "user", "content": prompt}
    ])
    return response


@register_tool()
def prompt_llm_with_info(prompt: str, result_references: List[str] = None):
    """
    Generate a response to a prompt using the LLM model.
    The LLM cannot see the conversation history, so make sure to include all necessary context in the prompt.
    Make sure and include ALL text, data, or other information that the LLM needs
    directly in the prompt. The LLM can't access any external information or context.
    """

    result_references = result_references or []
    result_info = "\n".join(str(result_references))

    response = generate_response([
        {"role": "user", "content": "<info>\n" + result_info + "\n</info>\n" + prompt}
    ])
    return response


@register_tool()
def prompt_llm_for_json(schema: dict, prompt: str):
    """
    Have the LLM generate JSON in response to a prompt. Always use this tool when you need structured data out of the LLM.
    This function takes a JSON schema that specifies the structure of the expected JSON response.
    """

    for i in range(3):
        try:
            response = generate_response([
                {"role": "system", "content": f"You MUST produce output that adheres to the following JSON schema:\n\n{json.dumps(schema, indent=4)}"},
                {"role": "user", "content": prompt}
            ])

            # Check if the response has the json inside of a markdown code block
            if "```json" in response:
                # Search from the front and then the back
                start = response.find("```json")
                end = response.rfind("```")
                response = response[start+7:end].strip()

            # Parse the JSON response
            response = json.loads(response)

            return response
        except Exception as e:
            if i == 2:
                raise e
            print(f"Error generating response: {e}")
            print("Retrying...")



@register_tool()
def prompt_expert(description_of_expert: str, prompt: str):
    """
    Generate a response to a prompt using the LLM model, acting as the provided expert.
    You should provide a detailed description of the expert to act as. Explain the expert's background, knowledge, and expertise.
    Provide a rich and highly detailed prompt for the expert that considers the most important frameworks, methodologies,
    analyses, techniques, etc. of relevance.
    """
    response = generate_response([
        {"role": "system", "content": f"Act as the following expert and respond accordingly: {description_of_expert}"},
        {"role": "user", "content": prompt}
    ])
    return response


@register_tool()
def send_http_request(url: str, headers:Dict, method: str = "GET", body: str = None) -> Dict:
    """
    Make an HTTP request to the provided URL with the given headers, method, and body.

    Parameters:
        url (str): The URL to request.
        headers (Dict): The headers to include in the request.
        method (str, optional): The HTTP method to use. Defaults to "GET".
        body (str, optional): The body to include in the request. Defaults to None.

    Returns:
        Dict: A dictionary containing the status code, headers, and body of the response.
    """
    response = requests.request(method, url, headers=headers, data=body)
    return {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": response.text
    }

@register_tool()
def get_web_page_text(url: str) -> str:
    """
    Get the text content of a web page at the provided URL.

    Parameters:
        url (str): The URL of the web page.

    Returns:
        str: The text content of the web page.
    """
    response = requests.get(url)

    # Use beautifulsoup to extract text content from HTML
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.text, 'html.parser')
    text_content = soup.get_text()

    return text_content



@register_tool(terminal=True)
def terminate(message: str, result_references: List = None):
    """
    Terminate the conversation.
    No other actions can be run after this one.
    This must be run when the task is complete.

    If you need to return some specific results, refer to them using the result_references parameter
    and they will be included.

    When you terminate, include results that are important for the user to see based on their original
    request. This could be the output of a calculation, the result of a web request, or any other
    that answers the user's question or completes the task. The message should explain the results if
    any are included.

    Returns:
        dict: The message to display to the user and the results of the actions in the
    """

    return {
        "message": message,
        "results": result_references
    }


