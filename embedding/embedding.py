import psycopg2
from psycopg2.extras import Json
import json
import os
import boto3
import logging
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
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
import time
from rag.rag_secrets import get_rag_secrets_for_document
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType, IMAGE_FILE_TYPES
from pycommon.api.data_sources import translate_user_data_sources_to_hash_data_sources
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
            "starting": ["processing", "failed"],
            "processing": ["completed", "failed"],
            "completed": [],  # Terminal state
            "failed": [],     # Terminal state
        }

        logging.info(
            f"[CHILD_CHUNK_UPDATE] Updating child chunk {child_chunk} for '{object_id}' to '{new_status}'"
        )

        table = get_progress_table()

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

        if current_status is None:
            logging.info(
                f"[CHILD_CHUNK_CREATE] Child chunk {child_chunk} does not exist, creating with status: '{new_status}'"
            )
        else:
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

        # Ensure parent structure exists in one operation to avoid overlapping paths
        try:
            table.update_item(
                Key={"object_id": object_id},
                UpdateExpression="SET #data = if_not_exists(#data, :full_structure)",
                ExpressionAttributeNames={"#data": "data"},
                ExpressionAttributeValues={":full_structure": {"childChunks": {}}}
            )
        except Exception:
            # Structure creation errors are non-critical, the main update below will handle missing paths
            pass

        # Now update the specific child chunk
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
        # Allow creation if chunk doesn't exist, or update if not in terminal state
        condition_expression = (
            "attribute_not_exists(#data.#childChunks.#chunkId.#status) OR "
            "(#data.#childChunks.#chunkId.#status <> :completed AND #data.#childChunks.#chunkId.#status <> :failed)"
        )

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
    """Update the parent chunk status based on child chunk states."""
    table = get_progress_table()

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
            # Use strong consistency to avoid reading stale data after recent chunk updates
            response = table.get_item(Key={"object_id": object_id}, ConsistentRead=True)
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

            # Count status of all child chunks (without verbose individual logging)
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
            
            # Enhanced debugging: show which chunks are in each state
            if processing_count > 0 or failed_count > 0 or starting_count > 0:
                processing_chunks = [chunk_id for chunk_id, chunk_data in child_chunks.items() 
                                   if chunk_data.get("status") == "processing"]
                failed_chunks = [chunk_id for chunk_id, chunk_data in child_chunks.items() 
                               if chunk_data.get("status") == "failed"]
                starting_chunks = [chunk_id for chunk_id, chunk_data in child_chunks.items() 
                                 if chunk_data.get("status") == "starting"]
                
                if processing_chunks:
                    logging.info(f"[PARENT_CHUNK_DEBUG] Processing chunks: {processing_chunks[:10]}{'...' if len(processing_chunks) > 10 else ''}")
                if failed_chunks:
                    logging.info(f"[PARENT_CHUNK_DEBUG] Failed chunks: {failed_chunks[:10]}{'...' if len(failed_chunks) > 10 else ''}")
                if starting_chunks:
                    logging.info(f"[PARENT_CHUNK_DEBUG] Starting chunks: {starting_chunks[:10]}{'...' if len(starting_chunks) > 10 else ''}")

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


