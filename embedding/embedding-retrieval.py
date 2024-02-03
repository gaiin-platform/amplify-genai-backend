# set up retriever function that accepts a a query, user, and/or list of keys for where claus
from openai import AzureOpenAI
import os
import json
import psycopg2
from pgvector.psycopg2 import register_vector
from common.credentials import get_credentials, get_endpoint
from common.validate import validated
import logging

pg_host = os.environ['RAG_POSTGRES_DB_READ_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
endpoints_arn = os.environ['ENDPOINTS_ARN']
endpoint, api_key = get_endpoint(embedding_model_name, endpoints_arn)
pg_password = get_credentials(rag_pg_password)


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


def get_top_similar_docs(query_embedding, current_user, src_ids=None, limit=5):
    with get_db_connection() as conn:
        # Register pgvector extension
        register_vector(conn)
        cur = conn.cursor()

        # Ensure the query_embedding is a list of floats
        assert isinstance(query_embedding, list), "Expected query_embedding to be a list of floats"
        print(f"here is the query embedding {query_embedding}")

        # Convert the query_embedding list to a PostgreSQL array literal
        embedding_literal = "[" + ",".join(map(str, query_embedding)) + "]"

        # Prepare SQL query and parameters based on whether src_ids are provided
        query_params = [current_user]
        src_clause = ""
        
        if src_ids:
            # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
            src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"
            src_clause = "AND src = ANY(%s)"
            query_params.append(src_ids_array)
        
        query_params.append(embedding_literal)
        query_params.append(limit)  # Append the limit to the query parameters

        # Create SQL query string with a placeholder for the optional src_clause and a limit
        sql_query = f"""
            SELECT content, src, locations, orig_indexes, char_index, owner_email, token_count
            FROM embeddings 
            WHERE owner_email = %s
            {src_clause}
            ORDER BY vector_embedding <=> %s 
            LIMIT %s  -- Use a placeholder for the limit
        """

        cur.execute(sql_query, query_params)
        top_docs = cur.fetchall()
        
    print(top_docs)
    return top_docs

# Rest of your code...



@validated("retrieval")
def process_input_with_retrieval(event, context, current_user, name, data):
    data = data['data']
    user_input = data['userInput']
    src_ids = data['dataSources']
    limit = data['limit']

    # Rest of your function ...
    embeddings = get_embeddings(user_input)
    print(f"This is some of my embeddings - {embeddings}")

    # Step 1: Get documents related to the user input from the database
    related_docs = get_top_similar_docs(embeddings, current_user, src_ids, limit)
    print(related_docs)

    # Return the related documents as a HTTP response
    return {"result":related_docs}
    


   






