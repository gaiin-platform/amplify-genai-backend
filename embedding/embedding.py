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
import urllib.parse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


pg_host = os.environ['RAG_POSTGRES_DB_WRITE_ENDPOINT']
pg_user = os.environ['RAG_POSTGRES_DB_USERNAME']
pg_database = os.environ['RAG_POSTGRES_DB_NAME']
ses_secret = os.environ['SES_SECRET_NAME']
rag_pg_password = os.environ['RAG_POSTGRES_DB_SECRET']
embedding_model_name = os.environ['EMBEDDING_MODEL_NAME']
sender_email = os.environ['SENDER_EMAIL']
endpoints_arn = os.environ['LLM_ENDPOINTS_SECRETS_NAME_ARN']
user_files_table = os.environ['USER_FILES_TABLE']

pg_password = get_credentials(rag_pg_password)

ses_credentials = get_json_credetials(ses_secret)

def trim_src(src):
    # Split the keyname by '.json'
    parts = src.split('.json')
    # Rejoin the first part with '.json' if there are any parts after splitting
    trimmed_src = parts[0] + '.json' if len(parts) > 1 else src
    return trimmed_src


def update_dynamodb_status(table, trimmed_src, chunk_index, total_chunks, status):
    """
    Update the DynamoDB table with the current status of the chunk processing.
    """
    try:
        response = table.update_item(
            Key={'id': trimmed_src},
            UpdateExpression="SET #data.#rag = :rag",
            ExpressionAttributeNames={
                "#data": "data",
                "#rag": "rag"
            },
            ExpressionAttributeValues={
                ":rag": {
                    "embedded": chunk_index,  # Assuming chunk_index is an integer
                    "total": total_chunks,    # Assuming total_chunks is an integer
                    "status": status          # Assuming status is a string
                }
            },
            ReturnValues="UPDATED_NEW"
        )
        logging.info(f"DynamoDB update response: {response}")
    except ClientError as e:
        logging.error("Failed to update DynamoDB table.")
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


def send_completion_email(owner_email, src, success=True):
    # Uncomment line below to set the logging level to INFO to avoid printing debug information
    #logging.basicConfig(level=logging.INFO)
    # Define the sender and subject
    sender = sender_email
    subject = f"Document {src} embedding"
    # Define the email body
    if success:
        body_text = f"The embedding process for document {src} has completed successfully."
    else:
        body_text = f"There was an error during the embedding process for document {src}. Please try again."
    # Create email message
    message = EmailMessage()
    message.set_content(body_text)
    message['Subject'] = subject
    message['From'] = sender
    message['To'] = owner_email
    # Use the SMTP credentials to send the email
    try:
        with smtplib.SMTP(ses_credentials['SMTP_ENDPOINT'], ses_credentials['SMTP_PORT']) as smtp:
            smtp.starttls()
            smtp.login(ses_credentials['ACCESS_KEY'], ses_credentials['SECRET_ACCESS_KEY'])
            smtp.send_message(message)
            print("Email sent successfully!")
    except Exception as e:
        logging.error(f"Error sending email: {e}")
        raise e
    
    #Get embedding token count from tiktoken

    

def extract_email_from_src(src):
    # Split the src string on the forward slash and take the first part
    email_address = src.split('/')[0] if '/' in src else src
    return email_address

def insert_chunk_data_to_db(src, locations, orig_indexes, char_index, token_count, embedding_index, owner_email, content, vector_embedding, qa_vector_embedding, cursor):
    insert_query = """
    INSERT INTO embeddings (src, locations, orig_indexes, char_index, token_count, embedding_index, owner_email, content, vector_embedding, qa_vector_embedding)
    
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    try:
        cursor.execute(insert_query, (src, Json(locations), Json(orig_indexes), char_index, token_count, embedding_index, owner_email, content, vector_embedding, qa_vector_embedding))
        logging.info(f"Data inserted into the database for content: {content[:30]}...")  # Log first 30 characters of content
    except psycopg2.Error as e:
        logging.error(f"Failed to insert data into the database: {e}")
        raise

db_connection = None
# AWS Lambda handler function
def lambda_handler(event, context):
    logging.basicConfig(level=logging.INFO)
    
    # Extract bucket name and file key from the S3 event
    bucket_name = event['Records'][0]['s3']['bucket']['name']
    url_encoded_key = event['Records'][0]['s3']['object']['key']
        
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
        success, owner_email, src = embed_chunks(data, user_files_table, db_connection)
        
        # If the extraction process was successful, send a completion email
        if success and owner_email:
            print(f"Embedding process completed successfully for {src}.")
            #send_completion_email(owner_email, src, success=True)
        else:
            print(f"An error occurred during the embedding process for {src}.")
            #send_completion_email(owner_email, src, success=False)
        

            db_connection.close()
        
        return {
            'statusCode': 200,
            'body': json.dumps('Embedding process completed successfully.')
        }
    except Exception as e:
        logging.exception("An error occurred during the lambda_handler execution.")
        return {
            'statusCode': 500,
            'body': json.dumps('An error occurred during the embedding process.')
        }
    finally:
        # Ensure the database connection is closed
        if db_connection is not None:
            db_connection.close()
        logging.info("Database connection closed.")    

# Function to assign the owner permission to the user for the embedding object
def insert_into_object_access(src, owner_email, cursor):
    object_type = "embedding"
    principal_type = "user"
    permission_level = "owner"
    principal_id = owner_email
    # Correctly prepare the parameters for the query
    query_params = (src, object_type, principal_type, principal_id, permission_level)
    insert_query = """
    INSERT INTO object_access (object_id, object_type, principle_type, principal_id, permission_level)
    VALUES (%s, %s, %s, %s, %s);
    """
    try:
        # Execute the query with the correct parameters
        cursor.execute(insert_query, query_params)
        logging.info(f"Data inserted into the object_access table for src: {src}")
        print(f"Data inserted into the object_access table for src: {src}")
    except psycopg2.Error as e:
        # Log the error and re-raise the exception
        logging.error(f"Failed to insert data into the object_access table: {e}")
        print(f"Failed to insert data into the object_access table: {e}")
        raise  # Re-raise the exception to be handled by the caller  
        
# Function to extract the chunks from the JSON data and insert them into the database
def embed_chunks(data, user_files_table, db_connection):
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(user_files_table)
    owner_email = None
    src = None
    try:
        # Extract the 'chunks' list from the JSON data
        chunks = data.get('chunks', [])
        src = data.get('src', '')
        embedding_index = 0
        # Extract the owner email from the src field
        owner_email = extract_email_from_src(src)
        trimmed_src = trim_src(src)
        # Get the total number of chunks
        total_chunks = len(chunks)

        # Update the DynamoDB table with the initial status
        update_dynamodb_status(table, trimmed_src, embedding_index, total_chunks, "embedding")        
        
        # Create a cursor using the existing database connection
        with db_connection.cursor() as cursor:
            # Insert the owner permission for the embedding object
            insert_into_object_access(src, owner_email, cursor)
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
                    insert_chunk_data_to_db(src, locations, orig_indexes, char_index, token_count, embedding_index, owner_email, content, vector_embedding, qa_vector_embedding, cursor)
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
        
        # Return True and owner_email if the process completes successfully
        return True, owner_email, src  
    
    except Exception as e:
        logging.exception("An error occurred during the embed_chunks execution.")
        update_dynamodb_status(table, trimmed_src, embedding_index, total_chunks, "failed")
        db_connection.rollback()
        return False, owner_email, src


