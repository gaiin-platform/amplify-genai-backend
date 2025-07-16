# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from enum import Enum
from openai import AzureOpenAI
from openai import OpenAI
import tiktoken
import json
import re
import os
import boto3
from boto3.dynamodb.conditions import Key
import logging
from pycommon.api.credentials import get_endpoint
from embedding_models import get_embedding_models
from pycommon.llm.chat import chat
from pycommon.api.get_endpoint import get_endpoint as get_chat_endpoint, EndpointType

logger = logging.getLogger()
logger.setLevel(logging.INFO)

endpoints_arn = os.environ["LLM_ENDPOINTS_SECRETS_NAME_ARN"]
api_version = os.environ["API_VERSION"]
hash_files_dynamo_table = os.environ["HASH_FILES_DYNAMO_TABLE"]
region = os.environ["REGION"]


class PROVIDERS(Enum):
    AZURE = "Azure"
    OPENAI = "OpenAI"
    BEDROCK = "Bedrock"


embedding_model_name = None
embedding_provider = None
qa_provider = None
qa_model_name = None
model_result = get_embedding_models()
print("Model_result", model_result)
if model_result["success"]:
    data = model_result["data"]
    embedding_model_name = data["embedding"]["model_id"]
    embedding_provider = data["embedding"]["provider"]
    qa_model_name = data["qa"]["model_id"]
    qa_provider = data["qa"]["provider"]


# Get embedding token count from tiktoken
def num_tokens_from_text(content, embedding_model_name):
    encoding = tiktoken.encoding_for_model(embedding_model_name)
    num_tokens = len(encoding.encode(content))
    return num_tokens


def clean_text(text):
    # Remove non-ASCII characters
    text = text.encode("ascii", "ignore").decode("ascii")
    # Remove punctuation using regex
    text_without_punctuation = re.sub(r"[^\w\s]", "", text)
    # Remove extra spaces using regex
    cleaned_text = re.sub(r"\s+", " ", text_without_punctuation)
    return cleaned_text.strip()


def preprocess_text(text):
    try:
        # Remove non-ASCII characters
        text = text.encode("ascii", "ignore").decode("ascii")
        # Remove punctuation using regex
        text_without_punctuation = re.sub(r"[^\w\s]", "", text)
        # Remove extra spaces using regex
        cleaned_text = re.sub(r"\s+", " ", text_without_punctuation)
        return {"success": True, "data": cleaned_text.strip()}
    except Exception as e:
        return {"success": False, "error": f"An error occurred: {str(e)}"}


def generate_embeddings(content):
    if not embedding_model_name:
        logging.error(f"No Models Provided:\nembedding: {embedding_model_name}")
        return {"success": False, "error": f"No Models Provided:\nembedding: {embedding_model_name}"}
    if embedding_provider == PROVIDERS.BEDROCK.value:
        return generate_bedrock_embeddings(content)
    if embedding_provider == PROVIDERS.AZURE.value:
        return generate_azure_embeddings(content)
    if embedding_provider == PROVIDERS.OPENAI.value:
        return generate_openai_embeddings(content)
    logger.error(f"Invalid embedding provider: {embedding_provider}")
    return {"success": False, "error": f"Invalid embedding provider: {embedding_provider}"}

def generate_bedrock_embeddings(content):
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        model_id = embedding_model_name

        native_request = {"inputText": content}
        request = json.dumps(native_request)

        response = client.invoke_model(modelId=model_id, body=request)
        model_response = json.loads(response["body"].read())

        embeddings = model_response["embedding"]
        input_token_count = model_response["inputTextTokenCount"]
        
        logger.info(f"Embedding generated. Input tokens: {input_token_count}, Embedding size: {len(embeddings)}")
        return {"success": True, "data": embeddings, "token_count": input_token_count}
    except Exception as e:
        logger.error(f"An error occurred with Bedrock: {e}", exc_info=True)
        return {"success": False, "error": f"An error occurred with Bedrock: {str(e)}"}


