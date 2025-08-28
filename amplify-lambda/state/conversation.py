import json
import math
import os
import re
import time
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
setup_validated(rules, get_permission_checker)
from botocore.exceptions import BotoCoreError, ClientError
import boto3
import boto3.dynamodb.conditions
import uuid
from datetime import datetime, timezone, timedelta
from pycommon.api.ops import api_tool
from pycommon.db_utils import convert_floats_to_decimal
from pycommon.lzw import lzw_compress, lzw_uncompress

def update_conversation_cache(user_id, conversation_data, folder=None):
    """Update conversation metadata cache when conversation changes"""
    try:
        if not os.environ.get("CONVERSATION_METADATA_TABLE"):
            return  # Cache not available

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ.get("CONVERSATION_METADATA_TABLE"))

        # Extract metadata from conversation
        metadata = pick_conversation_attributes(
            conversation_data, include_timestamp=True
        )

        item = {
            "user_id": user_id,
            "conversation_id": metadata.get("id", ""),
            "name": metadata.get("name", ""),
            "model": metadata.get("model", ""),
            "folder_id": metadata.get("folderId"),
            "tags": metadata.get("tags", []),
            "is_local": metadata.get("isLocal", False),
            "group_type": metadata.get("groupType"),
            "code_interpreter_assistant_id": metadata.get(
                "codeInterpreterAssistantId"
            ),
            "last_modified": int(
                time.time() * 1000
            ),  # Use current timestamp for uploads
            "s3_key": f"{user_id}/{metadata.get('id', '')}",
            "folder_name": folder.get("name") if folder else None,
            "updated_at": int(time.time() * 1000),
        }
        
        # Convert any float values to Decimal for DynamoDB compatibility
        item = convert_floats_to_decimal(item)
        
        table.put_item(Item=item)

        print(f"Updated cache for conversation {metadata.get('id', '')}")

    except Exception as e:
        print(f"Failed to update conversation cache (non-blocking): {str(e)}")


def upload_to_s3(key, conversation, folder=None):
    s3 = boto3.client("s3")
    conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]

    try:
        s3.put_object(
            Bucket=conversations_bucket,
            Key=key,
            Body=json.dumps({"conversation": conversation, "folder": folder}),
        )
        print(f"Successfully uploaded conversation to s3: {key}")
        return {"success": True, "message": "Succesfully uploaded conversation to s3"}
    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
            "success": False,
            "message": "Failed to uploaded conversation to s3",
            "error": str(e),
        }


@validated("conversation_upload")
def upload_conversation(event, context, current_user, name, data):
    data = data["data"]
    conversation = data["conversation"]
    conversation_id = data["conversationId"]
    folder = data.get("folder", None)

    conversation_key = f"{current_user}/{conversation_id}"
    result = upload_to_s3(conversation_key, conversation, folder)

    if result.get("success"):
        try:
            # Decompress the conversation to get metadata
            decompressed_conversation = lzw_uncompress(conversation)
            if decompressed_conversation:
                update_conversation_cache(
                    current_user, decompressed_conversation, folder
                )
        except Exception as e:
            print(f"Failed to update cache after upload (non-blocking): {str(e)}")

    return result


