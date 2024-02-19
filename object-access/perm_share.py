# set up retriever function that accepts a a query, user, and/or list of keys for where claus
from openai import AzureOpenAI
import os
import json
import psycopg2
from pgvector.psycopg2 import register_vector
from common.credentials import get_credentials, get_endpoint
from common.validate import validated
import logging

#Remove me
import yaml
import os

# Function to convert YAML content to .env format and load it
def load_yaml_as_env(yaml_path):
    with open(yaml_path, 'r') as stream:
        data_loaded = yaml.safe_load(stream)

    # Convert YAML dictionary to .env format (KEY=VALUE)
    for key, value in data_loaded.items():
        os.environ[key] = str(value)

yaml_file_path = "C:\\Users\\karnsab\Desktop\\amplify-lambda-mono-repo\\var\local-var.yml"
load_yaml_as_env(yaml_file_path)
#Remove me

pg_host = os.environ['RAG_POSTGRES_DB_READ_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
pg_password = get_credentials(rag_pg_password)

def insert_into_object_access(src_ids, principal_type, shared_email, permission_level):
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
            object_type = "embedding"
            

            insert_query = """
            INSERT INTO object_access (object_id, object_type, principle_type, principal_id, permission_level)
            VALUES (%s, %s, %s, %s, %s);
            """

            for src_id in src_ids:
                query_params = (src_id, object_type, principal_type, shared_email, permission_level)
                try:
                    # Execute the query with the correct parameters for each src_id
                    cur.execute(insert_query, query_params)
                    logging.info(f"Data inserted into the object_access table for src_id: {src_id}")
                    print(f"Data inserted into the object_access table for src_id: {src_id}")
                except psycopg2.Error as e:
                    # Log the error and re-raise the exception
                    logging.error(f"Failed to insert data into the object_access table for src_id: {src_id}: {e}")
                    print(f"Failed to insert data into the object_access table for src_id: {src_id}: {e}")
                    raise  # Re-raise the exception to be handled by the caller

        # Commit the transaction
        conn.commit()

def classify_src_ids_by_access(raw_src_ids, current_user):
    accessible_src_ids = []
    access_denied_src_ids = []

    # Define the permission levels that grant access
    permission_levels = 'owner'  # Use a list for permission_levels
    
    # Establish a connection to the PostgreSQL database
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=3306
    ) as conn:
        with conn.cursor() as cur:

            # Prepare a query to get all accessible src_ids for the user
            access_query = """
            SELECT object_id FROM object_access
            WHERE
                principal_id = %s AND
                object_id = ANY(%s) AND
                permission_level = %s;
            """

            try:
                # Execute the query with the user_email and list of src_ids
                cur.execute(access_query, (current_user, raw_src_ids, permission_levels))
                results = cur.fetchall()  # Fetch all results of the query

                # Create a set of accessible src_ids from the query results
                result_set = {row[0] for row in results}

                # Classify each src_id based on whether it's in the result_set
                for src_id in raw_src_ids:
                    if src_id in result_set:
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

@validated("share_src_ids")
def share_src_ids (event, context, current_user, name, data):
    data = data['data']
    raw_src_ids = data['dataSources']
    shared_email = data['sharedEmail']
    principal_type = data['principalType']
    permission_level = data['permissionLevel']

    # Classify the src_ids by access
    accessible_src_ids, access_denied_src_ids = classify_src_ids_by_access(raw_src_ids, current_user)

    # Insert the accessible src_ids into the object_access table
    insert_into_object_access(accessible_src_ids, principal_type, shared_email, permission_level)

    # Return the accessible and access_denied src_ids
    return {
        "statusCode": 200,
        "body": {
            "sharedSrcIds": accessible_src_ids,
            "accessDeniedSrcIds": access_denied_src_ids
        }
    }

  