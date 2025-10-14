import base64
import json
import re
import uuid
from datetime import datetime
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from pycommon.api.ops import api_tool
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType, IMAGE_FILE_TYPES
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.FILE_UPLOAD.value])

import os
import boto3
import rag.util
from boto3.dynamodb.conditions import Key
from rag.rag_secrets import store_ds_secrets_for_rag
from pycommon.api.data_sources import translate_user_data_sources_to_hash_data_sources, extract_key
from pycommon.api.object_permissions import can_access_objects
from pycommon.api.embeddings import delete_embeddings
from pycommon.api.amplify_groups import verify_member_of_ast_admin_group

dynamodb = boto3.resource("dynamodb")


@api_tool(
    path="/files/download",
    name="getDownloadUrl",
    tags=["files"],
    description="Get a url to download the file associated with a datasource key / ID.",
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The key or ID of the datasource to download.",
            }
        },
        "required": ["key"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the download URL generation was successful",
            },
            "downloadUrl": {
                "type": "string",
                "description": "Presigned URL for downloading the file",
            },
            "message": {
                "type": "string",
                "description": "Error message if unsuccessful",
            },
        },
        "required": ["success"],
    },
)
@validated("download")
def get_presigned_download_url(event, context, current_user, name, data):
    access_token = data["access_token"]
    data = data["data"]
    key = data["key"]
    group_id = data.get("groupId", None)

    if "://" in key:
        key = key.split("://")[1]

    s3 = boto3.client("s3")
    files_table_name = os.environ["FILES_DYNAMO_TABLE"]

    # Access the specific table
    files_table = dynamodb.Table(files_table_name)

    print(f"Getting presigned download URL for {key} for user {current_user}")
    print(f"GroupId attached to data source: {group_id}")

    # Retrieve the item from DynamoDB to check ownership
    try:
        response = files_table.get_item(Key={"id": key})
    except ClientError as e:
        print(f"Error getting file metadata from DynamoDB: {e}")
        error_message = e.response["Error"]["Message"]
        return {"success": False, "message": error_message}

    if "Item" not in response:
        # User doesn't match or item doesn't exist
        print(f"File not found for user {current_user}: {response}")
        return {"success": False, "message": "File not found"}
    item = response["Item"]
    print("Item found: ", item)
    access_result = can_access_file(item, current_user, key, group_id, access_token)

    if not access_result["success"]:
        return access_result

    download_filename = item["name"]
    is_file_type = item["type"] in IMAGE_FILE_TYPES
    response_headers = (
        {"ResponseContentDisposition": f'attachment; filename="{download_filename}"'}
        if download_filename and not is_file_type
        else {}
    )

    bucket_name = (
        os.environ["S3_IMAGE_INPUT_BUCKET_NAME"]
        if is_file_type
        else os.environ["S3_RAG_INPUT_BUCKET_NAME"]
    )
    # If the user matches, generate a presigned URL for downloading the file from S3
    try:
        presigned_url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket_name, "Key": key, **response_headers},
            ExpiresIn=3600,  # Expiration time for the presigned URL, in seconds
        )
    except ClientError as e:
        print(f"Error generating presigned download URL: {e}")
        return {"success": False, "message": "File not found"}

    if presigned_url:
        return {"success": True, "downloadUrl": presigned_url}
    else:
        return {"success": False, "message": "File not found"}


