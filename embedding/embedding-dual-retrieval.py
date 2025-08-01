# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

# set up retriever function that accepts a a query, user, and/or list of keys for where claus

import json
import os
import time
import psycopg2
from pgvector.psycopg2 import register_vector
from pycommon.api.credentials import get_credentials
from shared_functions import generate_embeddings
import logging
import boto3
from boto3.dynamodb.conditions import Key
from pycommon.api.ops import api_tool
from pycommon.api.amplify_groups import verify_user_in_amp_group
from datetime import datetime, timezone
from rag.rag_secrets import store_ds_secrets_for_rag
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
add_api_access_types([APIAccessType.CHAT.value, APIAccessType.DUAL_EMBEDDING.value])

setup_validated(rules, get_permission_checker)

# Configure Logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("embedding_dual_retrieval")

pg_host = os.environ["RAG_POSTGRES_DB_READ_ENDPOINT"]
pg_user = os.environ["RAG_POSTGRES_DB_USERNAME"]
pg_database = os.environ["RAG_POSTGRES_DB_NAME"]
rag_pg_password = os.environ["RAG_POSTGRES_DB_SECRET"]
api_version = os.environ["API_VERSION"]
object_access_table = os.environ["OBJECT_ACCESS_TABLE"]
groups_table = os.environ["GROUPS_DYNAMO_TABLE"]

queue_url = os.environ["RAG_CHUNK_DOCUMENT_QUEUE_URL"]
s3_bucket = os.environ["S3_FILE_TEXT_BUCKET_NAME"]
sqs = boto3.client("sqs")

# Define the permission levels that grant access
permission_levels = ["read", "write", "owner"]

pg_password = get_credentials(rag_pg_password)


def get_top_similar_qas(query_embedding, src_ids, limit=5):
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=3306,
    ) as conn:

        # Register pgvector extension
        register_vector(conn)
        with conn.cursor() as cur:
            # Ensure the query_embedding is a list of floats
            assert isinstance(
                query_embedding, list
            ), "Expected query_embedding to be a list of floats"
            # print(f"here is the query embedding {query_embedding}")

            # Convert the query_embedding list to a PostgreSQL array literal
            embedding_literal = "[" + ",".join(map(str, query_embedding)) + "]"

            # Prepare SQL query and parameters based on whether src_ids are provided
            query_params = [embedding_literal, limit]  # query_embedding is already a list of floats
            src_ids_array = "{}"
            if src_ids:
                # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
                src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"


            query_params.insert(
                1, src_ids_array
            )  # Append the limit to the query parameters
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
                logger.error(
                    f"An error occurred while fetching top similar QAs: {e}",
                    exc_info=True,
                )
                raise
    return top_docs


def get_top_similar_docs(query_embedding, src_ids, limit=5):
    with psycopg2.connect(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=3306,
    ) as conn:

        # Register pgvector extension
        register_vector(conn)
        with conn.cursor() as cur:

            # print(f"Here is the query embedding {query_embedding}")
            # Ensure the query_embedding is a list of floats
            assert isinstance(
                query_embedding, list
            ), "Expected query_embedding to be a list of floats"
            src_ids_array = "{}"
            if src_ids:
                # Convert src_ids list to a format suitable for the ANY clause in PostgreSQL
                src_ids_array = "{" + ",".join(map(str, src_ids)) + "}"

            # Prepare the query parameters
            # Note: query_embedding is passed directly as a list of floats
            query_params = [
                query_embedding,
                src_ids_array,
                limit,
            ]  # src_ids should be a tuple for psycopg2 to convert it to an array

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
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(object_access_table)

    try:
        # Iterate over each src_id and perform a query
        for src_id in raw_src_ids:
            response = table.query(
                KeyConditionExpression=Key("object_id").eq(src_id)
                & Key("principal_id").eq(current_user)
            )

            # Check if the response has any items with the required permission levels
            items_with_access = [
                item
                for item in response.get("Items", [])
                if item["permission_level"] in permission_levels
            ]

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
def classify_group_src_ids_by_access(raw_group_src_ids, current_user, token):
    accessible_src_ids = []
    access_denied_src_ids = []

    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource("dynamodb")
    group_table = dynamodb.Table(groups_table)
    obj_access_table = dynamodb.Table(object_access_table)
    # if user meets the following criteria for a group then all datasources are approved
    # prereq groupId has perms
    # 1. group is public
    # 2. user is a member of the group
    # 3. is member of listed amplify groups - call
    # Iterate over each groupId and associated dataSourceIds
    for groupId, dataSourceIds in raw_group_src_ids.items():
        print("Checking perms for GroupId: ", groupId, "\nDS Ids: ", dataSourceIds)
        try:
            # ensure the groupId has perms to the ds
            accessible_to_group_ds_ids = []
            for src_id in dataSourceIds:
                response = obj_access_table.query(
                    KeyConditionExpression=Key("object_id").eq(src_id)
                    & Key("principal_id").eq(groupId)
                )
                print(response.get("Items", []))
                # Check if the response has any items with the required permission levels
                items_with_access = [
                    item
                    for item in response.get("Items", [])
                    if item["permission_level"] in permission_levels
                ]
                print("i with access ", items_with_access)

                if items_with_access:
                    accessible_to_group_ds_ids.append(src_id)
                else:
                    accessible_to_group_ds_ids.append(src_id)

            denied_ids = set(dataSourceIds) - set(accessible_to_group_ds_ids)
            access_denied_src_ids.extend(denied_ids)

            print(
                f"group has access to the following ids: {accessible_to_group_ds_ids}\n Group was denied access to the following ids: {denied_ids}"
            )
            # ensure user is a part of the group
            response = group_table.get_item(Key={"group_id": groupId})
            # Check if the item was found
            if "Item" in response:
                item = response["Item"]
                # Check if the group is public or if the user is in the members
                if (
                    item.get("isPublic", False)
                    or current_user in item.get("members", {}).keys()
                    or current_user in item.get("systemUsers", [])
                    or verify_user_in_amp_group(token, item.get("amplifyGroups", []))
                ):
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


