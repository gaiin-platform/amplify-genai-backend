from openai import AzureOpenAI
import tiktoken
import re
import json
import os
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
import logging
import uuid
from datetime import datetime
from common.credentials import get_credentials, get_json_credetials, get_endpoint


logger = logging.getLogger()
logger.setLevel(logging.INFO)

endpoints_arn = os.environ['LLM_ENDPOINTS_SECRETS_NAME_ARN']
api_version    = os.environ['API_VERSION']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
keyword_model_name = os.environ['KEYWORD_MODEL_NAME']
qa_model_name = os.environ['QA_MODEL_NAME']
hash_files_dynamo_table = os.environ['HASH_FILES_DYNAMO_TABLE']



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
    try:
        # Remove non-ASCII characters
        text = text.encode('ascii', 'ignore').decode('ascii')
        # Remove punctuation using regex
        text_without_punctuation = re.sub(r'[^\w\s]', '', text)
        # Remove extra spaces using regex
        cleaned_text = re.sub(r'\s+', ' ', text_without_punctuation)
        return (success: True, data: cleaned_text.strip())
    except Exception as e:
        return (success: False, error: f"An error occurred: {str(e)}")


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
        embeddings = response.data[0].embedding
        return {"success":True, "data": embeddings}
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        return {"success":False, "error": f"An error occurred: {str(e)}"}
        

#Currently Not In Use but leaving in case we go back to it. 
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
            "success": True,
            "data": questions
        }
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        return {"success": False, "error": f"An error occurred: {str(e)}"}



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