@api_tool(
    path="/state/conversation/register",
    name="registerConversation",
    method="POST",
    tags=["default"],
    description="Register a new conversation with messages and metadata.",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "String. Required. Name of the conversation.",
            },
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                        "data": {"type": "object"},
                    },
                },
                "description": "Array. Required. List of message objects containing role (system/user/assistant), content, and data.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array. Optional. List of string tags for the conversation.",
            },
            "date": {
                "type": "string",
                "description": "String. Optional. Date in YYYY-MM-DD format.",
            },
            "data": {
                "type": "object",
                "description": "Object. Optional. Additional metadata for the conversation.",
            },
        },
        "required": ["name", "messages"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the conversation registration was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "error": {"type": "string", "description": "Error message if unsuccessful"},
        },
        "required": ["success"],
    },
)
@validated("conversation_upload")
def register_conversation(event, context, current_user, name, data):
    data = data["data"]

    prepMessages = data["messages"]

    for message in prepMessages:
        message["id"] = str(uuid.uuid4())
        message["type"] = "chat"

    current_utc_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conversation = {
        "id": data.get("id", str(uuid.uuid4())),
        "name": data["name"],
        "messages": prepMessages,
        "folderId": "agents",
        "tags": data.get("tags", []),
        "data": data.get("data", {}),
        "date": data.get("date", data.get("date", current_utc_time)),
        "isLocal": False,
    }

    compressed_conversation = lzw_compress(json.dumps(conversation))

    conversation_key = f"{current_user}/{conversation['id']}"
    return upload_to_s3(conversation_key, compressed_conversation, None)


@validated("read")
def get_conversation(event, context, current_user, name, data):
    query_param = get_conversation_query_param(event.get("queryStringParameters", {}))
    if not query_param["success"]:
        return query_param["response"]

    conversation_id = query_param["query_value"]
    s3 = boto3.client("s3")
    conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]

    conversation_key = f"{current_user}/{conversation_id}"

    try:
        response = s3.get_object(Bucket=conversations_bucket, Key=conversation_key)
        conversation_body = response["Body"].read().decode("utf-8")
        conversation_data = json.loads(conversation_body)
        return {"success": True, "conversation": conversation_data["conversation"]}

    except (BotoCoreError, ClientError) as e:
        error = {
            "success": False,
            "message": "Failed to retrieve conversation from S3",
            "error": str(e),
        }

        print(str(e))
        if e.response["Error"]["Code"] == "NoSuchKey":
            error["type"] = "NoSuchKey"

        return error


def pick_conversation_attributes(conversation, include_timestamp=False):
    """Extract essential conversation attributes for metadata responses"""
    attributes = [
        "id",
        "name",
        "model",
        "folderId",
        "tags",
        "isLocal",
        "groupType",
        "codeInterpreterAssistantId",
    ]
    result = {attr: conversation.get(attr, None) for attr in attributes}

    # Add timestamp if requested
    if include_timestamp:
        result["lastModified"] = conversation.get(
            "lastModified", int(time.time() * 1000)
        )

    return result


@validated("read")
def get_all_conversations(event, context, current_user, name, data):
    # Check for days query parameter
    query_params = event.get("queryStringParameters", {}) or {}
    days_param = query_params.get("days")
    days = None
    
    if days_param:
        try:
            days_value = int(days_param)
            # If days is 0 or negative, treat as "get all" (days = None)
            if days_value > 0:
                days = days_value
        except ValueError:
            return {"success": False, "message": "Days parameter must be a valid number"}
    
    conversations = get_all_complete_conversations(current_user, days)
    if conversations == None:
        return {"success": False, "message": "Failed to retrieve conversations from S3"}
    elif len(conversations) == 0:
        return {"success": True, "message": "No conversations saved to S3"}
    for item in conversations:
        if "conversation" in item:
            item["conversation"] = pick_conversation_attributes(item["conversation"])

    presigned_urls = get_presigned_urls(current_user, conversations)
    return {"success": True, "presignedUrls": presigned_urls}


@validated("read")
def get_empty_conversations(event, context, current_user, name, data):
    conversations = get_all_complete_conversations(current_user)
    if not conversations:
        return {"success": False, "message": "Failed to retrieve conversations from S3"}
    elif len(conversations) == 0:
        return {"success": True, "message": "No conversations saved to S3"}

    empty_conversations = []
    nonempty_conversations_ids = []
    for item in conversations:
        if "conversation" in item and (
            "messages" not in item["conversation"]
            or len(item["conversation"]["messages"]) == 0
        ):
            empty_conversations.append(
                pick_conversation_attributes(item["conversation"])
            )
        else:
            nonempty_conversations_ids.append(item["conversation"]["id"])

    presigned_urls = get_presigned_urls(current_user, empty_conversations)
    return {
        "success": True,
        "presignedUrls": presigned_urls,
        "nonEmptyIds": nonempty_conversations_ids,
    }


