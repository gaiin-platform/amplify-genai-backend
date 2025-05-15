import psycopg2
from psycopg2.extras import Json
import json
import os
import boto3
import logging
import re
from botocore.exceptions import ClientError
from common.credentials import get_credentials
from common.validate import validated
from shared_functions import generate_embeddings, generate_questions, record_usage, get_key_details, preprocess_text
import urllib
from create_table import create_table
from embedding_models import get_embedding_models
import datetime
sqs = boto3.client('sqs')


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

pg_host = os.environ['RAG_POSTGRES_DB_WRITE_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']

embedding_model_name = None
qa_model_name = None
model_result = get_embedding_models()
print('Model_result', model_result)

if (model_result['success']): 
    data = model_result['data']
    embedding_model_name = data['embedding']['model_id']
    qa_model_name = data['qa']['model_id']


endpoints_arn = os.environ['LLM_ENDPOINTS_SECRETS_NAME_ARN']
embedding_progress_table = os.environ['EMBEDDING_PROGRESS_TABLE']
embedding_chunks_index_queue = os.environ['EMBEDDING_CHUNKS_INDEX_QUEUE'] 
table_name = 'embeddings'
pg_password = get_credentials(rag_pg_password)


# Add this at the top of the file as documentation

"""
Embedding Process Status Flow

The embedding process tracks status at two levels:
1. Parent Chunk Status - tracks the overall document processing status
2. Child Chunk Status - tracks the status of individual chunks

states: 
- "starting" - Initial state when document is submitted for embedding
- "processing" - Child chunk is actively being processed
- "completed" - Child chunk has been successfully processed
- "failed" - Child chunk processing encountered an error
- "terminated" - Processing has been terminated for all chunks of a document

"""


def trim_src(src):
    # Split the keyname by '.json'
    parts = src.split('.json')
    # Rejoin the first part with '.json' if there are any parts after splitting
    trimmed_src = parts[0] + '.json' if len(parts) > 1 else src
    return trimmed_src

def extract_child_chunk_number_from_src(src):
    pattern = r'.json-(\d+)'
    match = re.search(pattern, src)
    if match:
        return str(match.group(1))  # Convert the matched item to string
    else:
        raise ValueError("Number not found in the key")


