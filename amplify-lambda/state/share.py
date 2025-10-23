# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from datetime import datetime
import json
import time
import logging
import os
import uuid

from boto3.dynamodb.conditions import Key
from pycommon.api.object_permissions import update_object_permissions
from pycommon.api.data_sources import get_data_source_keys
from pycommon.api.assistants import share_assistant
import boto3

from pycommon.api.ops import api_tool
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, S3Operation

dynamodb = boto3.resource("dynamodb")
from pycommon.api.amplify_users import are_valid_amplify_users
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.SHARE.value])

from pycommon.logger import getLogger
logger = getLogger("shares")

# Import user_data functions for USER_STORAGE_TABLE
from state.user_data import handle_put_item, handle_query_by_type


def get_s3_data(s3_key):
    """Fetch data from S3 with backward compatibility for legacy and consolidation buckets"""
    s3 = boto3.resource("s3")
    consolidation_bucket = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]
    shares_bucket = os.environ.get("S3_SHARE_BUCKET_NAME")  # Legacy bucket
    
    # Try consolidation bucket first (new format)  
    consolidation_key = f"shares/{s3_key}"
    try:
        logger.debug("Fetching data from consolidation bucket: %s/%s", consolidation_bucket, consolidation_key)
        obj = s3.Object(consolidation_bucket, consolidation_key)
        data = obj.get()["Body"].read().decode("utf-8")
        return data
    except Exception as e:
        logger.debug("Not found in consolidation bucket: %s", str(e))
    
    # Fallback to legacy bucket if available
    if shares_bucket:
        try:
            logger.debug("Fetching data from legacy bucket: %s/%s", shares_bucket, s3_key)
            obj = s3.Object(shares_bucket, s3_key) 
            data = obj.get()["Body"].read().decode("utf-8")
            return data
        except Exception as e:
            logger.debug("Not found in legacy bucket either: %s", str(e))
            
    raise Exception(f"Data not found in either bucket for key: {s3_key}")


def get_data_from_dynamodb(user, name):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["SHARES_DYNAMODB_TABLE"])

    logger.debug("Querying DynamoDB for user: %s and name: %s", user, name)

    response = table.query(
        IndexName="UserNameIndex",
        KeyConditionExpression=Key("user").eq(user) & Key("name").eq(name),
    )

    items = response.get("Items", [])
    return items