def get_all_complete_conversations(current_user, days=None):
    s3 = boto3.client("s3")
    conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]
    user_prefix = current_user + "/"

    # Calculate cutoff date if days parameter is provided
    cutoff_date = None
    if days is not None:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        print(f"Filtering conversations newer than: {cutoff_date}")

    try:
        # List all objects in the bucket with the given prefix
        response = s3.list_objects_v2(Bucket=conversations_bucket, Prefix=user_prefix)
        if "Contents" not in response:
            return []
        
        print(f"Number of conversations in list obj: {len(response['Contents'])}")

        filtered_objects = []
        
        # Filter objects by date if cutoff_date is specified
        if cutoff_date:
            for obj in response["Contents"]:
                if (obj["LastModified"] >= cutoff_date):
                    filtered_objects.append(obj) 
            print(f"Number of conversations after date filtering: {len(filtered_objects)}")
        else:
            filtered_objects = response["Contents"]

           
        conversations = []
        for obj in filtered_objects:
            conversation_key = obj["Key"]
            # Get each conversation object
            try:
                conversation_response = s3.get_object(
                    Bucket=conversations_bucket, Key=conversation_key
                )
                conversation_body = conversation_response["Body"].read().decode("utf-8")
                conversation = json.loads(conversation_body)
                uncompressed_conversation = lzw_uncompress(conversation["conversation"])
                if uncompressed_conversation:
                    conversations.append(
                        {
                            "conversation": uncompressed_conversation,
                            "folder": conversation["folder"],
                        }
                    )
                else:
                    print("Conversation failed to uncompress")
            except (BotoCoreError, ClientError) as e:
                print(f"Failed to retrieve : {obj} with error: {str(e)}")
        print("Number of conversations retrieved: ", len(conversations))

        return conversations

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return None


@validated("get_multiple_conversations")
def get_multiple_conversations(event, context, current_user, name, data):
    data = data["data"]
    conversation_ids = data["conversationIds"]

    s3 = boto3.client("s3")
    conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]
    user_prefix = current_user + "/"

    try:
        conversations = []
        failedToFetchConversations = []
        noSuchKeyConversations = []

        for id in conversation_ids:
            conversation_key = user_prefix + id
            # Get each conversation object
            try:
                conversation_response = s3.get_object(
                    Bucket=conversations_bucket, Key=conversation_key
                )
                conversation_body = conversation_response["Body"].read().decode("utf-8")
                conversation_data = json.loads(conversation_body)
                conversations.append(conversation_data["conversation"])

            except (BotoCoreError, ClientError) as e:
                print(f"Failed to retrieve conversation id: {id} with error: {str(e)}")
                if e.response["Error"]["Code"] == "NoSuchKey":
                    print("added to no such key list: ", id)
                    noSuchKeyConversations.append(id)
                else:
                    failedToFetchConversations.append(id)

        # Generate a pre-signed URL for the uploaded file
        presigned_urls = get_presigned_urls(current_user, conversations, 100)

        return {
            "success": True,
            "presignedUrls": presigned_urls,
            "noSuchKeyConversations": noSuchKeyConversations,
            "failed": failedToFetchConversations,
        }

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
            "success": False,
            "message": "Failed to retrieve conversations from S3",
            "error": str(e),
        }


