
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from datetime import datetime, timedelta
import hashlib
import os
import re
import time
import boto3
import json
import uuid
import csv
import io
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from common.data_sources import (
    get_data_source_keys,
    translate_user_data_sources_to_hash_data_sources,
)
from common.encoders import CombinedEncoder, DecimalEncoder
from common.object_permissions import (
    update_object_permissions,
    can_access_objects,
    simulate_can_access_objects,
)

from common.validate import validated
from decimal import Decimal


SYSTEM_TAG = "amplify:system"
ASSISTANT_BUILDER_TAG = "amplify:assistant-builder"
ASSISTANT_TAG = "amplify:assistant"
AMPLIFY_AUTOMATION_TAG = "amplify:automation"
AMPLIFY_API_KEY_MANAGER_TAG = "amplify:api-key-manager"
AMPLIFY_API_DOC_HELPER_TAG = "amplify:api-doc-helper"

RESERVED_TAGS = [
    SYSTEM_TAG,
    ASSISTANT_BUILDER_TAG,
    ASSISTANT_TAG,
    AMPLIFY_AUTOMATION_TAG,
    AMPLIFY_API_KEY_MANAGER_TAG,
    AMPLIFY_API_DOC_HELPER_TAG,
]


# used for system users who have access to a group. Group assistants are based on group permissions
# currently the data returned is best for our amplify wordpress plugin
@validated(op="get")
def retrieve_astg_for_system_use(event, context, current_user, name, data):
    query_params = event.get("queryStringParameters", {})
    print("Query params: ", query_params)
    assistantId = query_params.get("assistantId", "")
    pattern = r"^[a-zA-Z0-9-]+-\d{6}$"
    # must be in system user format
    if (
        not assistantId
        or assistantId[:6] == "astgp"
        or not re.match(pattern, current_user)
    ):
        return json.dumps(
            {
                "statusCode": 400,
                "body": {
                    "error": "Invalid or missing assistantId parameter or not a system user."
                },
            }
        )
    print("retrieving astgp data")
    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

    astgp = get_most_recent_assistant_version(assistants_table, assistantId)
    if not astgp:
        return json.dumps(
            {
                "statusCode": 400,
                "body": {"error": "AssistantId parameter does not match any assistant"},
            }
        )

    ast_data = astgp.get("data", {})

    groupId = ast_data.get("groupId", None)
    if not groupId:
        return json.dumps(
            {
                "statusCode": 400,
                "body": {"error": "The assistant does not have a groupId."},
            }
        )

    print("checking perms from group table")
    # check system user has access to group assistant
    groups_table = dynamodb.Table(os.environ["GROUPS_DYNAMO_TABLE"])

    try:
        response = groups_table.get_item(Key={"group_id": groupId})
        # Check if the item was found
        if "Item" in response:
            item = response["Item"]
            if current_user not in item.get("systemUsers", []):
                return json.dumps(
                    {
                        "statusCode": 401,
                        "body": {
                            "error": "User is not authorized to access assistant details"
                        },
                    }
                )
        else:
            return json.dumps(
                {
                    "statusCode": 400,
                    "body": {"error": "Item with group_id not found in dynamo"},
                }
            )

    except Exception as e:
        print(f"Error getting group from dynamo: {e}")
        return json.dumps(
            {
                "statusCode": 400,
                "body": {"error": "Failed to retrieve group from dynamo"},
            }
        )

    group_types_data = {
        group_type: {
            "isDisabled": details["isDisabled"],
            "disabledMessage": details["disabledMessage"],
        }
        for group_type, details in ast_data.get("groupTypeData", {}).items()
    }

    return {
        "statusCode": 200,
        "body": {
            "assistant": {
                "name": astgp["name"],
                "groupId": groupId,
                "instructions": astgp["instructions"],
                "group_types": group_types_data,
                "group_type_questions": ast_data.get("groupUserTypeQuestion", None),
                "model": ast_data.get("model", None),
                "disclaimer": astgp.get("disclaimer", None),
            }
        },
    }


def check_user_can_share_assistant(assistant, user_id):
    if assistant:
        return assistant["user"] == user_id
    return False


def check_user_can_delete_assistant(assistant, user_id):
    if assistant:
        return assistant["user"] == user_id
    return False


def check_user_can_update_assistant(assistant, user_id):
    if assistant:
        return assistant["user"] == user_id
    return False


@validated(op="delete")
def delete_assistant(event, context, current_user, name, data):
    access = data["allowed_access"]
    if "assistants" not in access and "full_access" not in access:
        return {
            "success": False,
            "message": "API key does not have access to assistant functionality",
        }
    """
    Deletes an assistant from the DynamoDB table based on the assistant's public ID.

    Args:
        event (dict): The event data from the API Gateway.
        context (dict): The Lambda function context.
        current_user (str): The ID of the current user.
        name (str): The name of the operation
        data (dict): The data for the delete operation, including the assistant's public ID.

    Returns:
        dict: A dictionary containing the success status and message.
    """
    print(f"Deleting assistant with data: {data}")

    users_who_have_perms = data["data"].get("removePermsForUsers", [])

    assistant_public_id = data["data"].get("assistantId", None)
    if not assistant_public_id:
        print("Assistant ID is required for deletion.")
        return {"success": False, "message": "Assistant ID is required for deletion."}

    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

    try:
        # Check if the user is authorized to delete the assistant
        existing_assistant = get_most_recent_assistant_version(
            assistants_table, assistant_public_id
        )
        if not check_user_can_delete_assistant(existing_assistant, current_user):
            print(
                f"User {current_user} is not authorized to delete assistant {assistant_public_id}"
            )
            return {
                "success": False,
                "message": "You are not authorized to delete this assistant.",
            }

        delete_assistant_by_public_id(assistants_table, assistant_public_id)
        # remove permissions
        delete_assistant_permissions_by_public_id(
            assistant_public_id, [current_user] + users_who_have_perms
        )
        delete_assistant_permissions_by_id(existing_assistant["id"], current_user)
        print(f"Assistant {assistant_public_id} deleted successfully.")
        return {"success": True, "message": "Assistant deleted successfully."}
    except Exception as e:
        print(f"Error deleting assistant: {e}")
        return {"success": False, "message": "Failed to delete assistant."}


