from openai import AzureOpenAI
import tiktoken
import re
import json
import os
import boto3

import logging
from common.credentials import get_credentials, get_json_credetials, get_endpoint


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

endpoints_arn = os.environ['ENDPOINTS_ARN']
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
    print(f"Getting Embedding Endpoints")
    endpoint, api_key = get_endpoint(embedding_model_name, endpoints_arn)
    print(f"Endpoint: {endpoint}")

    client = AzureOpenAI(
    api_key = api_key,
    azure_endpoint = endpoint,
    api_version = api_version
)
    try:
        #print(f"Getting embeddings for: {content}")
        response = client.embeddings.create(input=content, model=embedding_model_name)
        return response.data[0].embedding
    except Exception as e:
        print(f"An error occurred: {e}")
        # Optionally, re-raise the exception if you want it to propagate
        raise


def generate_keywords(content):
    print(f"Getting Keywords Endpoints")
    endpoint, api_key = get_endpoint(keyword_model_name, endpoints_arn)
    print(f"Endpoint: {endpoint}")

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
        #print(f"Keywords: {keywords}")
        return {
            "statusCode": 200,
            "body": {
                "keywords": keywords
            }
        }


    except Exception as e:
        # Handle other errors
        return {
            "statusCode": 500,
            "body": {
                "error": f"An error occurred: {str(e)}"
            }
        }
def generate_questions(content):

    print(f"Getting QA Endpoints")
    endpoint, api_key = get_endpoint(qa_model_name, endpoints_arn)
    print(f"Endpoint: {endpoint}")

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
        #print(f"Questions: {questions}")

        return {
            "statusCode": 200,
            "body": {
                "questions": questions
            }
        }


    except Exception as e:
        # Handle other errors
        return {
            "statusCode": 500,
            "body": {
                "error": f"An error occurred: {str(e)}"
            }
        }