def get_presigned_urls(current_user, conversations, chunk_size=400):
    conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]
    s3 = boto3.client("s3")

    total_chunks = math.ceil(len(conversations) / chunk_size)
    presigned_urls = []

    for i in range(total_chunks):
        start_index = i * chunk_size
        end_index = min(start_index + chunk_size, len(conversations))

        # Extract the chunk of conversation data
        chunk_data = conversations[start_index:end_index]
        chunk_json = json.dumps(chunk_data)

        chunk_key = f"temp/{current_user}/conversations_chunk_{i}.json"

        s3.put_object(
            Bucket=conversations_bucket,
            Key=chunk_key,
            Body=chunk_json,
            ContentType="application/json",
        )

        # Generate a GET presigned URL for this chunk
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": conversations_bucket, "Key": chunk_key},
            ExpiresIn=3600,  # 1 hour
        )

        presigned_urls.append(presigned_url)
    print("Number of presigned urls needed: ", len(presigned_urls))
    return presigned_urls


@validated("delete")
def delete_conversation(event, context, current_user, name, data):
    query_param = get_conversation_query_param(event.get("queryStringParameters", {}))
    if not query_param["success"]:
        return query_param["response"]

    conversation_id = query_param["query_value"]
    s3 = boto3.client("s3")
    conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]

    conversation_key = current_user + "/" + conversation_id

    try:
        s3.delete_object(Bucket=conversations_bucket, Key=conversation_key)
        return {"success": True, "message": "Successfully deleted conversation from S3"}

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
            "success": False,
            "message": "Failed to delete conversation from S3",
            "error": str(e),
        }


@validated("delete_multiple_conversations")
def delete_multiple_conversations(event, context, current_user, name, data):
    data = data["data"]
    conversation_ids = data["conversationIds"]

    s3 = boto3.client("s3")
    conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]
    user_prefix = current_user + "/"

    try:
        for id in conversation_ids:
            conversation_key = user_prefix + id
            # Get each conversation object
            try:
                s3.delete_object(Bucket=conversations_bucket, Key=conversation_key)
            except (BotoCoreError, ClientError) as e:
                print(f"Failed to delete conversation id: {id} with error: {str(e)}")

        return {
            "success": True,
            "message": "Successfully deleted all conversations from S3",
        }

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
            "success": False,
            "message": "Failed to delete all conversations from S3",
            "error": str(e),
        }


def get_conversation_query_param(query_params):
    print("Query params: ", query_params)
    conversation_id = query_params.get("conversationId", "")
    if (not conversation_id) or (not is_valid_uuidv4(conversation_id)):
        return {
            "success": False,
            "error": "Invalid or missing conversation id parameter",
        }
    return {"success": True, "query_value": conversation_id}


def is_valid_uuidv4(uuid):
    # Regular expression for validating a UUID version 4
    regex = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    match = re.fullmatch(regex, uuid, re.IGNORECASE)
    return bool(match)


def get_conversations_metadata_lightweight(current_user):
    """Optimized function to get only conversation metadata without full download"""
    s3 = boto3.client("s3")
    conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]
    user_prefix = current_user + "/"

    try:
        # List all objects to get S3 metadata (timestamps, sizes) without downloading content
        response = s3.list_objects_v2(Bucket=conversations_bucket, Prefix=user_prefix)
        if "Contents" not in response:
            return []

        metadata = []
        print(f"Processing {len(response['Contents'])} conversations for metadata")

        for obj in response["Contents"]:
            conversation_key = obj["Key"]
            conversation_id = conversation_key.split("/")[
                -1
            ]  # Extract ID from key path
            s3_last_modified = int(obj["LastModified"].timestamp() * 1000)

            try:
                # Download and decompress just to get basic metadata - but do it efficiently
                conversation_response = s3.get_object(
                    Bucket=conversations_bucket, Key=conversation_key
                )
                conversation_body = conversation_response["Body"].read().decode("utf-8")
                conversation_data = json.loads(conversation_body)

                # Only decompress to get the metadata fields we need
                uncompressed_conversation = lzw_uncompress(
                    conversation_data["conversation"]
                )
                if uncompressed_conversation:
                    # Extract only metadata attributes
                    conv_meta = pick_conversation_attributes(
                        uncompressed_conversation, include_timestamp=True
                    )
                    conv_meta["lastModified"] = s3_last_modified  # Use S3 timestamp

                    # Add folder info if available
                    if "folder" in conversation_data:
                        conv_meta["folder"] = conversation_data["folder"]

                    metadata.append(conv_meta)

            except (BotoCoreError, ClientError) as e:
                print(f"Failed to process conversation {conversation_id}: {str(e)}")
                # Create basic metadata from S3 info only
                metadata.append(
                    {
                        "id": conversation_id,
                        "name": f"Conversation {conversation_id}",
                        "lastModified": s3_last_modified,
                        "model": None,
                        "folderId": None,
                        "tags": [],
                        "isLocal": False,
                        "groupType": None,
                        "codeInterpreterAssistantId": None,
                        "folder": None,
                    }
                )

        return metadata

    except (BotoCoreError, ClientError) as e:
        print(f"Error listing conversations: {str(e)}")
        return None


