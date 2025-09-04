# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from datetime import datetime
import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from pycommon.const import APIAccessType
from pycommon.api.amplify_users import are_valid_amplify_users

# Initialize AWS services
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")


from pycommon.api.amplify_groups import (
    verify_member_of_ast_admin_group,
    verify_user_in_amp_group,
)

from pycommon.api.data_sources import (
    translate_user_data_sources_to_hash_data_sources,
)

from pycommon.api.ops import api_tool
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ASSISTANTS.value])

from pycommon.encoders import CustomPydanticJSONEncoder

from service.core import check_user_can_update_assistant, get_most_recent_assistant_version, is_group_sys_user, save_assistant, update_assistant_latest_alias

@api_tool(
    path="/assistant/lookup",
    name="lookupAssistant",
    method="POST",
    tags=["standaloneAst"],
    description="""Lookup an assistant by its ID or path.""",
    parameters={
        "type": "object",
        "properties": {
            "assistantId": {
                "type": "string",
                "description": "ID of the assistant to lookup. Example: 'astp/3io4u5ipy34jkelkdfweiorwur'",
            },
            "path": {
                "type": "string",
                "description": "Alternative lookup by path. Example: 'my/assistant/path'",
            },
        },
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the lookup was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "properties": {
                    "assistantId": {
                        "type": "string",
                        "description": "Public identifier of the assistant",
                    },
                    "name": {"type": "string", "description": "Name of the assistant"},
                    "description": {
                        "type": "string",
                        "description": "Description of the assistant's purpose",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Instructions for the assistant",
                    },
                    "disclaimer": {
                        "type": "string",
                        "description": "Disclaimer for the assistant's responses",
                    },
                    "dataSources": {
                        "type": "array",
                        "description": "List of data sources used by the assistant",
                        "items": {"type": "object"},
                    },
                    "createdAt": {
                        "type": "string",
                        "description": "Timestamp when the assistant was created",
                    },
                    "version": {
                        "type": "integer",
                        "description": "Version number of the assistant",
                    },
                },
                "required": [
                    "assistantId",
                    "name",
                    "description",
                    "instructions",
                    "dataSources",
                    "createdAt",
                    "version",
                ],
            },
        },
        "required": ["success", "message", "data"],
    },
)
@validated(op="lookup")
def lookup_assistant_path(event, context, current_user, name, data):
    token = data["access_token"]
    try:
        # Get the astPath from the request data
        ast_path = data.get("data", {}).get("astPath")

        print(f"Looking up assistant with path: '{ast_path}', type: {type(ast_path)}")

        if not ast_path:
            print("No astPath provided in the request")
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "success": False,
                        "message": "astPath is required",
                        "data": None,
                    },
                    cls=CustomPydanticJSONEncoder,
                ),
            }

        # Convert the path to lowercase to match frontend behavior
        ast_path = ast_path.lower() if isinstance(ast_path, str) else ast_path
        print(f"Using lowercase path for lookup: '{ast_path}'")

        # Get DynamoDB resource
        dynamodb = boto3.resource("dynamodb")
        lookup_table = dynamodb.Table(os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE"))

        # Print the table name for debugging
        table_name = os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE")
        print(f"Using lookup table: {table_name}")

        # Look up the assistant in the table
        print(f"Querying DynamoDB with Key={{'astPath': '{ast_path}'}}")
        response = lookup_table.get_item(Key={"astPath": ast_path})

        # Log the raw response
        print(
            f"DynamoDB response: {json.dumps(response, cls=CustomPydanticJSONEncoder)}"
        )

        # Check if the item exists
        if "Item" not in response:
            print(f"No item found for path: '{ast_path}'")

            return {
                "statusCode": 404,
                "body": json.dumps(
                    {
                        "success": False,
                        "message": f"No assistant found for path: {ast_path}",
                        "data": None,
                    },
                    cls=CustomPydanticJSONEncoder,
                ),
            }

        # Get the item from the response
        item = response["Item"]

        assistant_id = item.get("assistantId")

        if not assistant_id:
            return {
                "statusCode": 404,
                "body": json.dumps(
                    {
                        "success": False,
                        "message": f"Path is no longer associated with an assistant",
                        "data": None,
                    },
                    cls=CustomPydanticJSONEncoder,
                ),
            }

        # Update lastAccessed
        current_time = datetime.now().isoformat()
        try:
            # Update the item with access tracking information
            lookup_table.update_item(
                Key={"astPath": ast_path},
                UpdateExpression="SET lastAccessed = :time",
                ExpressionAttributeValues={":time": current_time},
            )
            print(
                f"Updated access tracking for path '{ast_path}': lastAccessed={current_time}, lastAccessedBy={current_user}"
            )
        except Exception as update_error:
            # Log the error but continue - don't fail the lookup just because tracking failed
            print(f"Error updating access tracking: {str(update_error)}")

        # Initialize accessTo outside the conditional block
        accessTo = item.get("accessTo", {})

        # Check if the assistant is public or if the user has access
        if not item.get("public", False):
            # check if user is listed in the entry or are part of the amplify groups
            if (
                current_user != item.get("createdBy")
                and current_user not in accessTo.get("users", [])
            ) and not verify_user_in_amp_group(
                token, accessTo.get("amplifyGroups", [])
            ):
                return {
                    "statusCode": 403,
                    "body": json.dumps(
                        {
                            "success": False,
                            "message": "User is not authorized to access this assistant",
                            "data": None,
                        },
                        cls=CustomPydanticJSONEncoder,
                    ),
                }

        # Get the assistant definition to include the astPath
        assistant_id = item.get("assistantId")
        assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
        assistant_definition = get_most_recent_assistant_version(
            assistants_table, assistant_id
        )

        group_id = assistant_definition.get("data", {}).get("groupId", None)
        if group_id and group_id != current_user:
            # check group membership for group ast admin group
            print("Checking if user is a member of the ast group: ", group_id)
            is_member = verify_member_of_ast_admin_group(token, group_id)
            if not is_member:
                return {
                    "statusCode": 403,
                    "body": json.dumps(
                        {
                            "success": False,
                            "message": "User is not authorized to access the group associated with this assistant",
                            "data": None,
                        },
                        cls=CustomPydanticJSONEncoder,
                    ),
                }
            print("User is a member of the ast group: ", group_id)

        # Create response with path information from both lookup table and assistant definition
        response_data = {
            "assistantId": assistant_id,
            "astPath": ast_path,
            "public": item.get("public", False),
            "accessTo": accessTo,
        }

        # Add path from assistant definition if available
        if (
            assistant_definition
            and assistant_definition.get("data")
            and assistant_definition["data"].get("astPath")
        ):
            response_data["pathFromDefinition"] = assistant_definition["data"][
                "astPath"
            ]

        # Add assistant name to the response if available
        if assistant_definition:
            # First check if name is at the top level of the definition
            if "name" in assistant_definition:
                response_data["name"] = assistant_definition["name"]
            # As a fallback, check if name exists in data
            elif (
                assistant_definition.get("data")
                and "name" in assistant_definition["data"]
            ):
                response_data["name"] = assistant_definition["data"]["name"]

            # Also include the full definition for the frontend to use
            response_data["definition"] = assistant_definition

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "success": True,
                    "message": "Assistant found",
                    "data": response_data,
                },
                cls=CustomPydanticJSONEncoder,
            ),
        }
    except Exception as e:
        print(f"Error looking up assistant: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "success": False,
                    "message": f"Error looking up assistant: {str(e)}",
                    "data": None,
                },
                cls=CustomPydanticJSONEncoder,
            ),
        }



