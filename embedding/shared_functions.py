
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from openai import AzureOpenAI
import tiktoken
import re
import os
import logging
from common.credentials import get_endpoint

logger = logging.getLogger()
logger.setLevel(logging.INFO)

endpoints_arn = os.environ['LLM_ENDPOINTS_SECRETS_NAME_ARN']
api_version    = os.environ['API_VERSION']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
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