def can_access_file(table_item, current_user, key, group_id, access_token):
    print(
        f"Checking if user {current_user} can access file {key} with groupId {group_id}"
    )
    created_by = table_item["createdBy"]
    if created_by == current_user:
        pass
    elif group_id and created_by == group_id:
        # ensure the user/system user has access to the group by either
        print("Checking if user is a member of the group: ", group_id)
        is_member = verify_member_of_ast_admin_group(access_token, group_id)
        if not is_member:
            return {
                "success": False,
                "message": f"User is not a member of groupId: {group_id}",
            }
        print("User is a member of the group: ", group_id)
    else:
        translated_ds = None
        try:  # need global
            translated_ds = translate_user_data_sources_to_hash_data_sources(
                [{"id": key, "type": table_item["type"]}]
            )
        except:
            print("Datasource translation failed")

        if not translated_ds or len(translated_ds) == 0:
            print("Translation for data source failed: ", translated_ds)
            return {
                "success": False,
                "message": "Internal Server Error: Translation for data source failed",
            }

        # Since groups can have documents that belongs to others and the group itself only has permission to we need to check the table
        if group_id:  # checks can access of the group_id
            object_table = dynamodb.Table(os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"])

            try:
                object_id = translated_ds[0]["id"]
                print("Checking Object Access for groupId permission to the datasource")
                # Check if any permissions already exist for the object_id
                query_response = object_table.get_item(
                    Key={"object_id": object_id, "principal_id": group_id}
                )
                item = query_response.get("Item")

                if not item:
                    print("Groupd Id does not have access")
                    return {
                        "success": False,
                        "message": "GroupId does not have access to the data source",
                    }

                permission_level = item.get("permission_level")
                policy = item.get("policy")
                # sufficient privilege for read access
                sufficient_privilege = (
                    permission_level in ["owner", "write", "read"] or policy == "public"
                )
                if not sufficient_privilege:
                    print("Groupd Id has insufficient privilege")
                    return {
                        "success": False,
                        "message": "GroupId does not have sufficient privilege to access the data source",
                    }

                print("Groupd Id has sufficient privilege to download datasource")
            except ClientError as e:
                print(
                    f"Error accessing DynamoDB for can_access_objects: {e.response['Error']['Message']}"
                )
                return {
                    "success": False,
                    "message": f"Error performing can_access on ds with groupid {group_id}",
                }

        elif not can_access_objects(
            access_token, translated_ds
        ):  # checks can access on the user
            print(f"User {current_user} does not have acces to download: {table_item}")
            return {
                "success": False,
                "message": "User does not have access to the data source",
            }

    return {"success": True}


# due to lambda layer requirements in rag.core, we have to define this function here
@validated("upload")
def reprocess_document_for_rag(event, context, current_user, name, data):
    """
    Reprocess a document that has already been processed.
    This function simply flags the document for reprocessing - the embedding service
    will handle all cleanup and determine what needs to be reprocessed.
    """
    s3 = boto3.client("s3")
    access_token = data["access_token"]

    account_data = {
        "user": current_user,
        "account": data["account"],
        "rate_limit": data["rate_limit"],
        "access_token": access_token,
    }

    data = data["data"]
    key = data["key"]
    group_id = data.get("groupId")
    bucket = os.environ["S3_RAG_INPUT_BUCKET_NAME"]

    if not bucket or not key:
        return {
            "success": False,
            "message": "Missing required parameters: bucket and key",
        }

    print(f"Reprocessing document: {bucket}/{key}")
    files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
    
    try:
        response = files_table.get_item(Key={"id": key})
        if "Item" not in response:
            print(f"File not found for user {current_user}: {response}")
            return {"success": False, "message": "File not found"}
        
        item = response["Item"]
        file_type = item.get("type")
        if file_type and file_type in IMAGE_FILE_TYPES:
            print(f"File {key} is an image file, not supported for reprocessing")
            return {
                "success": False,
                "message": "Image files are not supported for reprocessing",
            }
        
        access_result = can_access_file(item, current_user, key, group_id, access_token)
        if not access_result["success"]:
            return access_result

    except ClientError as e:
        print(f"Error getting file metadata from DynamoDB: {e}")
        error_message = e.response["Error"]["Message"]
        return {"success": False, "message": error_message}
    
    try:
        # Verify the file exists
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except Exception as e:
            print(f"Error checking S3 object: {str(e)}")
            return {
                "success": False,
                "message": f"File not found or not accessible: {bucket}/{key}",
            }
         
        if not store_ds_secrets_for_rag(key, account_data)['success']:
            return {
                "success": False,
                "message": "Failed to store RAG secrets for document",
            }

        # Create synthetic S3 event with force_reprocess flag
        # Embedding service will handle all cleanup and selective logic
        record = {
            "force_reprocess": True,
            "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
        }
        
        queue_url = os.environ["RAG_PROCESS_DOCUMENT_QUEUE_URL"]
        message_body = json.dumps(record)
        print(f"Sending reprocess message to queue: {message_body}")
        
        sqs = boto3.client("sqs")
        sqs.send_message(QueueUrl=queue_url, MessageBody=message_body)
        print(f"Message sent to queue: {message_body}")

        return {"success": True, "message": "Document queued for reprocessing"}

    except Exception as e:
        print(f"Error reprocessing document: {str(e)}")
        return {"success": False, "message": f"Error reprocessing document: {str(e)}"}


def create_file_metadata_entry(
    current_user, name, file_type, tags, data_props, knowledge_base
):
    bucket_name = os.environ[
        (
            "S3_IMAGE_INPUT_BUCKET_NAME"
            if (file_type in IMAGE_FILE_TYPES)
            else "S3_RAG_INPUT_BUCKET_NAME"
        )
    ]
    dt_string = datetime.now().strftime("%Y-%m-%d")
    key = f"{current_user}/{dt_string}/{uuid.uuid4()}.json"

    files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
    files_table.put_item(
        Item={
            "id": key,
            "name": name,
            "type": file_type,
            "tags": tags,
            "data": data_props,
            "knowledgeBase": knowledge_base,
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat(),
            "createdBy": current_user,
            "updatedBy": current_user,
        }
    )

    if tags is not None and len(tags) > 0:
        update_file_tags(current_user, key, tags)

    return bucket_name, key


@validated("set")
def set_datasource_metadata_entry(event, context, current_user, name, data):

    data = data["data"]
    key = data["id"]
    name = data["name"]
    dtype = data["type"]
    kb = data.get("knowledge_base", "default")
    data_props = data.get("data", {})
    tags = data.get("tags", [])

    files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])

    # Check if the item already exists
    response = files_table.get_item(Key={"id": key})

    if "Item" in response and response["Item"].get("createdBy") != current_user:
        # Item already exists, return some error or existing key
        return {"success": False, "message": "Item already exists"}

    # Item does not exist, proceed with insertion
    files_table.put_item(
        Item={
            "id": key,
            "name": name,
            "type": dtype,
            "tags": tags,
            "data": data_props,
            "knowledgeBase": kb,
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat(),
            "createdBy": current_user,
            "updatedBy": current_user,
        }
    )

    if tags is not None and len(tags) > 0:
        update_file_tags(current_user, key, tags)

    return key


