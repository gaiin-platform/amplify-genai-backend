from openai import AzureOpenAI
import tiktoken
import psycopg2
from psycopg2.extras import Json
import json
import os
import boto3
import smtplib
from email.message import EmailMessage
import logging
from common.credentials import get_credentials, get_json_credetials, get_endpoint
from botocore.exceptions import ClientError
from shared_functions import num_tokens_from_text, generate_embeddings, generate_questions
import urllib
sqs = boto3.client('sqs')


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

pg_host = os.environ['RAG_POSTGRES_DB_WRITE_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
sender_email = os.environ['SENDER_EMAIL']
endpoints_arn = os.environ['LLM_ENDPOINTS_SECRETS_NAME_ARN']
embedding_progress_table = os.environ['EMBEDDING_PROGRESS_TABLE']
embedding_chunks_index_queue = os.environ['EMBEDDING_CHUNKS_INDEX_QUEUE'] 


pg_password = get_credentials(rag_pg_password)



def trim_src(src):
    # Split the keyname by '.json'
    parts = src.split('.json')
    # Rejoin the first part with '.json' if there are any parts after splitting
    trimmed_src = parts[0] + '.json' if len(parts) > 1 else src
    return trimmed_src



def update_dynamodb_status(table, object_id, chunk_index, total_chunks, status):

    try:
        # Attempt to get the item
        response = table.get_item(Key={'object_id': object_id})
        item = response.get('Item')

        if item:
            # The item exists, update it
            response = table.update_item(
                Key={'object_id': object_id},
                UpdateExpression="SET #data.#chunkIndex = :chunkIndex, #data.#totalChunks = :totalChunks, #data.#status = :status",
                ExpressionAttributeNames={
                    "#data": "data",
                    "#chunkIndex": "chunkIndex",
                    "#totalChunks": "totalChunks",
                    "#status": "status"
                },
                ExpressionAttributeValues={
                    ":chunkIndex": chunk_index,
                    ":totalChunks": total_chunks,
                    ":status": status
                },
                ReturnValues="UPDATED_NEW"
            )
            logging.info("Item updated successfully.")
        else:
            # The item does not exist, create it
            response = table.put_item(
                Item={
                    'object_id': object_id,
                    'data': {
                        'chunkIndex': chunk_index,
                        'totalChunks': total_chunks,
                        'status': status
                    }
                }
            )
            logging.info("Item created successfully.")

    except ClientError as e:
        logging.error("Failed to create or update item in DynamoDB table.")
        logging.error(e)
        raise






#initially set db_connection to none/closed 
db_connection = None


# Function to establish a database connection
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
        # Extract bucket name and file key from the S3 event
        #bucket_name = event['Records'][0]['s3']['bucket']['name']
        #url_encoded_key = event['Records'][0]['s3']['object']['key']
        print(f"Processing message: {record}")
        # Assuming the message body is a JSON string, parse it
        s3_info = json.loads(record['body'])
        print(f"Message body: {s3_info}")
        s3_info = s3_info["s3"]

        # Get the bucket and object key from the event
        print(f"Getting text from {s3_info['object']['key']}")
        bucket_name = s3_info['bucket']['name']
        url_encoded_key = s3_info['object']['key']

        #Print the bucket name and key for debugging purposes
        print(f"url_key={url_encoded_key}")

        #url decode the key
        object_key = urllib.parse.unquote(url_encoded_key)

        #Print the bucket name and key for debugging purposes
        print(f"bucket = {bucket_name} and key = {object_key}")


        # Create an S3 client
        s3_client = boto3.client('s3')

        db_connection = None

        try:
            # Get the object from the S3 bucket
            response = s3_client.get_object(Bucket=bucket_name, Key=object_key)

            # Read the content of the object
            data = json.loads(response['Body'].read().decode('utf-8'))

            # Get or establish a database connection
            db_connection = get_db_connection()

            # Call the embed_chunks function with the JSON data
            success, src = embed_chunks(data, embedding_progress_table, db_connection)

            # If the extraction process was successful, send a completion email
            if success:
                print(f"Embedding process completed successfully for {src}.")

                receipt_handle = record['receiptHandle']
                print(f"Deleting message {receipt_handle} from queue")
                
                # Delete received message from queue
                sqs.delete_message(
                    QueueUrl=embedding_chunks_index_queue,
                    ReceiptHandle=receipt_handle
                )
                print(f"Deleted message {record['messageId']} from queue")

            else:
                print(f"An error occurred during the embedding process for {src}.")

                db_connection.close()

            return {
                'statusCode': 200,
                'body': json.dumps('Embedding process completed successfully.')
            }
        except Exception as e:
            logging.exception(f"Error processing SQS message: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps('Error processing SQS message.')
            }
        finally:
            # Ensure the database connection is closed
            if db_connection is not None:
                db_connection.close()
            logging.info("Database connection closed.")    


def embed_chunks(data, embedding_progress_table, db_connection):
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(embedding_progress_table)
   
    src = None
    try:
        # Extract the 'chunks' list from the JSON data
        chunks = data.get('chunks', [])
        src = data.get('src', '')
        embedding_index = 0

        trimmed_src = trim_src(src)
        # Get the total number of chunks
        total_chunks = len(chunks)
        print(f"Total chunks: {total_chunks}")
        # Update the DynamoDB table with the initial status
        update_dynamodb_status(table, trimmed_src, embedding_index, total_chunks, "embedding")        
        
        # Create a cursor using the existing database connection
        with db_connection.cursor() as cursor:
    
            db_connection.commit()
            # Extract the 'content' field from each chunk
            for chunk_index, chunk in enumerate(chunks, start=1):  # Start enumeration at 1
                try:
                    content = chunk['content']
                    locations = chunk['locations']
                    orig_indexes = chunk['indexes']
                    char_index = chunk['char_index']
                    embedding_index += 1

                   
                    
                    #Print the current number and total chunks
                    print(f"Processing chunk {chunk_index} of {total_chunks}")

                    # Update the DynamoDB table with the current chunk index
                    update_dynamodb_status(table, trimmed_src, chunk_index, total_chunks, "embedding")

                    vector_embedding = generate_embeddings(content)

                    response = generate_questions(content)
                    if response["statusCode"] == 200:
                        qa_summary = response["body"]["questions"]

                    else:
                        # If there was an error, you can handle it accordingly.
                        error = response["body"]["error"]
                        print(f"Error occurred: {error}")
                    qa_vector_embedding = generate_embeddings(content=qa_summary)


                    # Calculate token count for the content
                    vector_token_count = num_tokens_from_text(content, embedding_model_name)
                    qa_summary_token_count = num_tokens_from_text(qa_summary, embedding_model_name)
                    token_count = vector_token_count + qa_summary_token_count


                    # Insert data into the database
                    insert_chunk_data_to_db(src, locations, orig_indexes, char_index, token_count, embedding_index, content, vector_embedding, qa_vector_embedding, cursor)
                    ()
                    # Commit the transaction
                    db_connection.commit()
                except Exception as e:
                    logging.error(f"An error occurred embedding chunk index: {chunk_index}")
                    logging.error(f"An error occurred during the embedding process: {e}")
                    update_dynamodb_status(table, trimmed_src, chunk_index, total_chunks, "failed")
                    raise

        # After all chunks are processed, update the status to 'complete'
        update_dynamodb_status(table, trimmed_src, total_chunks, total_chunks, "complete")        
        
        return True, src  
    
    except Exception as e:
        logging.exception("An error occurred during the embed_chunks execution.")
        update_dynamodb_status(table, trimmed_src, embedding_index, total_chunks, "failed")
        db_connection.rollback()
        return False, src