@validated(op="list")
def list_assistants(event, context, current_user, name, data):
    access = data["allowed_access"]
    if "assistants" not in access and "full_access" not in access:
        return {
            "success": False,
            "message": "API key does not have access to assistant functionality",
        }
    """
    Retrieves all assistants associated with the current user.

    Args:
        event (dict): The event object containing the request data.
        context (dict): The context object containing information about the current environment.
        current_user (str): The ID of the current user.
        name (str): The name of the assistant (not used in this function).
        data (dict): The data object containing additional parameters (not used in this function).

    Returns:
        dict: A dictionary containing the list of assistants.
    """
    assistants = list_user_assistants(current_user)

    assistant_ids = [assistant["id"] for assistant in assistants]

    access_rights = {}
    if not data[ "is_group_sys_user" ]:  # saves us the call, access is determined by group members access list
        access_rights = simulate_can_access_objects(
            data["access_token"], assistant_ids, ["read", "write"]
        )

    # Make sure each assistant has a data field and initialize it if it doesn't
    for assistant in assistants:
        if "data" not in assistant:
            assistant["data"] = {}

    # for each assistant, add to its data the access rights
    for assistant in assistants:
        try:
            if assistant["data"] is None:
                assistant["data"] = {"access": None}
            assistant["data"]["access"] = access_rights.get(assistant["id"], {})
        except Exception as e:
            print(f"Error adding access rights to assistant {assistant['id']}: {e}")

    return {
        "success": True,
        "message": "Assistants retrieved successfully",
        "data": assistants,
    }


def list_user_assistants(user_id):
    """
    Retrieves all assistants associated with the given user ID and returns them as a list of dictionaries.

    Args:
        user_id (str): The ID of the user.

    Returns:
        list: A list of dictionaries, where each dictionary represents an assistant.
    """
    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

    # Query the DynamoDB table to get all assistants for the user
    response = assistants_table.query(
        IndexName="UserNameIndex",
        KeyConditionExpression=Key("user").eq(user_id),
    )

    # Create a list of dictionaries representing the assistants
    assistants = [item for item in response["Items"]]


    # filter out old versions 
    return get_latest_assistants(assistants)


def get_latest_assistants(assistants):
    latest_assistants = {}
    for assistant in assistants:
        # Set version to 1 if it doesn't exist
        assistant.setdefault('version', 1)
        assistant_id = assistant.get('assistantId', None)
        # will exclude system ast since they dont have assistantId
        if (assistant_id and (assistant_id not in latest_assistants or latest_assistants[assistant_id]['version'] < assistant['version'])):
            latest_assistants[assistant_id] = assistant
    
    return list(latest_assistants.values())
    

def get_assistant(assistant_id):
    """
    Retrieves the assistant with the given ID.

    Args:
        assistant_id (str): The ID of the assistant to retrieve.

    Returns:
        dict: A dictionary representing the assistant, or None if the assistant is not found.
    """
    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

    try:
        # Fetch the item from the DynamoDB table using the assistant ID
        response = assistants_table.get_item(Key={"id": assistant_id})

        # If the item is found, return it
        if "Item" in response:
            return response["Item"]
        else:
            return None
    except Exception as e:
        print(f"Error fetching assistant {assistant_id}: {e}")
        return None