@api_tool(
    path="/files/upload",
    name="uploadFile",
    method="POST",
    tags=["apiDocumentation"],
    description="""Initiate a file upload to the Amplify platform, enabling interaction via prompts and assistants.

    Example request:
    {
        "data": {
            "type": "application/fileExtension",
            "name": "fileName.pdf",
            "knowledgeBase": "default",
            "tags": [],
            "data": {}
        }
    }

    Example response:
    {
        "success": true,
        "uploadUrl": "<uploadUrl>",
        "statusUrl": "<statusUrl>",
        "contentUrl": "<contentUrl>",
        "metadataUrl": "<metadataUrl>",
        "key": "yourEmail@vanderbilt.edu/date/293088.json"
    }

    The user can use the presigned url 'uploadUrl' to upload their file to Amplify.
    """,
    parameters={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "String. Required. MIME type of the file to be uploaded. Example: 'application/pdf'.",
            },
            "name": {
                "type": "string",
                "description": "String. Required. Name of the file to be uploaded.",
            },
            "knowledgeBase": {
                "type": "string",
                "description": "String. Required. Knowledge base the file should be associated with. Default: 'default'.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of strings. Tags to associate with the file. Example: ['tag1', 'tag2'].",
            },
            "data": {
                "type": "object",
                "description": "Object. Additional metadata associated with the file upload.",
            },
        },
        "required": ["type", "name", "knowledgeBase"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the upload URL generation was successful",
            },
            "uploadUrl": {
                "type": "string",
                "description": "Presigned URL for uploading the file",
            },
            "statusUrl": {
                "type": "string",
                "description": "URL to check processing status",
            },
            "contentUrl": {
                "type": "string",
                "description": "URL to access processed content",
            },
            "metadataUrl": {
                "type": "string",
                "description": "URL to access file metadata",
            },
            "key": {
                "type": "string",
                "description": "Unique identifier for the uploaded file",
            },
        },
        "required": ["success"],
    },
)
@api_tool(
    path="/files/upload",
    name="getUploadUrl",
    tags=["files"],
    description="Get a url to upload a file to.",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The name of the file to upload.",
            },
            "type": {
                "type": "string",
                "description": "The mime type of the file to upload as a string.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The tags associated with the file as a list of strings.",
            },
            "data": {
                "type": "object",
                "description": "The data associated with the file or an empty dictionary.",
            },
            "knowledgeBase": {
                "type": "string",
                "description": "The knowledge base associated with the file. You can put 'default' if you don't have a specific knowledge base.",
            },
        },
        "required": ["name", "type", "knowledgeBase"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the upload URL generation was successful",
            },
            "uploadUrl": {
                "type": "string",
                "description": "Presigned URL for uploading the file",
            },
            "statusUrl": {
                "type": "string",
                "description": "URL to check processing status",
            },
            "contentUrl": {
                "type": "string",
                "description": "URL to access processed content",
            },
            "metadataUrl": {
                "type": "string",
                "description": "URL to access file metadata",
            },
            "key": {
                "type": "string",
                "description": "Unique identifier for the uploaded file",
            },
        },
        "required": ["success"],
    },
)
@validated("upload")
def get_presigned_url(event, context, current_user, name, data):
    access = data["allowed_access"]
    if APIAccessType.FILE_UPLOAD.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        print("User does not have access to the file_upload functionality")
        return {
            "success": False,
            "error": "User does not have access to the file_upload functionality",
        }

    # we need the perms to be under the groupId if applicable
    groupId = data["data"].get("groupId", None)

    # Extract ragOn parameter, default to True if not provided
    rag_on = data["data"].get("ragOn", False)
    print(f"RAG processing is {'enabled' if rag_on else 'disabled'} for this upload")

    if groupId:
        print("GroupId ds upload: ", groupId)
        current_user = groupId

    account_data = {
        "user": current_user,
        "account": data["account"],
        "rate_limit": data["rate_limit"],
        "access_token": data["access_token"],
    }

    # print(f"Data is {data}")
    data = data["data"]

    s3 = boto3.client("s3")

    name = data["name"]
    name = re.sub(r"[_\s]+", "_", name)
    file_type = data["type"]
    tags = data["tags"]
    props = data["data"]
    knowledge_base = data["knowledgeBase"]

    print(
        f"\nGetting presigned URL for {name} of type {type} with tags {tags} and data {data} and knowledge base {knowledge_base}"
    )

    # Set the S3 bucket and key
    bucket_name, key = create_file_metadata_entry( current_user, name, file_type, tags, props, knowledge_base )
    print(f"Created metadata entry for file {key} in bucket {bucket_name}")

    # Generate a presigned URL for uploading the file to S3
    presigned_url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket_name,
            "Key": key,
            "ContentType": file_type,
            "Metadata": {
                # 'encrypted_metadata': encrypted_metadata,
                "rag_enabled": str(
                    rag_on
                ).lower()  # Add this metadata to control RAG processing
            },
            # Add any additional parameters like ACL, ContentType, etc. if needed
        },
        ExpiresIn=3600,  # Set the expiration time for the presigned URL, in seconds
    )

    if file_type in IMAGE_FILE_TYPES:
        print("Generating presigned urls for Image file")
        metadata_key = key + ".metadata.json"
        presigned_metadata_url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket_name, "Key": metadata_key},
            ExpiresIn=3600,
        )
        return {
            "success": True,
            "uploadUrl": presigned_url,
            "metadataUrl": presigned_metadata_url,
            "key": key,
        }

    [file_text_content_bucket_name, text_content_key] = (
        rag.util.get_text_content_location(bucket_name, key)
    )

    print(f"Getting presigned URL for text content {text_content_key} in bucket {file_text_content_bucket_name}")

    presigned_text_status_content_url = s3.generate_presigned_url(
        ClientMethod="head_object",
        Params={"Bucket": file_text_content_bucket_name, "Key": text_content_key},
        ExpiresIn=3600,  # Set the expiration time for the presigned URL, in seconds
    )

    presigned_text_content_url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": file_text_content_bucket_name, "Key": text_content_key},
        ExpiresIn=3600,  # Set the expiration time for the presigned URL, in seconds
    )

    [file_text_metadata_bucket_name, text_metadata_key] = (
        rag.util.get_text_metadata_location(bucket_name, key)
    )

    presigned_text_metadata_url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": file_text_metadata_bucket_name, "Key": text_metadata_key},
        ExpiresIn=3600,  # Set the expiration time for the presigned URL, in seconds
    )

    if presigned_url and store_ds_secrets_for_rag(key, account_data)['success']:
        return {
            "success": True,
            "uploadUrl": presigned_url,
            "statusUrl": presigned_text_status_content_url,
            "contentUrl": presigned_text_content_url,
            "metadataUrl": presigned_text_metadata_url,
            "key": key,
        }
        
    return {"success": False}


@api_tool(
    path="/files/tags/list",
    name="listTagsForUser",
    tags=["files"],
    method="GET",
    description="Get a list of all tags that can be added to files or used to search for groups of files.",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tag retrieval was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of available tags",
                    }
                },
            },
        },
        "required": ["success", "data"],
    },
)
@api_tool(
    path="/files/tags/list",
    name="listFileTags",
    method="GET",
    tags=["apiDocumentation"],
    description="""Retrieve a list of all tags associated with files, conversations, and assistants on the Amplify platform.

    Example response:
    {
        "success": true,
        "data": {
            "tags": ["NewTag", "Important", "Archived"]
        }
    }
    """,
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tag retrieval was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of available tags",
                    }
                },
            },
        },
        "required": ["success", "data"],
    },
)
@validated("list")
def list_tags_for_user(event, context, current_user, name, data):
    table = dynamodb.Table(os.environ["USER_TAGS_DYNAMO_TABLE"])

    try:
        # Retrieve the item corresponding to the user
        response = table.get_item(Key={"user": current_user})
        # Check if 'Item' key is in the response which indicates a result was returned
        if "Item" in response:
            user_tags = response["Item"].get("tags", [])
            print(f"Tags for user ID '{current_user}': {user_tags}")
            return {"success": True, "data": {"tags": user_tags}}
        else:
            print(f"No tags found for user ID '{current_user}'.")
            return {"success": True, "data": {"tags": []}}
    except ClientError as e:
        print(
            f"Error getting tags for user ID '{current_user}': {e.response['Error']['Message']}"
        )
        return {"success": False, "data": {"tags": []}}


