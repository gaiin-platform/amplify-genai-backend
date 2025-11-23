import json
import os
import random
import re
import traceback
from typing import List

import litellm
from attr import dataclass
from litellm import completion
from agent.accounting import record_usage
import boto3
from pycommon.api.secrets import get_secret_value
from pycommon.authz import is_rate_limited

dynamodb = boto3.client("dynamodb")

@dataclass
class Prompt:
    messages: List[dict]
    tools: List[dict] = []
    metadata: dict = {}


def generate_response(
    model, prompt: Prompt, account_details: dict, details: dict = {}
) -> str:
    """Call LLM to get response"""

    rate_limit = account_details.get("rate_limit")
    if rate_limit and os.environ.get("COST_CALCULATIONS_DYNAMO_TABLE"):
        rate_limited, message = is_rate_limited(account_details["user"], rate_limit)
        if rate_limited:
            print(f"Rate limit exceeded: {message}")
            raise Exception(message)

    messages = prompt.messages
    tools = prompt.tools
    token_cost = 0.0
    result = None

    try:
        response = None
        if not tools:
            print("Prompting without tools.")
            response = completion(
                model=model,
                messages=messages,
                max_completion_tokens=1024,
            )
            result = response.choices[0].message.content
        else:
            print("Prompting with tools.")
            response = completion(
                model=model,
                messages=messages,
                tools=tools,
                max_completion_tokens=1024,
            )

            if response.choices[0].message.tool_calls:
                tool = response.choices[0].message.tool_calls[0]
                tool_args = None
                try:
                    tool_args = json.loads(tool.function.arguments)
                except:
                    print(
                        f"Error parsing tool arguments coming from litellm: {tool.function.arguments}"
                    )

                result = {
                    "tool": tool.function.name,
                    "args": tool_args,
                }
                result = json.dumps(result)
            else:
                result = response.choices[0].message.content

        # print(f"--litellm Response: {response}")
        print("Recording usage for litellm response id: ", response.id)
        model_id = model.split("/")[1]

        usage = response.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        details["litellm_used"] = True

        completion_tokens_details = usage.get("completion_tokens_details", None)
        prompt_tokens_details = usage.get("prompt_tokens_details", None)

        if completion_tokens_details:
            # Convert to dictionary using vars() or __dict__
            try:
                details["completion_tokens_details"] = {
                    "reasoning_tokens": getattr(
                        completion_tokens_details, "reasoning_tokens", 0
                    ),
                    "text_tokens": getattr(
                        completion_tokens_details, "text_tokens", None
                    ),
                }
            except:
                print(
                    f"Error converting completion_tokens_details to dictionary: {completion_tokens_details}"
                )

        cached_tokens = 0
        if prompt_tokens_details:
            try:
                details["prompt_tokens_details"] = {
                    "reasoning_tokens": getattr(
                        prompt_tokens_details, "reasoning_tokens", 0
                    ),
                    "text_tokens": getattr(prompt_tokens_details, "text_tokens", None),
                    "image_tokens": getattr(prompt_tokens_details, "image_tokens", 0),
                    "cached_tokens": getattr(prompt_tokens_details, "cached_tokens", 0),
                }
                cached_tokens = getattr(prompt_tokens_details, "cached_tokens", 0)
            except:
                print(
                    f"Error converting prompt_tokens_details to dictionary: {prompt_tokens_details}"
                )

        try:
            token_cost = record_usage(
                account_details,
                response.id,
                model_id,
                input_tokens,
                output_tokens,
                cached_tokens,
                details,
            )

        except Exception as e:
            print(f"Warning: Failed to record usage: {e}")

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

    return result, token_cost


def get_endpoint_data(parsed_data, model_name):
    if model_name in ["gpt-4-1106-Preview", "gpt-4-1106-preview"]:
        model_name = "gpt-4-turbo"
    elif model_name in ["gpt-35-1106", "gpt-35-1106"]:
        model_name = "gpt-35-turbo"
    elif model_name in ["gpt-4o", "gpt-4o"]:
        model_name = "gpt-4o"

    endpoint_data = next(
        (model for model in parsed_data["models"] if model_name in model), None
    )
    if not endpoint_data:
        raise ValueError("Model name not found in the secret data")

    endpoint_info = random.choice(endpoint_data[model_name]["endpoints"])
    return endpoint_info["key"], endpoint_info["url"]