@validated(op="create")
def create_assistant(event, context, current_user, name, data):
    access = data["allowed_access"]
    if "assistants" not in access and "full_access" not in access:
        return {
            "success": False,
            "message": "API key does not have access to assistant functionality",
        }

    print(f"Creating assistant with data: {data}")

    extracted_data = data["data"]
    assistant_name = extracted_data["name"]
    description = extracted_data["description"]
    uri = extracted_data.get("uri", None)
    assistant_public_id = extracted_data.get("assistantId", None)
    tags = extracted_data.get("tags", [])
    assistant_data = extracted_data.get("data", {})

    # delete any tag that starts with amplify: or is in the reserved tags
    tags = [
        tag
        for tag in tags
        if not tag.startswith("amplify:") and tag not in RESERVED_TAGS
    ]

    instructions = extracted_data["instructions"]
    disclaimer = extracted_data["disclaimer"]
    data_sources = extracted_data.get("dataSources", [])
    tools = extracted_data.get("tools", [])
    provider = extracted_data.get("provider", "amplify")
    is_group_sys_user = data["is_group_sys_user"]

    # datasource permission are handled in groups update assistant logic.
    if not is_group_sys_user:
        filtered_ds = []
        tag_data_sources = []
        for source in data_sources:
            if source["id"].startswith("tag://"):
                tag_data_sources.append(source)
            else:
                filtered_ds.append(source)

        print(f"Tag Data sources: {tag_data_sources}")

        if len(filtered_ds) > 0:
            print(f"Data sources before translation: {filtered_ds}")

            for i in range(len(filtered_ds)):
                source = filtered_ds[i]
                if "://" not in source["id"]:
                    filtered_ds[i]["id"] = source["key"]

            print(f"Final data sources before translation: {filtered_ds}")

            filtered_ds = translate_user_data_sources_to_hash_data_sources(filtered_ds)

            print(f"Data sources after translation and extraction: {filtered_ds}")

            data_sources = filtered_ds + tag_data_sources

            # Auth check: need to update to new permissions endpoint
            if not can_access_objects(data["access_token"], data_sources):
                return {
                    "success": False,
                    "message": "You are not authorized to access the referenced files",
                }

    # Assuming get_openai_client and file_keys_to_file_ids functions are defined elsewhere
    return create_or_update_assistant(
        access_token=data["access_token"],
        user_that_owns_the_assistant=current_user,
        assistant_name=assistant_name,
        description=description,
        instructions=instructions,
        assistant_data=assistant_data,
        disclaimer=disclaimer,
        tags=tags,
        data_sources=data_sources,
        tools=tools,
        provider=provider,
        uri=uri,
        assistant_public_id=assistant_public_id,
        is_group_sys_user=is_group_sys_user,
    )


@validated(op="share_assistant")
def share_assistant(event, context, current_user, name, data):
    access = data["allowed_access"]
    if "share" not in access and "full_access" not in access:
        return {
            "success": False,
            "message": "API key does not have access to share functionality",
        }

    extracted_data = data["data"]
    assistant_key = extracted_data["assistantId"]
    recipient_users = extracted_data["recipientUsers"]
    access_type = extracted_data.get("accessType", "read")
    note = extracted_data.get("note", "Shared via API")
    policy = extracted_data.get("policy", "")

    share_to_s3 = extracted_data.get("shareToS3", data["api_accessed"])

    return share_assistant_with(
        access_token=data["access_token"],
        current_user=current_user,
        assistant_key=assistant_key,
        recipient_users=recipient_users,
        access_type=access_type,
        note=note,
        share_to_S3=share_to_s3,
        policy=policy,
    )


def share_assistant_with(
    access_token,
    current_user,
    assistant_key,
    recipient_users,
    access_type,
    note,
    share_to_S3,
    policy="",
):  # data_sources,
    assistant_entry = get_assistant(assistant_key)

    if not assistant_entry:
        return {"success": False, "message": "Assistant not found"}

    data_sources = get_data_source_keys(assistant_entry["dataSources"])
    print("DS: ", data_sources)

    if not can_access_objects(
        access_token=access_token,
        data_sources=[{"id": assistant_key}],
        permission_level="owner",
    ):
        return {
            "success": False,
            "message": "You are not authorized to share this assistant",
        }

    assistant_public_id = assistant_entry["assistantId"]

    if not update_object_permissions(
        access_token=access_token,
        shared_with_users=recipient_users,
        keys=[assistant_public_id],
        object_type="assistant",
        principal_type="user",
        permission_level=access_type,
        policy=policy,
    ):
        print(f"Error updating permissions for assistant {assistant_public_id}")
        return {"success": False, "message": "Error updating permissions"}
    else:
        print(
            f"Update data sources object access permissions for users {recipient_users} for assistant {assistant_public_id}"
        )
        update_object_permissions(
            access_token=access_token,
            shared_with_users=recipient_users,
            keys=data_sources,
            object_type="datasource",
            principal_type="user",
            permission_level="read",
            policy="",
        )

        failed_shares = []
        for user in recipient_users:

            print(f"Creating alias for user {user} for assistant {assistant_public_id}")
            create_assistant_alias(
                user,
                assistant_public_id,
                assistant_entry["id"],
                assistant_entry["version"],
                "latest",
            )
            print(f"Created alias for user {user} for assistant {assistant_public_id}")

            # if api accessed
            if share_to_S3:
                print("API_accessed, sending to s3...")
                result = assistant_share_save(current_user, user, note, assistant_entry)
                if not result["success"]:
                    print("Failed share for: ", user)
                    failed_shares.append(user)

        print(f"Successfully updated permissions for assistant {assistant_public_id}")
        if len(failed_shares) > 0:
            return {
                "success": False,
                "message": "Unable to share with some users",
                "failedShares": failed_shares,
            }

        return {
            "success": True,
            "message": f"Assistants shared with users: {recipient_users}",
        }