@api_tool(
    path="/embedding-dual-retrieval",
    name="retrieveEmbeddings",
    method="POST",
    tags=["apiDocumentation"],
    description="""Retrieve embeddings from Amplify data sources based on user input using the dual retrieval method.

    Example request:
    {
        "data": {
            "userInput": "Can you describe the policies outlined in the document?",
            "dataSources": ["global/09342587234089234890.content.json"],
            "limit": 10
        }
    }

    Example response:
    {
        "result": [
            {
                "content": "xmlns:w=3D'urn:schemas-microsoft-com:office:word' ...",
                "file": "global/24059380341.content.json",
                "line_numbers": [15, 30],
                "score": 0.7489801645278931
            }
        ]
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "userInput": {
                "type": "string",
                "description": "Query text for embedding retrieval. Example: 'What are the main points of this document?'.",
            },
            "dataSources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of data source IDs to retrieve embeddings from. These ids must start with global/ Example: ['global/09342587234089234890.content.json']. User can find these keys by calling the /files/query endpoint",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return. Default: 10.",
            },
        },
        "required": ["userInput", "dataSources"],
    },
    output={
        "type": "object",
        "properties": {
            "result": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The content text from the embedding",
                        },
                        "file": {
                            "type": "string",
                            "description": "The source file identifier",
                        },
                        "line_numbers": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Line numbers where the content appears",
                        },
                        "score": {
                            "type": "number",
                            "description": "Similarity score for the embedding match",
                        },
                    },
                },
                "description": "Array of embedding results with content and metadata",
            }
        },
        "required": ["result"],
    },
)
@validated("dual-retrieval")
def process_input_with_dual_retrieval(event, context, current_user, name, data):
    token = data["access_token"]
    account_data = {
            "user": current_user,
            "account": data["account"],
            "rate_limit": data["rate_limit"],
            "access_token": token,
        }
    data = data["data"]
    content = data["userInput"]
    raw_src_ids = data["dataSources"]
    raw_group_src_ids = data.get("groupDataSources", {})
    limit = data.get("limit", 10)

    accessible_src_ids, access_denied_src_ids = classify_src_ids_by_access(
        raw_src_ids, current_user
    )
    group_accessible_src_ids, group_access_denied_src_ids = (
        classify_group_src_ids_by_access(raw_group_src_ids, current_user, token)
    )

    src_ids = accessible_src_ids + group_accessible_src_ids

    # wait until all embeddings are completed
    pending_ids = src_ids
    is_complete = False
    iteration_count = 0
    logger.info(
        f"Starting embedding completion check for {len(src_ids)} data sources: {src_ids}"
    )

    while not is_complete:
        iteration_count += 1
        logger.info(
            f"Polling iteration {iteration_count}: Waiting for embedding completion..."
        )
        time.sleep(3)
        completion_result = check_embedding_completion(pending_ids, account_data)
        possible_pending_ids = completion_result["requires_embedding"]
        is_complete = (
            completion_result["all_complete"] and len(possible_pending_ids) == 0
        )
        pending_ids = completion_result["pending_ids"]

        if is_complete:
            logger.info(f"All embeddings completed after {iteration_count} iterations")
        else:
            logger.info(
                f"Iteration {iteration_count}: {len(pending_ids) + len(possible_pending_ids)} data sources still pending embedding completion: {pending_ids + possible_pending_ids} "
            )
            if iteration_count % 10 == 0:  # Log a summary every 10 iterations
                logger.warning(
                    f"Long-running embedding completion check: {iteration_count} iterations completed, still waiting for {len(pending_ids) + len(possible_pending_ids)} data sources"
                )

    logger.info("Starting embedding generation for user input query")

    response_embeddings = generate_embeddings(content)

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

    return {"result": related_docs}


@validated("embeddings-check")
def queue_missing_embeddings(event, context, current_user, name, data):
    src_ids = data["data"]["dataSources"]
    account_data = {
            "user": current_user,
            "account": data["account"],
            "rate_limit": data["rate_limit"],
            "access_token": data["access_token"],
        }
    completion_result = check_embedding_completion(src_ids, account_data)
    embed_ids = completion_result["requires_embedding"]

    failed_ids = []
    if embed_ids:
        print(f"Queueing {len(embed_ids)} documents for embedding")
        for src_id in embed_ids:
            queue_result = manually_queue_embedding(src_id, account_data)
            if not queue_result["success"]:
                failed_ids.append(src_id)

    result = {"success": len(failed_ids) == 0}
    if failed_ids:
        print(f"Failed to queue {len(failed_ids)} documents for embedding: ", failed_ids)
        result["failed_ids"] = failed_ids

    return result


def check_embedding_completion(src_ids, account_data):
    if not src_ids:
        return {"all_complete": True, "pending_ids": [], "requires_embedding": []}

    dynamodb = boto3.resource("dynamodb")
    embedding_progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
    table = dynamodb.Table(embedding_progress_table)

    pending_ids = []
    requires_embedding = []
    print(f"Checking embedding completion for {src_ids}")

    for src_id in src_ids:
        try:
            # Normalize the source ID format
            trimmed_src = (
                src_id.split(".json")[0] + ".json" if ".json" in src_id else src_id
            )
            response = table.get_item(Key={"object_id": trimmed_src})

            # No record means document hasn't been submitted for embedding yet
            if "Item" not in response:
                logging.info(
                    f"[NO EMBEDDING RECORD] No embedding process record found for {src_id}"
                )
                requires_embedding.append(src_id)
                continue

            item = response["Item"]
            parent_status = item.get("parentChunkStatus", "")
            is_terminated = item.get("terminated", False)
            last_updated = item.get("lastUpdated")

            # Check for stalled jobs (running for too long without updates)
            if parent_status in ["starting", "processing"] and last_updated:
                try:
                    # Parse timestamp and ensure it's UTC-aware
                    if last_updated.endswith("Z"):
                        last_updated_time = datetime.fromisoformat(
                            last_updated.replace("Z", "+00:00")
                        )
                    else:
                        # Parse and assume UTC if no timezone info
                        last_updated_time = datetime.fromisoformat(last_updated)
                        if last_updated_time.tzinfo is None:
                            last_updated_time = last_updated_time.replace(
                                tzinfo=timezone.utc
                            )

                    current_time = datetime.now(timezone.utc)

                    # If job has been running for more than 3 minutes without updates, consider it stalled
                    if (
                        current_time - last_updated_time
                    ).total_seconds() > 600:  # 10 minutes
                        logging.warning(
                            f"[STALLED JOB] Document {src_id} appears stalled in state {parent_status}. Last updated: {last_updated}"
                        )
                        # Not re-embedding for now
                        # manually_queue_embedding(src_id)
                        # pending_ids.append(src_id)
                        continue
                except (ValueError, TypeError):
                    logging.warning(
                        f"[DATETIME ERROR] Could not parse timestamp for {src_id}: {last_updated}. Skipping stall check."
                    )
                    pass

            if parent_status == "completed":
                logging.info(f"[COMPLETED] Document {src_id} embedding is complete")
                continue  # Skip adding to pending_ids
            elif parent_status == "failed":
                logging.warning(
                    f"[FAILED] Document {src_id} embedding failed. Requeuing."
                )
                manually_queue_embedding(src_id, account_data)
                pending_ids.append(src_id)
                continue
            elif parent_status in ["starting", "processing"]:
                logging.info(
                    f"[PROCESSING] Document {src_id} is still being embedded. Current status: {parent_status}"
                )
                pending_ids.append(src_id)
                continue

            # Check termination flag (which may be separate from status)
            if is_terminated: 
                logging.warning(
                    f"[TERMINATED] Document {src_id} embedding was terminated. Requeuing."
                )
                manually_queue_embedding(src_id, account_data)
                pending_ids.append(src_id)
                continue

            # If we reach here, there's an unexpected status value
            logging.warning(
                f"[UNEXPECTED STATUS] Document {src_id} has unexpected status: {parent_status}. Treating as pending."
            )

        except Exception as e:
            logging.error(
                f"Error checking embedding progress for {src_id}: {str(e)}",
                exc_info=True,
            )
            # Conservatively assume it's still processing if we can't check
            pending_ids.append(src_id)

    return {
        "all_complete": len(pending_ids) == 0,
        "pending_ids": pending_ids,
        "requires_embedding": requires_embedding,
    }


def manually_queue_embedding(src_id, account_data):
    if not "global/" in src_id:
        print(f"Skipping non-global document {src_id}")
        return {"success": False}

    try:
        if not store_ds_secrets_for_rag(src_id, account_data)['success']:
            return {
                "success": False,
                "message": "Failed to store RAG secrets for document",
            }
        
        # Reset embedding status to "starting" to allow reprocessing
        logging.info(f"[MANUAL_QUEUE] Resetting embedding status to 'starting' for {src_id}")
        reset_embedding_status_to_starting(src_id)
        
        # Create a record for manual processing
        record = {"s3": {"bucket": {"name": s3_bucket}, "object": {"key": src_id}}}

        # Queue the document for processing
        message_body = json.dumps(record)
        logging.info(f"Queueing document for embedding: {message_body}")
        sqs.send_message(QueueUrl=queue_url, MessageBody=message_body)
        return {"success": True}

    except Exception as e:
        logging.error(f"Failed to queue {src_id} for embedding: {str(e)}")
        return {"success": False}


def reset_embedding_status_to_starting(src_id):
    """
    Reset the embedding status to 'starting' for a document to allow reprocessing.
    This is needed when manually re-queueing a failed embedding.
    """
    try:
        embedding_progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(embedding_progress_table)
        
        logging.info(f"[RESET_STATUS] Updating embedding status to 'starting' for {src_id}")
        
        # First, get the current item to see how many child chunks exist
        response = table.get_item(Key={"object_id": src_id})
        item = response.get("Item")
        
        if item:
            # Get existing child chunks to reset their status
            existing_data = item.get("data", {})
            child_chunks = existing_data.get("childChunks", {})
            
            # Build update expression to reset parent status, terminated flag, and all child chunk statuses
            update_expression = "SET parentChunkStatus = :starting, #terminated = :false, #timestamp = :timestamp"
            expression_attribute_values = {
                ":starting": "starting",
                ":false": False,
                ":timestamp": datetime.now().isoformat()
            }
            expression_attribute_names = {
                "#timestamp": "timestamp",
                "#terminated": "terminated"
            }
            
            # Add child chunk status updates
            for chunk_id in child_chunks:
                update_expression += f", #data.#childChunks.#chunk_{chunk_id}.#status = :starting"
                expression_attribute_names[f"#chunk_{chunk_id}"] = str(chunk_id)
            
            # Add common attribute names
            if child_chunks:
                expression_attribute_names.update({
                    "#data": "data",
                    "#childChunks": "childChunks", 
                    "#status": "status"
                })
            
            # Update the item
            table.update_item(
                Key={"object_id": src_id},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values
            )
            
            logging.info(f"[RESET_STATUS] ✅ Successfully reset parent and {len(child_chunks)} child chunks to 'starting' status for {src_id}")
        else:
            logging.warning(f"[RESET_STATUS] No existing embedding progress found for {src_id} - will be created during processing")
            
    except Exception as e:
        logging.error(f"[RESET_STATUS] ❌ Failed to reset embedding status for {src_id}: {str(e)}")
        # Don't raise the exception - let the manual queue continue even if reset fails
