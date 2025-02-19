import json
import os
import traceback
from typing import List

import litellm
from attr import dataclass
from litellm import completion

from common.secrets import get_llm_config


@dataclass
class Prompt:
    messages: List[dict]
    tools: List[dict] = []
    metadata: dict = {}


def generate_response(model, prompt: Prompt) -> str:
    """Call LLM to get response"""

    messages = prompt.messages
    tools = prompt.tools

    result = None

    try:

        if not tools:
            print("Prompting without tools.")
            response = completion(
                model=model,
                messages=messages,
                max_tokens=1024
            )
            result = response.choices[0].message.content
        else:
            print("Prompting with tools.")
            response = completion(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=1024
            )

            if response.choices[0].message.tool_calls:
                tool = response.choices[0].message.tool_calls[0]
                result = {
                    "tool": tool.function.name,
                    "args": json.loads(tool.function.arguments),
                }
                result = json.dumps(result)
            else:
                result = response.choices[0].message.content

    except Exception as e:
        traceback.print_exc()
        print(f"Error generating response: {e}")
        print(f"Prompt: ")
        for message in messages:
            print(f"Message: {message}")
        if tools:
            print(f"Tools:")
            for tool in tools:
                print(f"Tool: {tool}")
        print(f"Model: {model}")

        raise e

    return result

def create_llm(access_token, model):
    key, uri = get_llm_config(model)

    base, version = uri.split("?")
    version = version.split("=")[1]

    base = base.split("/openai")[0]

    os.environ["AZURE_API_KEY"] = key
    os.environ["AZURE_API_BASE"] = base
    os.environ["AZURE_API_VERSION"] = version

    def llm(prompt):
        return generate_response("azure/" + model, prompt)

    return llm