@api_tool(
    path="/assistant/path/add",
    name="addAssistantPath",
    method="POST",
    tags=["standaloneAst"],
    description="""Add or update a path for an Amplify assistant.""",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The API path to add. Example: '/api/v1/example'",
            },
            "assistantId": {
                "type": "string",
                "description": "ID of the assistant to add the path to. Example: 'astp/3io4u5ipy34jkelkdfweiorwur'",
            },
            "method": {
                "type": "string",
                "description": "HTTP method for the path. Example: 'GET', 'POST', 'PUT', 'DELETE'",
            },
            "description": {
                "type": "string",
                "description": "Description of the endpoint functionality",
            },
        },
        "required": ["path", "assistantId", "method"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the path was added successfully",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "properties": {
                    "pathId": {
                        "type": "string",
                        "description": "Unique identifier for the added path",
                    },
                    "path": {
                        "type": "string",
                        "description": "The API path that was added",
                    },
                    "method": {
                        "type": "string",
                        "description": "HTTP method for the path",
                    },
                    "assistantId": {
                        "type": "string",
                        "description": "ID of the assistant the path was added to",
                    },
                },
                "required": ["pathId", "path", "method", "assistantId"],
            },
        },
        "required": ["success", "message", "data"],
    },
)
@validated(op="add_assistant_path")
def add_assistant_path(event, context, current_user, name, data):
    is_group_user = is_group_sys_user(data)
    access_token = data["access_token"]
    print(f"Adding path to assistant with data: {data}")

    # Extract the assistant ID and path from the data
    data = data["data"]
    ast_path = data["astPath"]
    assistant_id = data["assistantId"]
    is_public = data["isPublic"]
    access_to = data.get("accessTo", {})
    amplify_groups = access_to.get("amplifyGroups", [])
    users, _ = are_valid_amplify_users(access_token, access_to.get("users", []))

    print(f"Adding path '{ast_path}' to assistant '{assistant_id}'")

    # Get DynamoDB resources
    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
    lookup_table = dynamodb.Table(os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE"))

    try:
        # First, find the current version of the assistant
        existing_assistant = get_most_recent_assistant_version(
            assistants_table, assistant_id
        )

        if not existing_assistant:
            return {
                "success": False,
                "message": f"Assistant not found: {assistant_id}",
            }

        # Check if the user has permission to update this assistant
        if not check_user_can_update_assistant(existing_assistant, current_user):
            return {
                "success": False,
                "message": "You do not have permission to update this assistant.",
            }

        # Check if the new path already exists but belongs to a different assistant
        path_history = []
        prevAstPath = None  # used to path history
        try:
            existing_path_response = lookup_table.get_item(Key={"astPath": ast_path})
            if "Item" in existing_path_response:
                existing_item = existing_path_response["Item"]
                existing_assistant_id = existing_item.get("assistantId")
                if existing_assistant_id:
                    if existing_assistant_id != assistant_id:
                        return {
                            "success": False,
                            "message": f"Path '{ast_path}' is already in use by another assistant.",
                        }
                    prevAstPath = existing_item.get("astPath")
                path_history = existing_item.get("pathHistory", [])

        except Exception as e:
            print(f"Error checking for existing path: {str(e)}")

        if not prevAstPath:  # prevent losing path history when path is updated
            try:
                # Query for the current path entry for this assistant
                response = lookup_table.query(
                    IndexName="AssistantIdIndex",
                    KeyConditionExpression=Key("assistantId").eq(assistant_id),
                    Limit=1,  # We just need the most recent one
                )

                # If we found an existing path entry for this assistant, get its path history
                if response.get("Items") and len(response["Items"]) > 0:
                    current_path_item = response["Items"][0]

                    path_history = current_path_item.get("pathHistory", [])
                    prevAstPath = current_path_item.get("astPath")
                    print("previous AstPath", prevAstPath)
            except Exception as e:
                print(f"Error retrieving current path history: {str(e)}")
                # Continue with empty path history if we can't find the existing one

        created_at = datetime.now().isoformat()

        if (
            path_history
            and len(path_history) > 1
            and path_history[-1].get("path") == ast_path
            and path_history[-1].get("changedBy") == current_user
            and path_history[-1].get("assistant_id") == assistant_id
        ):
            path_history[-1]["changedAt"] = created_at
        else:
            # Otherwise append a new entry to the history
            path_history.append(
                {
                    "path": ast_path,
                    "assistant_id": assistant_id,
                    "changedAt": created_at,
                    "changedBy": current_user,
                }
            )

        # Add an entry to the lookup table
        lookup_item = {
            "astPath": ast_path,
            "assistantId": assistant_id,
            "public": is_public,  # Default to public for now
            "createdBy": current_user,
            "createdAt": created_at,
            "lastAccessed": created_at,
            "pathHistory": path_history,
            "accessTo": {
                "amplifyGroups": amplify_groups,
                "users": users,
            },
        }

        # Add the entry to the lookup table
        lookup_table.put_item(Item=lookup_item)
        print(f"Added lookup entry for path '{ast_path}' to assistant '{assistant_id}'")

        # Now create a new version of the assistant with the path saved in its definition
        assistant_version = existing_assistant.get("version", 1)

        # Clone the existing assistant data or initialize if not present
        assistant_data = existing_assistant.get("data", {})
        if assistant_data is None:
            assistant_data = {}
        # Update the path in the assistant data
        assistant_data["astPath"] = ast_path

        # Increment the version number
        new_version = assistant_version + 1

        data_sources = existing_assistant.get("dataSources", [])

        # Save the updated assistant
        new_item = save_assistant(
            assistants_table,
            existing_assistant["name"],
            existing_assistant["description"],
            existing_assistant["instructions"],
            assistant_data,
            existing_assistant.get("disclaimer", ""),
            data_sources,
            existing_assistant.get("provider", "amplify"),
            existing_assistant.get("tools", []),
            current_user,
            new_version,
            existing_assistant.get("tags", []),
            existing_assistant.get("uri", None),
            assistant_id,
            False,
        )

        # Update permissions for the new version to ensure the user retains edit rights
        try:
            # Determine the principal type (user for non-system users)
            principal_type = "group" if is_group_user else "user"

            # Add direct permissions entry in DynamoDB for the new version ID
            object_access_table = dynamodb.Table(
                os.environ.get("OBJECT_ACCESS_DYNAMODB_TABLE")
            )
            object_access_table.put_item(
                Item={
                    "object_id": new_item["id"],  # The ID of the new assistant version
                    "principal_id": current_user,
                    "permission_level": "owner",  # Give the user full ownership rights
                    "principal_type": principal_type,  # For individual users or groups
                    "object_type": "assistant",  # The type of object being accessed
                }
            )
            print(f"Successfully added direct permissions for {principal_type} {current_user} on assistant version {new_item['id']}")

            if principal_type != "group":
                filtered_drive_ds = []
                try:
                    from service.drive_datasources import extract_drive_datasources
                    drive_data_sources = extract_drive_datasources(assistant_data.get("integrationDriveData", {}))
                    print(f"Drive data sources before translation: {drive_data_sources}")
                    filtered_drive_ds = translate_user_data_sources_to_hash_data_sources(drive_data_sources)
                    print(f"Drive Data sources after translation and extraction: {filtered_drive_ds}")
                except Exception as e:
                    print(f"Error translating drive data sources to hash data sources: {str(e)}")

                for ds in data_sources + filtered_drive_ds:
                    ds_key = ds["id"]
                    try:
                        object_access_table.put_item(
                        Item={
                            "object_id": ds_key,  # The ID of the new assistant version
                            "principal_id": assistant_id,
                            "permission_level": "read",  # Give the user full ownership rights
                            "principal_type": principal_type,  # For individual users or groups
                            "object_type": "datasource",  # The type of object being accessed
                        })
                        print(f"Successfully added data source direct permissions for {assistant_id} on data source {ds_key}")
                    except Exception as e:
                        print(f"Error adding data source direct permissions for {assistant_id} on data source {ds_key}: {str(e)}")
        

        except Exception as e:
            print(f"Error adding permissions for assistant version: {str(e)}")

        # Update the latest alias to point to the new version
        update_assistant_latest_alias(assistant_id, new_item["id"], new_version)

        # Now that we've successfully saved the new path, remove ALL previous paths for this assistant except the new one
        try:
            # Query for all paths belonging to this assistant
            response = lookup_table.query(
                IndexName="AssistantIdIndex",
                KeyConditionExpression=Key("assistantId").eq(assistant_id),
            )

            paths_to_release = []
            for item in response.get("Items", []):
                # Skip the new path we just added
                if item["astPath"] != ast_path:
                    paths_to_release.append(item["astPath"])

            # Remove all old paths
            for path_to_release in paths_to_release:
                release_assistant_path(path_to_release, assistant_id, current_user)

            print(
                f"Removed {len(paths_to_release)} previous path(s) associated with assistant {assistant_id}"
            )
        except Exception as e:
            print(f"Error removing previous paths: {str(e)}")
            # Continue anyway - we've already saved the new path successfully

        return {
            "success": True,
            "message": "Assistant path updated successfully",
            "data": {
                "assistantId": assistant_id,
                "astPath": ast_path,
                "version": new_version,
            },
        }

    except Exception as e:
        print(f"Error adding assistant path: {str(e)}")
        import traceback

        traceback.print_exc()
        return {
            "success": False,
            "message": f"Failed to add path to assistant: {str(e)}",
        }



def release_assistant_path(ast_path, assistant_id, current_user):
    print(
        f"Attempting to release path '{ast_path}' for {assistant_id} from lookup table"
    )
    if not ast_path or not assistant_id:
        print("No ast_path or assistant_id provided... no action taken")
        return
    dynamodb = boto3.resource("dynamodb")
    lookup_table = dynamodb.Table(os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE"))

    try:
        print(f"Releasing '{ast_path}' from lookup table")
        existing_path_response = lookup_table.get_item(Key={"astPath": ast_path})
        if "Item" in existing_path_response:
            existing_item = existing_path_response["Item"]

            # verify data matches our records before releasing
            if existing_item.get("assistantId") != assistant_id:
                print(
                    f"Path '{ast_path}' is not associated with assistant {assistant_id}... no action taken"
                )
                return

            path_history = existing_item.get("pathHistory", [])
            path_history.append(
                {
                    "path": ast_path,
                    "assistant_id": None,
                    "changedAt": datetime.now().isoformat(),
                    "changedBy": current_user,
                }
            )

            # Update the lookup table to REMOVE the assistantId attribute entirely
            # This is better than setting to empty string which causes index issues
            lookup_table.update_item(
                Key={"astPath": ast_path},
                UpdateExpression="REMOVE assistantId SET pathHistory = :history",
                ExpressionAttributeValues={":history": path_history},
            )

            print(f"Path successfully released from lookup table")
        else:
            print(f"Path '{ast_path}' not found in lookup table... no action taken")
    except Exception as e:
        print(f"Error removing path '{ast_path}' from lookup table: {str(e)}")

