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
from pycommon.api.credentials import get_endpoint
from embedding_models import get_embedding_models
from pycommon.llm.chat import chat
from pycommon.api.get_endpoint import get_endpoint as get_chat_endpoint, EndpointType
import time
import random
from pycommon.logger import getLogger

logger = getLogger("embedding_shared_functions")

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
qa_input_context_window = 8192  # Default fallback
model_result = get_embedding_models()
logger.debug("Model_result: %s", model_result)
if model_result["success"]:
    data = model_result["data"]
    embedding_model_name = data["embedding"]["model_id"]
    embedding_provider = data["embedding"]["provider"]
    qa_model_name = data["qa"]["model_id"]
    qa_provider = data["qa"]["provider"]
    qa_input_context_window = data["qa"]["input_context_window"]


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


def extract_base_key_from_chunk(text_location_key):
    """Extract base key by removing chunk number suffix (e.g., -6.chunks.json)"""
    return re.sub(r'-\d+\.chunks\.json$', '', text_location_key)


def extract_chunk_number(text_location_key):
    """Extract chunk number from key (e.g., returns 6 from key ending with -6.chunks.json)"""
    chunk_match = re.search(r'-(\d+)\.chunks\.json$', text_location_key)
    return int(chunk_match.group(1)) if chunk_match else None


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
        logger.error(f"No Models Provided:\nembedding: {embedding_model_name}")
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


def truncate_content_for_model(content, model_name, max_tokens):
    """Truncate content to fit within model's token limit"""
    try:
        encoding = tiktoken.encoding_for_model(model_name)
        tokens = encoding.encode(content)
        
        if len(tokens) <= max_tokens:
            return content
        
        # Truncate to max_tokens and decode back to text
        truncated_tokens = tokens[:max_tokens]
        truncated_content = encoding.decode(truncated_tokens)
        
        logger.warning(f"[TOKEN_LIMIT] Content truncated from {len(tokens)} to {max_tokens} tokens for model {model_name}")
        return truncated_content
        
    except Exception as e:
        logger.error(f"[TOKEN_LIMIT] Error truncating content: {e}")
        # Fallback: truncate by character count (rough estimate)
        char_limit = max_tokens * 4  # Rough estimate of 4 chars per token
        if len(content) > char_limit:
            truncated = content[:char_limit]
            logger.warning(f"[TOKEN_LIMIT] Fallback truncation from {len(content)} to {char_limit} characters")
            return truncated
        return content


