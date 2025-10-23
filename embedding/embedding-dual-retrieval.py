# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

# set up retriever function that accepts a a query, user, and/or list of keys for where claus

import json
import os
import time
import asyncio
import psycopg2
from pgvector.psycopg2 import register_vector
from pycommon.decorators import required_env_vars
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Set, Optional
from datetime import datetime, timezone
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation, SQSOperation, SecretsManagerOperation
)
from pycommon.api.credentials import get_credentials
from shared_functions import generate_embeddings
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
from pycommon.logger import getLogger

add_api_access_types([APIAccessType.CHAT.value, APIAccessType.DUAL_EMBEDDING.value])
setup_validated(rules, get_permission_checker)
logger = getLogger("embedding_dual_retrieval")

pg_host = os.environ["RAG_POSTGRES_DB_READ_ENDPOINT"]
pg_user = os.environ["RAG_POSTGRES_DB_USERNAME"]
pg_database = os.environ["RAG_POSTGRES_DB_NAME"]
rag_pg_password = os.environ["RAG_POSTGRES_DB_SECRET"]
api_version = os.environ["API_VERSION"]
object_access_table = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]
queue_url = os.environ["RAG_CHUNK_DOCUMENT_QUEUE_URL"]
s3_bucket = os.environ["S3_FILE_TEXT_BUCKET_NAME"]
sqs = boto3.client("sqs")

# Define the permission levels that grant access
permission_levels = ["read", "write", "owner"]

pg_password = get_credentials(rag_pg_password)