def ensure_child_chunk_column_exists(cursor):
    """Check if child_chunk column exists, add it if not."""
    try:
        # Check if column exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'embeddings' AND column_name = 'child_chunk'
        """)
        
        if not cursor.fetchone():
            logging.info("[SCHEMA_UPDATE] Adding child_chunk column to embeddings table")
            cursor.execute("ALTER TABLE embeddings ADD COLUMN child_chunk VARCHAR(10)")
            cursor.execute("CREATE INDEX idx_src_child_chunk ON embeddings (src, child_chunk)")
            logging.info("[SCHEMA_UPDATE] ✅ child_chunk column and index added successfully")
        else:
            logging.info("[SCHEMA_UPDATE] child_chunk column already exists")
    except Exception as e:
        logging.error(f"[SCHEMA_UPDATE] ❌ Error checking/adding child_chunk column: {e}")
        raise


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
            
            # Ensure child_chunk column exists (safe to run multiple times)
            ensure_child_chunk_column_exists(cursor)
            db_connection.commit()

    # Return the database connection
    return db_connection


def insert_chunk_data_to_db(
    src,
    child_chunk,
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
    INSERT INTO embeddings (src, child_chunk, locations, orig_indexes, char_index, token_count, embedding_index, content, vector_embedding, qa_vector_embedding)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    try:
        cursor.execute(
            insert_query,
            (
                src,
                child_chunk,
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
            f"[DB_INSERT] ✅ Data inserted for chunk {child_chunk}."
        )  # Log first 30 characters of content
    except psycopg2.Error as e:
        logging.error(f"[DB_INSERT] ❌ Failed to insert data for chunk {child_chunk}: {e}")
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
            
            # Get object_key and force_reprocess flag from S3 object metadata
            is_force_reprocess = False
            try:
                s3_client = boto3.client('s3')
                head_response = s3_client.head_object(Bucket=bucket_name, Key=object_key)
                s3_metadata = head_response.get('Metadata', {})
                ds_key = s3_metadata.get('object_key')
                ds_key = urllib.parse.unquote(ds_key)
                is_force_reprocess = s3_metadata.get('force_reprocess', '').lower() == 'true'
                print(f"ds_key from S3 metadata: {ds_key}, force_reprocess: {is_force_reprocess}")
            except Exception as e:
                ds_key = trimmed_src # most likely coming from embeddings manual process
                print(f"Could not get S3 metadata: {e}")

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

            # For force reprocessing, check if we need selective cleanup
            if is_force_reprocess:
                perform_selective_reprocessing_setup(trimmed_src)

            should_continue = check_parent_terminal_status(trimmed_src, record)
            if should_continue:
                logging.info(
                    f"[MESSAGE_SKIP] ⏭️ Skipping processing due to terminal state"
                )
                continue

            # Check if this specific chunk is already completed (for selective reprocessing)
            if is_force_reprocess and check_chunk_already_completed(trimmed_src, childChunk, record):
                logging.info(
                    f"[SELECTIVE_SKIP] ⏭️ Skipping chunk {childChunk} - already completed"
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
                        childChunk,
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
        
        # Small delay to help with DynamoDB consistency before parent status check
        time.sleep(0.1)  # 100ms delay to reduce race conditions
        
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

        response = table.get_item(Key={"object_id": trimmed_src}, ConsistentRead=True)
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


def get_progress_table():
    """Get DynamoDB progress table resource."""
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(os.environ["EMBEDDING_PROGRESS_TABLE"])


def perform_selective_reprocessing_setup(trimmed_src):
    """
    One-time setup for selective reprocessing per document.
    This should only run once per document, not for every chunk.
    """
    try:
        # Use a function attribute to track processed documents in this Lambda invocation
        if not hasattr(perform_selective_reprocessing_setup, '_processed_documents'):
            perform_selective_reprocessing_setup._processed_documents = set()
        
        if trimmed_src in perform_selective_reprocessing_setup._processed_documents:
            # Already processed this document in this Lambda invocation
            return
            
        perform_selective_reprocessing_setup._processed_documents.add(trimmed_src)
        
        logging.info(f"[SELECTIVE_SETUP] 🎯 Setting up selective reprocessing for {trimmed_src}")
        
        # First, detect document structure changes by checking chunk numbers
        table = get_progress_table()
        response = table.get_item(Key={"object_id": trimmed_src}, ConsistentRead=True)
        item = response.get("Item")
        
        if not item or "data" not in item:
            logging.info(f"[SELECTIVE_SETUP] No existing progress - will process all chunks")
            logging.info(f"[TEST_CASE] 📝 FRESH_DOCUMENT - No prior progress data")
            return
            
        child_chunks = item.get("data", {}).get("childChunks", {})
        existing_chunk_ids = set(int(chunk_id) for chunk_id in child_chunks.keys())
        
        # Check S3 for actual chunk files that exist
        chunks_bucket = os.environ["S3_RAG_CHUNKS_BUCKET_NAME"]
        s3_client = boto3.client("s3")
        
        try:
            # List all chunk files for this document
            prefix = f"{trimmed_src}-"
            response = s3_client.list_objects_v2(Bucket=chunks_bucket, Prefix=prefix)
            
            actual_chunk_ids = set()
            if 'Contents' in response:
                for obj in response['Contents']:
                    # Extract chunk number from filename (e.g., "doc-1.chunks.json" -> 1)
                    key = obj['Key']
                    if '.chunks.json' in key:
                        chunk_num = key.split('-')[-1].replace('.chunks.json', '')
                        try:
                            actual_chunk_ids.add(int(chunk_num))
                        except ValueError:
                            pass
            
            logging.info(f"[SELECTIVE_SETUP] Existing chunks in DynamoDB: {sorted(existing_chunk_ids)}")
            logging.info(f"[SELECTIVE_SETUP] Actual chunks in S3: {sorted(actual_chunk_ids)}")
            
            # Check for structure change
            if existing_chunk_ids != actual_chunk_ids:
                # Detailed logging for structure changes
                added_chunks = actual_chunk_ids - existing_chunk_ids
                removed_chunks = existing_chunk_ids - actual_chunk_ids
                
                if added_chunks:
                    logging.info(f"[SELECTIVE_SETUP] 📈 Document grew: added chunks {sorted(added_chunks)}")
                if removed_chunks:
                    logging.info(f"[SELECTIVE_SETUP] 📉 Document shrank: removed chunks {sorted(removed_chunks)}")
                    
                logging.info(f"[SELECTIVE_SETUP] 🔄 Document structure changed ({len(existing_chunk_ids)} → {len(actual_chunk_ids)} chunks) - resetting progress")
                logging.info(f"[TEST_CASE] 📝 STRUCTURE_CHANGE - Chunk count mismatch ({len(existing_chunk_ids)} → {len(actual_chunk_ids)})")
                
                # Delete entire DynamoDB entry for fresh start
                table.delete_item(Key={"object_id": trimmed_src})
                # Delete all embeddings for fresh start
                db_connection = get_db_connection()
                try:
                    with db_connection.cursor() as cursor:
                        cursor.execute("DELETE FROM embeddings WHERE src = %s", [trimmed_src])
                        rows_deleted = cursor.rowcount
                        db_connection.commit()
                        logging.info(f"[SELECTIVE_SETUP] ✅ Deleted {rows_deleted} old embeddings for fresh start")
                finally:
                    if not db_connection.closed:
                        db_connection.close()
                return
                
        except Exception as e:
            logging.error(f"[SELECTIVE_SETUP] Error checking S3 chunks: {e}")
            # Continue with existing logic if S3 check fails
        
        # No structure change - do selective cleanup
        failed_chunks = []
        completed_chunks = []
        
        for chunk_id, chunk_info in child_chunks.items():
            if chunk_info.get("status") == "completed":
                completed_chunks.append(int(chunk_id))
            else:
                failed_chunks.append(int(chunk_id))
        
        logging.info(f"[SELECTIVE_SETUP] Completed chunks: {sorted(completed_chunks)}, Failed chunks: {sorted(failed_chunks)}")
        
        if failed_chunks:
            logging.info(f"[SELECTIVE_SETUP] Cleaning up {len(failed_chunks)} failed chunks")
            logging.info(f"[TEST_CASE] 📝 SELECTIVE_REPROCESS - Retrying {len(failed_chunks)} of {len(child_chunks)} chunks")
            
            # Delete only failed chunks from embeddings table
            db_connection = get_db_connection()
            try:
                with db_connection.cursor() as cursor:
                    # Check if child_chunk column exists
                    cursor.execute("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name='embeddings' AND column_name='child_chunk'
                    """)
                    has_child_chunk = cursor.fetchone() is not None
                    
                    if has_child_chunk:
                        # Selective delete - convert integers to strings for VARCHAR column
                        failed_chunks_str = [str(chunk) for chunk in failed_chunks]
                        placeholders = ','.join(['%s'] * len(failed_chunks_str))
                        delete_query = f"DELETE FROM embeddings WHERE src = %s AND child_chunk IN ({placeholders})"
                        cursor.execute(delete_query, [trimmed_src] + failed_chunks_str)
                        rows_deleted = cursor.rowcount
                        db_connection.commit()
                        logging.info(f"[SELECTIVE_SETUP] ✅ Deleted {rows_deleted} failed embeddings")
                    else:
                        # Without child_chunk column, we can't do selective cleanup
                        # Delete all and reprocess all
                        logging.info(f"[SELECTIVE_SETUP] No child_chunk column - deleting all embeddings")
                        logging.info(f"[TEST_CASE] 📝 FULL_REPROCESS_FALLBACK - No child_chunk column for selective delete")
                        cursor.execute("DELETE FROM embeddings WHERE src = %s", [trimmed_src])
                        rows_deleted = cursor.rowcount
                        db_connection.commit()
                        # Also reset DynamoDB to force all chunks to reprocess
                        table.delete_item(Key={"object_id": trimmed_src})
                        logging.info(f"[SELECTIVE_SETUP] ✅ Deleted {rows_deleted} embeddings and reset progress")
            finally:
                if not db_connection.closed:
                    db_connection.close()
        else:
            logging.info(f"[SELECTIVE_SETUP] All chunks completed - no cleanup needed")
            logging.info(f"[TEST_CASE] 📝 ALL_CHUNKS_COMPLETE - Skipping all chunks")
            
    except Exception as e:
        logging.error(f"[SELECTIVE_SETUP] Error during setup: {e}")
        # Continue processing even if setup fails