@api_tool(
    path="/files/tags/delete",
    name="deleteFileTag",
    method="POST",
    tags=["apiDocumentation"],
    description="""Delete a specific tag from the Amplify platform.
    Example request:
    {
        "data": {
            "tag": "NewTag"
        }
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "description": "String. Required. The tag to be deleted. Example: 'NewTag'.",
            }
        },
        "required": ["tag"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tag deletion was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
        },
        "required": ["success", "message"],
    },
)
@api_tool(
    path="/files/tags/delete",
    name="deleteTagForUser",
    tags=["files"],
    description="Delete a tag from the list of the user's tags.",
    parameters={
        "type": "object",
        "properties": {"tag": {"type": "string", "description": "The tag to delete."}},
        "required": ["tag"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tag deletion was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
        },
        "required": ["success", "message"],
    },
)
@validated("delete")
def delete_tag_from_user(event, context, current_user, name, data):
    data = data["data"]
    tag_to_delete = data["tag"]

    table = dynamodb.Table(os.environ["USER_TAGS_DYNAMO_TABLE"])

    try:
        # Update the item to delete the tag from the set of tags
        response = table.update_item(
            Key={"user": current_user},  # Assumes that `current_user` holds the user ID
            UpdateExpression="DELETE #tags :tag",
            ExpressionAttributeNames={
                "#tags": "tags",  # Assumes 'Tags' is the name of the attribute
            },
            ExpressionAttributeValues={
                ":tag": set([tag_to_delete])  # The tag to delete, must be a set
            },
            ReturnValues="UPDATED_NEW",
        )
        print(
            f"Tag '{tag_to_delete}' deleted successfully from user ID: {current_user}"
        )
        return {"success": True, "message": "Tag deleted successfully"}

    except boto3.client("dynamodb").exceptions.ClientError as e:
        error_code = e.response["Error"]["Code"]
        if (
            error_code == "ValidationException"
            and "provided key element does not match" in e.response["Error"]["Message"]
        ):
            print(f"User ID: {current_user} does not exist or tag does not exist.")
            return {
                "success": False,
                "message": "User ID does not exist or tag does not exist",
            }
        else:
            return {"success": False, "message": e.response["Error"]["Message"]}


@api_tool(
    path="/files/tags/create",
    name="createFileTags",
    method="POST",
    tags=["apiDocumentation"],
    description="""Create new tags to associate with files, conversations, and assistants.

    Example request:
    {
        "data": {
            "tags": ["NewTag"]
        }
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of strings. Required. List of tags to create. Example: ['NewTag', 'Important'].",
            }
        },
        "required": ["tags"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tag creation was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
        },
        "required": ["success", "message"],
    },
)
@api_tool(
    path="/files/tags/create",
    name="createTagsForUser",
    tags=["files"],
    description="Create one or more tags for the user that can be added to files.",
    parameters={
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "A list of string tags to create for the user.",
            }
        },
        "required": ["tags"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tag creation was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
        },
        "required": ["success", "message"],
    },
)
@validated("create")
def create_tags(event, context, current_user, name, data):
    data = data["data"]
    tags_to_add = data["tags"]

    # Call the helper function to add tags to the user
    return add_tags_to_user(current_user, tags_to_add)


def add_tags_to_user(current_user, tags_to_add):
    """Add a tag to user's list of tags if it doesn't already exist."""
    table = dynamodb.Table(os.environ["USER_TAGS_DYNAMO_TABLE"])

    try:
        response = table.update_item(
            Key={"user": current_user},
            UpdateExpression="ADD #tags :tags",
            ExpressionAttributeNames={
                "#tags": "tags",  # Assuming 'Tags' is the name of the attribute
            },
            ExpressionAttributeValues={
                ":tags": set(tags_to_add)  # The tags to add as a set
            },
            ReturnValues="UPDATED_NEW",
        )
        print(f"Tags added successfully to user ID: {current_user}")
        return {"success": True, "message": "Tags added successfully"}

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ValidationException":
            # If the item doesn't exist, create it with the specified tags
            response = table.put_item(
                Item={"UserID": current_user, "tags": set(tags_to_add)}
            )
            print(f"New user created with tags for user ID: {current_user}")
            return {"success": True, "message": "Tags added successfully"}
        else:
            print(
                f"Error adding tags to user ID: {current_user}: {e.response['Error']['Message']}"
            )
            return {"success": False, "message": e.response["Error"]["Message"]}


