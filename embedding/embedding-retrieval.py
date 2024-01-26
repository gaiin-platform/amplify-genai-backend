# set up retriever function that accepts a a query, user, and/or list of keys for where claus
from openai import AzureOpenAI
import os
import psycopg2
from pgvector.psycopg2 import register_vector
from core.credentials import get_credentials, get_endpoint
import logging

#For local testing
yaml_file_path = "C:\\Users\\karnsab\Desktop\\amplify-lambda-mono-repo\\var\local-var.yml"

dotenv_path = os.path.join(os.path.dirname(__file__), '.*')

from dotenv import load_dotenv
import yaml


# Function to convert YAML content to .env format and load it
def load_yaml_as_env(yaml_path):
    with open(yaml_path, 'r') as stream:
        data_loaded = yaml.safe_load(stream)

    # Convert YAML dictionary to .env format (KEY=VALUE)
    for key, value in data_loaded.items():
        os.environ[key] = str(value)

load_yaml_as_env(yaml_file_path)      

user_email = os.environ['USER_EMAIL']
#Remove to here for local testing



pg_host = os.environ['RAG_POSTGRES_DB_WRITE_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
chat_model_name = os.environ['CHAT_MODEL_NAME']
endpoints_arn = os.environ['ENDPOINTS_ARN']

endpoint, api_key = get_endpoint(embedding_model_name, endpoints_arn)

pg_password = get_credentials(rag_pg_password)

chat_endpoint, chat_api_key = get_endpoint(chat_model_name, endpoints_arn)

client = AzureOpenAI(
    api_key = api_key,
    azure_endpoint = endpoint,
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
        print(f"Getting embeddings for: {text}")
        response = client.embeddings.create(input=text, model=embedding_model_name)
        return response.data[0].embedding
    except Exception as e:
        print(f"An error occurred: {e}")
        # Optionally, re-raise the exception if you want it to propagate
        raise


def get_top5_similar_docs(query_embedding, user_email):
    with get_db_connection() as conn:
        # Register pgvector extension
        register_vector(conn)
        cur = conn.cursor()

        # Ensure the query_embedding is a list of floats
        assert isinstance(query_embedding, list), "Expected query_embedding to be a list of floats"
        print(f"here is the query embedding {query_embedding}")

        # Convert the query_embedding list to a PostgreSQL array literal
        # This is assuming query_embedding is a list of floats
        embedding_literal = "[" + ",".join(map(str, query_embedding)) + "]"

        # Get the top 5 most similar documents using the KNN <=> operator limited to logged in user
        cur.execute("""
            SELECT content 
            FROM embeddings 
            WHERE owner_email = %s
            ORDER BY vector_embedding <=> %s 
            LIMIT 5
            """, (user_email, embedding_literal,))
        top5_docs = cur.fetchall()
    print(top5_docs)
    return top5_docs



# Function to process input with retrieval of most similar documents from the database
def process_input_with_retrieval(user_input, user_email):
    delimiter = "```"

    embeddings= get_embeddings(user_input)
    print(f"This is some  of my embeddings - {embeddings}")

    #Step 1: Get documents related to the user input from database
    related_docs = get_top5_similar_docs(embeddings, user_email)
    print(related_docs)
    return related_docs
    
chat_prompt = "what can you tell me about the Wendell"
response = process_input_with_retrieval(chat_prompt, user_email)

print(chat_prompt)
print(response)