class EmbeddingPerformanceCache:
    """
    Caching layer for embedding operations to eliminate redundant checks.
    """
    def __init__(self):
        self._permission_cache: Dict[str, Dict[str, Tuple[bool, float]]] = {}  # user -> {src_id: (has_access, timestamp)}
        self._embedding_status_cache: Dict[str, Tuple[str, float]] = {}  # src_id -> (status, timestamp)
        self._failed_documents_cache: Dict[str, Tuple[str, float, int]] = {}  # src_id -> (failure_reason, timestamp, failure_count)
        self._group_permission_cache: Dict[str, Dict[str, Tuple[List[str], List[str], float]]] = {}  # user -> {group_key: (accessible, denied, timestamp)}
        self._ast_permission_cache: Dict[str, Dict[str, Tuple[List[str], List[str], float]]] = {}  # user -> {ast_key: (accessible, denied, timestamp)}
        
        # Cache TTL settings (in seconds)
        self.permission_cache_ttl = 300  # 5 minutes for permissions
        self.embedding_status_cache_ttl = 30  # 30 seconds for embedding status
        self.failed_documents_cache_ttl = 3600  # 60 minutes for failures (increased from 30)
        self.sqs_permission_failure_ttl = 86400  # 24 hours for SQS permission failures
        
    def _is_cache_valid(self, timestamp: float, ttl: int) -> bool:
        """Check if cache entry is still valid based on TTL"""
        return time.time() - timestamp < ttl
    
    def get_cached_permission(self, user: str, src_id: str) -> Optional[bool]:
        """Get cached permission result for user+src_id"""
        user_cache = self._permission_cache.get(user, {})
        if src_id in user_cache:
            has_access, timestamp = user_cache[src_id]
            if self._is_cache_valid(timestamp, self.permission_cache_ttl):
                return has_access
        return None
    
    def cache_permission(self, user: str, src_id: str, has_access: bool):
        """Cache permission result for user+src_id"""
        if user not in self._permission_cache:
            self._permission_cache[user] = {}
        self._permission_cache[user][src_id] = (has_access, time.time())
    
    def get_cached_embedding_status(self, src_id: str) -> Optional[str]:
        """Get cached embedding status for src_id"""
        if src_id in self._embedding_status_cache:
            status, timestamp = self._embedding_status_cache[src_id]
            if self._is_cache_valid(timestamp, self.embedding_status_cache_ttl):
                return status
        return None
    
    def cache_embedding_status(self, src_id: str, status: str):
        """Cache embedding status for src_id"""
        self._embedding_status_cache[src_id] = (status, time.time())
    
    def is_document_failed(self, src_id: str) -> Tuple[bool, Optional[str], int]:
        """Check if document is marked as permanently failed"""
        if src_id in self._failed_documents_cache:
            failure_data = self._failed_documents_cache[src_id]
            failure_reason, timestamp, failure_count = failure_data[:3]
            ttl_key = failure_data[3] if len(failure_data) > 3 else 'default'
            
            # Use appropriate TTL based on failure type
            ttl = self.sqs_permission_failure_ttl if ttl_key == 'sqs_perm' else self.failed_documents_cache_ttl
            
            if self._is_cache_valid(timestamp, ttl):
                return True, failure_reason, failure_count
        return False, None, 0
    
    def mark_document_failed(self, src_id: str, failure_reason: str, failure_count: int = 1):
        """Mark document as permanently failed"""
        # Use longer TTL for SQS permission failures since they won't fix themselves
        ttl_key = 'sqs_perm' if 'SQS Permission' in failure_reason else 'default'
        self._failed_documents_cache[src_id] = (failure_reason, time.time(), failure_count, ttl_key)
    
    def get_cached_group_permissions(self, user: str, group_key: str) -> Optional[Tuple[List[str], List[str]]]:
        """Get cached group permission results"""
        user_cache = self._group_permission_cache.get(user, {})
        if group_key in user_cache:
            accessible, denied, timestamp = user_cache[group_key]
            if self._is_cache_valid(timestamp, self.permission_cache_ttl):
                return accessible, denied
        return None
    
    def cache_group_permissions(self, user: str, group_key: str, accessible: List[str], denied: List[str]):
        """Cache group permission results"""
        if user not in self._group_permission_cache:
            self._group_permission_cache[user] = {}
        self._group_permission_cache[user][group_key] = (accessible, denied, time.time())
    
    def get_cached_ast_permissions(self, user: str, ast_key: str) -> Optional[Tuple[List[str], List[str]]]:
        """Get cached AST permission results"""
        user_cache = self._ast_permission_cache.get(user, {})
        if ast_key in user_cache:
            accessible, denied, timestamp = user_cache[ast_key]
            if self._is_cache_valid(timestamp, self.permission_cache_ttl):
                return accessible, denied
        return None
    
    def cache_ast_permissions(self, user: str, ast_key: str, accessible: List[str], denied: List[str]):
        """Cache AST permission results"""
        if user not in self._ast_permission_cache:
            self._ast_permission_cache[user] = {}
        self._ast_permission_cache[user][ast_key] = (accessible, denied, time.time())
    
    def cleanup_expired_entries(self):
        """Clean up expired cache entries to prevent memory bloat"""
        current_time = time.time()
        
        # Clean permission cache
        for user in list(self._permission_cache.keys()):
            user_cache = self._permission_cache[user]
            expired_keys = [src_id for src_id, (_, timestamp) in user_cache.items() 
                          if not self._is_cache_valid(timestamp, self.permission_cache_ttl)]
            for key in expired_keys:
                del user_cache[key]
            if not user_cache:
                del self._permission_cache[user]
        
        # Clean embedding status cache
        expired_keys = [src_id for src_id, (_, timestamp) in self._embedding_status_cache.items() 
                       if not self._is_cache_valid(timestamp, self.embedding_status_cache_ttl)]
        for key in expired_keys:
            del self._embedding_status_cache[key]
        
        # Clean failed documents cache with appropriate TTL
        expired_keys = []
        for src_id, failure_data in self._failed_documents_cache.items():
            _, timestamp = failure_data[:2]
            ttl_key = failure_data[3] if len(failure_data) > 3 else 'default'
            ttl = self.sqs_permission_failure_ttl if ttl_key == 'sqs_perm' else self.failed_documents_cache_ttl
            if not self._is_cache_valid(timestamp, ttl):
                expired_keys.append(src_id)
        for key in expired_keys:
            del self._failed_documents_cache[key]