def check_chunk_already_completed(trimmed_src, child_chunk, record):
    """Check if a specific child chunk is already completed - used for selective reprocessing."""
    try:
        table = get_progress_table()
        
        response = table.get_item(Key={"object_id": trimmed_src}, ConsistentRead=True)
        item = response.get("Item")
        
        if item and "data" in item:
            child_chunks = item.get("data", {}).get("childChunks", {})
            chunk_info = child_chunks.get(str(child_chunk), {})
            chunk_status = chunk_info.get("status", "starting")
            
            if chunk_status == "completed":
                logging.info(f"[SELECTIVE_CHECK] ✅ Chunk {child_chunk} already completed, skipping")
                # Delete the message since we don't need to process it
                receipt_handle = record["receiptHandle"]
                sqs.delete_message(
                    QueueUrl=embedding_chunks_index_queue, ReceiptHandle=receipt_handle
                )
                # Check if all chunks are complete and update parent if needed
                update_parent_chunk_status(trimmed_src)
                return True
            else:
                logging.info(f"[SELECTIVE_CHECK] Chunk {child_chunk} needs processing (status: {chunk_status})")
                return False
        else:
            # No progress data - process the chunk
            return False
            
    except Exception as e:
        logging.error(f"[SELECTIVE_CHECK] Error checking chunk completion status: {e}")
        # On error, process the chunk to be safe
        return False