@api_tool(
    path="/state/share/load",
    name="loadSharedState",
    method="POST",
    tags=["apiDocumentation"],
    description="""Retrieve specific shared data elements using their unique identifier key. 
    Example request:
    {
        "data": {
            "key": "yourEmail@vanderbilt.edu/sharedByEmail@vanderbilt.edu/932934805-24382.json"
        }
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "String. Required. Unique identifier for the shared resource to retrieve. Users can find their keys by calling /state/share",
            }
        },
        "required": ["key"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the shared data retrieval was successful",
            },
            "item": {
                "type": "string",
                "description": "The shared data content retrieved from S3",
            },
            "message": {
                "type": "string",
                "description": "Error message if unsuccessful",
            },
        },
        "required": ["success"],
    },
)
@required_env_vars({
    "SHARES_DYNAMODB_TABLE": [DynamoDBOperation.QUERY],
    "USER_STORAGE_TABLE": [DynamoDBOperation.QUERY],
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.GET_OBJECT],
    # "S3_SHARE_BUCKET_NAME": [S3Operation.GET_OBJECT], #Marked for deletion
})
@validated("load")
def load_data_from_s3(event, context, current_user, name, data):
    access = data["allowed_access"]
    if APIAccessType.SHARE.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        logger.warning("User does not have access to the share functionality")
        return {
            "success": False,
            "message": "User does not have access to the share functionality",
        }

    s3_key = data["data"]["key"]
    logger.info("Loading data from S3: %s", s3_key)

    # Use backward compatibility - check both USER_STORAGE_TABLE and legacy table
    key_found = False

    try:
        # NEW: Check shares in USER_STORAGE_TABLE
        try:
            new_shares = handle_query_by_type(
                current_user=current_user,
                app_id="amplify-shares", 
                entity_type="received"
            )
            
            # Check if s3_key exists in USER_STORAGE_TABLE shares
            for share in new_shares:
                share_data = share.get("data", {})
                if share_data.get("key") == s3_key:
                    key_found = True
                    logger.debug("Found key in USER_STORAGE_TABLE")
                    break
                    
        except Exception as e:
            logger.error("Error querying USER_STORAGE_TABLE: %s", e)
            # Continue to check legacy table
            
        # OLD: Check shares in SHARES_DYNAMODB_TABLE (backward compatibility)
        if not key_found:
            try:
                user_data = get_data_from_dynamodb(current_user, "/state/share")
                
                # Check if the given s3_key exists in the legacy user's data
                if any(
                    s3_key == data_dict.get("key")
                    for item in user_data
                    for data_dict in item.get("data", [])
                ):
                    key_found = True
                    logger.debug("Found key in SHARES_DYNAMODB_TABLE")
                    
            except Exception as e:
                logger.error("Error querying SHARES_DYNAMODB_TABLE: %s", e)

        if key_found:
            # If s3_key found in either table, fetch data from S3 and return
            logger.info("Loading data from S3: %s", s3_key)
            return {
                "success": True,
                "item": get_s3_data(s3_key),
            }
        else:
            return {"success": False, "message": "Data not found"}
            
    except Exception as e:
        logger.error("Error in load_data_from_s3: %s", e)
        return {"success": False, "message": "Error loading shared data"}


def put_s3_data(filename, data):
    """Put data in consolidation bucket with shares/ prefix"""
    s3_client = boto3.client("s3")
    consolidation_bucket = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]
    
    # Use consolidation bucket format for new shares
    consolidation_key = f"shares/{filename}"

    # Check if bucket exists
    try:
        s3_client.head_bucket(Bucket=consolidation_bucket)
    except boto3.exceptions.botocore.exceptions.ClientError:
        # If bucket does not exist, create it
        s3_client.create_bucket(Bucket=consolidation_bucket)

    # Now put the object (file)
    # print(f"Putting data: {data} in the consolidation S3 bucket")
    s3_client.put_object(
        Body=json.dumps(data).encode(), Bucket=consolidation_bucket, Key=consolidation_key
    )
    
    logger.info("Successfully uploaded share to consolidation bucket: %s", consolidation_key)
    return filename  # Return original filename without shares/ prefix for DynamoDB storage


def handle_conversation_datasource_permissions(
    access_token, recipient_users, conversations
):
    logger.debug("Enter handle shared datasources in conversations")
    total_data_sources_keys = []
    for conversation in conversations:
        for message in conversation["messages"]:
            # Check if 'data' and 'dataSources' keys exist and 'dataSources' has items
            if (
                "data" in message
                and "dataSources" in message["data"]
                and len(message["data"]["dataSources"]) > 0
            ):
                data_sources_keys = get_data_source_keys(message["data"]["dataSources"])
                total_data_sources_keys.extend(data_sources_keys)

    logger.debug("All Datasource Keys: %s", total_data_sources_keys)

    if len(total_data_sources_keys) != 0 and not update_object_permissions(
        access_token=access_token,
        shared_with_users=recipient_users,
        keys=total_data_sources_keys,
        object_type="datasource",
        principal_type="user",
        permission_level="read",
        policy="",
    ):
        logger.error("Error adding permissions for shared files in conversations")
        return {"success": False, "error": "Error updating datasource permissions"}

    logger.debug("object permissions for datasources success")
    return {"success": True, "message": "Updated object access permissions"}


def handle_share_assistant(access_token, prompts, recipient_users):
    for prompt in prompts:
        try:
            if (
                "data" in prompt
                and "assistant" in prompt["data"]
                and "definition" in prompt["data"]["assistant"]
            ):
                data = {
                    "assistantId": prompt["id"],
                    "recipientUsers": recipient_users,
                    "accessType": "read",
                    "policy": "",
                }

                if not share_assistant(access_token, data):
                    logger.error(
                        "Error making share assistant calls for assistant: %s",
                        prompt["id"]
                    )
                    return {
                        "success": False,
                        "error": "Could not successfully make the call to share assistants",
                    }
        except Exception as e:
            logger.error("Error sharing assistant: %s", e)
            return {"success": False, "error": "Error sharing assistant"}

    logger.debug("Share assistant call was a success")
    return {
        "success": True,
        "message": "Successfully made the calls to share assistants",
    }


@api_tool(
    path="/state/share",
    name="viewSharedState",
    method="GET",
    tags=["apiDocumentation"],
    description="""View a list of shared resources, including assistants, conversations, and organizational folders distributed by other Amplify platform users.
    
    Example response:
    [
      {
        "note": "testing share with a doc",
        "sharedAt": 1720714099836,
        "key": "yourEmail@vanderbilt.edu/sharedByEmail@vanderbilt.edu/9324805-24382.json",
        "sharedBy": "sharedByEmail@vanderbilt.edu"
      }
    ]
    """,
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the shared resources retrieval was successful",
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "note": {
                            "type": "string",
                            "description": "Note or description of the shared resource",
                        },
                        "sharedAt": {
                            "type": "integer",
                            "description": "Timestamp when the resource was shared",
                        },
                        "key": {
                            "type": "string",
                            "description": "Unique identifier key for the shared resource",
                        },
                        "sharedBy": {
                            "type": "string",
                            "description": "Email of the user who shared the resource",
                        },
                    },
                },
                "description": "Array of shared resource objects",
            },
        },
        "required": ["success", "items"],
    },
)
@required_env_vars({
    "SHARES_DYNAMODB_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.PUT_OBJECT],
    # "S3_SHARE_BUCKET_NAME": [S3Operation.PUT_OBJECT], #Marked for deletion
})
@validated("append")
def share_with_users(event, context, current_user, name, data):
    access_token = data["access_token"]
    data = data["data"]

    valid_users, _ = are_valid_amplify_users(access_token, data["sharedWith"])

    if len(valid_users) == 0:
        return {"success": False, "message": "No valid users to share with."}
    

    note = data["note"]
    new_data = data["sharedData"]
    new_data["sharedBy"] = current_user.lower()

    conversations = remove_code_interpreter_details(
        new_data["history"]
    )  # if it has any, else it just returns the conv back

    # Saving a workspace is sharing with yourself, so we don't need to go through this if it is a workspace save
    if len(conversations) > 0 and len(valid_users) > 0 and current_user != valid_users[0]:

        object_permissions = handle_conversation_datasource_permissions(
            access_token, valid_users, conversations
        )
        if not object_permissions["success"]:
            return object_permissions
    prompts = new_data["prompts"]

    # Saving a workspace is sharing with yourself, so we don't need to go through this if it is a workspace save
    if len(prompts) > 0 and len(valid_users) > 0 and current_user != valid_users[0]:
        try:
            shared_assistants = handle_share_assistant(access_token, prompts, valid_users)
            if not shared_assistants["success"]:
                # We need to continue because workspaces still need to be saved
                logger.warning("Error sharing assistants: %s", shared_assistants["error"])
        except Exception as e:
            logger.error("Error sharing assistants: %s", e)

    succesful_shares = []

    for user in valid_users:
        try:
            # Generate a unique file key for each user
            dt_string = datetime.now().strftime("%Y-%m-%d")
            s3_key = "{}/{}/{}/{}.json".format(
                user, current_user, dt_string, str(uuid.uuid4())
            )

            stored_key = put_s3_data(s3_key, new_data)
            timestamp = int(time.time() * 1000)

            # Store in USER_STORAGE_TABLE using new schema
            # PK: "{user_id}#amplify-shares#received"  
            # SK: "{sharer_id}#{date}#{uuid}"
            share_id = f"{current_user}#{dt_string}#{str(uuid.uuid4())}"
            
            share_data = {
                "sharedBy": current_user,
                "note": note,
                "sharedAt": timestamp,
                "key": stored_key,
            }

            # Use handle_put_item from user_data.py (same service)
            result = handle_put_item(
                current_user=user,
                app_id="amplify-shares", 
                entity_type="received",
                item_id=share_id,
                data=share_data
            )
            
            if result.get("uuid"):
                succesful_shares.append(user)

        except Exception as e:
            logging.error(e)
            continue

    return {"success": True, "items": succesful_shares}


def remove_code_interpreter_details(conversations):
    for conversation in conversations:
        if "codeInterpreterAssistantId" in conversation:
            del conversation["codeInterpreterAssistantId"]
            for message in conversation["messages"]:
                if (
                    "data" in message
                    and "state" in message["data"]
                    and "codeInterpreter" in message["data"]["state"]
                ):
                    del message["data"]["state"]["codeInterpreter"]
    return conversations
@required_env_vars({
    "SHARES_DYNAMODB_TABLE": [DynamoDBOperation.QUERY],
    "USER_STORAGE_TABLE": [DynamoDBOperation.QUERY],
})
@validated("read")
def get_share_data_for_user(event, context, current_user, name, data):
    access = data["allowed_access"]
    if APIAccessType.SHARE.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        return {
            "success": False,
            "message": "API key does not have access to share functionality",
        }

    all_shares = []

    try:
        # NEW: Get shares from USER_STORAGE_TABLE
        try:
            new_shares = handle_query_by_type(
                current_user=current_user,
                app_id="amplify-shares", 
                entity_type="received"
            )
            
            # Transform USER_STORAGE_TABLE format to legacy format
            for share in new_shares:
                share_data = share.get("data", {})
                formatted_share = {
                    "sharedBy": share_data.get("sharedBy", ""),
                    "note": share_data.get("note", ""),
                    "sharedAt": share_data.get("sharedAt", 0),
                    "key": share_data.get("key", ""),
                }
                all_shares.append(formatted_share)
                
            logger.info("Found %d shares in USER_STORAGE_TABLE", len(new_shares))
            
        except Exception as e:
            logger.error("Error querying USER_STORAGE_TABLE: %s", e)
            # Continue to check legacy table even if new table fails
            
        # OLD: Get shares from SHARES_DYNAMODB_TABLE (backward compatibility)
        try:
            tableName = os.environ["SHARES_DYNAMODB_TABLE"]
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(tableName)

            response = table.query(
                IndexName="UserNameIndex",
                KeyConditionExpression=Key("user").eq(current_user) & Key("name").eq(name),
            )

            items = response.get("Items", [])
            
            if items:
                # Extract legacy share data
                item = items[0]
                if "data" in item and isinstance(item["data"], list):
                    all_shares.extend(item["data"])
                    
            logger.info("Found %d legacy share records in SHARES_DYNAMODB_TABLE", len(items))
            
        except Exception as e:
            logger.error("Error querying SHARES_DYNAMODB_TABLE: %s", e)
            # Continue even if legacy table fails

        if not all_shares:
            logging.info(
                "No shared data found for current user: {} and name: {}".format(
                    current_user, name
                )
            )
            return {"success": True, "items": []}
        else:
            # Sort by sharedAt timestamp (newest first)
            try:
                all_shares.sort(key=lambda x: x.get("sharedAt", 0), reverse=True)
            except:
                pass  # If sorting fails, return unsorted
                
            logger.info("Total shares found: %d", len(all_shares))
            return {"success": True, "items": all_shares}

    except Exception as e:
        logging.error(e)
        return {"success": False}