@api_tool(
    path="/files/set_tags",
    name="associateFileTags",
    method="POST",
    tags=["apiDocumentation"],
    description="""Associate one or more tags with a specific files only.

    Example request:
    {
        "data": {
            "id": "yourEmail@vanderbilt.edu/date/23094023573924890-208.json",
            "tags": ["NewTag", "Important"]
        }
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "String. Required. Unique identifier of the file. Example: 'yourEmail@vanderbilt.edu/date/23094023573924890-208.json'.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of strings. Required. List of tags to associate. Example: ['NewTag', 'Important'].",
            },
        },
        "required": ["id", "tags"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tag association was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
        },
        "required": ["success", "message"],
    },
)
@api_tool(
    path="/files/tags/set_tags",
    name="setTagsForFile",
    tags=["files"],
    description="Set a file's list of tags.",
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "The ID of the file to set tags for.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "A list of string tags to set for the file.",
            },
        },
        "required": ["id", "tags"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the tag setting was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
        },
        "required": ["success", "message"],
    },
)
@validated("set_tags")
def update_item_tags(event, context, current_user, name, data):
    data = data["data"]
    item_id = data["id"]
    tags = data["tags"]

    # Call the helper function to update tags and add them to the user.
    success, message = update_file_tags(current_user, item_id, tags)

    return {"success": success, "message": message}


def update_file_tags(current_user, item_id, tags):
    # Helper function that updates tags in DynamoDB and adds tags to the user
    table_name = os.environ[
        "FILES_DYNAMO_TABLE"
    ]  # Get the table name from the environment variable
    table = dynamodb.Table(table_name)

    try:
        response = table.get_item(Key={"id": item_id})
        item = response.get("Item")

        if item and item.get("createdBy") == current_user:
            # Update the item's tags in DynamoDB
            table.update_item(
                Key={"id": item_id},
                UpdateExpression="SET tags = :tags",
                ExpressionAttributeValues={":tags": tags},
            )

            # Add tags to the user
            tags_added = add_tags_to_user(current_user, tags)
            if tags_added["success"]:
                return True, "Tags updated and added to user"
            else:
                return False, f"Error adding tags to user: {tags_added['message']}"

        else:
            return False, "File not found or not authorized to update tags"

    except ClientError as e:
        print(f"Unable to update tags: {e.response['Error']['Message']}")
        return False, "Unable to update tags"


@api_tool(
    path="/files/query",
    name="queryUploadedFiles",
    method="POST",
    tags=["apiDocumentation"],
    description="""Retrieve a list of uploaded files stored on the Amplify. A user can retrieve details about their files include id, types, size, and more.

    Example request:
    {
        "data": {
            "pageSize": 10,
            "sortIndex": "",
            "forwardScan": false
        }
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "startDate": {
                "type": "string",
                "format": "date-time",
                "description": "String (date-time). Optional. Start date for querying files. Default: '2021-01-01T00:00:00Z'.",
            },
            "pageSize": {
                "type": "integer",
                "description": "Integer. Optional. Number of results to return. Default: 10.",
            },
            "pageKey": {
                "type": "object",
                "description": "Object. Optional. Includes 'id', 'createdAt', and 'type' for pagination purposes.",
            },
            "namePrefix": {
                "type": "string",
                "description": "String. Optional. Prefix for filtering file names.",
            },
            "createdAtPrefix": {
                "type": "string",
                "description": "String. Optional. Prefix for filtering creation date.",
            },
            "typePrefix": {
                "type": "string",
                "description": "String. Optional. Prefix for filtering file types.",
            },
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of strings. Optional. List of file types to filter by. Default: [].",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of strings. Optional. List of tags to filter files by. Default: [].",
            },
            "pageIndex": {
                "type": "integer",
                "description": "Integer. Optional. Page index for pagination. Default: 0.",
            },
            "forwardScan": {
                "type": "boolean",
                "description": "Boolean. Optional. Set to 'true' for forward scanning. Default: false.",
            },
            "sortIndex": {
                "type": "string",
                "description": "String. Optional. Attribute to sort results by. Default: 'createdAt'.",
            },
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "attribute": {"type": "string"},
                        "operator": {"type": "string"},
                        "value": {"type": "string"}
                    }
                },
                "description": "Array of objects. Optional. Dynamic filters for flexible filtering. Each filter has 'attribute' (supports nested like 'data.type'), 'operator' (startsWith, not_startsWith, contains, not_contains, equals, not_equals, exists, not_exists), and 'value'.",
            },
        },
        "required": [],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the file query was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Array of file objects matching the query",
                    },
                    "pageKey": {
                        "type": "object",
                        "description": "Pagination key for next page",
                    },
                },
            },
        },
        "required": ["success", "data"],
    },
)
@api_tool(
    path="/files/query",
    name="queryFilesByNameAndType",
    tags=["files"],
    description="Search a user's list of files with a query.",
    parameters={
        "type": "object",
        "properties": {
            "namePrefix": {
                "type": "string",
                "description": "The prefix to search for in the file names.",
            },
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "A list of file mime types (e.g., 'application/pdf', 'text/plain', etc.) and must not be empty.",
            },
        },
        "required": ["namePrefix", "types"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the file query was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Array of file objects matching the query",
                    },
                    "pageKey": {
                        "type": "object",
                        "description": "Pagination key for next page",
                    },
                },
            },
        },
        "required": ["success", "data"],
    },
)
@api_tool(
    path="/files/query",
    name="queryFilesByName",
    tags=["files"],
    description="Search a user's list of files with a query.",
    parameters={
        "type": "object",
        "properties": {
            "namePrefix": {
                "type": "string",
                "description": "The prefix to search for in the file names.",
            }
        },
        "required": ["namePrefix"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the file query was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Array of file objects matching the query",
                    },
                    "pageKey": {
                        "type": "object",
                        "description": "Pagination key for next page",
                    },
                },
            },
        },
        "required": ["success", "data"],
    },
)
@api_tool(
    path="/files/query",
    name="queryFilesByTags",
    tags=["files"],
    description="Search a user's list of files with a query.",
    parameters={
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "A list of tags to search for or an empty list.",
            }
        },
        "required": ["tags"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the file query was successful",
            },
            "data": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Array of file objects matching the query",
                    },
                    "pageKey": {
                        "type": "object",
                        "description": "Pagination key for next page",
                    },
                },
            },
        },
        "required": ["success", "data"],
    },
)
@validated("query")
def query_user_files(event, context, current_user, name, data):
    print(f"Querying user files for {current_user}")
    # Extract the query parameters from the event
    query_params = data["data"]

    # Map the provided sort key to the corresponding index name
    sort_index = query_params.get("sortIndex", "createdAt")
    sort_index_lookup = {
        "createdAt": "createdByAndAt",
        "name": "createdByAndName",
        "type": "createdByAndType",
    }
    index_name = sort_index_lookup.get(sort_index, "createdByAndAt")

    # Extract the pagination and filtering parameters
    start_date = query_params.get("startDate", "2021-01-01T00:00:00Z")
    page_size = query_params.get("pageSize", 10)
    exclusive_start_key = query_params.get("pageKey")
    name_prefix = query_params.get("namePrefix")
    created_at_prefix = query_params.get("createdAtPrefix")
    type_prefix = query_params.get("typePrefix")
    type_filters = query_params.get("types")
    tag_search = query_params.get("tags", None)
    dynamic_filters = query_params.get("filters", None)
    page_index = query_params.get("pageIndex", 0)
    forward_scan = query_params.get("forwardScan", False)

    # Determine the sort key and begins_with attribute based on sort_index
    sort_key_name = "createdAt" if sort_index == "createdAt" else sort_index

    sort_key_value_start = None
    # Initialize a list to hold any begins_with filters
    begins_with_filters = []

    # Determine the begins_with filters based on provided prefixes and the sort index
    if name_prefix:
        if sort_index == "name":
            sort_key_value_start = name_prefix
        else:
            begins_with_filters.append(
                {"attribute": "name", "value": name_prefix, "expression": "contains"}
            )

    if created_at_prefix:
        if sort_index == "createdAt":
            sort_key_value_start = created_at_prefix
        else:
            begins_with_filters.append(
                {
                    "attribute": "createdAt",
                    "value": created_at_prefix,
                    "expression": "begins_with",
                }
            )

    if type_prefix:
        if sort_index == "type":
            sort_key_value_start = type_prefix
        else:
            begins_with_filters.append(
                {"attribute": "type", "value": type_prefix, "expression": "begins_with"}
            )

    if tag_search:
        begins_with_filters.append(
            {"attribute": "tags", "value": tag_search, "expression": "contains"}
        )

    # Print all of the params (for debugging purposes)
    print(
        f"Querying user files with the following parameters: "
        f"start_date={start_date}, "
        f"page_size={page_size}, "
        f"exclusive_start_key={exclusive_start_key}, "
        f"name_prefix={name_prefix}, "
        f"created_at_prefix={created_at_prefix}, "
        f"type_prefix={type_prefix}, "
        f"type_filters={type_filters}, "
        f"tag_search={tag_search}, "
        f"page_index={page_index}"
        f"forward_scan={forward_scan}"
        f"sort_index={index_name}"
    )

    # Use 'query_table_index' as the refactored function with new parameters
    result = query_table_index(
        table_name=os.environ["FILES_DYNAMO_TABLE"],
        index_name=index_name,
        partition_key_name="createdBy",
        sort_key_name=sort_key_name,
        partition_key_value=current_user,
        sort_key_value_start=sort_key_value_start,
        filters=begins_with_filters,
        type_filters=type_filters,
        dynamic_filters=dynamic_filters,
        exclusive_start_key=exclusive_start_key,
        page_size=page_size,
        forward_scan=forward_scan,
    )

    # Extract and process results from 'result' as necessary before returning
    # This may include handling pagination, converting 'Items' to a more readable format, etc.

    # Return the processed result
    return result