def cleanup_embeddings_for_reprocess(trimmed_src, expected_chunk_count=None):
    """Clean up embeddings for reprocessing based on selective vs full strategy."""
    logging.info(f"[CLEANUP] 🎯 Analyzing reprocessing strategy for: {trimmed_src}")
    
    try:
        # Check if selective reprocessing is possible
        selective_result = check_selective_reprocessing_capability(trimmed_src, expected_chunk_count)
        
        if not selective_result["possible"]:
            logging.info(f"[CLEANUP] 🔄 Full cleanup required - {selective_result['reason']}")
            return perform_full_cleanup(trimmed_src)
        
        # Perform selective cleanup if needed
        failed_chunks = selective_result["failed_chunks"]
        if not failed_chunks:
            logging.info(f"[CLEANUP] ✅ No cleanup needed - all chunks completed")
            return {"cleanup_type": "none", "failed_chunks": []}
        
        return perform_selective_cleanup(trimmed_src, failed_chunks)
        
    except Exception as e:
        logging.error(f"[CLEANUP] ❌ Error during cleanup analysis: {e}")
        return perform_full_cleanup(trimmed_src)


def perform_selective_cleanup(trimmed_src, failed_chunks):
    """Perform selective cleanup of specific failed chunks."""
    logging.info(f"[SELECTIVE_CLEANUP] 🎯 Cleaning {len(failed_chunks)} chunks: {failed_chunks}")
    
    try:
        db_connection = get_db_connection()
        with db_connection.cursor() as cursor:
            # Convert integers to strings for VARCHAR column
            failed_chunks_str = [str(chunk) for chunk in failed_chunks]
            placeholders = ','.join(['%s'] * len(failed_chunks_str))
            delete_query = f"DELETE FROM embeddings WHERE src = %s AND child_chunk IN ({placeholders})"
            cursor.execute(delete_query, [trimmed_src] + failed_chunks_str)
            rows_deleted = cursor.rowcount
            db_connection.commit()
            
        logging.info(f"[SELECTIVE_CLEANUP] ✅ Deleted {rows_deleted} embeddings for {trimmed_src}")
        return {"cleanup_type": "selective", "failed_chunks": failed_chunks}
        
    except Exception as e:
        logging.error(f"[SELECTIVE_CLEANUP] ❌ Error: {e}")
        return perform_full_cleanup(trimmed_src)
    finally:
        if 'db_connection' in locals() and not db_connection.closed:
            db_connection.close()