def generate_azure_embeddings(content):
    logger.info("Getting Embedding Endpoints")
    endpoint, api_key = get_endpoint(embedding_model_name, endpoints_arn)
    logger.info(f"Endpoint: {endpoint}")

    client = AzureOpenAI(
        api_key=api_key, azure_endpoint=endpoint, api_version=api_version
    )
    try:
        response = client.embeddings.create(input=content, model=embedding_model_name)
        embedding = response.data[0].embedding
        token_count = num_tokens_from_text(content, embedding_model_name)
    except Exception as e:
        logger.error(f"An error occurred with Azure OpenAI: {e}", exc_info=True)
        return {"success": False, "error": f"An error occurred with Azure OpenAI: {str(e)}"}
    return {"success": True, "data": embedding, "token_count": token_count}


def generate_openai_embeddings(content):
    logger.info("Getting Embedding Endpoints")
    endpoint, api_key = get_endpoint(embedding_model_name, endpoints_arn)
    logger.info(f"Endpoint: {endpoint}")

    client = OpenAI(api_key=api_key)
    try:
        response = client.embeddings.create(input=content, model=embedding_model_name)
        embedding = response.data[0].embedding
        token_count = num_tokens_from_text(content, embedding_model_name)
    except Exception as e:
        logger.error(f"An error occurred with OpenAI: {e}", exc_info=True)
        return {"success": False, "error": f"An error occurred with OpenAI: {str(e)}"}

    logger.info(f"Embedding: {embedding}")
    return {"success": True, "data": embedding, "token_count": token_count}


def generate_questions(content, account_data = None):
    chat_endpoint = get_chat_endpoint(EndpointType.CHAT_ENDPOINT)

    if not chat_endpoint or not account_data or not account_data.get('access_token'):
        print("CHAT_ENDPOINT environment variable or account_data not set")
        raise Exception("CHAT_ENDPOINT environment variable or account_data not set")
    
    print(f"Generating questions with {qa_provider}")
    
    system_prompt = "With every prompt I send, think about what questions the text might be able to answer and return those questions. Please create many questions."
    payload = {
            "temperature": 0.1,  
            "dataSources": [],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "options": {
                "ragOnly": False,
                "skipRag": False,
                "model": {"id": qa_model_name}, 
                "prompt": "Do not include any preambles or comments. Respond only with the questions.",
                "accountId": account_data.get("account"),
                "rateLimit": account_data.get("rate_limit")
            },
        }
    try:
        response, metadata =  chat(chat_endpoint, account_data['access_token'], payload)    
        # Handle both error string returns and successful responses
        if response.startswith("Error:"):
            return {"success": False, "error": response}
        return {"success": True, "data": response}
    except Exception as e:
        logger.error(f"An error occurred with chat js call: {e}")
        return {"success": False, "error": str(e)}



def get_original_creator(textLocationKey):
    # Initialize a session using Amazon DynamoDB
    session = boto3.Session()

    # Initialize DynamoDB resource
    dynamodb = session.resource("dynamodb", region_name="us-east-1")

    # Select your DynamoDB table
    table = dynamodb.Table(hash_files_dynamo_table)

    try:
        # Query items from the table based on textLocationKey
        response = table.query(
            IndexName="TextLocationIndex",  # Specify the GSI name here
            KeyConditionExpression=Key("textLocationKey").eq(textLocationKey),
        )
        print(f"Response: {response}")

        # Check if the items exist in the table
        if "Items" in response and response["Items"]:
            print("items: ", response["Items"])
            # Filter items to ensure createdAt is present and valid
            valid_items = [
                item
                for item in response["Items"]
                if "createdAt" in item and item["createdAt"]
            ]

            # If there are no valid items, return None
            if not valid_items:
                return None

            # Sort valid items by createdAt in descending order
            sorted_items = sorted(
                valid_items, key=lambda x: x["createdAt"], reverse=True
            )
            most_recent_item = sorted_items[0]

            logging.info(
                f"Fetched originalCreator: {most_recent_item.get('originalCreator')}"
            )
            originalCreator = most_recent_item.get("originalCreator")

            return {
                "originalCreator": originalCreator,
            }

        else:
            return None

    except Exception as e:
        print(f"Error retrieving item: {e}")
        return None