def get_llm_config(model_name):
    secret_name = os.environ.get("LLM_ENDPOINTS_SECRETS_NAME")
    secret_data = get_secret_value(secret_name)
    parsed_secret = json.loads(secret_data)
    return get_endpoint_data(parsed_secret, model_name)


def is_openai_model(model):
    return model and ("gpt" in model or re.match(r'^o\d', model))


def is_bedrock_model(model):
    # Common Bedrock models include anthropic.claude, amazon.titan, ai21, etc.
    return any(
        provider in model
        for provider in [
            "anthropic",
            "claude",
            "amazon",
            "titan",
            "ai21",
            "cohere",
            "meta",
            "deepseek",
        ]
    )


def is_gemini_model(model):
    return model and "gemini" in model


def get_model_provider(model_id):
    """
    Lookup the provider for a model from the MODEL_RATE_TABLE
    """
    model_rate_table = os.environ.get("MODEL_RATE_TABLE")
    if not model_rate_table:
        print("MODEL_RATE_TABLE is not provided in environment variables")
        return None
    
    try:
        model_rate_response = dynamodb.query(
            TableName=model_rate_table,
            KeyConditionExpression="ModelID = :modelId",
            ExpressionAttributeValues={":modelId": {"S": model_id}},
        )
        
        if (
            not model_rate_response.get("Items")
            or len(model_rate_response["Items"]) == 0
        ):
            print(f"No model rate found for ModelID: {model_id}")
            return None
            
        model_rate = model_rate_response["Items"][0]
        provider = model_rate.get("Provider", {}).get("S")
        print(f"ModelID: {model_id} using provider: {provider}")
        return provider
        
    except Exception as e:
        print(f"Error looking up model provider: {e}")
        return None


def litellm_model_str(model):
    provider_prefix = ""
    if is_openai_model(model):
        # Lookup the provider from the model rate table to determine if we should use OpenAI or Azure
        provider = get_model_provider(model)
        
        if provider == "OpenAI":
            # Use direct OpenAI API
            secret_name = os.environ.get("SECRETS_ARN_NAME")
            secret_data = get_secret_value(secret_name)
            parsed_secret = json.loads(secret_data)
            openai_api_key = parsed_secret.get("OPENAI_API_KEY")
            if openai_api_key:
                os.environ["OPENAI_API_KEY"] = openai_api_key
                provider_prefix = "openai"
            else:
                raise ValueError("OPENAI_API_KEY not found in secrets for OpenAI provider")
        else:
            # Default to Azure OpenAI for backward compatibility
            key, uri = get_llm_config(model)

            base, version = uri.split("?")
            version = version.split("=")[1]

            base = base.split("/openai")[0]

            os.environ["AZURE_API_KEY"] = key
            os.environ["AZURE_API_BASE"] = base
            os.environ["AZURE_API_VERSION"] = version
            provider_prefix = "azure"
    elif is_bedrock_model(model):
        region = os.environ.get("AWS_REGION", "us-east-1")

        # Create a boto3 client for your bedrock interactions if needed
        bedrock_client = boto3.client("bedrock-runtime", region_name=region)
        litellm.bedrock_config = {"client": bedrock_client}
        provider_prefix = "bedrock"
    elif is_gemini_model(model):
        secret_name = os.environ.get("SECRETS_ARN_NAME")
        secret_data = get_secret_value(secret_name)
        parsed_secret = json.loads(secret_data)
        gemini_api_key = parsed_secret.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = gemini_api_key
        provider_prefix = "gemini"

    else:
        raise ValueError(f"Unsupported model: {model}")
    return f"{provider_prefix}/{model}"


def create_llm(
    access_token,
    model,
    current_user="Agent",
    account={"account_id": "general_account"},
    details: dict = {},
    advanced_model=None,
):
    agent_model_str = litellm_model_str(model)
    advanced_model_str = agent_model_str
    try:
        if advanced_model:
            advanced_model_str = litellm_model_str(advanced_model)
    except Exception as e:
        print(
            f"Error creating advanced model: {e}, using agent model as advanced model..."
        )

    account_details = {"user": current_user, "accessToken": access_token, **account}

    total_cost = 0.0

    def llm(prompt):
        nonlocal total_cost
        model_str = agent_model_str
        if isinstance(prompt.metadata, dict) and prompt.metadata.get(
            "advanced_reasoning", False
        ):
            print("Prompting using advanced model: ", advanced_model_str)
            model_str = advanced_model_str

        result, token_cost = generate_response(
            model_str, prompt, account_details, details
        )
        total_cost += token_cost
        return result

    llm.get_total_cost = lambda: total_cost
    return llm
