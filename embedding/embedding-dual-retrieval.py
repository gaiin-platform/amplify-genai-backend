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
import asyncio
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
    groups_table = os.environ["GROUPS_DYNAMO_TABLE"]
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

def classify_ast_src_ids_by_access(raw_ast_src_ids, current_user, token):
    # if user meets the following criteria for an ast then all datasources are approved
    # user has access to the ast
    # ast is a standalone ast 
    ### is publis
    ### is a listed member
    ### is member of listed amplify group
    # if yes to any then we must check if the ast has access to the datasources
    # classify_src_ids_by_access(raw_src_ids, assistant_id)
    
    accessible_src_ids = []
    access_denied_src_ids = []

    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource("dynamodb")
    assistant_lookup_table_name = os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE")
    
    if not assistant_lookup_table_name:
        logger.error("ASSISTANT_LOOKUP_DYNAMODB_TABLE environment variable is not set")
        # If table is not configured, deny all access
        for ast_id, data_source_ids in raw_ast_src_ids.items():
            access_denied_src_ids.extend(data_source_ids)
        return accessible_src_ids, access_denied_src_ids
    
    lookup_table = dynamodb.Table(assistant_lookup_table_name)

    # Iterate over each ast_id and associated dataSourceIds  
    for ast_id, data_source_ids in raw_ast_src_ids.items():
        logger.info(f"Checking permissions for AST ID: {ast_id}, DS IDs: {data_source_ids}")
        
        try:
            # Query the lookup table directly by assistantId using GSI
            logger.info(f"Querying DynamoDB lookup table for assistantId: {ast_id}")
            response = lookup_table.query(
                IndexName="AssistantIdIndex",
                KeyConditionExpression=Key("assistantId").eq(ast_id)
            )
            
            logger.info(f"DynamoDB response: {response}")
            
            # Check if any items were found
            if not response.get("Items") or len(response["Items"]) == 0:
                logger.info(f"No item found for assistantId: '{ast_id}'")
                access_denied_src_ids.extend(data_source_ids)
                continue

            item = response["Items"][0]
            # Get accessTo information
            access_to = item.get("accessTo", {})
            # Check if the assistant is public or if the user has access
            has_access = False
            
            if item.get("public", False):
                # Assistant is public
                has_access = True
                logger.info(f"AST {ast_id} is public - access granted")
            elif current_user == item.get("createdBy"):
                # User is the creator
                has_access = True
                logger.info(f"User {current_user} is creator of AST {ast_id} - access granted")
            elif current_user in access_to.get("users", []):
                # User is in the allowed users list
                has_access = True
                logger.info(f"User {current_user} is listed in accessTo.users for AST {ast_id} - access granted")
            elif verify_user_in_amp_group(token, access_to.get("amplifyGroups", [])):
                # User is member of allowed amplify groups
                has_access = True
                logger.info(f"User {current_user} is member of amplifyGroups for AST {ast_id} - access granted")
            
            if has_access:
                # User has access to the AST, now check if AST has access to the datasources
                logger.info(f"User has access to AST {ast_id}, checking AST access to datasources")
                ast_accessible_src_ids, ast_access_denied_src_ids = classify_src_ids_by_access(data_source_ids, ast_id)
                accessible_src_ids.extend(ast_accessible_src_ids)
                access_denied_src_ids.extend(ast_access_denied_src_ids)
            else:
                # User does not have access to the AST
                logger.info(f"User {current_user} does not have access to AST {ast_id} - access denied")
                access_denied_src_ids.extend(data_source_ids)
                
        except Exception as e:
            logger.error(f"Error processing AST {ast_id}: {str(e)}")
            # In case of error, deny access to be safe
            access_denied_src_ids.extend(data_source_ids)

    logger.info(f"AST Accessible src_ids: {accessible_src_ids}, AST Access denied src_ids: {access_denied_src_ids}")
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
    """
    Synchronous wrapper for the async dual retrieval implementation.
    """
    return asyncio.run(_async_process_input_with_dual_retrieval(event, context, current_user, name, data))


