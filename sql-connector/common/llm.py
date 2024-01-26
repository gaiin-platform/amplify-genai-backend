import json
import os
import random

from langchain_openai import AzureChatOpenAI

from common.secrets import get_secret_value


def get_chat_llm(model_name, temperature=0.7):
    conf = get_llm_config(model_name)

    llm = AzureChatOpenAI(
        temperature=temperature,
        openai_api_version="2023-12-01-preview",
        openai_api_type="azure",
        openai_api_key=conf["key"],
        azure_endpoint=conf["endpoint"],
        azure_deployment=conf["deployment"],
        model=model_name,
    )

    return llm


def get_endpoint_data(parsed_data, model_name):
    # Find the model in the list of models
    endpoint_data = next(
        (model[model_name] for model in parsed_data["models"] if model_name in model),
        None,
    )
    if not endpoint_data:
        raise ValueError("Model name not found in the secret data")

    # Randomly choose one of the endpoints
    endpoint_info = random.choice(endpoint_data["endpoints"])
    url = endpoint_info["url"]
    key = endpoint_info["key"]

    # Extract the endpoint and deployment from the URL
    endpoint_parts = url.split("/openai/")
    endpoint = endpoint_parts[0] if len(endpoint_parts) > 0 else None
    deployment_parts = url.split("/deployments/")
    deployment = (
        deployment_parts[1].split("/")[0] if len(deployment_parts) > 1 else None
    )

    return {
        "key": key,
        "endpoint": endpoint,
        "deployment": deployment,
    }


def get_llm_config(model_name):
    secret_name = os.environ["LLM_ENDPOINTS_SECRETS_NAME"]
    secret_data = get_secret_value(secret_name)
    parsed_secret = json.loads(secret_data)

    return get_endpoint_data(parsed_secret, model_name)