def assistant_share_save(current_user, shared_with, note, assistant):
    try:
        # Generate a unique file key for each user
        dt_string = datetime.now().strftime("%Y-%m-%d")
        s3_key = "{}/{}/{}/{}.json".format(
            shared_with, current_user, dt_string, str(uuid.uuid4())
        )

        ast_id = assistant["id"]
        ast = assistant
        ast["tools"] = []
        ast["fileKeys"] = []
        # match frontend prompt data
        ast_prompt = {
            "id": ast_id,
            "type": "root_prompt",
            "name": assistant["name"],
            "description": assistant["description"],
            "content": assistant["instructions"],
            "folderId": "assistants",
            "data": {
                "assistant": {"id": ast_id, "definition": ast},
                **(assistant.get("data", {})),
                "noCopy": True,
                "noEdit": True,
                "noDelete": True,
                "noShare": True,
            },
        }
        ast_prompt["data"]["access"]["write"] = False
        shared_data = {
            "version": 1,
            "history": [],
            "prompts": [ast_prompt],
            "folders": [],
            "sharedBy": current_user,
        }
        bucket_name = os.environ["S3_SHARE_BUCKET_NAME"]
        s3_client = boto3.client("s3")

        print("Put assistant in s3")
        s3_client.put_object(
            Body=json.dumps(shared_data, default=str).encode(),
            Bucket=bucket_name,
            Key=s3_key,
        )

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ["SHARES_DYNAMODB_TABLE"])

        name = "/state/share"
        response = table.query(
            IndexName="UserNameIndex",
            KeyConditionExpression=Key("user").eq(shared_with) & Key("name").eq(name),
        )

        items = response.get("Items")
        timestamp = int(time.time() * 1000)

        if not items:
            # No item found with user and name, create a new item
            id_key = "{}/{}".format(
                shared_with, str(uuid.uuid4())
            )  # add the user's name to the key in DynamoDB
            new_item = {
                "id": id_key,
                "user": shared_with,
                "name": name,
                "data": [
                    {
                        "sharedBy": current_user,
                        "note": note,
                        "sharedAt": timestamp,
                        "key": s3_key,
                    }
                ],
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
            table.put_item(Item=new_item)

        else:
            # Otherwise, update the existing item
            item = items[0]

            result = table.update_item(
                Key={"id": item["id"]},
                ExpressionAttributeNames={"#data": "data"},
                ExpressionAttributeValues={
                    ":data": [
                        {
                            "sharedBy": current_user,
                            "note": note,
                            "sharedAt": timestamp,
                            "key": s3_key,
                        }
                    ],
                    ":updatedAt": timestamp,
                },
                UpdateExpression="SET #data = list_append(#data, :data), updatedAt = :updatedAt",
                ReturnValues="ALL_NEW",
            )
        print("Added to table")

        return {"success": True}

    except Exception as e:
        print(e)
        return {"success": False}


def decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError("Object of type 'Decimal' is not JSON serializable")


def get_most_recent_assistant_version(assistants_table, assistant_public_id):
    """
    Retrieves the most recent version of an assistant from the DynamoDB table.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        user_that_owns_the_assistant (str): The ID of the user that owns the assistant.
        assistant_name (str): The name of the assistant.
        assistant_public_id (str): The public ID of the assistant (optional).

    Returns:
        dict: The most recent assistant item, or None if not found.
    """
    if assistant_public_id:
        response = assistants_table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=Key("assistantId").eq(assistant_public_id),
            Limit=1,
            ScanIndexForward=False,
        )
        if response["Count"] > 0:
            return max(response["Items"], key=lambda x: x.get("version", 1))

    return None


def save_assistant(
    assistants_table,
    assistant_name,
    description,
    instructions,
    assistant_data,
    disclaimer,
    data_sources,
    provider,
    tools,
    user_that_owns_the_assistant,
    version,
    tags,
    uri=None,
    assistant_public_id=None,
    is_group_sys_user=False,
):
    """
    Saves the assistant data to the DynamoDB table.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        assistant_name (str): The name of the assistant.
        description (str): The description of the assistant.
        instructions (str): The instructions for the assistant.
        data_sources (list): A list of data sources used by the assistant.
        provider (str): The provider of the assistant (e.g., 'amplify', 'openai').
        tools (list): A list of tools used by the assistant.
        user_that_owns_the_assistant (str): The ID of the user that owns the assistant.
        assistant_public_id (str): The public ID of the assistant (optional).

    Returns:
        dict: The saved assistant data.
        :param assistant_public_id:
        :param version:
        :param tags:
        :param uri:
    """
    # Get the current timestamp in the format 2024-01-16T12:40:23.308162
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Create a dictionary of the core details of the assistant
    # This will be used to create a hash to check if the assistant already exists
    (
        core_sha256,
        datasources_sha256,
        full_sha256,
        instructions_sha256,
        disclaimer_sha256,
    ) = get_assistant_hashes(
        assistant_name,
        description,
        instructions,
        disclaimer,
        data_sources,
        provider,
        tools,
    )

    # to differentiate Group ast because when a group member chats with it they wont have access directly but the group system user will
    # so the object access relies on looking up if a user is a member of that group and the group system user has perms
    ast_prefix = "astg" if is_group_sys_user else "ast"
    assistant_database_id = f"{ast_prefix}/{str(uuid.uuid4())}"

    # Create an assistantId
    if not assistant_public_id:
        assistant_public_id = f"{ast_prefix}p/{str(uuid.uuid4())}"

    # Create the new item for the DynamoDB table
    new_item = {
        "id": assistant_database_id,
        "assistantId": assistant_public_id,
        "user": user_that_owns_the_assistant,
        "dataSourcesHash": datasources_sha256,
        "instructionsHash": instructions_sha256,
        "disclaimerHash": disclaimer_sha256,
        "tags": tags,
        "uri": uri,
        "coreHash": core_sha256,
        "hash": full_sha256,
        "name": assistant_name,
        "data": assistant_data,
        "description": description,
        "instructions": instructions,
        "disclaimer": disclaimer,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "dataSources": data_sources,
        "version": version,
    }

    assistants_table.put_item(Item=new_item)
    return new_item


