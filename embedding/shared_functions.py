# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from openai import OpenAI  # Ensure you have the OpenAI client library imported correctly
from openai import AzureOpenAI  # Ensure you have the Azure OpenAI client library imported correctly
import tiktoken
import json
import re
import os
import logging
import boto3
from common.credentials import get_endpoint

logger = logging.getLogger()
logger.setLevel(logging.INFO)

endpoints_arn = os.environ['LLM_ENDPOINTS_SECRETS_NAME_ARN']
api_version = os.environ['API_VERSION']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
qa_model_name = os.environ['QA_MODEL_NAME']
embedding_provider = os.environ['EMBEDDING_PROVIDER'] or os.environ['OPENAI_PROVIDER']
openai_provider = os.environ['OPENAI_PROVIDER']
region = os.environ['REGION'] # Set this environment variable to either "openai" or "azure"

# Get embedding token count from tiktoken
def num_tokens_from_text(content, embedding_model_name):
    encoding = tiktoken.encoding_for_model(embedding_model_name)
    num_tokens = len(encoding.encode(content))
    return num_tokens

def clean_text(text):
    # Remove non-ASCII characters
    text = text.encode('ascii', 'ignore').decode('ascii')
    # Remove punctuation using regex
    text_without_punctuation = re.sub(r'[^\w\s]', '', text)
    # Remove extra spaces using regex
    cleaned_text = re.sub(r'\s+', ' ', text_without_punctuation)
    return cleaned_text.strip()

def preprocess_text(text):
    # Remove punctuation and other non-word characters
    cleaned_text = re.sub(r'[^\w\s]', ' ', text)
    # Split the text into words based on whitespace
    words = cleaned_text.split()
    # Join the words with a space, a | symbol, and another space
    return ' | '.join(words)

def generate_embeddings(content, embedding_provider="azure"):
    if embedding_provider == "bedrock":
        return generate_bedrock_embeddings(content)
    if embedding_provider == "azure":
        return generate_azure_embeddings(content)
    if embedding_provider == "openai":
        return generate_openai_embeddings(content)

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
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version
    )
    try:
        response = client.embeddings.create(input=content, model=embedding_model_name)
        embedding = response.data[0].embedding
        token_count = num_tokens_from_text(content, embedding_model_name)
    except Exception as e:
        logger.error(f"An error occurred with Azure OpenAI: {e}", exc_info=True)
        raise

    logger.info(f"Embedding: {embedding}")
    return {"success": True, "data": embedding, "token_count": token_count}

def generate_openai_embeddings(content):
    logger.info("Getting Embedding Endpoints")
    endpoint, api_key = get_endpoint(embedding_model_name, endpoints_arn)
    logger.info(f"Endpoint: {endpoint}")
        
    client = OpenAI(
        api_key=api_key
    )
    try:
        response = client.embeddings.create(input=content, model=embedding_model_name)
        embedding = response.data[0].embedding
        token_count = num_tokens_from_text(content, embedding_model_name)
    except Exception as e:
        logger.error(f"An error occurred with OpenAI: {e}", exc_info=True)
        raise

    logger.info(f"Embedding: {embedding}")
    return {"success": True, "data": embedding, "token_count": token_count}

def generate_questions(content, embedding_provider="azure"):
    if embedding_provider == "bedrock":
        return generate_bedrock_questions(content)
    if embedding_provider == "azure":
        return generate_azure_questions(content)
    if embedding_provider == "openai":
        return generate_openai_questions(content)

def generate_bedrock_questions(content):
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        model_id = qa_model_name
        
        system_prompt = f"With every prompt I send, think about what questions the text might be able to answer and return those questions. Please create many questions."

        native_request = {
            "anthropic_version": "bedrock-2023-05-31",
            "system": system_prompt,
            "max_tokens": 512,
            "temperature": 0.7,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": content}],
                }
            ],
        }
        request = json.dumps(native_request)
        
        response = client.invoke_model(modelId=model_id, body=request)
        model_response = json.loads(response["body"].read())
        
        questions = model_response["content"][0]["text"]
        input_tokens = model_response["usage"]["input_tokens"]
        output_tokens = model_response["usage"]["output_tokens"]
        
        logger.info(f"Questions generated: {questions}")
        return {
            "success": True,
            "data": questions,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens
        }
    except Exception as e:
        logger.error(f"An error occurred with Bedrock: {e}", exc_info=True)
        return {"success": False, "error": f"An error occurred with Bedrock: {str(e)}"}

def generate_azure_questions(content):
    logger.info("Getting QA Endpoints")
    endpoint, api_key = get_endpoint(qa_model_name, endpoints_arn)
    logger.info(f"Endpoint: {endpoint}")
        
    client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version
    )
    try:
        input_tokens = num_tokens_from_text(content, qa_model_name)
        
        response = client.chat.completions.create(
            model=qa_model_name,
            messages=[
                {"role": "system", "content": "With every prompt I send, think about what questions the text might be able to answer and return those questions. Please create many questions."},
                {"role": "user", "content": content}
            ],
            max_tokens=500,
            temperature=0.7
        )
        questions = response.choices[0].message.content.strip()
        output_tokens = num_tokens_from_text(questions, qa_model_name)
    except Exception as e:
        logger.error(f"An error occurred with Azure OpenAI: {e}", exc_info=True)
        return {"success": False, "error": f"An error occurred with Azure OpenAI: {str(e)}"}

    logger.info(f"Questions: {questions}")
    return {
        "success": True,
        "data": questions,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens
    }

def generate_openai_questions(content):
    logger.info("Getting QA Endpoints")
    endpoint, api_key = get_endpoint(qa_model_name, endpoints_arn)
    logger.info(f"Endpoint: {endpoint}")
        
    client = OpenAI(
        api_key=api_key
    )
    try:
        input_tokens = num_tokens_from_text(content, "gpt-3.5-turbo")
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "With every prompt I send, think about what questions the text might be able to answer and return those questions. Please create many questions."},
                {"role": "user", "content": content}
            ],
            max_tokens=500,
            temperature=0.7
        )
        questions = response.choices[0].message.content.strip()
        output_tokens = num_tokens_from_text(questions, "gpt-3.5-turbo")
    except Exception as e:
        logger.error(f"An error occurred with OpenAI: {e}", exc_info=True)
        return {"success": False, "error": f"An error occurred with OpenAI: {str(e)}"}

    logger.info(f"Questions: {questions}")
    return {
        "success": True,
        "data": questions,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens
    }