def generate_questions(content, account_data = None):
    chat_endpoint = get_chat_endpoint(EndpointType.CHAT_ENDPOINT)

    if not chat_endpoint or not account_data or not account_data.get('access_token'):
        logger.error("CHAT_ENDPOINT environment variable or account_data not set")
        raise Exception("CHAT_ENDPOINT environment variable or account_data not set")
    
    logger.info(f"Generating questions with {qa_provider}")
    
    # Reserve tokens for system prompt and response
    reserved_tokens = 200  # Conservative estimate for system prompt + response space
    max_content_tokens = qa_input_context_window - reserved_tokens
    
    # Truncate content to fit within model's token limit
    truncated_content = truncate_content_for_model(content, qa_model_name, max_content_tokens)
    
    system_prompt = "With every prompt I send, think about what questions the text might be able to answer and return those questions. Please create many questions."
    payload = {
            "temperature": 0.1,  
            "dataSources": [],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": truncated_content},
            ],
            "options": {
                "ragOnly": False,
                "skipRag": False,
                "model": {"id": qa_model_name}, 
                "prompt": "Do not include any preambles or comments. Respond only with the questions.",
                "accountId": account_data.get("account"),
                "rateLimit": account_data.get("rate_limit"),
                "max_tokens": 1000  # Reasonable limit for question generation
            },
        }

    max_normal_retries = 2  
    max_rate_limit_retries = 3 
    base_delay = 0.5 
    max_delay = 5.0  
    
    def is_internal_rate_limit(error_message):
        """Check if the error is from internal rate limiting (should not retry)."""
        internal_indicators = [
            "Request limit reached",
            "Admin limit", 
            "Group limit",
            "User limit",
            "Rate limit:",
            "Current Spent:"
        ]
        return any(indicator in error_message for indicator in internal_indicators)
    
    def is_model_rate_limit(error_message):
        """Check if the error is from model rate limiting (should retry with longer waits)."""
        model_indicators = [
            "Too Many Requests",
            "too many requests",
            "rate limit",
            "quota exceeded",
            "Request Timed Out"
        ]
        return any(indicator in error_message for indicator in model_indicators)
    
    def calculate_backoff_delay(attempt, is_rate_limit=False):
        """Calculate exponential backoff with jitter."""
        if is_rate_limit:
            # Longer delays for rate limits: 2^attempt + random jitter
            delay = min(max_delay, (2 ** attempt) + random.uniform(0.5, 2.0))
        else:
            # Shorter delays for normal errors: base_delay * attempt + jitter
            delay = min(max_delay, base_delay * attempt + random.uniform(0.1, 0.5))
        return delay
    
    last_error = None
    
    for attempt in range(max(max_normal_retries, max_rate_limit_retries) + 1):
        try:
            logger.debug(f"[QA_RETRY] Attempt {attempt + 1} for question generation")
            response, metadata = chat(chat_endpoint, account_data['access_token'], payload)    
            
            # Handle both error string returns and successful responses
            if response.startswith("Error:"):
                error_message = response
                logger.debug(f"[QA_RETRY] Error response received: {error_message}")
                
                # Check if it's an internal rate limit (don't retry)
                if is_internal_rate_limit(error_message):
                    logger.warning(f"[QA_RETRY] 🚫 Internal rate limit detected - not retrying: {error_message}")
                    return {"success": False, "error": error_message}
                
                # Check if it's a model rate limit
                is_model_rl = is_model_rate_limit(error_message)
                max_retries = max_rate_limit_retries if is_model_rl else max_normal_retries
                
                if attempt >= max_retries:
                    logger.error(f"[QA_RETRY] ❌ Max retries ({max_retries}) reached for {'model rate limit' if is_model_rl else 'normal error'}")
                    return {"success": False, "error": error_message}
                
                # Calculate and wait before retry
                delay = calculate_backoff_delay(attempt, is_model_rl)
                error_type = "model rate limit" if is_model_rl else "error"
                logger.debug(f"[QA_RETRY] ⏳ {error_type} detected, waiting {delay:.2f}s before retry {attempt + 2}/{max_retries + 1}")
                time.sleep(delay)
                last_error = error_message
                continue
            
            # Success case
            logger.info(f"[QA_RETRY] ✅ Question generation successful on attempt {attempt + 1}")
            return {"success": True, "data": response}
            
        except Exception as e:
            error_message = str(e)
            logger.error(f"[QA_RETRY] Exception occurred: {error_message}")
            
            # Check if it's an internal rate limit (don't retry)
            if "429" in error_message and is_internal_rate_limit(error_message):
                logger.warning(f"[QA_RETRY] 🚫 Internal rate limit exception - not retrying: {error_message}")
                return {"success": False, "error": error_message}
            
            # Check if it's a model rate limit or timeout
            is_model_rl = "429" in error_message or is_model_rate_limit(error_message)
            max_retries = max_rate_limit_retries if is_model_rl else max_normal_retries
            
            if attempt >= max_retries:
                logger.error(f"[QA_RETRY] ❌ Max retries ({max_retries}) reached for {'model rate limit' if is_model_rl else 'normal error'}")
                logger.error(f"An error occurred with chat js call after {attempt + 1} attempts: {e}")
                return {"success": False, "error": error_message}
            
            # Calculate and wait before retry
            delay = calculate_backoff_delay(attempt, is_model_rl)
            error_type = "model rate limit" if is_model_rl else "exception"
            logger.debug(f"[QA_RETRY] ⏳ {error_type} detected, waiting {delay:.2f}s before retry {attempt + 2}/{max_retries + 1}")
            time.sleep(delay)
            last_error = error_message
    
    # This should never be reached, but just in case
    final_error = last_error or "Unknown error occurred"
    logger.error(f"Question generation failed after all retry attempts: {final_error}")
    return {"success": False, "error": final_error}



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
        logger.debug(f"Response: {response}")

        # Check if the items exist in the table
        if "Items" in response and response["Items"]:
            logger.debug("items: %s", response["Items"])
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

            logger.info(
                f"Fetched originalCreator: {most_recent_item.get('originalCreator')}"
            )
            originalCreator = most_recent_item.get("originalCreator")

            return {
                "originalCreator": originalCreator,
            }

        else:
            return None

    except Exception as e:
        logger.error(f"Error retrieving item: {e}")
        return None