def delete_assistant_by_public_id(assistants_table, assistant_public_id):
    """
    Deletes all versions of an assistant from the DynamoDB table based on the assistant's public ID.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        assistant_public_id (str): The public ID of the assistant.

    Returns:
        None
    """
    response = assistants_table.query(
        IndexName="AssistantIdIndex",
        KeyConditionExpression=Key("assistantId").eq(assistant_public_id),
    )

    for item in response["Items"]:
        assistants_table.delete_item(Key={"id": item["id"]})


@validated(op="remove_astp_permissions")
def remove_shared_ast_permissions(event, context, current_user, name, data):
    extracted_data = data["data"]
    ast_public_id = extracted_data["assistant_public_id"]
    users = extracted_data["users"]

    print(f"Removing permission for users {users}  for Astp {ast_public_id}")

    return delete_assistant_permissions_by_public_id(ast_public_id, users)


def delete_assistant_permissions_by_public_id(assistant_public_id, users):
    # delete public id is not as sensitive as assistant id
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"])
    for user in users:
        try:
            response = table.delete_item(
                Key={"object_id": assistant_public_id, "principal_id": user}
            )
            print(f"Deleted permissions for user {user}")
        except Exception as e:
            print(f"Failed to delete permissions for user {user}. Error: {str(e)}")

    return {"success": True, "message": "Permissions successfully deleted."}


def delete_assistant_permissions_by_id(ast_id, current_user):
    # current user must be principal user to do this
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"])
    try:
        response = table.get_item(
            Key={"object_id": ast_id, "principal_id": current_user}
        )

        if "Item" in response:
            delete_response = table.delete_item(
                Key={"object_id": ast_id, "principal_id": current_user}
            )
            print(f"Permissions deleted for assistant ID {ast_id}.")
            return {"success": True, "message": "Permissions successfully deleted."}
        else:
            # Current user is not authorized to delete the entry
            return {"success": False, "message": "Not authorized to delete permissions"}

    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return {"success": False, "message": str(e)}


def delete_assistant_by_id(assistants_table, assistant_id):
    """
    Deletes a specific version of an assistant from the DynamoDB table based on the assistant's ID.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        assistant_id (str): The ID of the assistant.

    Returns:
        None
    """
    assistants_table.delete_item(Key={"id": assistant_id})


def delete_assistant_version(assistants_table, assistant_public_id, version):
    """
    Deletes a specific version of an assistant from the DynamoDB table based on the assistant's public ID and version.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        assistant_public_id (str): The public ID of the assistant.
        version (int): The version of the assistant to delete.

    Returns:
        None
    """
    response = assistants_table.query(
        IndexName="AssistantIdIndex",
        KeyConditionExpression=Key("assistantId").eq(assistant_public_id),
        FilterExpression=Attr("version").eq(version),
    )

    for item in response["Items"]:
        assistants_table.delete_item(Key={"id": item["id"]})


def create_or_update_assistant(
    access_token,
    user_that_owns_the_assistant,
    assistant_name,
    description,
    instructions,
    assistant_data,
    disclaimer,
    tags,
    data_sources,
    tools,
    provider,
    uri,
    assistant_public_id=None,
    is_group_sys_user=False,
):
    """
    Creates a new assistant in the DynamoDB table and sets the appropriate permissions.

    Args:
        access_token (str): The access token of the user (required for updating permissions to give the user access).
        user_that_owns_the_assistant (str): The ID of the user creating the assistant.
        assistant_name (str): The name of the assistant.
        description (str): The description of the assistant.
        instructions (str): The instructions for the assistant.
        tags (list): A list of tags associated with the assistant.
        data_sources (list): A list of data sources used by the assistant.
        tools (list): A list of tools used by the assistant.
        provider (str): The provider of the assistant (e.g., 'amplify', 'openai').
        uri (str): The URI of the assistant (optional).
        assistant_public_id (str): The public ID of the assistant (optional).

    Returns:
        dict: A dictionary containing the success status, message, and data (assistant ID and version).
    """
    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

    existing_assistant = get_most_recent_assistant_version(
        assistants_table, assistant_public_id
    )

    principal_type = "group" if is_group_sys_user else "user"

    if existing_assistant:

        if not check_user_can_update_assistant(
            existing_assistant, user_that_owns_the_assistant
        ):
            return {
                "success": False,
                "message": "You are not authorized to update this assistant",
            }

        # The assistant already exists, so we need to create a new version
        assistant_public_id = existing_assistant["assistantId"]
        assistant_name = assistant_name
        assistant_version = existing_assistant[
            "version"
        ]  # Default to version 1 if not present

        # Increment the version number
        new_version = assistant_version + 1

        new_item = save_assistant(
            assistants_table,
            assistant_name,
            description,
            instructions,
            assistant_data,
            disclaimer,
            data_sources,
            provider,
            tools,
            user_that_owns_the_assistant,
            new_version,
            tags,
            uri,
            assistant_public_id,
            is_group_sys_user,
        )
        new_item["version"] = new_version

        # Update the permissions for the new assistant
        if not update_object_permissions(
            access_token,
            [user_that_owns_the_assistant],
            [new_item["id"]],
            "assistant",
            principal_type,
            "owner",
        ):
            print(f"Error updating permissions for assistant {new_item['id']}")
        else:
            print(f"Successfully updated permissions for assistant {new_item['id']}")

        update_assistant_latest_alias(assistant_public_id, new_item["id"], new_version)

        print(f"Indexing assistant {new_item['id']} for RAG")
        save_assistant_for_rag(new_item)
        print(f"Added RAG entry for {new_item['id']}")

        # Return success response
        return {
            "success": True,
            "message": "Assistant created successfully",
            "data": {
                "assistantId": assistant_public_id,
                "id": new_item["id"],
                "version": new_version,
            },
        }
    else:
        new_item = save_assistant(
            assistants_table,
            assistant_name,
            description,
            instructions,
            assistant_data,
            disclaimer,
            data_sources,
            provider,
            tools,
            user_that_owns_the_assistant,
            1,
            tags,
            uri,
            None,
            is_group_sys_user,
        )

        # Update the permissions for the new assistant
        if not update_object_permissions(
            access_token,
            [user_that_owns_the_assistant],
            [new_item["assistantId"], new_item["id"]],
            "assistant",
            principal_type,
            "owner",
        ):
            print(f"Error updating permissions for assistant {new_item['id']}")
        else:
            print(f"Successfully updated permissions for assistant {new_item['id']}")

        create_assistant_alias(
            user_that_owns_the_assistant,
            new_item["assistantId"],
            new_item["id"],
            1,
            "latest",
        )

        print(f"Indexing assistant {new_item['id']} for RAG")
        save_assistant_for_rag(new_item)
        print(f"Added RAG entry for {new_item['id']}")

        # Return success response
        return {
            "success": True,
            "message": "Assistant created successfully",
            "data": {
                "assistantId": new_item["assistantId"],
                "id": new_item["id"],
                "version": new_item["version"],
            },
        }