def get_cached_conversation_metadata(current_user):
    """Get metadata from DynamoDB cache"""
    try:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ.get("CONVERSATION_METADATA_TABLE"))

        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("user_id").eq(
                current_user
            )
        )

        print(f"Cache query returned {len(response['Items'])} items")
        return response["Items"]

    except Exception as e:
        print(f"Cache query failed: {str(e)}")
        return []


def populate_cache_async(current_user, metadata_list):
    """Populate cache without blocking the response"""
    try:
        if not metadata_list:
            return

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ.get("CONVERSATION_METADATA_TABLE"))

        print(
            f"Populating cache with {len(metadata_list)} conversations for {current_user}"
        )

        # Batch write for efficiency
        with table.batch_writer() as batch:
            for metadata in metadata_list:
                item = {
                    "user_id": current_user,
                    "conversation_id": metadata.get("id", ""),
                    "name": metadata.get("name", ""),
                    "model": metadata.get("model", ""),
                    "folder_id": metadata.get("folderId"),
                    "tags": metadata.get("tags", []),
                    "is_local": metadata.get("isLocal", False),
                    "group_type": metadata.get("groupType"),
                    "code_interpreter_assistant_id": metadata.get(
                        "codeInterpreterAssistantId"
                    ),
                    "last_modified": metadata.get(
                        "lastModified", int(time.time() * 1000)
                    ),
                    "s3_key": f"{current_user}/{metadata.get('id', '')}",
                    "folder_name": (
                        metadata.get("folder", {}).get("name")
                        if metadata.get("folder")
                        else None
                    ),
                    "cached_at": int(time.time() * 1000),
                }
                
                # Convert any float values to Decimal for DynamoDB compatibility
                item = convert_floats_to_decimal(item)
                batch.put_item(Item=item)

        print(
            f"Successfully cached {len(metadata_list)} conversations for {current_user}"
        )

    except Exception as e:
        print(f"Error populating cache (non-blocking): {str(e)}")
        # Non-blocking - cache population failure doesn't break the API


