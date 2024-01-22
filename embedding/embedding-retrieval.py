
from openai import AzureOpenAI
from pgvector.psycopg import register_vector
import psycopg2
import os
from secrets import get_credentials, get_endpoint
import logging


pg_host = os.environ['RAG_POSTGRES_DB_WRITE_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
model_name = os.environ['MODEL_NAME']
endpoints_arn = os.environ['ENDPOINTS_ARN']

endpoint, api_key = get_endpoint(model_name, endpoints_arn)

pg_password = get_credentials(rag_pg_password)

client = AzureOpenAI(
    api_key = api_key,
    azure_endpoint = endpoint,
    api_version = "2023-05-15"
)

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