def get_assistant_hashes(
    assistant_name, description, instructions, disclaimer, data_sources, provider, tools
):
    core_details = {
        "instructions": instructions,
        "disclaimer": disclaimer,
        "dataSources": data_sources,
        "tools": tools,
        "provider": provider,
    }
    # Create a sha256 of the core details to use as a hash
    # This will be used to check if the assistant already exists
    # and to check if the assistant has been updated
    core_sha256 = hashlib.sha256(
        json.dumps(core_details, sort_keys=True).encode()
    ).hexdigest()
    datasources_sha256 = hashlib.sha256(
        json.dumps(data_sources.sort(key=lambda x: x["id"])).encode()
    ).hexdigest()
    instructions_sha256 = hashlib.sha256(
        json.dumps(instructions, sort_keys=True).encode()
    ).hexdigest()
    disclaimer_sha256 = hashlib.sha256(
        json.dumps(disclaimer, sort_keys=True).encode()
    ).hexdigest()
    core_details["assistant"] = assistant_name
    core_details["description"] = description
    full_sha256 = hashlib.sha256(
        json.dumps(core_details, sort_keys=True).encode()
    ).hexdigest()
    return (
        core_sha256,
        datasources_sha256,
        full_sha256,
        instructions_sha256,
        disclaimer_sha256,
    )


def alias_key_of_type(assistant_public_id, alias_type):
    return f"{assistant_public_id}?type={alias_type}"


def create_assistant_alias(user, assistant_public_id, database_id, version, alias_type):
    dynamodb = boto3.resource("dynamodb")
    alias_table = dynamodb.Table(os.environ["ASSISTANTS_ALIASES_DYNAMODB_TABLE"])
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    new_item = {
        "assistantId": alias_key_of_type(assistant_public_id, alias_type),
        "user": user,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "aliasTo": alias_type,
        "currentVersion": version,
        "data": {"id": database_id},
    }
    alias_table.put_item(Item=new_item)


def update_assistant_latest_alias(assistant_public_id, new_id, version):
    update_assistant_alias_by_type(assistant_public_id, new_id, version, "latest")


def update_assistant_published_alias(assistant_public_id, new_id, version):
    update_assistant_alias_by_type(
        assistant_public_id, new_id, version, "latest_published"
    )


def update_assistant_alias_by_type(assistant_public_id, new_id, version, alias_type):
    try:
        dynamodb = boto3.resource("dynamodb")
        alias_table = dynamodb.Table(os.environ["ASSISTANTS_ALIASES_DYNAMODB_TABLE"])

        alias_key = alias_key_of_type(assistant_public_id, alias_type)

        # Find all current entries for assistantId (hash) across all users (range) where version = "latest"
        response = alias_table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=boto3.dynamodb.conditions.Key("assistantId").eq(
                alias_key
            ),
        )

        for item in response["Items"]:
            try:
                print(f"Updating assistant alias: {item}")
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
                updated_item = {
                    "assistantId": alias_key,
                    "user": item["user"],
                    "updatedAt": timestamp,
                    "createdAt": item["createdAt"],
                    "currentVersion": version,
                    "aliasTo": item["aliasTo"],
                    "data": {"id": new_id},
                }
                alias_table.put_item(Item=updated_item)
                print(f"Updated assistant alias: {updated_item}")
            except ClientError as e:
                print(f"Error updating assistant alias: {e}")
    except ClientError as e:
        print(f"Error updating assistant alias: {e}")