def check_selective_reprocessing_capability(trimmed_src, expected_chunk_count=None):
    """
    Check if selective reprocessing is possible for a document.
    
    Args:
        trimmed_src: Document identifier
        expected_chunk_count: Number of chunks the current document should produce
    
    Returns:
        dict: {
            "possible": bool,
            "reason": str,
            "failed_chunks": list
        }
    """
    try:
        # Check if we have progress data
        table = get_progress_table()
        
        try:
            response = table.get_item(Key={"object_id": trimmed_src})
            item = response.get("Item")
            
            if not item or "data" not in item:
                return {
                    "possible": False,
                    "reason": "No progress data found",
                    "failed_chunks": []
                }
                
            child_chunks = item.get("data", {}).get("childChunks", {})
            if not child_chunks:
                return {
                    "possible": False,
                    "reason": "No child chunk data found",
                    "failed_chunks": []
                }
            
            # CRITICAL: Check if document structure has changed (chunk count mismatch)
            if expected_chunk_count is not None:
                existing_chunk_count = len(child_chunks)
                if existing_chunk_count != expected_chunk_count:
                    logging.info(f"[SELECTIVE_CHECK] Document structure changed: {existing_chunk_count} existing chunks → {expected_chunk_count} expected chunks")
                    return {
                        "possible": False,
                        "reason": f"Document structure changed: {existing_chunk_count} existing chunks vs {expected_chunk_count} expected chunks",
                        "failed_chunks": []
                    }
                
        except Exception as e:
            return {
                "possible": False,
                "reason": f"Could not read progress data: {str(e)}",
                "failed_chunks": []
            }
        
        # Check 2: Do existing embeddings have child_chunk data?
        db_connection = get_db_connection()
        with db_connection.cursor() as cursor:
            # Check if any embeddings exist with child_chunk data
            cursor.execute("""
                SELECT COUNT(*) as total, 
                       COUNT(child_chunk) as with_chunks 
                FROM embeddings 
                WHERE src = %s
            """, (trimmed_src,))
            
            result = cursor.fetchone()
            total_embeddings = result[0] if result else 0
            embeddings_with_chunks = result[1] if result else 0
            
            logging.info(f"[SELECTIVE_CHECK] Embeddings for {trimmed_src}: {total_embeddings} total, {embeddings_with_chunks} with chunk data")
            
            if total_embeddings == 0:
                # No existing embeddings - fresh start (can be selective)
                failed_chunks = [chunk_id for chunk_id, chunk_data in child_chunks.items() 
                               if chunk_data.get("status") != "completed"]
                return {
                    "possible": True,
                    "reason": "No existing embeddings - fresh start",
                    "failed_chunks": failed_chunks
                }
            
            if embeddings_with_chunks == 0:
                # Embeddings exist but no chunk data - must do full reprocess
                return {
                    "possible": False,
                    "reason": "Existing embeddings lack chunk identification",
                    "failed_chunks": []
                }
            
            if embeddings_with_chunks < total_embeddings:
                # Mixed data - some embeddings have chunk data, some don't
                return {
                    "possible": False,
                    "reason": "Mixed embedding data (some with/without chunk IDs)",
                    "failed_chunks": []
                }
            
            # All embeddings have chunk data - can do selective reprocessing
            failed_chunks = [chunk_id for chunk_id, chunk_data in child_chunks.items() 
                           if chunk_data.get("status") != "completed"]
            
            return {
                "possible": True,
                "reason": "All embeddings have chunk identification",
                "failed_chunks": failed_chunks
            }
            
    except Exception as e:
        logging.error(f"[SELECTIVE_CHECK] Error checking selective capability: {e}")
        return {
            "possible": False,
            "reason": f"Error during capability check: {str(e)}",
            "failed_chunks": []
        }
    finally:
        if 'db_connection' in locals() and not db_connection.closed:
            db_connection.close()