def query_table_index(
    table_name,
    index_name,
    partition_key_name,
    sort_key_name,
    partition_key_value,
    sort_key_value_start=None,
    filters=None,
    type_filters=None,
    dynamic_filters=None,
    exclusive_start_key=None,
    page_size=10,
    forward_scan=False,
):
    """
    Do not allow the client to directly provide the table_name, index_name, partition_key_name,
    or any of the attribute value names in the filters. This is not a safe function to directly
    expose, just like you wouldn't expose the raw query interface of Dynamo.

    :param table_name:
    :param index_name:
    :param partition_key_name:
    :param sort_key_name:
    :param partition_key_value:
    :param sort_key_value_start:
    :param filters:
    :param exclusive_start_key:
    :param page_size:
    :param forward_scan:
    :return:
    """
    dynamodb = boto3.client("dynamodb")

    # Initialize the key condition expression for the partition key
    key_condition_expression = f"{partition_key_name} = :partition_key_value"
    expression_attribute_values = {":partition_key_value": {"S": partition_key_value}}
    expression_attribute_names = {}

    if sort_key_value_start is not None:
        # Placeholder for sort key to handle reserved words
        sort_key_placeholder = f"#{sort_key_name}"
        expression_attribute_names[sort_key_placeholder] = sort_key_name
        key_condition_expression += (
            f" AND {sort_key_placeholder} >= :sort_key_value_start"
        )
        expression_attribute_values[":sort_key_value_start"] = {
            "S": sort_key_value_start
        }

    # Prepare the query parameters
    query_params = {
        "TableName": table_name,
        "IndexName": index_name,
        "KeyConditionExpression": key_condition_expression,
        "ExpressionAttributeValues": expression_attribute_values,
        "ScanIndexForward": forward_scan,
    }

    # Add filter expression if begins_with_filters are provided
    filter_expressions = []

    if type_filters is not None:
        type_filter_expressions = []
        for index, type_filter in enumerate(type_filters):
            type_filter_expressions.append(f"#type_f = :type_value_{index}")
            # Assuming the type values are strings
            expression_attribute_values[f":type_value_{index}"] = {"S": type_filter}

        expression_attribute_names["#type_f"] = "type"
        type_filter_expression = " OR ".join(type_filter_expressions)
        filter_expressions.append(type_filter_expression)

    # Process dynamic filters
    if dynamic_filters is not None:
        for i, filter_def in enumerate(dynamic_filters):
            attr_path = filter_def.get("attribute", "")
            operator = filter_def.get("operator", "")
            value = filter_def.get("value", "")
            
            if not attr_path or not operator:
                continue
                
            # Handle nested attributes like "data.type"
            attr_parts = attr_path.split(".")
            if len(attr_parts) == 1:
                # Simple attribute
                attr_name_placeholder = f"#dyn_attr_{i}"
                expression_attribute_names[attr_name_placeholder] = attr_parts[0]
                attr_expression = attr_name_placeholder
            else:
                # Nested attribute like "data.type"
                attr_placeholders = []
                for j, part in enumerate(attr_parts):
                    placeholder = f"#dyn_attr_{i}_{j}"
                    expression_attribute_names[placeholder] = part
                    attr_placeholders.append(placeholder)
                attr_expression = ".".join(attr_placeholders)
            
            value_placeholder = f":dyn_value_{i}"
            expression_attribute_values[value_placeholder] = {"S": str(value)}
            
            # Build filter expression based on operator
            if operator == "startsWith":
                filter_expressions.append(f"begins_with({attr_expression}, {value_placeholder})")
            elif operator == "not_startsWith":
                filter_expressions.append(f"NOT begins_with({attr_expression}, {value_placeholder})")
            elif operator == "contains":
                filter_expressions.append(f"contains({attr_expression}, {value_placeholder})")
            elif operator == "not_contains":
                filter_expressions.append(f"NOT contains({attr_expression}, {value_placeholder})")
            elif operator == "equals":
                filter_expressions.append(f"{attr_expression} = {value_placeholder}")
            elif operator == "not_equals":
                filter_expressions.append(f"{attr_expression} <> {value_placeholder}")
            elif operator == "exists":
                filter_expressions.append(f"attribute_exists({attr_expression})")
            elif operator == "not_exists":
                filter_expressions.append(f"attribute_not_exists({attr_expression})")

    if filters:
        for filter_def in filters:
            attr_name = filter_def["attribute"]
            attr_values = filter_def["value"]
            attr_op = filter_def["expression"]

            # If attr_values is a single value, make it a list to standardize processing
            if not isinstance(attr_values, list):
                attr_values = [attr_values]

            # Create placeholders for attribute names
            attr_name_placeholder = f"#{attr_name}"
            expression_attribute_names[attr_name_placeholder] = attr_name

            # Create a separate contains condition for each value in the list
            for i, val in enumerate(attr_values):
                # Create placeholders for attribute values
                attr_value_placeholder = f":{attr_op}_value_{attr_name}_{i}"

                # Set the expression attribute values, conservatively assuming the values are strings
                expression_attribute_values[attr_value_placeholder] = {"S": str(val)}

                # Depending on the operation, add the correct filter expression
                if (
                    attr_op == "begins_with" and len(attr_values) == 1
                ):  # 'begins_with' can't be used with lists
                    filter_expressions.append(
                        f"begins_with({attr_name_placeholder}, {attr_value_placeholder})"
                    )
                elif attr_op == "contains":  # Check each value in the provided list
                    filter_expressions.append(
                        f"contains({attr_name_placeholder}, {attr_value_placeholder})"
                    )

    # Join all filter expressions with AND (if any)
    if filter_expressions:
        query_params["FilterExpression"] = " AND ".join(filter_expressions)
        if len(expression_attribute_names) > 0:
            query_params["ExpressionAttributeNames"] = expression_attribute_names
        if len(expression_attribute_values) > 0:
            query_params["ExpressionAttributeValues"] = expression_attribute_values

    # Always set a limit to control pagination
    # When there are filters, we may need to scan more items to get enough results
    if filter_expressions:
        # With filters, set a reasonable upper bound to avoid scanning entire table
        # while still allowing enough items to be scanned to meet the page_size after filtering
        query_params["Limit"] = min(page_size * 10, 1000)  # Cap at 1000 to avoid large scans
    else:
        query_params["Limit"] = page_size

    # Use exclusive_start_key if provided
    if exclusive_start_key:
        serializer = TypeSerializer()
        exclusive_start_key = {
            k: serializer.serialize(v) for k, v in exclusive_start_key.items()
        }
        query_params["ExclusiveStartKey"] = exclusive_start_key

    print(f"Query: {query_params}")

    # Query the DynamoDB table or index
    response = dynamodb.query(**query_params)

    items = [unmarshal_dynamodb_item(item) for item in response.get("Items", [])]
    last_evaluated_key = response.get("LastEvaluatedKey")
    if last_evaluated_key:
        last_evaluated_key = unmarshal_dynamodb_item(last_evaluated_key)

    # When filters are applied, we need to limit results to the requested page_size
    # and handle pagination correctly
    if filter_expressions and len(items) > page_size:
        # Limit items to requested page size
        items = items[:page_size]
        # If we're truncating results, create a pagination key from the last item
        if len(items) == page_size:
            last_item = items[-1]
            # Create pagination key based on the index being used
            last_evaluated_key = {
                partition_key_name: partition_key_value,
                sort_key_name: last_item.get(sort_key_name),
                "id": last_item.get("id"),  # Primary key for the main table
                "createdAt": last_item.get("createdAt"),  # Always include for GSI
                "type": last_item.get("type")  # Include type for type-based sorts
            }

    return {"success": True, "data": {"items": items, "pageKey": last_evaluated_key}}


