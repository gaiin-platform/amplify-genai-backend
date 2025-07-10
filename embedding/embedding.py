import psycopg2
from psycopg2.extras import Json
import json
import os
import boto3
import logging
import re
from botocore.exceptions import ClientError
from pycommon.api.credentials import get_credentials
from shared_functions import (
    generate_embeddings,
    generate_questions,
    preprocess_text,
)
import urllib
from create_table import create_table
from embedding_models import get_embedding_models
import datetime
from rag.rag_secrets import get_rag_secrets_for_document, delete_rag_secrets_for_document
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.EMBEDDING.value])

sqs = boto3.client("sqs")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

pg_host = os.environ["RAG_POSTGRES_DB_WRITE_ENDPOINT"]
pg_user = os.environ["RAG_POSTGRES_DB_USERNAME"]
pg_database = os.environ["RAG_POSTGRES_DB_NAME"]
rag_pg_password = os.environ["RAG_POSTGRES_DB_SECRET"]

embedding_model_name = None
qa_model_name = None
model_result = get_embedding_models()
print("Model_result", model_result)

if model_result["success"]:
    data = model_result["data"]
    embedding_model_name = data["embedding"]["model_id"]
    qa_model_name = data["qa"]["model_id"]


endpoints_arn = os.environ["LLM_ENDPOINTS_SECRETS_NAME_ARN"]
embedding_progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
embedding_chunks_index_queue = os.environ["EMBEDDING_CHUNKS_INDEX_QUEUE"]
table_name = "embeddings"
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
    parts = src.split(".json")
    # Rejoin the first part with '.json' if there are any parts after splitting
    trimmed_src = parts[0] + ".json" if len(parts) > 1 else src
    return trimmed_src


def extract_child_chunk_number_from_src(src):
    pattern = r".json-(\d+)"
    match = re.search(pattern, src)
    if match:
        return str(match.group(1))  # Convert the matched item to string
    else:
        raise ValueError("Number not found in the key")