def perform_full_cleanup(trimmed_src):
    """Perform full cleanup of all embeddings and DynamoDB progress for a document."""
    try:
        # Clean up PostgreSQL embeddings
        db_connection = get_db_connection()
        with db_connection.cursor() as cursor:
            cursor.execute("DELETE FROM embeddings WHERE src = %s", (trimmed_src,))
            rows_deleted = cursor.rowcount
            db_connection.commit()
            
        logging.info(f"[FULL_CLEANUP] 🔄 Deleted {rows_deleted} PostgreSQL embeddings for {trimmed_src}")
        
        # Reset DynamoDB progress structure for fresh start  
        table = get_progress_table()
        table.delete_item(Key={"object_id": trimmed_src})
        logging.info(f"[FULL_CLEANUP] 🗑️ Reset DynamoDB progress structure for {trimmed_src}")
        
        return {"cleanup_type": "full", "failed_chunks": []}
        
    except Exception as e:
        logging.error(f"[FULL_CLEANUP] ❌ Error during full cleanup: {e}")
        return {"cleanup_type": "failed", "failed_chunks": []}
    finally:
        if 'db_connection' in locals() and not db_connection.closed:
            db_connection.close()


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


async def _check_image_status_async(ds_key, image_bucket, executor):
    """Async helper to check individual image status"""
    try:
        if not image_bucket:
            logging.error(f"[GET_STATUS] S3_IMAGE_INPUT_BUCKET_NAME not configured for image: {ds_key}")
            return ds_key, None
        
        # Run S3 head_object in thread pool
        loop = asyncio.get_event_loop()
        s3_client = boto3.client("s3")
        
        def _head_object():
            return s3_client.head_object(Bucket=image_bucket, Key=ds_key)
        
        try:
            head_response = await loop.run_in_executor(executor, _head_object)
            content_type = head_response.get("ContentType", "")
            
            # If ContentType is text/plain, it means the image was processed to base64
            if content_type == "text/plain":
                return ds_key, "completed"
            elif content_type in IMAGE_FILE_TYPES:
                # Original image exists but not yet processed - check if recent upload
                last_modified = head_response.get("LastModified")
                if last_modified:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    time_diff = (now - last_modified).total_seconds()
                    
                    # If uploaded within last 5 minutes, consider it processing
                    if time_diff <= 300:  # 5 minutes
                        return ds_key, "processing"
                    else:
                        # Been too long, likely failed
                        logging.warning(f"[GET_STATUS] Image {ds_key} uploaded {time_diff:.0f}s ago, likely failed processing")
                        return ds_key, "failed"
                else:
                    return ds_key, "failed"
            else:
                # Unknown content type
                logging.warning(f"[GET_STATUS] Image {ds_key} has unexpected ContentType: {content_type}")
                return ds_key, "failed"
                
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchKey":
                # Image doesn't exist at all
                return ds_key, "not_found"
            else:
                # Other S3 error
                logging.error(f"[GET_STATUS] S3 error checking image {ds_key}: {e}")
                return ds_key, None
                
    except Exception as e:
        logging.error(f"[GET_STATUS] Error checking image status for {ds_key}: {e}")
        return ds_key, None


async def _check_text_status_async(original_key, global_id, progress_table, executor):
    """Async helper to check individual text status"""
    try:
        loop = asyncio.get_event_loop()
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(progress_table)
        
        def _get_item():
            return table.get_item(Key={"object_id": global_id})
        
        response = await loop.run_in_executor(executor, _get_item)
        item = response.get("Item")
        
        if not item:
            return original_key, "not_found"
        
        # Check if terminated first
        if item.get("terminated", False):
            return original_key, "terminated"
        
        # Get parent chunk status
        parent_status = item.get("parentChunkStatus")
        if parent_status:
            return original_key, parent_status
        else:
            # If no parent status set, default to starting
            return original_key, "starting"
            
    except Exception as e:
        logging.error(f"[GET_STATUS] Error getting status for global_id {global_id} (original: {original_key}): {e}")
        return original_key, None