def unmarshal_dynamodb_item(item):
    deserializer = TypeDeserializer()
    # Unmarshal a DynamoDB item into a normal Python dictionary
    python_data = {k: deserializer.deserialize(v) for k, v in item.items()}
    return python_data


def query_user_files_by_created_at2(
    user, created_at_start, page_size, exclusive_start_key=None
):
    # Initialize a boto3 DynamoDB client
    dynamodb = boto3.client("dynamodb")

    # Define the query parameters
    query_params = {
        "TableName": os.environ["FILES_DYNAMO_TABLE"],
        "IndexName": "createdByAndAt",  # This is the name of the GSI
        "KeyConditionExpression": "createdBy = :created_by AND createdAt >= :created_at_start",
        "ExpressionAttributeValues": {
            ":created_by": {"S": user},
            ":created_at_start": {
                "S": created_at_start
            },  # assuming 'createdAt' is a string timestamp
        },
        "Limit": page_size,
        "ScanIndexForward": True,  # Set to False if you want to sort by createdAt in descending order
    }

    # If an `exclusive_start_key` is provided, add it to the parameters
    if exclusive_start_key:
        query_params["ExclusiveStartKey"] = exclusive_start_key

    # Query the DynamoDB GSI
    response = dynamodb.query(**query_params)

    # Extract the items and the last evaluated key for pagination
    items = response.get("Items", [])
    plain_items = [unmarshal_dynamodb_item(item) for item in items]
    last_evaluated_key = response.get("LastEvaluatedKey")

    if last_evaluated_key:
        last_evaluated_key = unmarshal_dynamodb_item(last_evaluated_key)

    # Return the result as items and the pagination key to continue the query
    return {
        "success": True,
        "data": {"items": plain_items, "pageKey": last_evaluated_key},
    }


@validated("delete")
def delete_file(event, context, current_user, name, data):
    """
    Function to delete a file from the company's database. Handles partial and full deletions
    based on access permissions and file ownership.

    :param event: Event payload containing 'key'
    :param context: Lambda context (not used here)
    """

    try:
        # Extract the access token from the Authorization header
        access_token = data["access_token"]

        data = data.get("data", {})
        key = data.get("key")
        key = extract_key(key)

        if not key or not current_user:
            raise ValueError("'key' and 'name' are required")

        print(f"Key: {key}")
        print(f"Current User: {current_user}")
        # check if the file is an image
        try:
            print("Looking up file in files table")
            files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
            response = files_table.get_item(Key={"id": key})
            if "Item" not in response:
                # User doesn't match or item doesn't exist
                print(f"File not found for user {current_user}: {response}")
                return {"success": False, "message": "File not found"}
        except ClientError as e:
            print(f"Error getting file metadata from DynamoDB: {e}")
            error_message = e.response["Error"]["Message"]
            return {"success": False, "message": error_message}

        item = response["Item"]
        file_type = item.get("type")
        print(f"File type: {file_type}")
        is_image = file_type and file_type in IMAGE_FILE_TYPES

        file_contents_hash = None
        global_key = key
        if not is_image:
            # Read the hash files table to get global key
            hash_table = os.environ["HASH_FILES_DYNAMO_TABLE"]
            hash_files_table = dynamodb.Table(hash_table)
            hash_response = hash_files_table.get_item(Key={"id": key})
            if "Item" not in hash_response:
                print(f"File not found in hash files table")
                # applicable to files who have never been processed by RAG
                if current_user in key:
                    print("Deleting entry from user files table only")
                    # if they are owner then delete from user files
                    delete_file_from_table(key)
                    return {
                        "success": True,
                        "message": "File not found in hash files table, deleted from user files table only",
                    }

                return {
                    "success": False,
                    "message": "File not found in hash files table",
                }

            global_key = hash_response["Item"].get("textLocationKey")
            file_contents_hash = hash_response["Item"].get("hash")
            print(f"Global Key: {global_key}")

        # Get all access entries for this file
        oa_table = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]
        object_access_table = dynamodb.Table(oa_table)
        access_response = object_access_table.query(
            KeyConditionExpression=Key("object_id").eq(global_key)
        )

        if not access_response["Items"]:
            print(f"No access entries found for this file")
            return {
                "success": False,
                "message": "No access entries found for this file",
            }

        # Analyze access rights
        current_user_permission = None
        users_with_access = []
        high_access_count = 0

        for item in access_response["Items"]:
            users_with_access.append(
                {
                    "principal_id": item["principal_id"],
                    "permission_level": item["permission_level"],
                }
            )
            if item["principal_id"] == current_user:
                current_user_permission = item["permission_level"]
            if item["permission_level"].lower() in ["write", "owner"]:
                high_access_count += 1

        if current_user_permission is None:
            return {
                "success": False,
                "message": "Current user has no access to this file",
            }

        # Handle deletion paths
        if current_user_permission.lower() == "read":
            print("Current user has read access only")
            route_personal_file_deletion(global_key, key, current_user, is_image)

            # Delete from object access table
            try:
                object_access_table.delete_item(
                    Key={"object_id": global_key, "principal_id": current_user}
                )
                print("Deleted from object access file table")
            except ClientError as e:
                print(f"Error deleting file text from object access table: {e}")

        elif current_user_permission.lower() in ["write", "owner"]:
            if high_access_count > 1:
                # Multiple users have write access
                route_personal_file_deletion(global_key, key, current_user, is_image)

                # Delete from object access table
                try:
                    object_access_table.delete_item(
                        Key={"object_id": global_key, "principal_id": current_user}
                    )
                    print("Deleted from object access file table")
                except ClientError as e:
                    print(f"Error deleting file text from object access table: {e}")

            elif len(users_with_access) > 1:
                # Other users have access, but current user is the only one with write access
                print(
                    "Other users have access, but current user is the only one with write access"
                )
                # TODO Update with whatever we want to do to the other lower accessed people
                route_full_file_deletion(
                    global_key,
                    key,
                    file_contents_hash,
                    current_user,
                    access_token,
                    is_image,
                )
            else:
                # Current user is the only one with access
                print("Current user is the only one with access")
                route_full_file_deletion(
                    global_key,
                    key,
                    file_contents_hash,
                    current_user,
                    access_token,
                    is_image,
                )

        return {"success": True, "message": f"File deleted successfully"}

    except Exception as e:
        print(f"Exception occurred: {str(e)}")
        return {"success": False, "message": str(e)}


