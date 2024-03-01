# set up retriever function that accepts a a query, user, and/or list of keys for where claus

import os
import json
import psycopg2
from pgvector.psycopg2 import register_vector
from common.credentials import get_credentials, get_endpoint
from common.validate import validated
from shared_functions import generate_keywords, generate_embeddings
import logging
import boto3
from boto3.dynamodb.conditions import Key


pg_host = os.environ['RAG_POSTGRES_DB_READ_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
keyword_model_name = os.environ['KEYWORD_MODEL_NAME']
qa_model_name = os.environ['QA_MODEL_NAME']
api_version = os.environ['API_VERSION']
object_access_table = os.environ['OBJECT_ACCESS_TABLE']


pg_password = get_credentials(rag_pg_password)

def get_top_similar_qas(query_embedding, src_ids, limit=5):
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=3306
    ) as conn:
        
        # Register pgvector extension
        register_vector(conn)
        with conn.cursor() as cur:

            # Ensure the query_embedding is a list of floats
            assert isinstance(query_embedding, list), "Expected query_embedding to be a list of floats"
            #print(f"here is the query embedding {query_embedding}")

            # Convert the query_embedding list to a PostgreSQL array literal
            embedding_literal = "[" + ",".join(map(str, query_embedding)) + "]"

            # Prepare SQL query and parameters based on whether src_ids are provided
            query_params = [embedding_literal, limit]  # query_embedding is already a list of floats
            src_clause = ""

            if src_ids:
                # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
                src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"
            
                

            query_params.insert(1, src_ids_array)  # Append the limit to the query parameters
             # Append the limit to the query parameters

            # Create SQL query string with a placeholder for the optional src_clause and a limit
            sql_query = f"""
                SELECT content, src, locations, orig_indexes, char_index, owner_email, token_count, id, ((qa_vector_embedding <#> %s::vector) * -1) AS distance
                FROM embeddings 
                WHERE src = ANY(%s)  -- Use the ARRAY constructor for src_ids
                ORDER BY distance DESC  -- Order by distance for ordering  
                LIMIT %s  -- Use a placeholder for the limit
            """
            #print(f"Here is the qa sql query {sql_query}")
            cur.execute(sql_query, query_params)
            top_docs = cur.fetchall()
        
    return top_docs


def get_top_similar_docs(query_embedding, src_ids, limit=5):
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=3306
    ) as conn:
        
        # Register pgvector extension
        register_vector(conn)
        with conn.cursor() as cur:
            
            # Ensure the query_embedding is a list of floats
            assert isinstance(query_embedding, list), "Expected query_embedding to be a list of floats"

            if src_ids:
                # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
                src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"

            # Prepare the query parameters
            # Note: query_embedding is passed directly as a list of floats
            query_params = [query_embedding, src_ids_array, limit]  # src_ids should be a tuple for psycopg2 to convert it to an array

            # Create SQL query string with placeholders for parameters
            sql_query = """
                SELECT content, src, locations, orig_indexes, char_index, owner_email, token_count, id, ((vector_embedding <#> %s::vector) * -1) AS distance
                FROM embeddings 
                WHERE src = ANY(%s)  -- Use the ARRAY constructor for src_ids
                ORDER BY distance DESC  -- Order by distance for ordering  
                LIMIT %s  -- Use a placeholder for the limit
            """
            #print(f"Here is the top match sql query {sql_query}")
            cur.execute(sql_query, query_params)  # Pass the query parameters to the execute method
            top_docs = cur.fetchall()
        
    return top_docs

def get_top_similar_ft_docs(input_keywords, src_ids, limit=5):
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=3306
    ) as conn:
        with conn.cursor() as cur:
            # Prepare SQL query and parameters based on whether src_ids are provided
            src_ids_array = []
            query_params = [input_keywords, input_keywords, input_keywords]
            
            if src_ids:
                # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
                src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"
           
                query_params.append(src_ids_array)            
            # Append the limit to the query parameters
            query_params.append(limit)
            
            #print(f"Here are the query params {query_params}")
            # Create SQL query string with a placeholder for the optional src_clause and a limit
            sql_query = f"""
                SELECT content, src, locations, orig_indexes, char_index, owner_email, token_count, id,
                    ts_rank_cd(to_tsvector('english', content), to_tsquery('english', replace(%s,' ','|'))) AS text_rank
                    
                FROM embeddings 
                WHERE ts_rank_cd(to_tsvector('english', content), to_tsquery('english', replace(%s,' ','|'))) > 0
                AND to_tsvector('english', content) @@ to_tsquery('english', replace(%s,' ','|'))
                AND src = ANY(%s)
                ORDER BY text_rank DESC  -- Order by text rank for ordering
                LIMIT %s  -- Use a placeholder for the limit
            """
            #print(f"Here is the ft sql query {sql_query}")
            # Execute the query with the correct number of parameters
            cur.execute(sql_query, query_params)
            top_ft_docs = cur.fetchall()
        return top_ft_docs
    


def classify_src_ids_by_access(raw_src_ids, current_user):
    accessible_src_ids = []
    access_denied_src_ids = []

    # Define the permission levels that grant access
    permission_levels = ['read', 'write', 'owner']

    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(object_access_table)

    try:
        # Iterate over each src_id and perform a query
        for src_id in raw_src_ids:
            response = table.query(
                KeyConditionExpression=Key('object_id').eq(src_id) & Key('principal_id').eq(current_user)
            )

            # Check if the response has any items with the required permission levels
            items_with_access = [item for item in response.get('Items', []) if item['permission_level'] in permission_levels]

            # Classify the src_id based on whether it has accessible items
            if items_with_access:
                accessible_src_ids.append(src_id)
            else:
                access_denied_src_ids.append(src_id)

    except Exception as e:
        logging.error(f"An error occurred while classifying src_ids by access: {e}")
        # Depending on the use case, you may want to handle the error differently
        # Here we're considering all src_ids as denied if there's an error
        access_denied_src_ids.extend(raw_src_ids)
    print(f"Accessible src_ids: {accessible_src_ids}, Access denied src_ids: {access_denied_src_ids}")
    return accessible_src_ids, access_denied_src_ids

 
@validated("dual-retrieval")
def process_input_with_dual_retrieval(event, context, current_user, name, data):
    data = data['data']
    content = data['userInput']
    raw_src_ids = data['dataSources']
    #src_ids = data['dataSources']
    limit = data['limit']

    accessible_src_ids, access_denied_src_ids = classify_src_ids_by_access(raw_src_ids, current_user)
    src_ids = accessible_src_ids
    #src_ids_message = f"Accessible src_ids: {accessible_src_ids}, Access denied src_ids: {access_denied_src_ids}"

    # Rest of your function ...
    embeddings = generate_embeddings(content)
    response = generate_keywords(content)
    if response["statusCode"] == 200:
        input_keywords = response["body"]["keywords"]
    else:
        # If there was an error, you can handle it accordingly.
        error = response["body"]["error"]
        print(f"Error occurred: {error}") 



    # Step 1: Get documents related to the user input from the database
    related_docs = get_top_similar_docs(embeddings, src_ids, limit)
    #print(f"Here are the related docs {related_docs}")
    related_qas = get_top_similar_qas(embeddings, src_ids, limit)
    related_docs.extend(related_qas)
    related_ft_docs = get_top_similar_ft_docs(input_keywords, src_ids, limit)
    related_docs.extend(related_ft_docs)
    #related_docs.extend(src_ids_message)

    # Return the related documents as a HTTP response
    return {"result":related_docs}
    


   