async def _get_embedding_status_async(data_sources_input):
    """Async helper function to process status lookups in parallel"""
    status_map = {}
    
    # Initialize all with None
    for ds in data_sources_input:
        ds_key = ds.get("key")
        if ds_key:
            status_map[ds_key] = None
    
    # Separate image files from text files
    text_data_sources = []
    image_data_sources = []
    
    for ds in data_sources_input:
        ds_key = ds.get("key")
        ds_type = ds.get("type", "")
        
        if not ds_key:
            logging.warning(f"[GET_STATUS] Skipping data source with missing key: {ds}")
            continue
            
        if ds_type in IMAGE_FILE_TYPES:
            image_data_sources.append(ds)
        else:
            text_data_sources.append(ds)
    
    logging.info(f"[GET_STATUS] Processing {len(image_data_sources)} images, {len(text_data_sources)} text files")
    
    # Create a thread pool executor
    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = []
        
        # Handle image files in parallel
        image_bucket = os.environ.get("S3_IMAGE_INPUT_BUCKET_NAME")
        for ds in image_data_sources:
            ds_key = ds.get("key")
            task = _check_image_status_async(ds_key, image_bucket, executor)
            tasks.append(task)
        
        # Handle text files - first translate, then check in parallel
        if text_data_sources:
            # Convert to format expected by translate function (id -> key, keep type)
            translate_sources = [{"id": ds["key"], "type": ds["type"]} for ds in text_data_sources]
            
            # Translate user data sources to global hash keys (this is sync and can't be easily parallelized)
            translated_sources = translate_user_data_sources_to_hash_data_sources(translate_sources)
            
            # Create mapping from original key to translated global ID
            original_to_global = {}
            for i, translated in enumerate(translated_sources):
                if i < len(text_data_sources):
                    original_key = text_data_sources[i]["key"]
                    global_id = translated.get("id")
                    if global_id:
                        original_to_global[original_key] = global_id
                    else:
                        logging.warning(f"[GET_STATUS] No global ID found for {original_key}")
            
            # Now lookup embedding status for the global IDs in parallel
            progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
            for original_key, global_id in original_to_global.items():
                task = _check_text_status_async(original_key, global_id, progress_table, executor)
                tasks.append(task)
        
        # Wait for all tasks to complete
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for result in results:
                if isinstance(result, Exception):
                    logging.error(f"[GET_STATUS] Task failed with exception: {result}")
                    continue
                if result and len(result) == 2:
                    key, status = result
                    if key and key in status_map:
                        status_map[key] = status
    
    # Log summary of results
    status_counts = {}
    for status in status_map.values():
        status_counts[status] = status_counts.get(status, 0) + 1
    
    logging.info(f"[GET_STATUS] Status summary: {status_counts}")
    
    # Log details for items needing attention
    not_found_items = [key for key, status in status_map.items() if status == "not_found"]
    if not_found_items:
        logging.warning(f"[GET_STATUS] NOT FOUND ({len(not_found_items)}): {not_found_items}")
    
    failed_items = [key for key, status in status_map.items() if status == "failed"]
    if failed_items:
        logging.warning(f"[GET_STATUS] FAILED ({len(failed_items)}): {failed_items}")
    
    return status_map


@validated(op="get_status")
def get_embedding_status(event, context, current_user, name, data):
    """
    Get embedding status for a list of data sources.
    
    Args:
        data: Dictionary containing "dataSources" - list of dicts with "key" and "type" attributes
        
    Returns:
        Dictionary mapping original data_source_key -> status, where status can be:
        - "starting" - Initial state when document is submitted for embedding
        - "processing" - Child chunk is actively being processed  
        - "completed" - All chunks have been successfully processed
        - "failed" - Child chunk processing encountered an error
        - "terminated" - Processing has been terminated for all chunks of a document
        - "not_found" - No record found for this object_id
        - None - Translation or lookup failed
    """
    data_sources_input = data["data"].get("dataSources", [])
    if not data_sources_input:
        logging.error("[GET_STATUS] No dataSources provided")
        return {"success": False, "error": "No dataSources provided"}
    
    logging.info(f"[GET_STATUS] Looking up status for {len(data_sources_input)} data sources")
    
    try:
        # Run the async function
        status_map = asyncio.run(_get_embedding_status_async(data_sources_input))
        
        logging.info(f"[GET_STATUS] ✅ Completed lookup for {len(status_map)} data sources")
        return {"success": True, "data": status_map}
        
    except Exception as e:
        logging.error(f"[GET_STATUS] ❌ Error processing data sources: {e}")
        # Return all as None on critical failure
        status_map = {ds.get("key"): None for ds in data_sources_input if ds.get("key")}
        return {"success": True, "data": status_map}
