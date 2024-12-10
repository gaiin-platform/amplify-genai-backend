
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from openai import AzureOpenAI
from openai import OpenAI
import tiktoken
import json
import re
import os
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
import logging
import uuid
from datetime import datetime
import json
from common.credentials import get_credentials, get_json_credetials, get_endpoint


logger = logging.getLogger()
logger.setLevel(logging.INFO)

endpoints_arn = os.environ['LLM_ENDPOINTS_SECRETS_NAME_ARN']
api_version = os.environ['API_VERSION']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
qa_model_name = os.environ['QA_MODEL_NAME']
hash_files_dynamo_table = os.environ['HASH_FILES_DYNAMO_TABLE']
region = os.environ['REGION']
openai_provider = os.environ['OPENAI_PROVIDER']
embedding_provider = os.environ['EMBEDDING_PROVIDER'] or os.environ['OPENAI_PROVIDER']




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
    try:
        # Remove non-ASCII characters
        text = text.encode('ascii', 'ignore').decode('ascii')
        # Remove punctuation using regex
        text_without_punctuation = re.sub(r'[^\w\s]', '', text)
        # Remove extra spaces using regex
        cleaned_text = re.sub(r'\s+', ' ', text_without_punctuation)
        return {"success": True, "data": cleaned_text.strip()}
    except Exception as e:
        return {"success": False, "error": f"An error occurred: {str(e)}"}



def generate_embeddings(content, embedding_provider= "azure"):
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

def record_usage(account, requestId, user, model, input_tokens, output_tokens, details=None, api_key=None):
    dynamodb = boto3.client('dynamodb')
    dynamo_table_name = os.environ.get('CHAT_USAGE_DYNAMO_TABLE')
    if not dynamo_table_name:
        logger.error("CHAT_USAGE_DYNAMO_TABLE table is not provided in the environment variables.")
        return False

    try:
        account_id = account

        # Initialize details if None
        if details is None:
            details = {}

        # Ensure input_tokens and output_tokens are not None and default to 0 if they are
        if input_tokens is None:
            input_tokens = 0
        if output_tokens is None:
            output_tokens = 0

        if api_key:
            details.update({'api_key': api_key})

        item = {
            'id': {'S': str(uuid.uuid4())},
            'requestId': {'S': requestId},
            'user': {'S': user},
            'time': {'S': datetime.utcnow().isoformat()},
            'accountId': {'S': account_id},
            'inputTokens': {'N': str(input_tokens)},
            'outputTokens': {'N': str(output_tokens)},
            'modelId': {'S': model},
            'details': {'M': {k: {'S': str(v)} for k, v in details.items()}}
        }

        response = dynamodb.put_item(
            TableName=dynamo_table_name,
            Item=item
        )
        
        # Check the HTTPStatusCode and log accordingly
        status_code = response['ResponseMetadata']['HTTPStatusCode']
        if status_code == 200:
            text_location_key = details.get('textLocationKey', 'unknown')
            account = details.get('account', account)
            original_creator = details.get('originalCreator', 'unknown')
            logger.info(f"Token Usage recorded for embedding source {text_location_key}, to {account} of {original_creator}.")
        else:
            logger.error(f"Failed to record usage: {response}")
    
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)

   


def get_key_details(textLocationKey):
    # Initialize a session using Amazon DynamoDB
    session = boto3.Session()
    
    # Initialize DynamoDB resource
    dynamodb = session.resource('dynamodb', region_name='us-east-1')
    
    # Select your DynamoDB table
    table = dynamodb.Table(hash_files_dynamo_table)
    
    try:
        # Query items from the table based on textLocationKey
        response = table.query(                        
            IndexName='TextLocationIndex',  # Specify the GSI name here
            KeyConditionExpression=Key('textLocationKey').eq(textLocationKey)
        )
        print(f"Response: {response}")
        
        # Check if the items exist in the table
        if 'Items' in response and response['Items']:
            # Filter items to ensure createdAt is present and valid
            valid_items = [item for item in response['Items'] if 'createdAt' in item and item['createdAt']]
            
            # If there are no valid items, return None
            if not valid_items:
                return None
            
            # Sort valid items by createdAt in descending order
            sorted_items = sorted(valid_items, key=lambda x: x['createdAt'], reverse=True)
            most_recent_item = sorted_items[0]

            if most_recent_item.get('apiKey'):
                logging.info("Fetched apiKey: ********")
            else: 
                logging.info(f"Fetched apiKey has no value")
            
        
            logging.info(f"Fetched account: {most_recent_item.get('account')}")
            logging.info(f"Fetched originalCreator: {most_recent_item.get('originalCreator')}")
            
            apiKey = most_recent_item.get('apiKey')
            account = most_recent_item.get('account')
            originalCreator = most_recent_item.get('originalCreator')

            

            return {
                'apiKey': apiKey,
                'account': account,
                'originalCreator': originalCreator,

            }
            
        else:
            return None
    
    except Exception as e:
        print(f"Error retrieving item: {e}")
        return None


