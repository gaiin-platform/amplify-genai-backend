# set up retriever function that accepts a a query, user, and/or list of keys for where claus

import os
import json
import psycopg2
from pgvector.psycopg2 import register_vector
from common.credentials import get_credentials, get_endpoint
from common.validate import validated
from shared_functions import generate_keywords, generate_embeddings
import logging



pg_host = os.environ['RAG_POSTGRES_DB_READ_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
keyword_model_name = os.environ['KEYWORD_MODEL_NAME']
qa_model_name = os.environ['QA_MODEL_NAME']
api_version = os.environ['API_VERSION']

pg_password = get_credentials(rag_pg_password)

def get_top_similar_qas(query_embedding, current_user, src_ids=None, limit=5):
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
            query_params = [current_user]
            src_clause = ""

            if src_ids:
                # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
                src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"
                
                

            query_params.append(embedding_literal)
            query_params.append(limit)  # Append the limit to the query parameters

            # Create SQL query string with a placeholder for the optional src_clause and a limit
            sql_query = f"""
                SELECT content, src, locations, orig_indexes, char_index, owner_email, token_count, id, ((qa_vector_embedding <#> '{embedding_literal}') * -1) AS distance
                FROM embeddings 
                WHERE owner_email = '{current_user}' and ((qa_vector_embedding <#> '{embedding_literal}') * -1) > .69
                AND src = ANY('{src_ids_array}')
                ORDER BY distance DESC  -- Order by distance for ordering  
                LIMIT {limit}  -- Use a placeholder for the limit
            """

            cur.execute(sql_query)
            top_docs = cur.fetchall()
        
    return top_docs


def get_top_similar_docs(query_embedding, current_user, src_ids=None, limit=5):
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
            query_params = [current_user]
            src_clause = ""

            if src_ids:
                # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
                src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"
                
                

            query_params.append(embedding_literal)
            query_params.append(limit)  # Append the limit to the query parameters

            # Create SQL query string with a placeholder for the optional src_clause and a limit
            sql_query = f"""
                SELECT content, src, locations, orig_indexes, char_index, owner_email, token_count, id, ((vector_embedding <#> '{embedding_literal}') * -1) AS distance
                FROM embeddings 
                WHERE owner_email = '{current_user}' and ((vector_embedding <#> '{embedding_literal}') * -1) > .69
                AND src = ANY('{src_ids_array}')
                ORDER BY distance DESC  -- Order by distance for ordering  
                LIMIT {limit}  -- Use a placeholder for the limit
            """

            cur.execute(sql_query)
            top_docs = cur.fetchall()
        
   
    return top_docs

def get_top_similar_ft_docs(input_keywords, current_user, src_ids=None, limit=5):
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=3306
    ) as conn:
        with conn.cursor() as cur:
            # Prepare SQL query and parameters based on whether src_ids are provided
            
            query_params = [input_keywords, current_user]
            
            query_params.append(input_keywords)
            
            if src_ids:
                # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
                src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"
           
                query_params.append(src_ids_array)            
            # Append the limit to the query parameters
            query_params.append(limit)
            
            print(f"Here are the query params {query_params}")
            # Create SQL query string with a placeholder for the optional src_clause and a limit
            sql_query = f"""
                SELECT content, src, locations, orig_indexes, char_index, owner_email, token_count, id,
                    ts_rank_cd(to_tsvector('english', content), to_tsquery('english', replace('{input_keywords}',' ','|'))) AS text_rank
                    
                FROM embeddings 
                WHERE owner_email = '{current_user}' and ts_rank_cd(to_tsvector('english', content), to_tsquery('english', replace('{input_keywords}',' ','|'))) > .6
                AND to_tsvector('english', content) @@ to_tsquery('english', replace('{input_keywords}',' ','|'))
                AND src = ANY('{src_ids_array}')
                ORDER BY text_rank DESC  -- Order by text rank for ordering
                LIMIT {limit}  -- Use a placeholder for the limit
            """
            
            # Execute the query with the correct number of parameters
            cur.execute(sql_query)
            top_ft_docs = cur.fetchall()
        return top_ft_docs




@validated("dual-retrieval")
def process_input_with_dual_retrieval(event, context, current_user, name, data):
    data = data['data']
    content = data['userInput']
    src_ids = data['dataSources']
    limit = data['limit']

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
    related_docs = get_top_similar_docs(embeddings, current_user, src_ids, limit)
    related_qas = get_top_similar_qas(embeddings, current_user, src_ids, limit)
    related_docs.extend(related_qas)
    related_ft_docs = get_top_similar_ft_docs(input_keywords, current_user, src_ids, limit)
    related_docs.extend(related_ft_docs)

    # Return the related documents as a HTTP response
    return {"result":related_docs}
    


   






