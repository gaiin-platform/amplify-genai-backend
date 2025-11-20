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

dynamodb = boto3.resource("dynamodb")
from pycommon.api.amplify_users import are_valid_amplify_users
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.SHARE.value])


def get_s3_data(bucket_name, s3_key):
    print("Fetching data from S3: {}/{}".format(bucket_name, s3_key))
    s3 = boto3.resource("s3")
    obj = s3.Object(bucket_name, s3_key)
    data = obj.get()["Body"].read().decode("utf-8")
    return data


def get_data_from_dynamodb(user, name):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

    print("Querying DynamoDB for user: {} and name: {}".format(user, name))

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
@validated("load")
def load_data_from_s3(event, context, current_user, name, data):
    access = data["allowed_access"]
    if APIAccessType.SHARE.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        print("User does not have access to the share functionality")
        return {
            "success": False,
            "message": "User does not have access to the share functionality",
        }

    s3_key = data["data"]["key"]
    print("Loading data from S3: {}".format(s3_key))

    user_data = get_data_from_dynamodb(current_user, "/state/share")

    # Check if the given s3_key exists in the user's data
    if any(
        s3_key == data_dict.get("key")
        for item in user_data
        for data_dict in item.get("data", [])
    ):
        # If s3_key found, fetch data from S3 and return
        print("Loading data from S3: {}".format(s3_key))
        return {
            "success": True,
            "item": get_s3_data(os.environ["S3_BUCKET_NAME"], s3_key),
        }

    else:
        return {"success": False, "message": "Data not found"}


def put_s3_data(bucket_name, filename, data):
    s3_client = boto3.client("s3")

    # Check if bucket exists
    try:
        s3_client.head_bucket(Bucket=bucket_name)
    except boto3.exceptions.botocore.exceptions.ClientError:
        # If bucket does not exist, create it
        s3_client.create_bucket(Bucket=bucket_name)

    # Now put the object (file)
    # print(f"Putting data: {data} in the share S3 bucket")
    s3_client.put_object(
        Body=json.dumps(data).encode(), Bucket=bucket_name, Key=filename
    )

    return filename


def handle_conversation_datasource_permissions(
    access_token, recipient_users, conversations
):
    print("Enter handle shared datasources in conversations")
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

    print("All Datasource Keys: ", total_data_sources_keys)

    if len(total_data_sources_keys) != 0 and not update_object_permissions(
        access_token=access_token,
        shared_with_users=recipient_users,
        keys=total_data_sources_keys,
        object_type="datasource",
        principal_type="user",
        permission_level="read",
        policy="",
    ):
        print(f"Error adding permissions for shared files in conversations")
        return {"success": False, "error": "Error updating datasource permissions"}

    print("object permissions for datasources success")
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
                    print(
                        "Error making share assistant calls for assistant: ",
                        prompt["id"],
                    )
                    return {
                        "success": False,
                        "error": "Could not successfully make the call to share assistants",
                    }
        except Exception as e:
            print("Error sharing assistant: ", e)
            return {"success": False, "error": "Error sharing assistant"}

    print("Share assistant call was a success")
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
                print("Error sharing assistants: ", shared_assistants["error"])
        except Exception as e:
            print("Error sharing assistants: ", e)

    succesful_shares = []

    for user in valid_users:
        try:
            # Generate a unique file key for each user
            dt_string = datetime.now().strftime("%Y-%m-%d")
            s3_key = "{}/{}/{}/{}.json".format(
                user, current_user, dt_string, str(uuid.uuid4())
            )

            put_s3_data(os.environ["S3_BUCKET_NAME"], s3_key, new_data)

            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

            # Step 1: Query using the secondary index to get the primary key
            response = table.query(
                IndexName="UserNameIndex",
                KeyConditionExpression=Key("user").eq(user) & Key("name").eq(name),
            )

            items = response.get("Items")
            timestamp = int(time.time() * 1000)

            if not items:
                # No item found with user and name, create a new item
                id_key = "{}/{}".format(
                    user, str(uuid.uuid4())
                )  # add the user's name to the key in DynamoDB
                new_item = {
                    "id": id_key,
                    "user": user,
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
                succesful_shares.append(user)

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


@validated("read")
def get_share_data_for_user(event, context, current_user, name, data):
    access = data["allowed_access"]
    if APIAccessType.SHARE.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        return {
            "success": False,
            "message": "API key does not have access to share functionality",
        }

    tableName = os.environ["DYNAMODB_TABLE"]
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(tableName)

    try:
        # Step 1: Query using the secondary index to get the primary key
        response = table.query(
            IndexName="UserNameIndex",
            KeyConditionExpression=Key("user").eq(current_user) & Key("name").eq(name),
        )

        items = response.get("Items")

        if not items:
            # No item found with user and name, return message
            logging.info(
                "No shared data found for current user: {} and name: {}".format(
                    current_user, name
                )
            )
            return {"success": True, "items": []}
        else:
            # Otherwise, retrieve the shared data
            item = items[0]
            if "data" in item:
                share_data = item["data"]
                return {"success": True, "items": share_data}

    except Exception as e:
        logging.error(e)
        return {"success": False}
