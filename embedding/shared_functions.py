from openai import AzureOpenAI
import tiktoken
import re
import json
import os
import boto3
import logging
import os
import uuid
from datetime import datetime
from botocore.exceptions import ClientError
from common.credentials import get_credentials, get_json_credetials, get_endpoint


logger = logging.getLogger()
logger.setLevel(logging.INFO)

endpoints_arn = os.environ['LLM_ENDPOINTS_SECRETS_NAME_ARN']
api_version    = os.environ['API_VERSION']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
keyword_model_name = os.environ['KEYWORD_MODEL_NAME']
qa_model_name = os.environ['QA_MODEL_NAME']



  #Get embedding token count from tiktoken
def num_tokens_from_text(content, embedding_model_name):
    encoding = tiktoken.encoding_for_model(embedding_model_name)
    num_tokens = len(encoding.encode(content))
    return num_tokens


def clean_text(text):
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



def generate_embeddings(content):
    logger.info("Getting Embedding Endpoints")
    endpoint, api_key = get_endpoint(embedding_model_name, endpoints_arn)
    logger.info(f"Endpoint: {endpoint}")

    client = AzureOpenAI(
    api_key = api_key,
    azure_endpoint = endpoint,
    api_version = api_version
    )
    try:
        response = client.embeddings.create(input=content, model=embedding_model_name)
        logger.info(f"Embedding: {response.data[0].embedding}")
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        raise


def generate_keywords(content):
    logger.info("Getting Keywords Endpoints")
    endpoint, api_key = get_endpoint(keyword_model_name, endpoints_arn)
    logger.info(f"Endpoint: {endpoint}")

    client = AzureOpenAI(
    api_key = api_key,
    azure_endpoint = endpoint,
    api_version = api_version
)
    try:
        response = client.chat.completions.create(
            model=keyword_model_name,
            messages=[{"role": "system", "content": "You are an assistant that helps extract keywords from a given text. Create a complete but concise representation Provide output in the following format ('word1 word2 word3 word4 word5') "},
                     {"role": "user", "content": f"Please extract keywords from the following text. #######:\n\n{content}"}],
            max_tokens=10,
            temperature=0
        )
      
        raw_keywords = response.choices[0].message.content.strip()
        keywords = clean_text(raw_keywords)
        logger.info(f"Keywords: {keywords}")
        return {
            "statusCode": 200,
            "body": {
                "keywords": keywords
            }
        }
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": {
                "error": f"An error occurred: {str(e)}"
            }
        }
    
def generate_questions(content):

    logger.info("Getting QA Endpoints")
    endpoint, api_key = get_endpoint(qa_model_name, endpoints_arn)
    logger.info(f"Endpoint: {endpoint}")

    client = AzureOpenAI(
    api_key = api_key,
    azure_endpoint = endpoint,
    api_version = api_version
)
    try:
        response = client.chat.completions.create(
            model=qa_model_name,
            messages=[{"role": "system", "content": "With every prompt I send, think about what questions the text might be able to answer and return those questions. Please create many questions."},
                     {"role": "user", "content": content}],
            max_tokens=500,
            temperature=.7
        )
        questions = response.choices[0].message.content.strip()
        logger.info(f"Questions: {questions}")

        return {
            "statusCode": 200,
            "body": {
                "questions": questions
            }
        }
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": {
                "error": f"An error occurred: {str(e)}"
            }
        }

dynamodb = boto3.client('dynamodb')
dynamo_table_name = os.environ.get('CHAT_USAGE_DYNAMO_TABLE')

def record_usage(account,requestId, user, model, input_tokens, output_tokens, details, api_key=None):
    if not dynamo_table_name:
        logger.error("CHAT_USAGE_DYNAMO_TABLE table is not provided in the environment variables.")
        return False

    try:
        account_id = account.get('accountId', 'general_account')

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

    except ClientError as e:
        logger.error(f"Failed to record usage: {e}")
        return False

    return True




import boto3

def get_key_details(textLocationKey):
    # Initialize a session using Amazon DynamoDB
    session = boto3.Session()
    
    # Initialize DynamoDB resource
    dynamodb = session.resource('dynamodb')
    
    # Select your DynamoDB table
    table = dynamodb.Table('HASH_FILES_DYNAMO_TABLE')
    
    try:
        # Get item from the table
        response = table.get_item(
            Key={
                'textLocationKey': textLocationKey
            }
        )
        
        # Check if the item exists in the table
        if 'Item' in response:
            item = response['Item']
            apiKey = item.get('apiKey')
            account = item.get('account')
            originalCreator = item.get('originalCreator')
            return {
                'apiKey': apiKey,
                'account': account,
                'originalCreator': originalCreator
            }
        else:
            return None
    
    except Exception as e:
        print(f"Error retrieving item: {e}")
        return None

# Replace 'your_text_location_key' with the actual key value