def route_personal_file_deletion(global_key, key, current_user, is_image):
    print(f"Route personal file deletion - is_image: {is_image}")
    if is_image:
        delete_file_from_table(key)
    else:
        delete_text_file_personally(global_key, key, current_user)


def route_full_file_deletion(
    global_key, key, file_contents_hash, current_user, access_token, is_image
):
    print(f"Route full file deletion - is_image: {is_image}")
    if is_image:
        delete_file_from_table(key)
        delete_image_file(key)
    else:
        delete_text_file_fully(
            global_key, key, file_contents_hash, current_user, access_token
        )


def delete_text_file_personally(global_key, key, current_user):
    """
    Perform a partial deletion of a file.
    """
    # Delete from hash files table
    hash_table = os.environ["HASH_FILES_DYNAMO_TABLE"]
    hash_files_table = dynamodb.Table(hash_table)

    # Query the GSI to get all items with the same textLocationKey
    response = hash_files_table.query(
        IndexName="TextLocationIndex",
        KeyConditionExpression=Key("textLocationKey").eq(global_key),
    )

    items_to_delete = response["Items"]
    # Check if there are more items (pagination)
    while "LastEvaluatedKey" in response:
        response = hash_files_table.query(
            IndexName="TextLocationIndex",
            KeyConditionExpression=Key("textLocationKey").eq(global_key),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items_to_delete.extend(response["Items"])

    print("Deleting items:")
    # Delete each item
    for item in items_to_delete:
        user = item.get("originalCreator") or item["id"].split("/")[0]
        print("item: ", item)
        print(f"user: {user}")
        if user == current_user:
            try:
                print(f"Deleting entry from hash files table: {item['id']}")
                hash_files_table.delete_item(Key={"id": item["id"]})
                print(f"Deleted item with id {item['id']} from hash files table")
            except ClientError as e:
                print(f"Error deleting file text from hash file table: {e}")
        else:
            print(
                f"Skipping delete, entry belongs to another user with the entry ID: {item['id']}"
            )

    print(f"Total items deleted: {len(items_to_delete)}")

    # Delete from user files table
    delete_file_from_table(key)


def delete_text_file_fully(
    global_key, key, file_contents_hash, current_user, access_token
):
    """
    Perform a full deletion of a file.
    """
    delete_text_file_personally(global_key, key, current_user)

    hash_table = os.environ["HASH_FILES_DYNAMO_TABLE"]
    hash_files_table = dynamodb.Table(hash_table)

    try:
        print(f"Deleting hash entry from hash files table: {file_contents_hash}")
        # delete hash
        hash_files_table.delete_item(Key={"id": file_contents_hash})
        print(f"Deleted item with id {file_contents_hash} from hash files table")
    except ClientError as e:
        print(f"Error deleting file text from hash file table: {e}")

    # Delete embedding progress
    embedding_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
    embedding_progress_table = dynamodb.Table(embedding_table)

    try:
        embedding_progress_table.delete_item(Key={"object_id": global_key})
        print("Deleted from embedding progress file table")
    except ClientError as e:
        print(f"Error deleting file text from embedding progress table: {e}")

    # Delete file from S3
    s3 = boto3.client("s3")
    s3_bucket_name = os.environ["S3_RAG_INPUT_BUCKET_NAME"]

    try:
        print(f"Deleting file from S3: {key}")
        s3.delete_object(Bucket=s3_bucket_name, Key=key)
        print("Deleted file from S3")
    except ClientError as e:
        print(f"Error deleting file from S3: {e}")

    # Delete from file-text bucket
    file_text_bucket = os.environ["S3_FILE_TEXT_BUCKET_NAME"]

    try:
        s3.delete_object(Bucket=file_text_bucket, Key=global_key)
        print("Deleted file from file-text bucket")
    except ClientError as e:
        print(f"Error deleting file text from S3: {e}")

    # Delete from embeddings
    success, result = delete_embeddings(access_token, global_key)
    if success:
        print(f"Embeddings deleted successfully. Result: {result}")
    else:
        print(f"Failed to delete embeddings. Error: {result}")

    # Finally: Delete from object access
    oa_table = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]
    object_access_table = dynamodb.Table(oa_table)
    try:
        object_access_table.delete_item(
            Key={"object_id": global_key, "principal_id": current_user}
        )
        print("Deleted from object access file table")
    except ClientError as e:
        print(f"Error deleting file text from object access table: {e}")


def delete_file_from_table(key):
    # Delete from user files table
    user_table = os.environ["FILES_DYNAMO_TABLE"]
    user_files_table = dynamodb.Table(user_table)
    try:
        user_files_table.delete_item(Key={"id": key})
        print("Deleted from user file table")
    except ClientError as e:
        print(f"Error deleting file text from the user file table: {e}")


def delete_image_file(key):
    bucket_name = os.environ["S3_IMAGE_INPUT_BUCKET_NAME"]
    s3 = boto3.client("s3")
    try:
        print(f"Deleting image file from S3: {key}")
        s3.delete_object(Bucket=bucket_name, Key=key)
        print("Deleted from S3")
    except ClientError as e:
        print(f"Error deleting file from S3: {e}")