@validated("read")
def get_conversations_metadata_only(event, context, current_user, name, data):
    """Get metadata with lazy cache population and S3 fallback"""
    try:
        print(f"Getting conversation metadata for user: {current_user}")

        # Try cache first (will be empty for existing users initially)
        cached_metadata = []
        try:
            if os.environ.get("CONVERSATION_METADATA_TABLE"):
                cached_metadata = get_cached_conversation_metadata(current_user)
        except Exception as e:
            print(f"Cache lookup failed, falling back to S3: {str(e)}")

        if cached_metadata:
            print(
                f"Cache hit: Retrieved {len(cached_metadata)} conversations from cache"
            )
            return {
                "success": True,
                "conversations": cached_metadata,
                "serverTimestamp": int(time.time() * 1000),
                "source": "cache",
            }

        # Cache miss - fallback to S3 and populate cache
        print("Cache miss - reading from S3 and populating cache")
        s3_metadata = get_conversations_metadata_lightweight(current_user)

        if s3_metadata is None:
            return {
                "success": False,
                "message": "Failed to retrieve conversations from S3",
            }

        # Populate cache for future requests (non-blocking)
        if os.environ.get("CONVERSATION_METADATA_TABLE") and s3_metadata:
            try:
                populate_cache_async(current_user, s3_metadata)
            except Exception as e:
                print(f"Cache population failed (non-blocking): {str(e)}")

        print(f"Retrieved {len(s3_metadata)} conversations from S3")
        return {
            "success": True,
            "conversations": s3_metadata,
            "serverTimestamp": int(time.time() * 1000),
            "source": "s3_with_cache_population",
        }

    except Exception as e:
        print(f"Error getting conversation metadata: {str(e)}")
        return {
            "success": False,
            "message": f"Failed to get conversation metadata: {str(e)}",
        }


@validated("read")
def get_conversations_since_timestamp(event, context, current_user, name, data):
    """Get conversations modified after a specific timestamp"""
    try:
        # Get timestamp from path parameters
        timestamp_str = event.get("pathParameters", {}).get("timestamp")
        if not timestamp_str:
            return {"success": False, "message": "Timestamp parameter is required"}

        try:
            since_timestamp = int(timestamp_str)
        except ValueError:
            return {"success": False, "message": "Invalid timestamp format"}

        print(f"Getting conversations since {since_timestamp} for user: {current_user}")

        s3 = boto3.client("s3")
        conversations_bucket = os.environ["S3_CONVERSATIONS_BUCKET_NAME"]
        user_prefix = current_user + "/"

        # Use list_objects_v2 to get timestamps efficiently
        response = s3.list_objects_v2(Bucket=conversations_bucket, Prefix=user_prefix)
        if "Contents" not in response:
            return {
                "success": True,
                "presignedUrls": [],
                "serverTimestamp": int(time.time() * 1000),
            }

        changed_conversations = []

        # Filter conversations by timestamp using S3 metadata
        for obj in response["Contents"]:
            last_modified_ms = int(obj["LastModified"].timestamp() * 1000)

            if last_modified_ms > since_timestamp:
                conversation_key = obj["Key"]
                conversation_id = conversation_key.split("/")[-1]
                print(
                    f"Conversation {conversation_id} modified at {last_modified_ms} (after {since_timestamp})"
                )

                try:
                    # Now download and decompress only the changed conversations
                    conversation_response = s3.get_object(
                        Bucket=conversations_bucket, Key=conversation_key
                    )
                    conversation_body = (
                        conversation_response["Body"].read().decode("utf-8")
                    )
                    conversation_data = json.loads(conversation_body)
                    uncompressed_conversation = lzw_uncompress(
                        conversation_data["conversation"]
                    )

                    if uncompressed_conversation:
                        changed_conversations.append(
                            {
                                "conversation": uncompressed_conversation,
                                "folder": conversation_data.get("folder"),
                            }
                        )

                except (BotoCoreError, ClientError) as e:
                    print(
                        f"Failed to retrieve changed conversation {conversation_id}: {str(e)}"
                    )
                    continue

        print(f"Found {len(changed_conversations)} changed conversations")

        if not changed_conversations:
            return {
                "success": True,
                "presignedUrls": [],
                "serverTimestamp": int(time.time() * 1000),
            }

        # Use existing chunking logic to return presigned URLs
        presigned_urls = get_presigned_urls(current_user, changed_conversations)
        return {
            "success": True,
            "presignedUrls": presigned_urls,
            "serverTimestamp": int(time.time() * 1000),
        }

    except Exception as e:
        print(f"Error getting conversations since timestamp: {str(e)}")
        return {
            "success": False,
            "message": f"Failed to get conversations since timestamp: {str(e)}",
        }