def update_child_chunk_status(object_id, child_chunk, new_status, error_message=None):
    try:
        # First, verify the current status and validate the transition
        valid_transitions = {
            "starting": [
                "processing",
                "failed",
            ],  # Starting can go to processing or failed
            "processing": [
                "completed",
                "failed",
            ],  # Processing can go to completed or failed
            "completed": [],  # Completed is a terminal state
            "failed": [],  # Failed is a terminal state
        }

        progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
        logging.info(
            f"[CHILD_CHUNK_UPDATE] Attempting to update child chunk {child_chunk} for object_id '{object_id}' to status '{new_status}'"
        )

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(progress_table)

        # Get current status first
        response = table.get_item(
            Key={"object_id": object_id},
            ProjectionExpression="#data.#childChunks.#chunkId.#status",
            ExpressionAttributeNames={
                "#data": "data",
                "#childChunks": "childChunks",
                "#chunkId": str(child_chunk),
                "#status": "status",
            },
        )

        # Extract the current status
        current_status = None
        if "Item" in response:
            item = response["Item"]
            if "data" in item and "childChunks" in item["data"]:
                child_chunks = item["data"]["childChunks"]
                if str(child_chunk) in child_chunks:
                    current_status = child_chunks[str(child_chunk)].get("status")

        logging.info(
            f"[CHILD_CHUNK_STATUS] Child chunk {child_chunk} current status: '{current_status}' -> requested status: '{new_status}'"
        )

        # Validate the transition if there's a current status
        if current_status:
            if current_status in ["completed", "failed"]:
                logging.warning(
                    f"[CHILD_CHUNK_TERMINAL] Cannot update chunk {child_chunk} status from {current_status} to {new_status} (already in terminal state)"
                )
                return  # Skip the update, already in terminal state

            if new_status not in valid_transitions.get(current_status, []):
                logging.warning(
                    f"[CHILD_CHUNK_INVALID_TRANSITION] Invalid transition from {current_status} to {new_status} for chunk {child_chunk}"
                )
                return  # Skip the invalid transition

        # Add timestamp for tracking processing age
        current_time = datetime.datetime.now().isoformat()

        # Add a version attribute to track changes
        update_expression = """
            SET #data.#childChunks.#chunkId.#status = :new_status,
                #data.#childChunks.#chunkId.#lastUpdated = :timestamp,
                #data.#childChunks.#chunkId.#version = if_not_exists(#data.#childChunks.#chunkId.#version, :zero) + :one
        """

        expression_attribute_names = {
            "#data": "data",
            "#childChunks": "childChunks",
            "#chunkId": str(child_chunk),
            "#status": "status",
            "#lastUpdated": "lastUpdated",
            "#version": "version",
        }

        expression_attribute_values = {
            ":new_status": new_status,
            ":timestamp": current_time,
            ":zero": 0,
            ":one": 1,
            ":completed": "completed",
            ":failed": "failed",
        }

        # Add error message if applicable
        if error_message and new_status == "failed":
            update_expression += ", #data.#childChunks.#chunkId.#error = :error"
            expression_attribute_names["#error"] = "error"
            expression_attribute_values[":error"] = error_message
            logging.error(
                f"[CHILD_CHUNK_FAILED] Child chunk {child_chunk} failed with error: {error_message}"
            )

        # Define condition that prevents updating terminal states
        condition_expression = (
            "attribute_not_exists(#data.#childChunks.#chunkId.#status) OR "
        )
        condition_expression += "(#data.#childChunks.#chunkId.#status <> :completed AND #data.#childChunks.#chunkId.#status <> :failed)"

        result = table.update_item(
            Key={"object_id": object_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression=condition_expression,
            ReturnValues="UPDATED_NEW",
        )

        logging.info(f"Successfully updated child chunk status: {result}")
        logging.info(
            f"[CHILD_CHUNK_SUCCESS] Successfully updated child chunk {child_chunk} to '{new_status}' for object_id '{object_id}'"
        )

    except Exception as e:
        logging.error(
            f"[CHILD_CHUNK_ERROR] Failed to update child chunk {child_chunk} status in DynamoDB: {str(e)}"
        )
        logging.exception(e)


def update_parent_chunk_status(object_id, new_status=None, error_message=None):
    """
    Update the parent chunk status.

    Args:
        object_id: The unique identifier for the document
        new_status: Status to set ('processing', 'completed', 'failed')
        error_message: Optional error message when status is 'failed'
    """
    dynamodb = boto3.resource("dynamodb")
    progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
    table = dynamodb.Table(progress_table)

    try:
        logging.info(
            f"[PARENT_CHUNK_UPDATE] Updating parent chunk status for object_id: {object_id}"
        )
        if new_status:
            logging.info(f"[PARENT_CHUNK_UPDATE] Requested status: {new_status}")
        else:
            logging.info(
                f"[PARENT_CHUNK_UPDATE] Auto-determining status based on child chunks"
            )

        # If no status provided, check if all chunks are complete
        if new_status is None:
            response = table.get_item(Key={"object_id": object_id})
            item = response.get("Item")

            if not item:
                raise ValueError(f"No item found with object_id {object_id}")

            child_chunks = item.get("data", {}).get("childChunks", {})
            current_status = item.get("parentChunkStatus", "")

            logging.info(
                f'[PARENT_CHUNK_STATUS] Current parent status: "{current_status}"'
            )
            logging.info(
                f"[PARENT_CHUNK_ANALYSIS] Analyzing {len(child_chunks)} child chunks"
            )

            # Log status of all child chunks for visibility
            completed_count = 0
            failed_count = 0
            processing_count = 0
            starting_count = 0

            for chunk_id, chunk_data in child_chunks.items():
                chunk_status = chunk_data.get("status", "unknown")
                logging.info(
                    f"[CHILD_CHUNK_STATUS_CHECK] Chunk {chunk_id}: {chunk_status}"
                )

                if chunk_status == "completed":
                    completed_count += 1
                elif chunk_status == "failed":
                    failed_count += 1
                elif chunk_status == "processing":
                    processing_count += 1
                elif chunk_status == "starting":
                    starting_count += 1

            logging.info(
                f"[PARENT_CHUNK_SUMMARY] Child chunk counts - Completed: {completed_count}, Failed: {failed_count}, Processing: {processing_count}, Starting: {starting_count}"
            )

            # Skip if already completed or failed
            if current_status in ["completed", "failed"]:
                logging.info(
                    f"[PARENT_CHUNK_TERMINAL] Parent chunk already in terminal state: {current_status}"
                )
                return

            # Check if all child chunks are complete
            all_complete = all(
                chunk["status"] == "completed" for chunk in child_chunks.values()
            )
            any_failed = any(
                chunk["status"] == "failed" for chunk in child_chunks.values()
            )

            if any_failed:
                new_status = "failed"
                logging.warning(
                    f"[PARENT_CHUNK_DECISION] Setting parent to FAILED - {failed_count} child chunks failed"
                )
            elif all_complete:
                new_status = "completed"
                logging.info(
                    f"[PARENT_CHUNK_DECISION] Setting parent to COMPLETED - all {completed_count} child chunks completed"
                )
            else:
                new_status = "processing"
                logging.info(
                    f"[PARENT_CHUNK_DECISION] Setting parent to PROCESSING - still has chunks in progress"
                )

        # Update the status with timestamp
        current_time = datetime.datetime.now().isoformat()

        update_expression = "SET parentChunkStatus = :status, lastUpdated = :timestamp"

        # Use condition expression to prevent race conditions
        condition_expression = "attribute_not_exists(parentChunkStatus) OR "
        condition_expression += (
            "(parentChunkStatus <> :completed AND parentChunkStatus <> :failed)"
        )

        expression_values = {
            ":status": new_status,
            ":timestamp": current_time,
            ":completed": "completed",
            ":failed": "failed",
        }

        # Add error message if provided
        if error_message and new_status == "failed":
            update_expression += ", errorMessage = :error"
            expression_values[":error"] = error_message
            logging.error(
                f"[PARENT_CHUNK_ERROR] Parent failure with error: {error_message}"
            )

        try:
            table.update_item(
                Key={"object_id": object_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ConditionExpression=condition_expression,
            )
            logging.info(
                f'[PARENT_CHUNK_SUCCESS] ✅ Parent chunk status updated to "{new_status}" for object_id: {object_id}'
            )

            # Add specific logging for terminal states
            if new_status == "completed":
                logging.info(
                    f"[PARENT_CHUNK_COMPLETED] 🎉 Document {object_id} embedding process COMPLETED successfully!"
                )
            elif new_status == "failed":
                logging.error(
                    f"[PARENT_CHUNK_FAILED] ❌ Document {object_id} embedding process FAILED!"
                )
            elif new_status == "processing":
                logging.info(
                    f"[PARENT_CHUNK_PROCESSING] 🔄 Document {object_id} is actively processing"
                )

        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logging.info(
                    f"[PARENT_CHUNK_RACE_CONDITION] Parent chunk already in terminal state, not updating to {new_status}"
                )
            else:
                raise
    except Exception as e:
        logging.error(
            f"[PARENT_CHUNK_ERROR] Failed to update parent chunk status for {object_id}: {str(e)}"
        )
        logging.exception(e)


def table_exists(cursor, table_name):
    cursor.execute(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
        (table_name,),
    )
    return cursor.fetchone()[0]


# initially set db_connection to none/closed
db_connection = None


# Function to establish a database connection
def get_db_connection():
    global db_connection
    if db_connection is None or db_connection.closed:
        admin_conn_params = {
            "dbname": pg_database,
            "user": pg_user,
            "password": pg_password,
            "host": pg_host,
            "port": 3306,  # ensure the port matches the PostgreSQL port which is 5432 by default
        }
        try:
            db_connection = psycopg2.connect(
                host=pg_host,
                database=pg_database,
                user=pg_user,
                password=pg_password,
                port=3306,  # ensure the port matches the PostgreSQL port which is 5432 by default
            )
            logging.info("Database connection established.")
        except psycopg2.Error as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

        # Once the database connection is established, check if the table exists
        with db_connection.cursor() as cursor:
            if not table_exists(cursor, table_name):
                logging.info(
                    f"Table {table_name} does not exist. Attempting to create table..."
                )
                if create_table():
                    logging.info(f"Table {table_name} created successfully.")
                else:
                    logging.error(f"Failed to create the table {table_name}.")
                    raise Exception(f"Table {table_name} creation failed.")
            else:
                logging.info(f"Table {table_name} exists.")

    # Return the database connection
    return db_connection


def insert_chunk_data_to_db(
    src,
    locations,
    orig_indexes,
    char_index,
    token_count,
    embedding_index,
    content,
    vector_embedding,
    qa_vector_embedding,
    cursor,
):
    insert_query = """
    INSERT INTO embeddings (src, locations, orig_indexes, char_index, token_count, embedding_index, content, vector_embedding, qa_vector_embedding)
    
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    try:
        cursor.execute(
            insert_query,
            (
                src,
                Json(locations),
                Json(orig_indexes),
                char_index,
                token_count,
                embedding_index,
                content,
                vector_embedding,
                qa_vector_embedding,
            ),
        )
        logging.info(
            f"Data inserted into the database for content: {content[:30]}..."
        )  # Log first 30 characters of content
    except psycopg2.Error as e:
        logging.error(f"Failed to insert data into the database: {e}")
        raise


db_connection = None


# AWS Lambda handler function
def lambda_handler(event, context):
    logging.basicConfig(level=logging.INFO)

    logging.info(
        f"[LAMBDA_START] 🚀 Lambda function started - processing {len(event['Records'])} SQS messages"
    )

    account_data = None

    for record_index, record in enumerate(event["Records"]):
        logging.info(
            f"[MESSAGE_PROCESSING] 📨 Processing message {record_index + 1}/{len(event['Records'])}: {record.get('messageId', 'unknown')}"
        )
        ds_key = None
        try:
            s3_event = json.loads(record["body"])
            logging.info(f"[MESSAGE_BODY] Message body parsed successfully")
            s3_record = s3_event["Records"][0]
            s3_info = s3_record["s3"]

            bucket_name = s3_info["bucket"]["name"]
            url_encoded_key = s3_info["object"]["key"]
            print("s3 Info", s3_info)
            object_key = urllib.parse.unquote(url_encoded_key)
            
            # Extract these early so we can mark parent as failed if needed
            childChunk = extract_child_chunk_number_from_src(object_key)
            trimmed_src = trim_src(object_key)
            
            # Get object_key from S3 object metadata instead of SQS message
            try:
                s3_client = boto3.client('s3')
                head_response = s3_client.head_object(Bucket=bucket_name, Key=object_key)
                s3_metadata = head_response.get('Metadata', {})
                ds_key = s3_metadata.get('object_key')
                ds_key = urllib.parse.unquote(ds_key)
                print("ds_key from S3 metadata:", ds_key)
            except Exception as e:
                ds_key = trimmed_src # most likely coming from embeddings manual process

            if account_data is None:
                if not ds_key:
                    error_msg = f"No ds_key found for {object_key}"
                    logging.error(f"[RAG_SECRETS_ERROR] {error_msg}")
                    # Mark parent as failed before raising exception
                    update_parent_chunk_status(trimmed_src, "failed", error_msg)
                    raise Exception("No ds_key found")
                rag_secrets = get_rag_secrets_for_document(ds_key)
                if not rag_secrets.get('success'):
                    error_msg = f"Failed to retrieve RAG secrets for {ds_key}"
                    logging.error(f"[RAG_SECRETS_ERROR] {error_msg}")
                    # Mark parent as failed before raising exception
                    update_parent_chunk_status(trimmed_src, "failed", error_msg)
                    raise Exception("Failed to retrieve RAG secrets")
                account_data = rag_secrets.get('data')

            logging.info(
                f"[MESSAGE_DETAILS] Bucket: {bucket_name}, Object: {object_key}"
            )
            logging.info(
                f"[MESSAGE_DETAILS] Child chunk: {childChunk}, Trimmed src: {trimmed_src}"
            )

            should_continue = check_parent_terminal_status(trimmed_src, record)
            if should_continue:
                logging.info(
                    f"[MESSAGE_SKIP] ⏭️ Skipping processing due to terminal state"
                )
                continue

            # Create an S3 client
            s3_client = boto3.client("s3")
            db_connection = None

            try:
                # Get the object from the S3 bucket
                response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
                data = json.loads(response["Body"].read().decode("utf-8"))
                src = data.get("src", "")
                trimmed_src = trim_src(src)

                logging.info(
                    f"[S3_FETCH] ✅ Successfully retrieved and parsed S3 object"
                )

                # Mark parent as processing if not already
                update_parent_chunk_status(trimmed_src, "processing")

                # Get database connection
                db_connection = get_db_connection()

                success, src, error_msg = embed_chunks(data, childChunk, embedding_progress_table, db_connection, account_data)

                if success:
                    logging.info(
                        f"[EMBEDDING_SUCCESS] 🎉 Embedding process completed successfully for {src}"
                    )
                    receipt_handle = record["receiptHandle"]

                    # Delete received message from queue
                    sqs.delete_message(
                        QueueUrl=embedding_chunks_index_queue,
                        ReceiptHandle=receipt_handle,
                    )
                    logging.info(
                        f"[QUEUE_DELETE] 🗑️ Deleted message {record['messageId']} from queue after successful processing"
                    )

                    # Update the parent chunk status
                    update_parent_chunk_status(
                        trimmed_src
                    )  # Will auto-determine status
                else:
                    logging.error(
                        f"[EMBEDDING_FAILED] ❌ Embedding process failed for {src}: {error_msg}"
                    )
                    # Parent status should already be set to failed by embed_chunks

                    # Still delete the message to prevent retries
                    receipt_handle = record["receiptHandle"]
                    sqs.delete_message(
                        QueueUrl=embedding_chunks_index_queue,
                        ReceiptHandle=receipt_handle,
                    )
                    logging.info(
                        f"[QUEUE_DELETE] 🗑️ Deleted failed message {record['messageId']} from queue to prevent retries"
                    )

            except Exception as e:
                logging.exception(
                    f"[PROCESSING_ERROR] ❌ Error processing S3 object for message {record['messageId']}: {str(e)}"
                )
                # Mark parent as failed in case of unhandled exceptions
                if "trimmed_src" in locals():
                    update_parent_chunk_status(trimmed_src, "failed", str(e))

                # Delete message to prevent infinite retries
                receipt_handle = record["receiptHandle"]
                sqs.delete_message(
                    QueueUrl=embedding_chunks_index_queue, ReceiptHandle=receipt_handle
                )
                logging.info(
                    f"[QUEUE_DELETE] 🗑️ Deleted error message {record['messageId']} from queue"
                )

            finally:
                # Ensure the database connection is closed
                if db_connection is not None and not db_connection.closed:
                    db_connection.close()
                    logging.info(f"[DB_CONNECTION] 🔌 Database connection closed")

        except Exception as e:
            logging.exception(
                f"[MESSAGE_ERROR] ❌ Critical error processing message {record.get('messageId', 'unknown')}: {str(e)}"
            )
            # Still try to delete the message to prevent infinite loops
            try:
                receipt_handle = record["receiptHandle"]
                sqs.delete_message(
                    QueueUrl=embedding_chunks_index_queue, ReceiptHandle=receipt_handle
                )
                logging.info(
                    f"[QUEUE_DELETE] 🗑️ Deleted critical error message from queue"
                )
            except Exception as delete_error:
                logging.error(
                    f"[QUEUE_DELETE_ERROR] Failed to delete message after critical error: {delete_error}"
                )
        finally:
            if ds_key: delete_rag_secrets_for_document(ds_key)

    logging.info(
        f"[LAMBDA_COMPLETE] ✅ Lambda function completed processing all messages"
    )
   
    return {
        "statusCode": 200,
        "body": json.dumps("Embedding process completed successfully."),
    }


def embed_chunks(data, childChunk, embedding_progress_table, db_connection, account_data):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(embedding_progress_table)
    src = None
    trimmed_src = None
    error_msg = None

    try:
        local_chunks = data.get("chunks", [])
        src = data.get("src", "")
        trimmed_src = trim_src(src)
        childChunk = str(childChunk)

        logging.info(
            f"[EMBED_CHUNKS_START] 🚀 Starting embedding process for child chunk {childChunk} of document: {trimmed_src}"
        )
        logging.info(
            f"[EMBED_CHUNKS_INFO] Processing {len(local_chunks)} local chunks within child chunk {childChunk}"
        )

        # Mark this child chunk as processing
        update_child_chunk_status(trimmed_src, childChunk, "processing")

        if not embedding_model_name or not qa_model_name:
            error_msg = f"No Models Provided: embedding: {embedding_model_name}, qa: {qa_model_name}"
            logging.error(f"[EMBED_CHUNKS_MODEL_ERROR] {error_msg}")
            update_child_chunk_status(trimmed_src, childChunk, "failed", error_msg)
            # Immediately mark parent as failed
            update_parent_chunk_status(trimmed_src, "failed", error_msg)
            return False, src, error_msg

        try:
            response = table.get_item(Key={"object_id": trimmed_src})
            item = response.get("Item")
            if item and "data" in item:
                total_chunks = item["data"].get("totalChunks")
                logging.info(
                    f"[EMBED_CHUNKS_PROGRESS] Processing child chunk: {childChunk} of total parent chunks: {total_chunks}"
                )
                local_chunks_to_process = len(local_chunks)
                logging.info(
                    f"[EMBED_CHUNKS_LOCAL] There are {local_chunks_to_process} (max 10) local chunks within child chunk: {childChunk}"
                )

                if not item["data"].get("terminated", True):
                    logging.warning(
                        f"[EMBED_CHUNKS_TERMINATED] ⛔ The file embedding process has been terminated for {trimmed_src}"
                    )
                    return False, src, "Process terminated"
            else:
                logging.warning(
                    f"[EMBED_CHUNKS_NO_ITEM] No item found in DynamoDB table for {trimmed_src}"
                )
        except ClientError as e:
            logging.error(
                f"[EMBED_CHUNKS_DYNAMO_ERROR] Failed to fetch item from DynamoDB table: {e}"
            )

        logging.info(
            f"[EMBED_CHUNKS_PROCESSING] Processing child chunk {childChunk} of {total_chunks} (fetched from DynamoDB)"
        )
        current_local_chunk_index = 0

        with db_connection.cursor() as cursor:
            db_connection.commit()
            for local_chunk_index, chunk in enumerate(
                local_chunks[current_local_chunk_index:],
                start=current_local_chunk_index + 1,
            ):
                try:
                    content = chunk["content"]
                    locations = chunk["locations"]
                    orig_indexes = chunk["indexes"]
                    char_index = chunk["char_index"]

                    response_clean_text = preprocess_text(content)
                    if not response_clean_text["success"]:
                        raise Exception(
                            f"Text preprocessing failed: {response_clean_text['error']}"
                        )
                    clean_text = response_clean_text["data"]

                    response_vector_embedding = generate_embeddings(clean_text)
                    if not response_vector_embedding["success"]:
                        raise Exception(
                            f"Vector embedding generation failed: {response_vector_embedding['error']}"
                        )
                    vector_embedding = response_vector_embedding["data"]

                    response_qa_summary = generate_questions(clean_text, account_data)
                    if not response_qa_summary["success"]:
                        raise Exception(
                            f"QA summary generation failed: {response_qa_summary['error']}"
                        )
                    qa_summary = response_qa_summary["data"]

                    response_qa_embedding = generate_embeddings(content=qa_summary)
                    if not response_qa_embedding["success"]:
                        raise Exception(
                            f"QA embedding generation failed: {response_qa_embedding['error']}"
                        )
                    qa_vector_embedding = response_qa_embedding["data"]

                    vector_token_count = response_vector_embedding["token_count"]
                    qa_vector_token_count = response_qa_embedding["token_count"]
                    total_vector_token_count = (
                        vector_token_count + qa_vector_token_count
                    )

                    logging.info(
                        f"Embedding local chunk index: {current_local_chunk_index}"
                    )
                    insert_chunk_data_to_db(
                        src,
                        locations,
                        orig_indexes,
                        char_index,
                        total_vector_token_count,
                        current_local_chunk_index,
                        content,
                        vector_embedding,
                        qa_vector_embedding,
                        cursor,
                    )

                    logging.info(f"Getting Account information for {trimmed_src}")

                    current_local_chunk_index += 1
                    db_connection.commit()

                    logging.info(
                        f"[LOCAL_CHUNK_COMPLETE] ✅ Local chunk {local_chunk_index} completed successfully"
                    )

                except Exception as e:
                    error_msg = f"Error processing local chunk {local_chunk_index} of child chunk {childChunk} in {src}: {str(e)}"
                    logging.error(f"[LOCAL_CHUNK_ERROR] ❌ {error_msg}")
                    # Mark this child as failed
                    update_child_chunk_status(
                        trimmed_src, childChunk, "failed", error_msg
                    )
                    # Immediately mark parent as failed
                    update_parent_chunk_status(trimmed_src, "failed", error_msg)
                    if not db_connection.closed:
                        db_connection.rollback()
                    return False, src, error_msg

        logging.info(
            f"[EMBED_CHUNKS_SUCCESS] 🎉 All local chunks processed successfully for child chunk {childChunk}"
        )
        update_child_chunk_status(trimmed_src, childChunk, "completed")
        logging.info(
            f"[EMBED_CHUNKS_COMPLETE] ✅ Child chunk {childChunk} marked as completed"
        )
        return True, src, None

    except Exception as e:
        error_msg = f"Critical error in embed_chunks for child chunk {childChunk} of {src}: {str(e)}"
        logging.exception(f"[EMBED_CHUNKS_CRITICAL_ERROR] ❌ {error_msg}")
        if trimmed_src:
            update_child_chunk_status(trimmed_src, childChunk, "failed", error_msg)
            # Immediately mark parent as failed
            update_parent_chunk_status(trimmed_src, "failed", error_msg)
        if db_connection and not db_connection.closed:
            db_connection.rollback()
        return False, src, error_msg


def check_parent_terminal_status(trimmed_src, record):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(embedding_progress_table)
    try:
        logging.info(
            f"[TERMINAL_CHECK] Checking parent terminal status for: {trimmed_src}"
        )

        response = table.get_item(Key={"object_id": trimmed_src})
        item = response.get("Item")

        if item:
            parent_status = item.get("parentChunkStatus")
            terminated = item.get("terminated", False)

            logging.info(
                f"[TERMINAL_CHECK] Current parent status: '{parent_status}', terminated: {terminated}"
            )

            if parent_status in ["failed", "completed"]:
                logging.warning(
                    f"[TERMINAL_CHECK] ⛔ Parent chunk is already in terminal state: {parent_status}. Skipping processing."
                )
                receipt_handle = record["receiptHandle"]
                sqs.delete_message(
                    QueueUrl=embedding_chunks_index_queue, ReceiptHandle=receipt_handle
                )
                logging.info(
                    f"[TERMINAL_CHECK] 🗑️ Deleted message from queue due to terminal parent status"
                )
                return True

            if terminated:
                logging.warning(
                    f"[TERMINAL_CHECK] ⛔ Job for {trimmed_src} has been terminated. Skipping processing."
                )
                receipt_handle = record["receiptHandle"]
                sqs.delete_message(
                    QueueUrl=embedding_chunks_index_queue, ReceiptHandle=receipt_handle
                )
                logging.info(
                    f"[TERMINAL_CHECK] 🗑️ Deleted message from queue due to termination"
                )
                return True
        else:
            logging.info(
                f"[TERMINAL_CHECK] No existing item found for {trimmed_src} - continuing with processing"
            )

        logging.info(
            f"[TERMINAL_CHECK] ✅ Parent is not in terminal state - proceeding with processing"
        )
        return False

    except Exception as e:
        logging.error(
            f"[TERMINAL_CHECK_ERROR] Error checking parent status for {trimmed_src}: {e}"
        )
        return False


@validated(op="terminate")
def terminate_embedding(event, context, current_user, name, data):
    object_id = data["data"].get("object_key")
    dynamodb = boto3.resource("dynamodb")
    progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
    table = dynamodb.Table(progress_table)

    try:
        response = table.update_item(
            Key={"object_id": object_id},
            UpdateExpression="SET #terminated = :val",
            ExpressionAttributeNames={"#terminated": "terminated"},
            ExpressionAttributeValues={":val": True},
            ReturnValues="UPDATED_NEW",
        )

        if response.get("Attributes", {}).get("terminated") is True:
            print(f"Successfully terminated object with ID: {object_id}")
            return True
        else:
            print(
                f"Failed to update termination status for object with ID: {object_id}"
            )
            return False
    except Exception as e:
        print(f"Error terminating embedding for object_id {object_id}: {e}")
        return False