# Global cache instance
performance_cache = EmbeddingPerformanceCache()


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
    """Cached + parallel permission checking with early failure detection"""
    accessible_src_ids = []
    access_denied_src_ids = []
    uncached_src_ids = []

    # Check cache first for all source IDs
    for src_id in raw_src_ids:
        # Skip documents that are permanently failed
        is_failed, failure_reason, _ = performance_cache.is_document_failed(src_id)
        if is_failed:
            access_denied_src_ids.append(src_id)
            continue
        
        # Check permission cache
        cached_permission = performance_cache.get_cached_permission(current_user, src_id)
        if cached_permission is not None:
            if cached_permission:
                accessible_src_ids.append(src_id)
            else:
                access_denied_src_ids.append(src_id)
        else:
            uncached_src_ids.append(src_id)

    # Parallel permission checking for uncached source IDs
    if uncached_src_ids:
        def check_single_permission(src_id):
            """Check permission for a single source ID - designed for parallel execution"""
            try:
                dynamodb = boto3.resource("dynamodb")
                table = dynamodb.Table(object_access_table)
                
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

                has_access = len(items_with_access) > 0
                
                # Cache the result
                performance_cache.cache_permission(current_user, src_id, has_access)
                
                return src_id, has_access
                
            except Exception as e:
                logger.error(f"Error checking permission for {src_id}: {str(e)}")
                # Cache as denied on error to avoid repeated failures
                performance_cache.cache_permission(current_user, src_id, False)
                return src_id, False

        # Execute permission checks in parallel
        max_workers = min(10, len(uncached_src_ids))  # Limit concurrent DynamoDB queries
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all permission check tasks
            future_to_src_id = {executor.submit(check_single_permission, src_id): src_id for src_id in uncached_src_ids}
            
            # Collect results as they complete
            for future in as_completed(future_to_src_id):
                try:
                    src_id, has_access = future.result()
                    if has_access:
                        accessible_src_ids.append(src_id)
                    else:
                        access_denied_src_ids.append(src_id)
                except Exception as e:
                    src_id = future_to_src_id[future]
                    logger.error(f"Error in parallel permission check for {src_id}: {str(e)}")
                    access_denied_src_ids.append(src_id)

    logger.info(f"Accessible src_ids: {accessible_src_ids}, Access denied src_ids: {access_denied_src_ids}")
    
    return accessible_src_ids, access_denied_src_ids