def generate_assistant_chunks_metadata(assistant):
    output = {
        "chunks": [
            {
                "content": f"{assistant['description']}",
                "locations": [
                    {
                        "assistantId": assistant["assistantId"],
                        "version": assistant["version"],
                        "updatedAt": assistant["updatedAt"],
                        "createdAt": assistant["createdAt"],
                        "tags": assistant["tags"],
                    }
                ],
                "indexes": [0],
                "char_index": 0,
            },
            {
                "content": f"{assistant['name']}: {assistant['description']}. {', '.join(assistant['tags'])}",
                "locations": [
                    {
                        "assistantId": assistant["assistantId"],
                        "version": assistant["version"],
                        "updatedAt": assistant["updatedAt"],
                        "createdAt": assistant["createdAt"],
                        "tags": assistant["tags"],
                    }
                ],
                "indexes": [0],
                "char_index": 0,
            },
            {
                "content": assistant["instructions"],
                "locations": [
                    {
                        "assistantId": assistant["assistantId"],
                        "version": assistant["version"],
                        "updatedAt": assistant["updatedAt"],
                        "createdAt": assistant["createdAt"],
                        "tags": assistant["tags"],
                    }
                ],
                "indexes": [0],
                "char_index": 0,
            },
            {
                "content": f"{assistant['name']}: {assistant['instructions']}. {', '.join(assistant['tags'])}",
                "locations": [
                    {
                        "assistantId": assistant["assistantId"],
                        "version": assistant["version"],
                        "updatedAt": assistant["updatedAt"],
                        "createdAt": assistant["createdAt"],
                        "tags": assistant["tags"],
                    }
                ],
                "indexes": [0],
                "char_index": 0,
            },
        ],
        "src": assistant["id"],
    }
    return output


def save_assistant_for_rag(assistant):
    try:
        key = assistant["id"]
        assistant_chunks = generate_assistant_chunks_metadata(assistant)
        chunks_bucket = os.environ["S3_RAG_CHUNKS_BUCKET_NAME"]

        s3 = boto3.client("s3")
        print(f"Saving assistant description to {key}-assistant.chunks.json")
        chunks_key = f"assistants/{key}-assistant.chunks.json"
        s3.put_object(
            Bucket=chunks_bucket,
            Key=chunks_key,
            Body=json.dumps(assistant_chunks, cls=CombinedEncoder),
        )
        print(f"Uploaded chunks to {chunks_bucket}/{chunks_key}")
    except Exception as e:
        print(f"Error saving assistant for RAG: {e}")


# queries GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE (updated at the end of every conversation via amplify-lambda-js/common/chat/controllers/sequentialChat.js)
# to see all conversations of a specific group assistant. assistantId must be provided in the data field.
@validated(op="get_group_assistant_conversations")
def get_group_assistant_conversations(event, context, current_user, name, data):
    if "data" not in data or "assistantId" not in data["data"]:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "assistantId is required"}),
        }

    assistant_id = data["data"]["assistantId"]

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE"])

    try:
        response = table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=Key("assistantId").eq(assistant_id),
        )

        conversations = response["Items"]
        # print(f"Found {len(conversations)} conversations for assistant {assistant_id}")
        # print(f"Conversations: {json.dumps(conversations, cls=CombinedEncoder)}")

        while "LastEvaluatedKey" in response:
            response = table.query(
                IndexName="AssistantIdIndex",
                KeyConditionExpression=Key("assistantId").eq(assistant_id),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            conversations.extend(response["Items"])

        return {
            "statusCode": 200,
            "body": json.dumps(conversations, cls=CombinedEncoder),
        }

    except ClientError as e:
        print(f"DynamoDB ClientError: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "An unexpected error occurred"}),
        }


@validated(op="get_group_conversations_data")
def get_group_conversations_data(event, context, current_user, name, data):
    if (
        "data" not in data
        or "conversationId" not in data["data"]
        or "assistantId" not in data["data"]
    ):
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"error": "conversationId and assistantId are required"}
            ),
        }

    conversation_id = data["data"]["conversationId"]
    assistant_id = data["data"]["assistantId"]

    s3 = boto3.client("s3")
    bucket_name = os.environ["S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME"]
    key = f"{assistant_id}/{conversation_id}.txt"

    try:
        response = s3.get_object(Bucket=bucket_name, Key=key)
        content = response["Body"].read().decode("utf-8")

        return {
            "statusCode": 200,
            "body": json.dumps({"content": content}),
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Conversation not found"}),
            }
        else:
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Error retrieving conversation content"}),
            }