async def _async_process_input_with_dual_retrieval(event, context, current_user, name, data):
    """
    Async implementation of dual retrieval processing.
    """
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
    raw_ast_src_ids = data.get("astDataSources", {})
    limit = data.get("limit", 10)

    accessible_src_ids, access_denied_src_ids = classify_src_ids_by_access(raw_src_ids, current_user)
    group_accessible_src_ids, group_access_denied_src_ids = classify_group_src_ids_by_access(raw_group_src_ids, current_user, token)
    ast_accessible_src_ids, ast_access_denied_src_ids = classify_ast_src_ids_by_access(raw_ast_src_ids, current_user, token)

    src_ids = accessible_src_ids + group_accessible_src_ids + ast_accessible_src_ids

    # wait until all embeddings are completed - only check individual accessible sources
    pending_ids = accessible_src_ids  # Only check individual user accessible sources, not group sources
    is_complete = False
    iteration_count = 0
    requeue_failures = {}  # Track requeue failures per document
    max_requeue_failures = 3  # Maximum failures before giving up
    failed_documents = []  # Track documents that failed after max retries
    
    logger.info(
        f"Starting embedding completion check for {len(accessible_src_ids)} individual accessible data sources: {accessible_src_ids}"
    )

    while not is_complete:
        iteration_count += 1
        logger.info(
            f"Polling iteration {iteration_count}: Waiting for embedding completion..."
        )
        if (iteration_count > 1): await asyncio.sleep(3)
        completion_result = await check_embedding_completion(pending_ids, account_data, requeue_failures, max_requeue_failures)
        possible_pending_ids = completion_result["requires_embedding"]
        is_complete = completion_result["all_complete"] and len(possible_pending_ids) == 0
        
        pending_ids = completion_result["pending_ids"]
        failed_documents = completion_result.get("failed_documents", [])
        
        # Check if we have documents that exceeded max requeue failures
        if failed_documents:
            logger.warning(f"Failed to process embeddings for {len(failed_documents)} documents after {max_requeue_failures} requeue attempts: {failed_documents}")
            # Remove failed documents from the source IDs so we can continue with successful ones
            for failed_doc in failed_documents:
                if failed_doc in src_ids:
                    src_ids.remove(failed_doc)
                if failed_doc in pending_ids:
                    pending_ids.remove(failed_doc)
            
            # If ALL documents failed, then return error
            if len(src_ids) == 0:
                logger.error(f"All documents failed embedding. Cannot proceed with retrieval.")
                return {
                    "error": f"All {len(failed_documents)} documents failed to process embeddings after multiple attempts",
                    "failed_documents": failed_documents,
                    "details": "No documents could be embedded. Please try again later or contact support."
                }
            
            # Otherwise, continue with partial success
            logger.info(f"Continuing with {len(src_ids)} successful documents out of {len(src_ids) + len(failed_documents)} total")
            # Mark as complete since we're removing failed docs from pending
            is_complete = len(pending_ids) == 0

        if is_complete:
            logger.info(f"All individual accessible embeddings completed after {iteration_count} iterations")
        else:
            logger.info(
                f"Iteration {iteration_count}: {len(pending_ids) + len(possible_pending_ids)} individual accessible data sources still pending embedding completion: {pending_ids + possible_pending_ids} "
            )
            if iteration_count % 10 == 0:  # Log a summary every 10 iterations
                logger.warning(
                    f"Long-running embedding completion check: {iteration_count} iterations completed, still waiting for {len(pending_ids) + len(possible_pending_ids)} individual accessible data sources"
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

    # Build response with partial success information if needed
    response = {"result": related_docs}
    
    # Add warning about failed documents if any
    if failed_documents:
        response["warning"] = f"Partial results: {len(failed_documents)} document(s) could not be processed"
        response["failed_documents"] = failed_documents
        response["successful_documents"] = src_ids
        logger.info(f"Returning partial results from {len(src_ids)} successful documents, {len(failed_documents)} failed")
    
    return response


@validated("embeddings-check")
def queue_missing_embeddings(event, context, current_user, name, data):
    """
    Synchronous wrapper for the async queue missing embeddings implementation.
    """
    return asyncio.run(_async_queue_missing_embeddings(event, context, current_user, name, data))


async def _async_queue_missing_embeddings(event, context, current_user, name, data):
    """
    Async implementation of queue missing embeddings.
    """
    src_ids = data["data"]["dataSources"]
    account_data = {
            "user": current_user,
            "account": data["account"],
            "rate_limit": data["rate_limit"],
            "access_token": data["access_token"],
        }
    completion_result = await check_embedding_completion(src_ids, account_data, None, 3)
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


async def check_embedding_completion(src_ids, account_data, requeue_failures=None, max_requeue_failures=3):
    if not src_ids:
        return {"all_complete": True, "pending_ids": [], "requires_embedding": [], "failed_documents": []}

    embedding_progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
    print(f"Checking embedding completion for {src_ids}")

    # Initialize requeue_failures if not provided
    if requeue_failures is None:
        requeue_failures = {}
    
    async def check_single_embedding(src_id):
        """Check embedding status for a single source ID"""
        pending_ids = []
        requires_embedding = []
        
        def sync_check():
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(embedding_progress_table)
            
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
                    return {"pending": [], "requires_embedding": requires_embedding}

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

                        # If job has been running for more than 30 minutes without updates, consider it stalled
                        if (
                            current_time - last_updated_time
                        ).total_seconds() > 1800:  # 30 minutes (increased from 10)
                            # Track requeue failures for stalled documents
                            if requeue_failures is not None:
                                failures = requeue_failures.get(src_id, 0)
                                if failures >= max_requeue_failures:
                                    logging.error(
                                        f"[MAX_FAILURES] Stalled document {src_id} exceeded max requeue attempts ({max_requeue_failures})"
                                    )
                                    return {"pending": [], "requires_embedding": [], "failed": src_id}
                            
                            logging.warning(
                                f"[STALLED JOB] Document {src_id} appears stalled in state {parent_status}. Last updated: {last_updated}. Attempting requeue (attempt {requeue_failures.get(src_id, 0) + 1} of {max_requeue_failures})."
                            )
                            
                            requeue_result = manually_queue_embedding(src_id, account_data)
                            if not requeue_result.get("success", False):
                                if requeue_failures is not None:
                                    requeue_failures[src_id] = requeue_failures.get(src_id, 0) + 1
                                    logging.error(f"[REQUEUE_FAILED] Failed to requeue stalled {src_id}. Failure count: {requeue_failures[src_id]}")
                            
                            pending_ids.append(src_id)
                            return {"pending": pending_ids, "requires_embedding": []}
                    except (ValueError, TypeError):
                        logging.warning(
                            f"[DATETIME ERROR] Could not parse timestamp for {src_id}: {last_updated}. Skipping stall check."
                        )
                        pass

                if parent_status == "completed":
                    logging.info(f"[COMPLETED] Document {src_id} embedding is complete")
                    return {"pending": [], "requires_embedding": []}
                elif parent_status == "failed":
                    # Track requeue failures
                    if requeue_failures is not None:
                        failures = requeue_failures.get(src_id, 0)
                        if failures >= max_requeue_failures:
                            logging.error(
                                f"[MAX_FAILURES] Document {src_id} exceeded max requeue attempts ({max_requeue_failures})"
                            )
                            return {"pending": [], "requires_embedding": [], "failed": src_id}
                    
                    logging.warning(
                        f"[FAILED] Document {src_id} embedding failed. Attempting requeue (attempt {requeue_failures.get(src_id, 0) + 1} of {max_requeue_failures})."
                    )
                    
                    requeue_result = manually_queue_embedding(src_id, account_data)
                    if not requeue_result.get("success", False):
                        if requeue_failures is not None:
                            requeue_failures[src_id] = requeue_failures.get(src_id, 0) + 1
                            logging.error(f"[REQUEUE_FAILED] Failed to requeue {src_id}. Failure count: {requeue_failures[src_id]}")
                    
                    pending_ids.append(src_id)
                    return {"pending": pending_ids, "requires_embedding": []}
                elif parent_status in ["starting", "processing"]:
                    logging.info(
                        f"[PROCESSING] Document {src_id} is still being embedded. Current status: {parent_status}"
                    )
                    pending_ids.append(src_id)
                    return {"pending": pending_ids, "requires_embedding": []}

                # Check termination flag (which may be separate from status)
                if is_terminated:
                    # Track requeue failures for terminated documents
                    if requeue_failures is not None:
                        failures = requeue_failures.get(src_id, 0)
                        if failures >= max_requeue_failures:
                            logging.error(
                                f"[MAX_FAILURES] Terminated document {src_id} exceeded max requeue attempts ({max_requeue_failures})"
                            )
                            return {"pending": [], "requires_embedding": [], "failed": src_id}
                    
                    logging.warning(
                        f"[TERMINATED] Document {src_id} embedding was terminated. Attempting requeue (attempt {requeue_failures.get(src_id, 0) + 1} of {max_requeue_failures})."
                    )
                    
                    requeue_result = manually_queue_embedding(src_id, account_data)
                    if not requeue_result.get("success", False):
                        if requeue_failures is not None:
                            requeue_failures[src_id] = requeue_failures.get(src_id, 0) + 1
                            logging.error(f"[REQUEUE_FAILED] Failed to requeue terminated {src_id}. Failure count: {requeue_failures[src_id]}")
                    
                    pending_ids.append(src_id)
                    return {"pending": pending_ids, "requires_embedding": []}

                # If we reach here, there's an unexpected status value
                logging.warning(
                    f"[UNEXPECTED STATUS] Document {src_id} has unexpected status: {parent_status}. Treating as pending."
                )
                return {"pending": [], "requires_embedding": []}

            except Exception as e:
                logging.error(
                    f"Error checking embedding progress for {src_id}: {str(e)}",
                    exc_info=True,
                )
                # Conservatively assume it's still processing if we can't check
                pending_ids.append(src_id)
                return {"pending": pending_ids, "requires_embedding": []}

        # Run the synchronous DynamoDB operation in a thread
        return await asyncio.to_thread(sync_check)

    # Execute all checks concurrently
    tasks = [check_single_embedding(src_id) for src_id in src_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    print("Asynchronous embedding check complete")
    
    # Combine results
    all_pending_ids = []
    all_requires_embedding = []
    all_failed_documents = []
    
    for result in results:
        if isinstance(result, Exception):
            logging.error(f"Error in concurrent embedding check: {result}")
            # Treat exceptions as pending
            continue
        
        all_pending_ids.extend(result.get("pending", []))
        all_requires_embedding.extend(result.get("requires_embedding", []))
        
        # Collect failed documents
        if "failed" in result:
            all_failed_documents.append(result["failed"])

    return {
        "all_complete": len(all_pending_ids) == 0 and len(all_failed_documents) == 0,
        "pending_ids": all_pending_ids,
        "requires_embedding": all_requires_embedding,
        "failed_documents": all_failed_documents,
    }


def manually_queue_embedding(src_id, account_data):
    if not "global/" in src_id:
        print(f"Skipping non-global document {src_id}")
        return {"success": False}

    # Implement retry logic for AWS rate limits
    max_retries = 3
    retry_delay = 2  # Start with 2 seconds
    last_error = None
    
    for retry_attempt in range(max_retries):
        try:
            # Try to store RAG secrets with retry on rate limits
            secrets_stored = False
            for secret_retry in range(max_retries):
                secrets_result = store_ds_secrets_for_rag(src_id, account_data)
                if secrets_result.get('success'):
                    secrets_stored = True
                    break
                    
                # If not last attempt, wait and retry
                if secret_retry < max_retries - 1:
                    wait_time = retry_delay * (2 ** secret_retry)  # Exponential backoff: 2, 4, 8 seconds
                    logging.warning(f"[RATE_LIMIT_RETRY] Failed to store RAG secrets for {src_id}. Retrying in {wait_time} seconds (attempt {secret_retry + 1}/{max_retries})")
                    time.sleep(wait_time)
            
            if not secrets_stored:
                logging.error(f"Failed to store RAG secrets for {src_id} after {max_retries} attempts")
                return {
                    "success": False,
                    "message": "Failed to store RAG secrets after multiple attempts",
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
            error_str = str(e)
            last_error = error_str
            
            # Check if it's an SQS permission error - no point retrying
            if 'AccessDenied' in error_str and 'sqs:sendmessage' in error_str.lower():
                logging.error(f"[PERMISSION_ERROR] Lambda lacks SQS SendMessage permission for {src_id}: {error_str}")
                return {"success": False, "message": "Missing SQS permissions"}
            
            # Check if it's other rate limit errors
            if 'TooManyUpdates' in error_str or 'Throttling' in error_str or 'Rate exceeded' in error_str:
                if retry_attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** retry_attempt)
                    logging.warning(f"[RATE_LIMIT] AWS rate limit error for {src_id}: {error_str}. Retrying in {wait_time} seconds (attempt {retry_attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                    
            logging.error(f"Failed to queue {src_id} for embedding: {error_str}")
            return {"success": False}
    
    logging.error(f"Failed to queue {src_id} after {max_retries} attempts. Last error: {last_error}")
    return {"success": False}


def reset_embedding_status_to_starting(src_id):
    """
    Reset the embedding status to 'starting' for a document to allow reprocessing.
    This is needed when manually re-queueing a failed embedding.
    Raises exceptions on failure to ensure the dual embeddings process stops.
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
            
            # Rebuild child chunks with all statuses set to "starting"
            updated_child_chunks = {}
            for chunk_id, chunk_data in child_chunks.items():
                updated_chunk_data = chunk_data.copy() if isinstance(chunk_data, dict) else {}
                updated_chunk_data["status"] = "starting"
                updated_child_chunks[chunk_id] = updated_chunk_data
            
            # Build update expression to reset parent status, terminated flag, and replace child chunks
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
            
            # Add child chunks replacement if any exist
            if updated_child_chunks:
                update_expression += ", #data.#childChunks = :childChunks"
                expression_attribute_values[":childChunks"] = updated_child_chunks
                expression_attribute_names.update({
                    "#data": "data",
                    "#childChunks": "childChunks"
                })
            
            # Update the item
            table.update_item(
                Key={"object_id": src_id},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values
            )
            
            logging.info(f"[RESET_STATUS] ✅ Successfully reset parent and {len(updated_child_chunks)} child chunks to 'starting' status for {src_id}")
        else:
            logging.warning(f"[RESET_STATUS] No existing embedding progress found for {src_id} - will be created during processing")
            
    except Exception as e:
        logging.error(f"[RESET_STATUS] ❌ Failed to reset embedding status for {src_id}: {str(e)}")
        raise e