def update_child_chunk_status(object_id, child_chunk, new_status, error_message=None):
    try:
        # First, verify the current status and validate the transition
        valid_transitions = {
            'starting': ['processing', 'failed'],  # Starting can go to processing or failed
            'processing': ['completed', 'failed'],  # Processing can go to completed or failed
            'completed': [],  # Completed is a terminal state
            'failed': []     # Failed is a terminal state
        }
        
        progress_table = os.environ['EMBEDDING_PROGRESS_TABLE']
        logging.info(f"Updating status of child chunk {child_chunk} for {object_id} to {new_status}")
        
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(progress_table)
        
        # Get current status first
        response = table.get_item(
            Key={'object_id': object_id},
            ProjectionExpression='#data.#childChunks.#chunkId.#status',
            ExpressionAttributeNames={
                '#data': 'data',
                '#childChunks': 'childChunks',
                '#chunkId': str(child_chunk),
                '#status': 'status'
            }
        )
        
        # Extract the current status
        current_status = None
        if 'Item' in response:
            item = response['Item']
            if 'data' in item and 'childChunks' in item['data']:
                child_chunks = item['data']['childChunks']
                if str(child_chunk) in child_chunks:
                    current_status = child_chunks[str(child_chunk)].get('status')
        
        # Validate the transition if there's a current status
        if current_status:
            if current_status in ['completed', 'failed']:
                logging.warning(f"Cannot update chunk {child_chunk} status from {current_status} to {new_status} (terminal state)")
                return  # Skip the update, already in terminal state
            
            if new_status not in valid_transitions.get(current_status, []):
                logging.warning(f"Invalid transition from {current_status} to {new_status} for chunk {child_chunk}")
                return  # Skip the invalid transition
        
        # Add timestamp for tracking processing age
        current_time = datetime.datetime.now().isoformat()
        
        # Add a version attribute to track changes
        update_expression = '''
            SET #data.#childChunks.#chunkId.#status = :new_status,
                #data.#childChunks.#chunkId.#lastUpdated = :timestamp,
                #data.#childChunks.#chunkId.#version = if_not_exists(#data.#childChunks.#chunkId.#version, :zero) + :one
        '''
        
        expression_attribute_names = {
            '#data': 'data',
            '#childChunks': 'childChunks',
            '#chunkId': str(child_chunk),
            '#status': 'status',
            '#lastUpdated': 'lastUpdated',
            '#version': 'version'
        }
        
        expression_attribute_values = {
            ':new_status': new_status,
            ':timestamp': current_time,
            ':zero': 0,
            ':one': 1,
            ':completed': 'completed',
            ':failed': 'failed'
        }
        
        # Add error message if applicable
        if error_message and new_status == 'failed':
            update_expression += ", #data.#childChunks.#chunkId.#error = :error"
            expression_attribute_names['#error'] = 'error'
            expression_attribute_values[':error'] = error_message
        
        # Define condition that prevents updating terminal states
        condition_expression = "attribute_not_exists(#data.#childChunks.#chunkId.#status) OR "
        condition_expression += "(#data.#childChunks.#chunkId.#status <> :completed AND #data.#childChunks.#chunkId.#status <> :failed)"
        
        result = table.update_item(
            Key={'object_id': object_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression=condition_expression,
            ReturnValues='UPDATED_NEW'
        )
        
        logging.info(f"Successfully updated child chunk status: {result}")
    
    except Exception as e:
        logging.error(f"Failed to update the child chunk status in DynamoDB table: {str(e)}")
        logging.exception(e)


def update_parent_chunk_status(object_id, new_status=None, error_message=None):
    """
    Update the parent chunk status.
    
    Args:
        object_id: The unique identifier for the document
        new_status: Status to set ('processing', 'completed', 'failed')
        error_message: Optional error message when status is 'failed'
    """
    dynamodb = boto3.resource('dynamodb')
    progress_table = os.environ['EMBEDDING_PROGRESS_TABLE']
    table = dynamodb.Table(progress_table)
    
    try:
        logging.info(f'Updating parent chunk status for {object_id} to {new_status}')
        
        # If no status provided, check if all chunks are complete
        if new_status is None:
            response = table.get_item(Key={'object_id': object_id})
            item = response.get('Item')
            
            if not item:
                raise ValueError(f"No item found with object_id {object_id}")
            
            child_chunks = item.get('data', {}).get('childChunks', {})
            current_status = item.get('parentChunkStatus', '')
            
            # Skip if already completed or failed
            if current_status in ['completed', 'failed']:
                logging.info(f'Parent chunk already in terminal state: {current_status}')
                return
            
            # Check if all child chunks are complete
            all_complete = all(chunk['status'] == 'completed' for chunk in child_chunks.values())
            any_failed = any(chunk['status'] == 'failed' for chunk in child_chunks.values())
            
            if any_failed:
                new_status = 'failed'
            elif all_complete:
                new_status = 'completed'
            else:
                new_status = 'processing'
        
        # Update the status with timestamp
        current_time = datetime.datetime.now().isoformat()
        
        update_expression = "SET parentChunkStatus = :status, lastUpdated = :timestamp"
        
        # Use condition expression to prevent race conditions
        condition_expression = "attribute_not_exists(parentChunkStatus) OR "
        condition_expression += "(parentChunkStatus <> :completed AND parentChunkStatus <> :failed)"
        
        expression_values = {
            ':status': new_status,
            ':timestamp': current_time,
            ':completed': 'completed',
            ':failed': 'failed'
        }
        
        # Add error message if provided
        if error_message and new_status == 'failed':
            update_expression += ", errorMessage = :error"
            expression_values[':error'] = error_message

        try:
            table.update_item(
                Key={'object_id': object_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ConditionExpression=condition_expression
            )
            logging.info(f'parentChunkStatus updated to {new_status} for {object_id}')
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logging.info(f'Parent chunk already in terminal state, not updating to {new_status}')
            else:
                raise
    except Exception as e:
        logging.error(f"Failed to update parentChunkStatus for {object_id}: {str(e)}")
        logging.exception(e)

def table_exists(cursor, table_name):
    cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);", (table_name,))
    return cursor.fetchone()[0]


#initially set db_connection to none/closed 
db_connection = None


# Function to establish a database connection
def get_db_connection():
    global db_connection
    if db_connection is None or db_connection.closed:
        admin_conn_params = {
            'dbname': pg_database,
            'user': pg_user,
            'password': pg_password,
            'host': pg_host,
            'port': 3306  # ensure the port matches the PostgreSQL port which is 5432 by default
        }
        try:
            db_connection = psycopg2.connect(
                host=pg_host,
                database=pg_database,
                user=pg_user,
                password=pg_password,
                port=3306  # ensure the port matches the PostgreSQL port which is 5432 by default
            )
            logging.info("Database connection established.")
        except psycopg2.Error as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

        # Once the database connection is established, check if the table exists
        with db_connection.cursor() as cursor:
            if not table_exists(cursor, table_name):
                logging.info(f"Table {table_name} does not exist. Attempting to create table...")
                if create_table():
                    logging.info(f"Table {table_name} created successfully.")
                else:
                    logging.error(f"Failed to create the table {table_name}.")
                    raise Exception(f"Table {table_name} creation failed.")
            else:
                logging.info(f"Table {table_name} exists.")

    # Return the database connection
    return db_connection


def insert_chunk_data_to_db(src, locations, orig_indexes, char_index, token_count, embedding_index, content, vector_embedding, qa_vector_embedding, cursor):
    insert_query = """
    INSERT INTO embeddings (src, locations, orig_indexes, char_index, token_count, embedding_index, content, vector_embedding, qa_vector_embedding)
    
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    try:
        cursor.execute(insert_query, (src, Json(locations), Json(orig_indexes), char_index, token_count, embedding_index, content, vector_embedding, qa_vector_embedding))
        logging.info(f"Data inserted into the database for content: {content[:30]}...")  # Log first 30 characters of content
    except psycopg2.Error as e:
        logging.error(f"Failed to insert data into the database: {e}")
        raise

db_connection = None
# AWS Lambda handler function
def lambda_handler(event, context):
    logging.basicConfig(level=logging.INFO)
    
    for record in event['Records']:
        print(f"Processing message: {record}")
        s3_info = json.loads(record['body'])
        print(f"Message body: {s3_info}")
        s3_info = s3_info["s3"]

        bucket_name = s3_info['bucket']['name']
        url_encoded_key = s3_info['object']['key']
        object_key = urllib.parse.unquote(url_encoded_key)
        childChunk = extract_child_chunk_number_from_src(object_key)
        trimmed_src = trim_src(urllib.parse.unquote(url_encoded_key))

        # Check if parent is already failed or completed before processing
        dynamodb = boto3.resource('dynamodb')
        progress_table = os.environ['EMBEDDING_PROGRESS_TABLE']

        should_continue = check_parent_terminal_status(trimmed_src, record)
        if should_continue:
            continue
                
        # Create an S3 client
        s3_client = boto3.client('s3')
        db_connection = None

        try:
            # Get the object from the S3 bucket
            response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
            data = json.loads(response['Body'].read().decode('utf-8'))
            src = data.get('src', '')
            trimmed_src = trim_src(src)
            
            # Mark parent as processing if not already
            update_parent_chunk_status(trimmed_src, 'processing')
            
            # Get database connection
            db_connection = get_db_connection()
            success, src, error_msg = embed_chunks(data, childChunk, embedding_progress_table, db_connection)

            if success:
                print(f"Embedding process completed successfully for {src}.")
                receipt_handle = record['receiptHandle']
                
                # Delete received message from queue
                sqs.delete_message(
                    QueueUrl=embedding_chunks_index_queue,
                    ReceiptHandle=receipt_handle
                )
                print(f"Deleted message {record['messageId']} from queue")
                
                # Update the parent chunk status
                update_parent_chunk_status(trimmed_src)  # Will auto-determine status
            else:
                print(f"An error occurred during the embedding process for {src}: {error_msg}")
                # Parent status should already be set to failed by embed_chunks
                
                # Still delete the message to prevent retries
                receipt_handle = record['receiptHandle']
                sqs.delete_message(
                    QueueUrl=embedding_chunks_index_queue,
                    ReceiptHandle=receipt_handle
                )
                
            return {
                'statusCode': 200,
                'body': json.dumps('Embedding process completed successfully.')
            }
        except Exception as e:
            logging.exception(f"Error processing SQS message: {str(e)}")
            # Mark parent as failed in case of unhandled exceptions
            if 'trimmed_src' in locals():
                update_parent_chunk_status(trimmed_src, 'failed', str(e))
            return {
                'statusCode': 500,
                'body': json.dumps('Error processing SQS message.')
            }
        finally:
            # Ensure the database connection is closed
            if db_connection is not None and not db_connection.closed:
                db_connection.close()
                logging.info("Database connection closed.")


def embed_chunks(data, childChunk, embedding_progress_table, db_connection):
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(embedding_progress_table)
    src = None
    trimmed_src = None
    error_msg = None
    
    try:
        local_chunks = data.get('chunks', [])
        src = data.get('src', '')
        trimmed_src = trim_src(src)
        childChunk = str(childChunk) 
        
        # Mark this child chunk as processing
        update_child_chunk_status(trimmed_src, childChunk, "processing")
        
        if (not embedding_model_name or not qa_model_name):
            error_msg = f"No Models Provided: embedding: {embedding_model_name}, qa: {qa_model_name}"
            logging.error(error_msg)
            update_child_chunk_status(trimmed_src, childChunk, "failed", error_msg)
            # Immediately mark parent as failed
            update_parent_chunk_status(trimmed_src, "failed", error_msg)
            return False, src, error_msg

        try:
            response = table.get_item(Key={'object_id': trimmed_src})
            item = response.get('Item')
            if item and 'data' in item:
                total_chunks = item['data'].get('totalChunks')
                logging.info(f"Processing child chunk: {childChunk} of total parent chunks: {total_chunks}")
                local_chunks_to_process = len(local_chunks)
                logging.info(f"There are {local_chunks_to_process} (max 10) within child chunk: {childChunk}")
                
                if not item['data'].get('terminated', True):
                    logging.info("The file embedding process has been terminated.")
                    return False, src, error_msg
            else:
                logging.warning("No item found in DynamoDB table.")
        except ClientError as e:
            logging.error(f"Failed to fetch item from DynamoDB table: {e}")

        logging.info(f"Processing {childChunk} of {total_chunks} (fetched from DynamoDB)")
        current_local_chunk_index = 0

        with db_connection.cursor() as cursor:
            db_connection.commit()
            for local_chunk_index, chunk in enumerate(local_chunks[current_local_chunk_index:], start=current_local_chunk_index + 1):
                try:
                    content = chunk['content']
                    locations = chunk['locations']
                    orig_indexes = chunk['indexes']
                    char_index = chunk['char_index']

                    response_clean_text = preprocess_text(content)
                    if not response_clean_text["success"]:
                        raise Exception(f"Text preprocessing failed: {response_clean_text['error']}")
                    clean_text = response_clean_text["data"]

                    response_vector_embedding = generate_embeddings(clean_text)
                    if not response_vector_embedding["success"]:
                        raise Exception(f"Vector embedding generation failed: {response_vector_embedding['error']}")
                    vector_embedding = response_vector_embedding["data"]

                    response_qa_summary = generate_questions(clean_text)
                    if not response_qa_summary["success"]:
                        raise Exception(f"QA summary generation failed: {response_qa_summary['error']}")
                    qa_summary = response_qa_summary["data"]

                    response_qa_embedding = generate_embeddings(content=qa_summary)
                    if not response_qa_embedding["success"]:
                        raise Exception(f"QA embedding generation failed: {response_qa_embedding['error']}")
                    qa_vector_embedding = response_qa_embedding["data"]

                    qa_summary_input_tokens = response_qa_summary["input_tokens"]
                    qa_summary_output_token_count = response_qa_summary["output_tokens"]
                    vector_token_count = response_vector_embedding["token_count"]
                    qa_vector_token_count = response_qa_embedding["token_count"]
                    total_vector_token_count = vector_token_count + qa_vector_token_count
                    

                    logging.info(f"Embedding local chunk index: {current_local_chunk_index}")
                    insert_chunk_data_to_db(src, locations, orig_indexes, char_index, total_vector_token_count, current_local_chunk_index, content, vector_embedding, qa_vector_embedding, cursor)

                    logging.info(f"Getting Account information for {trimmed_src}")
                    result = get_key_details(trimmed_src)
                    if result:
                        api_key = result['apiKey']
                        account = result['account']
                        user = result['originalCreator']
                        logging.info(f"Account details: retrieved for {trimmed_src}")
                        logging.info(f"Account: {account}, User: {user}, API Key: {api_key}")
                    else:   
                        logging.error(f"Failed to retrieve account details for {trimmed_src}")
                        raise Exception("Account details not found")

                    try:
                        record_usage(account, src, user, qa_model_name, api_key=api_key, input_tokens=qa_summary_input_tokens, output_tokens=None)
                        logging.info(f"Successfully recorded usage for qa_model_name input tokens. Account: {account}, User: {user}")
                    except Exception as e:
                        logging.error(f"Error recording usage for qa_model_name input tokens: {str(e)}")
                        logging.exception("Full traceback:")

                    try:
                        record_usage(account, src, user, qa_model_name, api_key=api_key, input_tokens=None, output_tokens=qa_summary_output_token_count)
                        logging.info(f"Successfully recorded usage for qa_model_name output tokens. Account: {account}, User: {user}")
                    except Exception as e:
                        logging.error(f"Error recording usage for qa_model_name output tokens: {str(e)}")
                        logging.exception("Full traceback:")

                    try:
                        record_usage(account, src, user, embedding_model_name, api_key=api_key, output_tokens=total_vector_token_count, input_tokens=None)
                        logging.info(f"Successfully recorded usage for embedding_model_name. Account: {account}, User: {user}")
                    except Exception as e:
                        logging.error(f"Error recording usage for embedding_model_name: {str(e)}")
                        logging.exception("Full traceback:")
                    
                    current_local_chunk_index += 1
                    db_connection.commit()
                
                except Exception as e:
                    error_msg = f"Error processing chunk {local_chunk_index} of {src}: {str(e)}"
                    logging.error(error_msg)
                    # Mark this child as failed
                    update_child_chunk_status(trimmed_src, childChunk, "failed", error_msg)
                    # Immediately mark parent as failed
                    update_parent_chunk_status(trimmed_src, "failed", error_msg)
                    if not db_connection.closed:
                        db_connection.rollback()
                    return False, src, error_msg

        update_child_chunk_status(trimmed_src, childChunk, "completed")
        return True, src, None

    except Exception as e:
        error_msg = f"Critical error in embed_chunks for {src}: {str(e)}"
        logging.exception(error_msg)
        if trimmed_src:
            update_child_chunk_status(trimmed_src, childChunk, "failed", error_msg)
            # Immediately mark parent as failed
            update_parent_chunk_status(trimmed_src, "failed", error_msg)
        if db_connection and not db_connection.closed:
            db_connection.rollback()
        return False, src, error_msg
    
def check_parent_terminal_status(trimmed_src, record):
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(embedding_progress_table)
    try:
            response = table.get_item(Key={'object_id': trimmed_src})
            item = response.get('Item')
            
            if item:
                parent_status = item.get('parentChunkStatus')
                if parent_status in ['failed', 'completed']:
                    logging.info(f"Parent chunk is already in terminal state: {parent_status}. Skipping processing.")
                    receipt_handle = record['receiptHandle']
                    sqs.delete_message(
                        QueueUrl=embedding_chunks_index_queue,
                        ReceiptHandle=receipt_handle
                    )
                    return True
                
                if item.get('terminated', False):
                    logging.info(f"Job for {trimmed_src} has been terminated. Skipping processing.")
                    receipt_handle = record['receiptHandle']
                    sqs.delete_message(
                        QueueUrl=embedding_chunks_index_queue,
                        ReceiptHandle=receipt_handle
                    )
                    return True
    except Exception as e:
        logging.error(f"Error checking parent status: {e}")
        return False
        


@validated(op='terminate')
def terminate_embedding(event, context, current_user, name, data):
    object_id = data['data'].get("object_key")
    dynamodb = boto3.resource('dynamodb')
    progress_table = os.environ['EMBEDDING_PROGRESS_TABLE']
    table = dynamodb.Table(progress_table)

    try:
        response = table.update_item(
            Key={'object_id': object_id},
            UpdateExpression="SET #terminated = :val",
            ExpressionAttributeNames={'#terminated': 'terminated'},
            ExpressionAttributeValues={':val': True},
            ReturnValues="UPDATED_NEW"
        )
        
        if response.get('Attributes', {}).get('terminated') is True:
            print(f"Successfully terminated object with ID: {object_id}")
            return True
        else:
            print(f"Failed to update termination status for object with ID: {object_id}")
            return False
    except Exception as e:
        print(f"Error terminating embedding for object_id {object_id}: {e}")
        return False