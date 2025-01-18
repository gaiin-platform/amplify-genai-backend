import json
from typing import Dict, List

import requests

from agent.core import AgentContext
from agent.tools.python_tool import register_tool


@register_tool()
def list_available_agents(agent_context: AgentContext):
    """
    List all available agents that work can be handed-off to.

    Returns:
        List[Dict[str, str]]: A list of dictionaries containing the agent IDs and descriptions.
    """
    registry = agent_context.get_agent_registry()
    if registry is None:
        return []

    return agent_context.get_agent_registry().get_descriptions()


@register_tool(terminal=True)
def handoff_to_agent(agent_context: AgentContext, agent_id: str, instructions_for_agent: str) -> str:
    """
    Handoff the conversation to another agent.

    Parameters:
        agent_id (str): The ID of the agent to handoff to.
        instructions_for_agent (str): Instructions to provide to the agent on what to do.

    Returns:
        str: The response from the agent.
    """
    memory = agent_context.get_memory()
    to_agent = agent_context.get_agent_registry().get(agent_id)

    memory.add_memory({
        "type": "user",
        "content":
                f"You are the agent with ID '{agent_id}' and a new task has been handed-off to you."
                f"You can refer to the prior messages for information. "
    })

    if to_agent:
        try:
            to_agent.run(user_input=instructions_for_agent, memory=memory, action_context_props=agent_context.properties)
            return "Agent returned. Check the conversation for its output."
        except Exception as e:
            return f"Error running agent: {str(e)}"
    else:
        return f"Agent with ID {agent_id} not found."

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
async def prompt_llm(agent_context: AgentContext, prompt: str):
    """
    Generate a response to a prompt using the LLM model.
    Make sure and include ALL text, data, or other information that the LLM needs
    directly in the prompt. The LLM can't access any external information or context.
    """
    response = await agent_context.get_llm_service().generate_response([
        {"role": "user", "content": prompt}
    ])
    return response


@register_tool()
async def prompt_llm_for_json(agent_context: AgentContext, schema: dict, prompt: str):
    """
    Have the LLM generate JSON in response to a prompt. Always use this tool when you need structured data out of the LLM.
    This function takes a JSON schema that specifies the structure of the expected JSON response.
    """

    for i in range(3):
        try:
            response = await agent_context.get_llm_service().generate_response([
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
async def prompt_expert(agent_context: AgentContext, description_of_expert: str, prompt: str):
    """
    Generate a response to a prompt using the LLM model, acting as the provided expert.
    You should provide a detailed description of the expert to act as. Explain the expert's background, knowledge, and expertise.
    Provide a rich and highly detailed prompt for the expert that considers the most important frameworks, methodologies,
    analyses, techniques, etc. of relevance.
    """
    response = await agent_context.get_llm_service().generate_response([
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
def terminate(message: str):
    """
    Terminate the conversation.
    No other actions can be run after this one.
    This must be run when the task is complete.

    Returns:
        None
    """
    pass

