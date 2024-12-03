
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

# set up retriever function that accepts a a query, user, and/or list of keys for where claus

import os
import psycopg2
from pgvector.psycopg2 import register_vector
from common.credentials import get_credentials
from common.validate import validated
from shared_functions import generate_embeddings
import logging
import boto3
from boto3.dynamodb.conditions import Key

# Configure Logging 
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('embedding_dual_retrieval')

pg_host = os.environ['RAG_POSTGRES_DB_READ_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
embedding_provider = os.environ['EMBEDDING_PROVIDER']
embedding_provider = os.environ['EMBEDDING_PROVIDER']
qa_model_name = os.environ['QA_MODEL_NAME']
api_version = os.environ['API_VERSION']
object_access_table = os.environ['OBJECT_ACCESS_TABLE']
groups_table = os.environ['GROUPS_DYNAMO_TABLE']
embedding_provider = os.environ['EMBEDDING_PROVIDER']

# Define the permission levels that grant access
permission_levels = ['read', 'write', 'owner']

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
                SELECT content, src, locations, orig_indexes, char_index, token_count, id, ((qa_vector_embedding <#> %s::vector) * -1) AS distance
                FROM embeddings 
                WHERE src = ANY(%s)  -- Use the ARRAY constructor for src_ids
                ORDER BY distance DESC  -- Order by distance for ordering  
                LIMIT %s  -- Use a placeholder for the limit
            """
            logger.info(f"Executing QA SQL query: {sql_query}")
            try:
                cur.execute(sql_query, query_params)
                top_docs = cur.fetchall()
                logger.info(f"Top QA docs retrieved: {top_docs}")
            except Exception as e:
                logger.error(f"An error occurred while fetching top similar QAs: {e}", exc_info=True)
                raise
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
            
            #print(f"Here is the query embedding {query_embedding}")
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
                SELECT content, src, locations, orig_indexes, char_index, token_count, id, ((vector_embedding <#> %s::vector) * -1) AS distance
                FROM embeddings 
                WHERE src = ANY(%s)  -- Use the ARRAY constructor for src_ids
                ORDER BY distance DESC  -- Order by distance for ordering  
                LIMIT %s  -- Use a placeholder for the limit
            """
            logger.info(f"Executing Top Similar SQL query: {sql_query}")
            try:
                cur.execute(sql_query, query_params)
                top_docs = cur.fetchall()
                logger.info(f"Top similar docs retrieved: {top_docs}")
            except Exception as e:
                logger.error(f"An error occurred while fetching top similar docs: {e}", exc_info=True)
                raise
    return top_docs

    


def classify_src_ids_by_access(raw_src_ids, current_user):
    accessible_src_ids = []
    access_denied_src_ids = []

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
        logger.info(f"Accessible src_ids: {accessible_src_ids}, Access denied src_ids: {access_denied_src_ids}")        

    except Exception as e:
        logging.error(f"An error occurred while classifying src_ids by access: {e}")
        # Depending on the use case, you may want to handle the error differently
        # Here we're considering all src_ids as denied if there's an error
        access_denied_src_ids.extend(raw_src_ids)
    print(f"Accessible src_ids: {accessible_src_ids}, Access denied src_ids: {access_denied_src_ids}")
    return accessible_src_ids, access_denied_src_ids

# groupId is the key : list of globals is the value
def classify_group_src_ids_by_access(raw_group_src_ids, current_user): 
    accessible_src_ids = []
    access_denied_src_ids = []

    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource('dynamodb')
    group_table = dynamodb.Table(groups_table)
    obj_access_table = dynamodb.Table(object_access_table)
    # if user meets the following criteria for a group then all datasources are approved 
    # prereq groupId has perms 
    # 1. group is public 
    # 2. user is a member of the group 
     # Iterate over each groupId and associated dataSourceIds
    for groupId, dataSourceIds in raw_group_src_ids.items():
        print("Checking perms for GroupId: ", groupId, "\nDS Ids: ", dataSourceIds)
        try:
            # ensure the groupId has perms to the ds 
            accessible_to_group_ds_ids = []
            for src_id in dataSourceIds:
                response = obj_access_table.query(
                    KeyConditionExpression=Key('object_id').eq(src_id) & Key('principal_id').eq(groupId)
                )
                print(response.get('Items', []))
                # Check if the response has any items with the required permission levels
                items_with_access = [item for item in response.get('Items', []) if item['permission_level'] in permission_levels]
                print("i with access ", items_with_access)

                if items_with_access:
                    accessible_to_group_ds_ids.append(src_id)
                else:
                    accessible_to_group_ds_ids.append(src_id)
            
            denied_ids = set(dataSourceIds) - set(accessible_to_group_ds_ids)
            access_denied_src_ids.extend(denied_ids)

            print(f"group has access to the following ids: {accessible_to_group_ds_ids}\n Group was denied access to the following ids: {denied_ids}")
            # ensure user is a part of the group 
            response = group_table.get_item(Key={'group_id': groupId})
            # Check if the item was found
            if 'Item' in response:
                item = response['Item']
                # Check if the group is public or if the user is in the members
                if item.get('isPublic', False) or current_user in item.get('members', {}).keys() or current_user in item.get('systemUsers', []):
                    accessible_src_ids.extend(accessible_to_group_ds_ids)
                else:
                    access_denied_src_ids.extend(accessible_to_group_ds_ids)
            else:
                # If no item found for the groupId, assume access denied
                access_denied_src_ids.extend(accessible_to_group_ds_ids)

        except Exception as e:
            logging.error(f"An error occurred while processing groupId {groupId}: {e}")
            # In case of an error, consider the current groupId's src_ids as denied
            access_denied_src_ids.extend(dataSourceIds)

    print(f"Group Accessible src_ids: {accessible_src_ids}, Group Access denied src_ids: {access_denied_src_ids}")
    return accessible_src_ids, access_denied_src_ids


@validated("dual-retrieval")
def process_input_with_dual_retrieval(event, context, current_user, name, data):
    data = data['data']
    content = data['userInput']
    raw_src_ids = data['dataSources']
    raw_group_src_ids = data.get('groupDataSources', {})
    limit = data.get('limit', 10)

    accessible_src_ids, access_denied_src_ids = classify_src_ids_by_access(raw_src_ids, current_user)
    group_accessible_src_ids, group_access_denied_src_ids = classify_group_src_ids_by_access(raw_group_src_ids, current_user)

    src_ids = accessible_src_ids + group_accessible_src_ids

    response_embeddings = generate_embeddings(content, embedding_provider)

    if response_embeddings["success"]:
        embeddings = response_embeddings["data"]
        token_count = response_embeddings["token_count"]
        print(f"Here are the token count {token_count}")
    else:
        error = response_embeddings["error"]
        print(f"Error occurred: {error}")
        return {"error": error}


    # Step 1: Get documents related to the user input from the database
    related_docs = get_top_similar_docs(embeddings, src_ids, limit)

    related_qas = get_top_similar_qas(embeddings, src_ids, limit)
    related_docs.extend(related_qas)
    
    print(f"Here are the related docs {related_docs}")

    return {"result":related_docs}
    


   