# accessible via API gateway for users to collect data on a group assistant
# user MUST provide assistantId
# optional parameters to specify:
# - specify date range: startDate-endDate (default null, meaning provide all data regardless of date)
# - include conversation data: true/false (default false, meaning provide only dashboard data, NOT conversation statistics in CSV format)
# - include conversation content: true/false (default false, meaning content of conversations is not provided)
@validated(op="get_group_assistant_dashboards")
def get_group_assistant_dashboards(event, context, current_user, name, data):
    if "data" not in data or "assistantId" not in data["data"]:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "assistantId is required"}),
        }

    assistant_id = data["data"]["assistantId"]
    start_date = data["data"].get("startDate")
    end_date = data["data"].get("endDate")
    include_conversation_data = data["data"].get("includeConversationData", False)
    include_conversation_content = data["data"].get("includeConversationContent", False)

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE"])
    # table = dynamodb.Table("group-assistant-conversations-content-test")

    try:
        response = table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=Key("assistantId").eq(assistant_id),
        )

        conversations = response["Items"]

        while "LastEvaluatedKey" in response:
            response = table.query(
                IndexName="AssistantIdIndex",
                KeyConditionExpression=Key("assistantId").eq(assistant_id),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            conversations.extend(response["Items"])

        # Filter conversations by date range if specified
        if start_date and end_date:
            start = datetime.fromisoformat(start_date)
            end = datetime.fromisoformat(end_date)
            conversations = [
                conv
                for conv in conversations
                if start <= datetime.fromisoformat(conv.get("timestamp", "")) <= end
            ]

        # Prepare dashboard data
        assistant_name = (
            conversations[0].get("assistantName", "") if conversations else ""
        )
        unique_users = set(conv.get("user", "") for conv in conversations)
        total_prompts = sum(int(conv.get("numberPrompts", 0)) for conv in conversations)
        total_conversations = len(conversations)

        entry_points = {}
        categories = {}
        employee_types = {}
        user_employee_types = {}
        total_user_rating = 0
        total_system_rating = 0
        user_rating_count = 0
        system_rating_count = 0

        for conv in conversations:
            # Determine entry points
            entry_points[conv.get("entryPoint", "")] = (
                entry_points.get(conv.get("entryPoint", ""), 0) + 1
            )

            # Determine categories
            category = conv.get("category", "").strip()
            if category:  # Only add non-empty categories
                categories[category] = categories.get(category, 0) + 1

            # Update user_employee_types
            user = conv.get("user", "")
            employee_type = conv.get("employeeType", "")
            if user not in user_employee_types:
                user_employee_types[user] = employee_type
                employee_types[employee_type] = employee_types.get(employee_type, 0) + 1

            # Calculate user rating
            user_rating = conv.get("userRating")
            if user_rating is not None:
                try:
                    total_user_rating += float(user_rating)
                    user_rating_count += 1
                except ValueError:
                    print(f"Invalid user rating value: {user_rating}")

            # Calculate system rating
            system_rating = conv.get("systemRating")
            if system_rating is not None:
                try:
                    total_system_rating += float(system_rating)
                    system_rating_count += 1
                except ValueError:
                    print(f"Invalid system rating value: {system_rating}")

        average_user_rating = (
            float(total_user_rating) / float(user_rating_count)
            if user_rating_count > 0
            else None
        )
        average_system_rating = (
            float(total_system_rating) / float(system_rating_count)
            if system_rating_count > 0
            else None
        )

        dashboard_data = {
            "assistantId": assistant_id,
            "assistantName": assistant_name,
            "numUsers": len(unique_users),
            "totalConversations": total_conversations,
            "averagePromptsPerConversation": (
                float(total_prompts) / float(total_conversations)
                if total_conversations > 0
                else 0.0
            ),
            "entryPointDistribution": entry_points,
            "categoryDistribution": categories,
            "employeeTypeDistribution": employee_types,
            "averageUserRating": average_user_rating,
            "averageSystemRating": average_system_rating,
        }

        response_data = {"dashboardData": dashboard_data}

        if include_conversation_data or include_conversation_content:
            s3 = boto3.client("s3")
            bucket_name = os.environ["S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME"]

            for conv in conversations:
                if include_conversation_content:
                    conversation_id = conv.get("conversationId")
                    if conversation_id:
                        key = f"{assistant_id}/{conversation_id}.txt"
                        try:
                            obj = s3.get_object(Bucket=bucket_name, Key=key)
                            conv["conversationContent"] = (
                                obj["Body"].read().decode("utf-8")
                            )
                        except ClientError as e:
                            if e.response["Error"]["Code"] == "NoSuchKey":
                                print(
                                    f"Conversation content not found for {conversation_id}"
                                )
                            else:
                                print(
                                    f"Error retrieving S3 content for conversation {conversation_id}: {str(e)}"
                                )

            # response_data["conversationData"] = conversations

            # Generate a unique filename
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"conversation_data_{assistant_id}_{timestamp}.json"

            # Upload conversation data to S3
            s3.put_object(
                Bucket=bucket_name,
                Key=filename,
                Body=json.dumps(conversations, cls=CombinedEncoder),
                ContentType="application/json",
            )

            # Generate a pre-signed URL that's valid for 1 hour
            presigned_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket_name, "Key": filename},
                ExpiresIn=3600,
            )

            response_data["conversationDataUrl"] = presigned_url

        return {
            "statusCode": 200,
            "body": json.dumps(response_data, cls=CombinedEncoder),
        }

    except ClientError as e:
        print(f"DynamoDB ClientError: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "An unexpected error occurred"}),
        }


@validated(op="save_user_rating")
def save_user_rating(event, context, current_user, name, data):
    if (
        "data" not in data
        or "conversationId" not in data["data"]
        or "userRating" not in data["data"]
    ):
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "conversationId and userRating are required"}),
        }

    conversation_id = data["data"]["conversationId"]
    user_rating = data["data"]["userRating"]
    user_feedback = data["data"].get("userFeedback")  # Get userFeedback if present

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE"])

    try:
        # Construct the UpdateExpression based on whether userFeedback is present
        update_expression = "SET userRating = :rating"
        expression_attribute_values = {":rating": user_rating}

        if user_feedback:
            update_expression += ", userFeedback = :feedback"
            expression_attribute_values[":feedback"] = user_feedback

        response = table.update_item(
            Key={"conversationId": conversation_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="UPDATED_NEW",
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": (
                        "User rating and feedback saved successfully"
                        if user_feedback
                        else "User rating saved successfully"
                    ),
                    "updatedAttributes": response.get("Attributes"),
                },
                cls=DecimalEncoder,
            ),
        }

    except ClientError as e:
        print(f"DynamoDB ClientError: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "An unexpected error occurred"}),
        }