# groupId is the key : list of globals is the value
def classify_group_src_ids_by_access(raw_group_src_ids, current_user, token):
    accessible_src_ids = []
    access_denied_src_ids = []
    
    # Early exit if no group sources
    if not raw_group_src_ids:
        return accessible_src_ids, access_denied_src_ids

    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource("dynamodb")
    groups_table = os.environ["ASSISTANT_GROUPS_DYNAMO_TABLE"]
    group_table = dynamodb.Table(groups_table)
    obj_access_table = dynamodb.Table(object_access_table)
    # if user meets the following criteria for a group then all datasources are approved
    # prereq groupId has perms
    # 1. group is public
    # 2. user is a member of the group
    # 3. is member of listed amplify groups - call
    # Iterate over each groupId and associated dataSourceIds
    for groupId, dataSourceIds in raw_group_src_ids.items():
        logger.debug("Checking perms for GroupId: %s DS Ids: %s", groupId, dataSourceIds)
        try:
            # ensure the groupId has perms to the ds
            accessible_to_group_ds_ids = []
            for src_id in dataSourceIds:
                response = obj_access_table.query(
                    KeyConditionExpression=Key("object_id").eq(src_id)
                    & Key("principal_id").eq(groupId)
                )
                logger.debug("Response items: %s", response.get("Items", []))
                # Check if the response has any items with the required permission levels
                items_with_access = [
                    item
                    for item in response.get("Items", [])
                    if item["permission_level"] in permission_levels
                ]
                logger.debug("Items with access: %s", items_with_access)

                if items_with_access:
                    accessible_to_group_ds_ids.append(src_id)
                else:
                    accessible_to_group_ds_ids.append(src_id)

            denied_ids = set(dataSourceIds) - set(accessible_to_group_ds_ids)
            access_denied_src_ids.extend(denied_ids)

            logger.info(
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
            logger.error(f"An error occurred while processing groupId {groupId}: {e}")
            # In case of an error, consider the current groupId's src_ids as denied
            access_denied_src_ids.extend(dataSourceIds)

    logger.debug(f"Group Accessible src_ids: {accessible_src_ids}, Group Access denied src_ids: {access_denied_src_ids}")
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
    
    # Early exit if no AST sources
    if not raw_ast_src_ids:
        return accessible_src_ids, access_denied_src_ids

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
@required_env_vars({
    "OBJECT_ACCESS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY],
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "EMBEDDING_PROGRESS_TABLE": [
        DynamoDBOperation.GET_ITEM,
        DynamoDBOperation.UPDATE_ITEM,
    ],
    "RAG_CHUNK_DOCUMENT_QUEUE_URL": [SQSOperation.SEND_MESSAGE],
    "RAG_POSTGRES_DB_SECRET": [SecretsManagerOperation.GET_SECRET_VALUE],
    "LLM_ENDPOINTS_SECRETS_NAME_ARN": [SecretsManagerOperation.GET_SECRET_VALUE],
    "APP_ARN_NAME": [SecretsManagerOperation.GET_SECRET_VALUE],
})
@validated("dual-retrieval")
def process_input_with_dual_retrieval(event, context, current_user, name, data):
    """
    Synchronous wrapper for the async dual retrieval implementation.
    """
    return asyncio.run(_async_process_input_with_dual_retrieval(event, context, current_user, name, data))


async def _async_process_input_with_dual_retrieval(event, context, current_user, name, data):
    """
    Optimized dual retrieval processing with comprehensive performance enhancements
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

    # Parallel permission checking
    async def check_individual_permissions():
        return classify_src_ids_by_access(raw_src_ids, current_user)
    
    async def check_group_permissions():
        return classify_group_src_ids_by_access(raw_group_src_ids, current_user, token)
    
    async def check_ast_permissions():
        return classify_ast_src_ids_by_access(raw_ast_src_ids, current_user, token)

    # Run all permission checks in parallel - only run what's needed
    tasks = []
    results_map = {}
    
    if raw_src_ids:
        tasks.append(('individual', asyncio.to_thread(check_individual_permissions)))
    if raw_group_src_ids:
        tasks.append(('group', asyncio.to_thread(check_group_permissions)))
    if raw_ast_src_ids:
        tasks.append(('ast', asyncio.to_thread(check_ast_permissions)))
    
    if tasks:
        task_results = await asyncio.gather(*[task for _, task in tasks])
        for i, (task_type, _) in enumerate(tasks):
            results_map[task_type] = task_results[i]
    
    # Extract results with defaults for skipped checks
    accessible_src_ids, _ = results_map.get('individual', ([], []))
    group_accessible_src_ids, _ = results_map.get('group', ([], []))
    ast_accessible_src_ids, _ = results_map.get('ast', ([], []))

    src_ids = accessible_src_ids + group_accessible_src_ids + ast_accessible_src_ids

    # Advanced polling strategy with exponential backoff and early termination
    pending_ids = accessible_src_ids
    is_complete = False
    iteration_count = 0
    requeue_failures = {}
    max_requeue_failures = 3
    failed_documents = []
    
    # Polling parameters - optimized for faster failure detection
    max_iterations = 6  # Reduced from 8 - fail faster for bad documents
    base_sleep_time = 0.5  # Start with shorter wait
    max_sleep_time = 3  # Cap at 3 seconds instead of 5
    exponential_backoff = True
    
    # Pre-filter out known failed documents
    filtered_accessible_src_ids = []
    pre_failed_documents = []
    for src_id in accessible_src_ids:
        is_failed, failure_reason, _ = performance_cache.is_document_failed(src_id)
        if is_failed:
            logger.info(f"Pre-filtering failed document {src_id[:40]}... - {failure_reason}")
            pre_failed_documents.append(src_id)
        else:
            filtered_accessible_src_ids.append(src_id)
    
    accessible_src_ids = filtered_accessible_src_ids
    failed_documents.extend(pre_failed_documents)
    
    logger.info(f"Starting embedding completion check for {len(accessible_src_ids)} individual accessible data sources (filtered {len(pre_failed_documents)} pre-failed): {accessible_src_ids[:5]}..." if len(accessible_src_ids) > 5 else accessible_src_ids)
    
    while not is_complete and iteration_count < max_iterations:
        iteration_count += 1
        logger.info(f"Polling iteration {iteration_count}: Waiting for embedding completion...")
        
        # Dynamic sleep times based on iteration
        if iteration_count > 1:
            if exponential_backoff:
                sleep_time = min(base_sleep_time * (2 ** (iteration_count - 2)), max_sleep_time)
            else:
                sleep_time = base_sleep_time
            
            await asyncio.sleep(sleep_time)
        
        # Check embedding completion using optimized caching system
        completion_result = await check_embedding_completion(pending_ids, account_data, requeue_failures, max_requeue_failures)
        possible_pending_ids = completion_result["requires_embedding"]
        is_complete = completion_result["all_complete"] and len(possible_pending_ids) == 0
        
        pending_ids = completion_result["pending_ids"]
        failed_documents = completion_result.get("failed_documents", [])
        
        # Handle failed documents intelligently
        if failed_documents:
            logger.warning(f"Failed to process embeddings for {len(failed_documents)} documents after {max_requeue_failures} requeue attempts: {failed_documents}")
            
            # Remove failed documents from processing
            for failed_doc in failed_documents:
                if failed_doc in src_ids:
                    src_ids.remove(failed_doc)
                if failed_doc in pending_ids:
                    pending_ids.remove(failed_doc)
            
            # If ALL documents failed, return graceful error with more context
            if len(src_ids) == 0:
                logger.error(f"All documents failed embedding. Cannot proceed with retrieval.")
                # Categorize failures for better diagnostics
                failure_categories = {}
                for doc_id in failed_documents:
                    is_failed, reason, _ = performance_cache.is_document_failed(doc_id)
                    if is_failed:
                        category = "SQS Permissions" if "SQS" in reason else "Parameter Store" if "Parameter Store" in reason else "Other"
                        failure_categories[category] = failure_categories.get(category, 0) + 1
                
                return {
                    "error": f"All {len(failed_documents)} documents failed to process embeddings",
                    "failed_documents": failed_documents[:10],  # Limit to first 10 for readability
                    "total_failed": len(failed_documents),
                    "failure_breakdown": failure_categories,
                    "details": "Documents marked as failed and cached. Please check Lambda permissions for SQS and Parameter Store."
                }
            
            # Continue with partial success
            logger.info(f"Continuing with {len(src_ids)} successful documents out of {len(src_ids) + len(failed_documents)} total")
            is_complete = len(pending_ids) == 0

        # Early termination and timeout protection
        if is_complete:
            logger.info(f"All embeddings completed after {iteration_count} iterations")
        else:
            logger.info(f"Iteration {iteration_count}: {len(pending_ids) + len(possible_pending_ids)} sources still pending: {pending_ids + possible_pending_ids}")
            
            if iteration_count >= max_iterations:
                logger.warning(f"Reached max iterations ({max_iterations})")
                logger.warning(f"Continuing with completed embeddings, {len(pending_ids)} may be incomplete")
                break

    # Parallel embedding generation and retrieval
    logger.info("Starting embedding generation for user input query")

    response_embeddings = generate_embeddings(content)

    if response_embeddings["success"]:
        embeddings = response_embeddings["data"]
        token_count = response_embeddings["token_count"]
        logger.info(f"Generated embeddings with {token_count} tokens")
    else:
        error = response_embeddings["error"]
        logger.error(f"Embedding generation failed: {error}")
        return {"error": error}
    
    async def get_similar_docs():
        return get_top_similar_docs(embeddings, src_ids, limit)
    
    async def get_similar_qas():
        return get_top_similar_qas(embeddings, src_ids, limit)

    # Execute both retrieval operations in parallel
    related_docs, related_qas = await asyncio.gather(
        asyncio.to_thread(get_similar_docs),
        asyncio.to_thread(get_similar_qas)
    )
    
    # Combine results
    related_docs.extend(related_qas)
    
    logger.info(f"Retrieved {len(related_docs)} related documents/QAs")

    # Build response
    response = {
        "result": related_docs,
        "documents_processed": len(src_ids),
        "results_returned": len(related_docs)
    }
    
    # Add warning about failed documents if any
    if failed_documents:
        response["warning"] = f"Partial results: {len(failed_documents)} document(s) could not be processed"
        response["failed_documents"] = failed_documents
        response["successful_documents"] = src_ids
        response["failed_document_count"] = len(failed_documents)
        logger.info(f"Returning results from {len(src_ids)} successful documents, {len(failed_documents)} failed")
    else:
        logger.info(f"All {len(src_ids)} documents processed successfully")
    
    return response


@required_env_vars({
    "EMBEDDING_PROGRESS_TABLE": [
        DynamoDBOperation.GET_ITEM,
        DynamoDBOperation.UPDATE_ITEM,
    ],
    "RAG_CHUNK_DOCUMENT_QUEUE_URL": [SQSOperation.SEND_MESSAGE],
})
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
        logger.info(f"Queueing {len(embed_ids)} documents for embedding")
        for src_id in embed_ids:
            queue_result = manually_queue_embedding(src_id, account_data)
            if not queue_result["success"]:
                failed_ids.append(src_id)

    result = {"success": len(failed_ids) == 0}
    if failed_ids:
        logger.error(f"Failed to queue {len(failed_ids)} documents for embedding: %s", failed_ids)
        result["failed_ids"] = failed_ids

    return result


async def check_embedding_completion(src_ids, account_data, requeue_failures=None, max_requeue_failures=3):
    """Cached embedding status with intelligent failure handling"""
    if not src_ids:
        return {"all_complete": True, "pending_ids": [], "requires_embedding": [], "failed_documents": []}
    
    embedding_progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
    logger.debug(f"Checking embedding completion for {src_ids}")

    # Initialize requeue_failures if not provided
    if requeue_failures is None:
        requeue_failures = {}
    
    # Check cache and failed documents first
    cached_completed = []
    cached_failed = []
    uncached_src_ids = []
    cache_hits = 0
    
    for src_id in src_ids:
        # Check if document is permanently failed
        is_failed, failure_reason, failure_count = performance_cache.is_document_failed(src_id)
        if is_failed:
            logger.warning(f"Skipping permanently failed document: {src_id[:20]}... - {failure_reason}")
            cached_failed.append(src_id)
            continue
        
        # Check embedding status cache
        cached_status = performance_cache.get_cached_embedding_status(src_id)
        if cached_status == "completed":
            logger.info(f"Cache hit: {src_id[:20]}... is completed")
            cached_completed.append(src_id)
            cache_hits += 1
        elif cached_status in ["processing", "starting"]:
            logger.info(f"Cache hit: {src_id[:20]}... is {cached_status} - will check again")
            uncached_src_ids.append(src_id)  # Still need to check for updates
        else:
            uncached_src_ids.append(src_id)
    
    logger.info(f"Cache summary: {cache_hits} completed, {len(cached_failed)} failed, {len(uncached_src_ids)} need checking")
    
    # Parallel status checking for uncached source IDs  
    async def check_single_embedding(src_id):
        """Check embedding status for a single source ID with caching"""
        pending_ids = []
        requires_embedding = []
        
        def sync_check():
            try:
                dynamodb = boto3.resource("dynamodb")
                table = dynamodb.Table(embedding_progress_table)
                
                # Normalize the source ID format
                trimmed_src = (
                    src_id.split(".json")[0] + ".json" if ".json" in src_id else src_id
                )
                response = table.get_item(Key={"object_id": trimmed_src})

                # No record means document hasn't been submitted for embedding yet
                if "Item" not in response:
                    logger.info(
                        f"[NO EMBEDDING RECORD] No embedding process record found for {src_id}"
                    )
                    requires_embedding.append(src_id)
                    performance_cache.cache_embedding_status(src_id, "requires_embedding")
                    return {"pending": [], "requires_embedding": requires_embedding}

                item = response["Item"]
                parent_status = item.get("parentChunkStatus", "")
                is_terminated = item.get("terminated", False)
                last_updated = item.get("lastUpdated")

                # Handle completed status immediately
                if parent_status == "completed":
                    logger.info(f"[COMPLETED] Document {src_id} embedding is complete")
                    performance_cache.cache_embedding_status(src_id, "completed")
                    return {"pending": [], "requires_embedding": []}

                # Check for stalled jobs
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

                        # If job has been running for more than 15 minutes without updates, consider it stalled (reduced from 30)
                        if (current_time - last_updated_time).total_seconds() > 900:  # 15 minutes
                            # Track requeue failures
                            if requeue_failures is not None:
                                failures = requeue_failures.get(src_id, 0)
                                if failures >= max_requeue_failures:
                                    logger.error(
                                        f"[MAX_FAILURES] Stalled document {src_id} exceeded max requeue attempts ({max_requeue_failures})"
                                    )
                                    return {"pending": [], "requires_embedding": [], "failed": src_id}
                            
                            logger.warning(
                                f"[STALLED JOB] Document {src_id} appears stalled in state {parent_status}. Last updated: {last_updated}. Attempting requeue (attempt {requeue_failures.get(src_id, 0) + 1} of {max_requeue_failures})."
                            )
                            
                            # Attempt requeue with failure detection
                            requeue_result = manually_queue_embedding(src_id, account_data)
                            if not requeue_result.get("success", False):
                                if requeue_failures is not None:
                                    requeue_failures[src_id] = requeue_failures.get(src_id, 0) + 1
                                    logger.error(f"[REQUEUE_FAILED] Failed to requeue stalled {src_id}. Failure count: {requeue_failures[src_id]}")
                            
                            pending_ids.append(src_id)
                            performance_cache.cache_embedding_status(src_id, parent_status)  # Cache current status
                            return {"pending": pending_ids, "requires_embedding": []}
                    except (ValueError, TypeError):
                        logger.warning(
                            f"[DATETIME ERROR] Could not parse timestamp for {src_id}: {last_updated}. Skipping stall check."
                        )
                        pass

                if parent_status == "completed":
                    logger.info(f"[COMPLETED] Document {src_id} embedding is complete")
                    return {"pending": [], "requires_embedding": []}
                elif parent_status == "failed":
                    # Track requeue failures
                    if requeue_failures is not None:
                        failures = requeue_failures.get(src_id, 0)
                        if failures >= max_requeue_failures:
                            logger.error(
                                f"[MAX_FAILURES] Document {src_id} exceeded max requeue attempts ({max_requeue_failures})"
                            )
                            return {"pending": [], "requires_embedding": [], "failed": src_id}
                    
                    logger.warning(
                        f"[FAILED] Document {src_id} embedding failed. Attempting requeue (attempt {requeue_failures.get(src_id, 0) + 1} of {max_requeue_failures})."
                    )
                    
                    requeue_result = manually_queue_embedding(src_id, account_data)
                    if not requeue_result.get("success", False):
                        if requeue_failures is not None:
                            requeue_failures[src_id] = requeue_failures.get(src_id, 0) + 1
                            logger.error(f"[REQUEUE_FAILED] Failed to requeue {src_id}. Failure count: {requeue_failures[src_id]}")
                    
                    pending_ids.append(src_id)
                    return {"pending": pending_ids, "requires_embedding": []}
                elif parent_status in ["starting", "processing"]:
                    logger.info(
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
                            logger.error(
                                f"[MAX_FAILURES] Terminated document {src_id} exceeded max requeue attempts ({max_requeue_failures})"
                            )
                            return {"pending": [], "requires_embedding": [], "failed": src_id}
                    
                    logger.warning(
                        f"[TERMINATED] Document {src_id} embedding was terminated. Attempting requeue (attempt {requeue_failures.get(src_id, 0) + 1} of {max_requeue_failures})."
                    )
                    
                    requeue_result = manually_queue_embedding(src_id, account_data)
                    if not requeue_result.get("success", False):
                        if requeue_failures is not None:
                            requeue_failures[src_id] = requeue_failures.get(src_id, 0) + 1
                            logger.error(f"[REQUEUE_FAILED] Failed to requeue terminated {src_id}. Failure count: {requeue_failures[src_id]}")
                    
                    pending_ids.append(src_id)
                    return {"pending": pending_ids, "requires_embedding": []}

                # If we reach here, there's an unexpected status value
                logger.warning(
                    f"[UNEXPECTED STATUS] Document {src_id} has unexpected status: {parent_status}. Treating as pending."
                )
                return {"pending": [], "requires_embedding": []}

            except Exception as e:
                logger.error(
                    f"Error checking embedding progress for {src_id}: {str(e)}",
                    exc_info=True,
                )
                # Conservatively assume it's still processing if we can't check
                pending_ids.append(src_id)
                return {"pending": pending_ids, "requires_embedding": []}

        # Run the synchronous DynamoDB operation in a thread
        return await asyncio.to_thread(sync_check)

    # Parallel processing for uncached source IDs only - batch for better performance
    if uncached_src_ids:
        # Process in batches to avoid overwhelming DynamoDB
        batch_size = 20  # Process 20 documents at a time
        results = []
        
        for i in range(0, len(uncached_src_ids), batch_size):
            batch = uncached_src_ids[i:i + batch_size]
            tasks = [check_single_embedding(src_id) for src_id in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            results.extend(batch_results)
            
            # Small delay between batches to avoid throttling
            if i + batch_size < len(uncached_src_ids):
                await asyncio.sleep(0.1)
        
        print(f"Asynchronous embedding check complete - processed {len(uncached_src_ids)} documents in {(len(uncached_src_ids) + batch_size - 1) // batch_size} batches")
    else:
        results = []
        print("All embedding statuses served from cache!")
    
    # Combine cached results with fresh results
    all_pending_ids = []
    all_requires_embedding = []
    all_failed_documents = cached_failed.copy()  # Start with cached failed documents
    
    # Add results from database checks
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Error in concurrent embedding check: {result}")
            # Treat exceptions as pending
            continue
        
        all_pending_ids.extend(result.get("pending", []))
        all_requires_embedding.extend(result.get("requires_embedding", []))
        
        # Collect failed documents
        if "failed" in result:
            all_failed_documents.append(result["failed"])
    
    # Add cached completed documents to the "all_complete" calculation
    logger.info(f"Embedding check completed: {len(src_ids)} documents processed")
    logger.info(f"Cache efficiency: {cache_hits} cache hits, {len(uncached_src_ids)} DB queries, {len(cached_failed)} failed")

    return {
        "all_complete": len(all_pending_ids) == 0 and len(all_failed_documents) == 0,
        "pending_ids": all_pending_ids,
        "requires_embedding": all_requires_embedding,
        "failed_documents": all_failed_documents,
    }


def manually_queue_embedding(src_id, account_data):
    if not "global/" in src_id:
        logger.debug(f"Skipping non-global document {src_id}")
        return {"success": False}
    
    # Check if already marked as failed in cache to avoid unnecessary work
    is_failed, failure_reason, _ = performance_cache.is_document_failed(src_id)
    if is_failed:
        logger.info(f"[SKIP_FAILED] Document {src_id[:40]}... already marked as failed: {failure_reason}")
        return {"success": False, "message": f"Document permanently failed: {failure_reason}"}

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
                    logger.warning(f"[RATE_LIMIT_RETRY] Failed to store RAG secrets for {src_id}. Retrying in {wait_time} seconds (attempt {secret_retry + 1}/{max_retries})")
                    time.sleep(wait_time)
            
            if not secrets_stored:
                logger.error(f"Failed to store RAG secrets for {src_id} after {max_retries} attempts")
                return {
                    "success": False,
                    "message": "Failed to store RAG secrets after multiple attempts",
                }
            
            # Reset embedding status to "starting" to allow reprocessing
            logger.info(f"[MANUAL_QUEUE] Resetting embedding status to 'starting' for {src_id}")
            reset_embedding_status_to_starting(src_id)
            
            # Create a record for manual processing
            record = {"s3": {"bucket": {"name": s3_bucket}, "object": {"key": src_id}}}

            # Queue the document for processing
            message_body = json.dumps(record)
            logger.info(f"Queueing document for embedding: {message_body}")
            sqs.send_message(QueueUrl=queue_url, MessageBody=message_body)
            return {"success": True}

        except Exception as e:
            error_str = str(e)
            last_error = error_str
            
            # Check if it's an SQS permission error - no point retrying
            if 'AccessDenied' in error_str and 'sqs:sendmessage' in error_str.lower():
                logger.error(f"[PERMISSION_ERROR] Lambda lacks SQS SendMessage permission for {src_id}: {error_str}")
                return {"success": False, "message": "Missing SQS permissions"}
            
            # Check if it's other rate limit errors
            if 'TooManyUpdates' in error_str or 'Throttling' in error_str or 'Rate exceeded' in error_str:
                if retry_attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** retry_attempt)
                    logger.warning(f"[RATE_LIMIT] AWS rate limit error for {src_id}: {error_str}. Retrying in {wait_time} seconds (attempt {retry_attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                    
            logger.error(f"Failed to queue {src_id} for embedding: {error_str}")
            return {"success": False}
    
    logger.error(f"Failed to queue {src_id} after {max_retries} attempts. Last error: {last_error}")
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
        
        logger.info(f"[RESET_STATUS] Updating embedding status to 'starting' for {src_id}")
        
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
            
            logger.info(f"[RESET_STATUS] ✅ Successfully reset parent and {len(updated_child_chunks)} child chunks to 'starting' status for {src_id}")
        else:
            logger.warning(f"[RESET_STATUS] No existing embedding progress found for {src_id} - will be created during processing")
            
    except Exception as e:
        logger.error(f"[RESET_STATUS] ❌ Failed to reset embedding status for {src_id}: {str(e)}")
        raise e
