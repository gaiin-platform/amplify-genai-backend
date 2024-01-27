# set up retriever function that accepts a a query, user, and/or list of keys for where claus
from openai import AzureOpenAI
import os
import psycopg2
import array
from pgvector.psycopg2 import register_vector
from core.credentials import get_credentials, get_endpoint
import logging
#from random import ranbdits

from dotenv import load_dotenv
import yaml

# Function to convert YAML content to .env format and load it
def load_yaml_as_env(yaml_path):
    with open(yaml_path, 'r') as stream:
        data_loaded = yaml.safe_load(stream)

    # Convert YAML dictionary to .env format (KEY=VALUE)
    for key, value in data_loaded.items():
        os.environ[key] = str(value)
yaml_file_path = "C:\\Users\\karnsab\Desktop\\amplify-lambda-mono-repo\\var\local-var.yml"
load_yaml_as_env(yaml_file_path)



dotenv_path = os.path.join(os.path.dirname(__file__), '.*')
pg_host = os.environ['RAG_POSTGRES_DB_WRITE_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
chat_model_name = os.environ['CHAT_MODEL_NAME']
endpoints_arn = os.environ['ENDPOINTS_ARN']

embedding_endpoint, embedding_api_key = get_endpoint(chat_model_name, endpoints_arn)
print (embedding_endpoint)
pg_password = get_credentials(rag_pg_password)

chat_endpoint, chat_api_key = get_endpoint(embedding_model_name, endpoints_arn)

embedding_client = AzureOpenAI(
    api_key = embedding_api_key,
    azure_endpoint = embedding_endpoint,
    api_version = "2023-05-15"
)
chat_client = AzureOpenAI(
    api_key = chat_api_key,
    azure_endpoint = chat_endpoint,
    api_version = "2023-05-15"
)
db_connection = None

def get_db_connection():
    global db_connection
    if db_connection is None or db_connection.closed:
        try:
            db_connection = psycopg2.connect(
                host=pg_host,
                database=pg_database,
                user=pg_user,
                password=pg_password,
                port=3306
            )
            logging.info("Database connection established.")
        except psycopg2.Error as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise
    return db_connection

def get_embeddings(text):
    try:
        response = embedding_client.embeddings.create(input=text, model="text-embedding-ada-002")
        return response.data[0].embedding
    except Exception as e:
        print(f"An error occurred: {e}")
        # Optionally, re-raise the exception if you want it to propagate
        raise

def get_completion_from_messages(messages, model="gpt-3.5-turbo-0613", temperature=0, max_tokens=1000):
    # Debug: Print messages to ensure they're in the correct format
    print("Sending the following messages to OpenAI:")
    print(messages)

    try:
        response = chat_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature, 
            max_tokens=max_tokens, 
        )
        # Assuming the response structure is correct, extract the content
        return response.choices[0].message["content"]
    except Exception as e:
        print(f"An error occurred while getting chat completion: {e}")
        raise

def get_top5_similar_docs(query_embedding):
    with get_db_connection() as conn:
        # Register pgvector extension
        register_vector(conn)
        cur = conn.cursor()

        # Ensure the query_embedding is a list of floats
        assert isinstance(query_embedding, list), "Expected query_embedding to be a list of floats"

        # Convert the query_embedding list to a PostgreSQL array literal
        # This is assuming query_embedding is a list of floats
        embedding_literal = "[" + ",".join(map(str, query_embedding)) + "]"

        # Get the top 5 most similar documents using the KNN <=> operator
        cur.execute("SELECT content FROM embeddings ORDER BY vector_embedding <=> %s LIMIT 5", (embedding_literal,))
        top5_docs = cur.fetchall()
    print(top5_docs)
    return top5_docs

input = "What can you tell me about LLMs and fact rewriting"


# Function to process input with retrieval of most similar documents from the database
def process_input_with_retrieval(user_input):
    delimiter = "```"

    #Step 1: Get documents related to the user input from database
    related_docs = get_top5_similar_docs(get_embeddings(user_input))
    print(related_docs)

    # Step 2: Get completion from OpenAI API
    # Set system message to help set appropriate tone and context for model
    system_message = f"""
    You are a friendly chatbot. \
    You can answer questions about my data that are stored in the database. \
    You respond in a concise and credible tone. \
    """

    # Prepare messages to pass to model
    # We use a delimiter to help the model understand the where the user_input starts and ends
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": f"{delimiter}{user_input}{delimiter}"},
        {"role": "assistant", "content": f"Relevant Timescale case studies information: \n {related_docs[0][0]} \n {related_docs[1][0]} {related_docs[2][0]}"}   
    ]
    print(messages)
    final_response = get_completion_from_messages(messages)
    return final_response

response = process_input_with_retrieval(input)
print(input)
print(response)




