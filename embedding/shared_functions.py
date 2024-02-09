from openai import AzureOpenAI
import tiktoken
import re
import json
import os
import boto3

import logging
from common.credentials import get_credentials, get_json_credetials, get_endpoint

###Local Vars Remove Before Commit
import yaml
import os
 #Function to convert YAML content to .env format and load it
def load_yaml_as_env(yaml_path):
    with open(yaml_path, 'r') as stream:
        data_loaded = yaml.safe_load(stream)

    # Convert YAML dictionary to .env format (KEY=VALUE)
    for key, value in data_loaded.items():
        os.environ[key] = str(value)

yaml_file_path = "C:\\Users\\karnsab\Desktop\\amplify-lambda-mono-repo\\var\local-var.yml"
load_yaml_as_env(yaml_file_path)
###Local Vars Remove Before Commit


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

endpoints_arn = os.environ['ENDPOINTS_ARN']
api_version    = os.environ['API_VERSION']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
keyword_model_name = os.environ['KEYWORD_MODEL_NAME']
qa_model_name = os.environ['QA_MODEL_NAME']



def clean_text(text):
    # Remove punctuation using regex
    text_without_punctuation = re.sub(r'[^\w\s]', '', text)
    # Remove extra spaces using regex
    cleaned_text = re.sub(r'\s+', ' ', text_without_punctuation)
    return cleaned_text.strip()



def get_embeddings(user_input):
    print(f"Getting Embedding Endpoints")
    endpoint, api_key = get_endpoint(embedding_model_name, endpoints_arn)
    print(f"Endpoint: {endpoint}")

    client = AzureOpenAI(
    api_key = api_key,
    azure_endpoint = endpoint,
    api_version = api_version
)
    try:
        #print(f"Getting embeddings for: {text}")
        response = client.embeddings.create(input=user_input, model=embedding_model_name)
        return response.data[0].embedding
    except Exception as e:
        print(f"An error occurred: {e}")
        # Optionally, re-raise the exception if you want it to propagate
        raise


def generate_keywords(user_input):
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
            messages=[{"role": "system", "content": "You are an assistant that helps extract keywords from a given text. You can only respone with 4 total words. Provide output as a csv"},
                     {"role": "user", "content": f"Please extract 3 keywords from the following text. Please return it in the following format --- dog cat word:\n\n{user_input}"}],
            max_tokens=10,
            temperature=0
        )
        print(response)
        raw_keywords = response.choices[0].message.content.strip()
        keywords = clean_text(raw_keywords)
        print(f"Keywords: {keywords}")
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
def generate_questions(user_input, model_name, max_tokens):

    endpoint, api_key = get_endpoint(qa_model_name, endpoints_arn)

    client = AzureOpenAI(
    api_key = api_key,
    azure_endpoint = endpoint,
    api_version = api_version
)
    try:
        response = client.completions.create(
            model=qa_model_name,
            prompt=f"Please create a list of detailed questions that could be answered by the following text:\n\n{user_input}",
            max_tokens=max_tokens,
            temperature=0.7,
        )
        questions = response.choices[0].text.strip()

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


