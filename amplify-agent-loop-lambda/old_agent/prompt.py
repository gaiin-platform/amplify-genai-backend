import os

from litellm import completion

from common.secrets import get_llm_config
from llm.chat import chat_simple_messages


def generate_response(access_token, model, messages: []) -> str:
    response = chat_simple_messages(access_token, model, messages)
    return response

def create_lambda_llm(access_token, model):
    llm = lambda prompt: generate_response(access_token, model, prompt)
    return llm

def create_llm(access_token, model):
    key, uri = get_llm_config(model)

    base, version = uri.split("?")
    version = version.split("=")[1]

    base = base.split("/openai")[0]

    os.environ["AZURE_API_KEY"] = key
    os.environ["AZURE_API_BASE"] = base
    os.environ["AZURE_API_VERSION"] = version

    def llm(prompt):
        return completion(
            model = "azure/"+model,
            messages = prompt
        )

    return llm
