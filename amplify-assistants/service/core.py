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
import requests
import xmltodict
from bs4 import BeautifulSoup
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from common.validate import validated
from common.ops import op
from common.encoders import CombinedEncoder
from urllib.parse import urlparse

# Initialize AWS services
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

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
from common.ops import op

from decimal import Decimal

from common.amplify_groups import (
    verify_member_of_ast_admin_group,
    verify_user_in_amp_group,
)


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



@op(
    path="/assistant/delete",
    name="deleteAssistant",
    method="POST",
    tags=["apiDocumentation"],
    description="""Delete a specified Amplify assistant.

    Example request:
    {
        "data": {
            "assistantId": "astp/3209457834985793094"
        }
    }
    """,
    params={
        "assistantId": "String. Required. Unique identifier of the assistant to delete. Example: 'astp/3209457834985793094'."
    }
)
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

            # First, delete any paths associated with this assistant
        lookup_table = dynamodb.Table(os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE"))
        # Query for all paths belonging to this assistant
        response = lookup_table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=Key("assistantId").eq(assistant_public_id)
        )
        
        # Delete each path entry
        for item in response.get("Items", []):
            release_assistant_path(item["astPath"], assistant_public_id, current_user)
        
        # Now delete the assistant itself
        delete_assistant_by_public_id(assistants_table, assistant_public_id)
        # remove permissions
        delete_assistant_permissions_by_public_id(
            assistant_public_id, [current_user] + users_who_have_perms
        )
        delete_assistant_permissions_by_id(existing_assistant["id"], current_user)
        print(f"Assistant {assistant_public_id} and all associated paths deleted successfully.")
        return {"success": True, "message": "Assistant deleted successfully."}
    except Exception as e:
        print(f"Error deleting assistant: {e}")
        return {"success": False, "message": "Failed to delete assistant."}



@op(
    path="/assistant/list",
    name="listAssistants",
    method="GET",
    tags=["apiDocumentation"],
    description="""Retrieve a list of all Amplify assistants created or accessible by the user.

    Example response:
    {
        "success": true,
        "message": "Assistants retrieved successfully",
        "data": [
            {
                "assistantId": "astp/498370528-38594",
                "version": 3,
                "instructions": "<instructions>",
                "disclaimerHash": "348529340098580234959824580-pueiorupo4",
                "coreHash": "eiouqent84832n8989pdeer",
                "user": "yourEmail@vanderbilt.edu",
                "uri": null,
                "createdAt": "2024-07-15T19:07:57",
                "dataSources": [
                    {
                        "metadata": "<metadata>",
                        "data": "",
                        "name": "api_documentation.yml",
                        "raw": "",
                        "id": "global/7834905723785897982345088927.content.json",
                        "type": "application/x-yaml"
                    }
                ]
            }
        ]
    }
    """,
    params={}
)

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

    assistants = []
    last_evaluated_key = None

    while True:
        # Build the query parameters
        query_params = {
            'IndexName': "UserNameIndex",
            'KeyConditionExpression': Key("user").eq(user_id),
        }

        # If there is a last evaluated key, include it in the query
        if last_evaluated_key:
            query_params['ExclusiveStartKey'] = last_evaluated_key
        response = assistants_table.query(**query_params)

        assistants.extend(response.get("Items", []))

        # Check if there's more data to retrieve
        last_evaluated_key = response.get('LastEvaluatedKey')

        if not last_evaluated_key:
            print("No more data to retrieve")
            # No more data to retrieve
            break

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


@op(
    path="/assistant/create",
    name="createOrUpdateAssistant",
    method="POST",
    tags=["apiDocumentation"],
    description="""Create or update a customizable Amplify assistant.

    Example request:
    {
        "data": {
            "name": "Sample Assistant 3",
            "description": "This is a sample assistant for demonstration purposes.",
            "assistantId": "",
            "tags": ["test"],
            "instructions": "Respond to user queries about general knowledge topics.",
            "disclaimer": "This assistant's responses are for informational purposes only.",
            "dataSources": [{"id": "e48759073324384kjsf", "name": "api_paths_summary.csv", "type": "text/csv", "raw": "", "data": "", "key": "yourEmail@vanderbilt.edu/date/w3ou009we3.json", "metadata": {"name": "api_paths_summary.csv", "totalItems": 20, "locationProperties": ["row_number"], "contentKey": "yourEmail@vanderbilt.edu/date/w3ou009we3.json.content.json", "createdAt": "2024-07-15T18:58:24.912235", "totalTokens": 3750, "tags": [], "props": {}}}],
        }
    }

    Example response:
    {
        "success": true,
        "message": "Assistant created successfully.",
        "data": {
            "assistantId": "astp/3io4u5ipy34jkelkdfweiorwur",
            "id": "ast/03uio3904583049859482",
            "version": 1
        }
    }
    """,
    params={
        "name": "String. Required. Name of the assistant. Example: 'Sample Assistant 3'.",
        "description": "String. Required. Description of the assistant's purpose.",
        "assistantId": "String. Optional. If provided, updates an existing assistant. Example: 'astp/3io4u5ipy34jkelkdfweiorwur'. prefixed with astp",
        "tags": "Array of strings. Required. Tags to categorize the assistant.",
        "instructions": "String. Required. Detailed instructions on how the assistant should respond.",
        "disclaimer": "String. Optional. Disclaimer for the assistant's responses.",
        "dataSources": "Array of objects. Required. List of data sources the assistant can use. You can obtain ful data source objects by calling the /files/query endpoint",
    }
)

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

    # Identify and store website URLs
    website_data_sources = []
    standard_data_sources = []

    for source in data_sources:
        if (
            source.get("type") == "website/url"
            or source.get("type") == "website/sitemap"
        ):
            website_data_sources.append(source)
        else:
            standard_data_sources.append(source)

    # Process website URLs for future scraping
    scraped_data_sources = []
    if website_data_sources:
        for website_source in website_data_sources:
            # Extract URL and metadata
            url = website_source.get("id", "")
            is_sitemap = website_source.get("type") == "website/sitemap"
            scan_frequency = website_source.get("metadata", {}).get("scanFrequency", 7)

            # Store in assistant_data for future use
            if "websiteUrls" not in assistant_data:
                assistant_data["websiteUrls"] = []

            assistant_data["websiteUrls"].append(
                {
                    "url": url,
                    "isSitemap": is_sitemap,
                    "scanFrequency": scan_frequency,
                    "lastScanned": None,
                }
            )

            try:
                # Attempt immediate scraping
                scraped_data = scrape_website_content(url, is_sitemap, current_user)
                if scraped_data.get("success") and scraped_data.get("data", {}).get(
                    "dataSourceKey"
                ):
                    scraped_key = scraped_data["data"]["dataSourceKey"]

                    # Add scraped content as a data source
                    scraped_data_source = {
                        "id": scraped_key,  # This is now the content_key
                        "name": f"Scraped content from {url}",
                        "type": "application/json",
                        "metadata": {
                            "sourceUrl": url,
                            "isSitemap": is_sitemap,
                            "scrapedAt": datetime.now().isoformat(),
                            "scanFrequency": scan_frequency,
                            "isScrapedContent": True,
                        },
                    }
                    scraped_data_sources.append(scraped_data_source)

                    # Set permissions for the scraped content immediately after creating it
                    try:
                        update_object_permissions(
                            access_token=data["access_token"],
                            shared_with_users=[current_user],
                            keys=[scraped_key],
                            object_type="datasource",
                            principal_type="user",
                            permission_level="owner",
                            policy="",
                        )
                        print(
                            f"Set owner permissions for scraped content: {scraped_key}"
                        )
                    except Exception as perm_error:
                        print(
                            f"Error setting permissions for scraped content: {perm_error}"
                        )
            except Exception as e:
                print(f"Error initially scraping website {url}: {str(e)}")

    # Permissions handling for non-group users
    if not is_group_sys_user:
        # Process standard data sources (excluding website URLs which don't need permission checks)
        filtered_ds = []
        tag_data_sources = []

        for source in standard_data_sources:
            if source["id"].startswith("tag://"):
                tag_data_sources.append(source)
            else:
                filtered_ds.append(source)

        print(f"Tag Data sources: {tag_data_sources}")
        print(f"Website Data sources: {website_data_sources}")

        if len(filtered_ds) > 0:
            print(f"Data sources before translation: {filtered_ds}")

            for i in range(len(filtered_ds)):
                source = filtered_ds[i]
                if "://" not in source["id"]:
                    filtered_ds[i]["id"] = source.get("key", source["id"])

            print(f"Final data sources before translation: {filtered_ds}")

            filtered_ds = translate_user_data_sources_to_hash_data_sources(filtered_ds)

            print(f"Data sources after translation and extraction: {filtered_ds}")

            # Only check permissions on standard data sources
            if filtered_ds and not can_access_objects(
                data["access_token"], filtered_ds
            ):
                return {
                    "success": False,
                    "message": "You are not authorized to access the referenced files",
                }

        # Combine all types of data sources for the final assistant
        final_data_sources = (
            filtered_ds + tag_data_sources + scraped_data_sources
        )
    else:
        # For group system users, use all data sources as-is
        final_data_sources = (
            standard_data_sources + scraped_data_sources
        )

    # Create or update the assistant with the final data sources
    return create_or_update_assistant(
        current_user=current_user,
        access_token=data["access_token"],
        user_that_owns_the_assistant=current_user,
        assistant_name=assistant_name,
        description=description,
        instructions=instructions,
        assistant_data=assistant_data,
        disclaimer=disclaimer,
        tags=tags,
        data_sources=final_data_sources,
        tools=tools,
        provider=provider,
        uri=uri,
        assistant_public_id=assistant_public_id,
        is_group_sys_user=is_group_sys_user,
    )


@op(
    path="/assistant/share",
    name="shareAssistant",
    method="POST",
    tags=["apiDocumentation"],
    description="""Share an Amplify assistant with other users on the platform.

    Example request:
    {
        "data": {
            "assistantId": "ast/8934572093982034020-9",
            "recipientUsers": ["yourEmail@vanderbilt.edu"],
            "note": "Sharing label"
        }
    }
    """,
    params={
        "assistantId": "String. Required. Unique identifier of the assistant to share. Example: 'ast/8934572093982034020-9'. prefixed with ast",
        "recipientUsers": "Array of strings. Required. List of email addresses of the users to share the assistant with. Example: ['user1@example.com', 'user2@example.com'].",
        "note": "String. Optional. A note to include with the shared assistant. Example: 'Sharing this assistant for project collaboration.'"
    }
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
    # print("DS: ", data_sources)

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
    current_user,
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
    object_access_table = dynamodb.Table(os.environ.get("OBJECT_ACCESS_DYNAMODB_TABLE"))

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

        if (existing_assistant.get("data", {}).get("astPath") and \
            not assistant_data.get("astPath")):
            if assistant_data.get("astPathData"): 
                del assistant_data["astPathData"]
            release_assistant_path(existing_assistant["data"]["astPath"], assistant_public_id, current_user)

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

        # Collect all data source keys, including scraped content
        all_data_source_keys = []
        scraped_data_source_keys = []

        # Process all data sources to collect their keys
        for source in data_sources:
            if "id" in source:
                all_data_source_keys.append(source["id"])
                if source.get("metadata", {}).get(
                    "isScrapedContent", False
                ) or source.get("metadata", {}).get("sourceUrl"):
                    scraped_data_source_keys.append(source["id"])

        # Set permissions for the assistant
        if not update_object_permissions(
            access_token,
            [user_that_owns_the_assistant],
            [new_item["id"], new_item["assistantId"]],
            "assistant",
            principal_type,
            "owner",
        ):
            print(f"Error updating permissions for assistant {new_item['id']}")
        else:
            print(f"Successfully updated permissions for assistant {new_item['id']}")

        # Set permissions for all data sources, including scraped content
        if all_data_source_keys:
            update_result = update_object_permissions(
                access_token,
                [user_that_owns_the_assistant],
                all_data_source_keys,
                "datasource",
                principal_type,
                "owner",
            )
            if not update_result:
                print(
                    f"Error updating permissions for data sources: {all_data_source_keys}"
                )
            else:
                print(
                    f"Successfully updated permissions for data sources: {all_data_source_keys}"
                )

        # Update permissions for the new version to ensure the user retains edit rights
        try:
            # Add direct permissions entry in DynamoDB for the new version ID
            object_access_table.put_item(
                Item={
                    "object_id": new_item["id"],  # The ID of the new assistant version
                    "principal_id": user_that_owns_the_assistant,
                    "permission_level": "owner",  # Give the user full ownership rights
                    "principal_type": principal_type,  # For individual users or groups
                    "object_type": "assistant"    # The type of object being accessed
                }
            )
            print(f"Successfully added direct permissions for {principal_type} {user_that_owns_the_assistant} on assistant version {new_item['id']}")
        except Exception as e:
            print(f"Error adding permissions for assistant version: {str(e)}")
        
        # Update the latest alias to point to the new version
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

        # Set permissions for all data sources, including scraped content
        all_data_source_keys = []
        scraped_sources = []

        # Collect all data source keys
        for data_source in data_sources:
            if "id" in data_source:
                all_data_source_keys.append(data_source["id"])
                if data_source.get("metadata", {}).get("sourceUrls"):
                    scraped_sources.append(data_source)

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

        # Set permissions for all data sources
        if all_data_source_keys:
            update_object_permissions(
                access_token,
                [user_that_owns_the_assistant],
                all_data_source_keys,
                "datasource",
                principal_type,
                "owner",
            )

        # Special handling for scraped content
        for scraped_source in scraped_sources:
            scraped_key = scraped_source["id"]
            try:
                # Ensure owner permissions for scraped content
                update_object_permissions(
                    access_token=access_token,
                    shared_with_users=[user_that_owns_the_assistant],
                    keys=[scraped_key],
                    object_type="datasource",
                    principal_type=principal_type,
                    permission_level="owner",
                    policy="",
                )
                print(f"Set owner permissions for scraped content: {scraped_key}")
            except Exception as perm_error:
                print(f"Error setting permissions for scraped content: {perm_error}")

        # Also add direct permissions in DynamoDB
        try:
            # Add direct permissions entry in DynamoDB for both IDs
            object_access_table.put_item(
                Item={
                    "object_id": new_item["id"],  # The ID of the new assistant version
                    "principal_id": user_that_owns_the_assistant,
                    "permission_level": "owner",  # Give the user full ownership rights
                    "principal_type": principal_type,  # For individual users or groups
                    "object_type": "assistant"    # The type of object being accessed
                }
            )
            
            object_access_table.put_item(
                Item={
                    "object_id": new_item["assistantId"],  # The public ID of the assistant
                    "principal_id": user_that_owns_the_assistant,
                    "permission_level": "owner",  # Give the user full ownership rights
                    "principal_type": principal_type,  # For individual users or groups
                    "object_type": "assistant"    # The type of object being accessed
                }
            )
            print(f"Successfully added direct permissions for {principal_type} {user_that_owns_the_assistant} on assistant {new_item['id']} and {new_item['assistantId']}")
        except Exception as e:
            print(f"Error adding direct permissions for assistant: {str(e)}")

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
        json.dumps(core_details, sort_keys=True, cls=CombinedEncoder).encode()
    ).hexdigest()
    datasources_sha256 = hashlib.sha256(
        json.dumps(data_sources.sort(key=lambda x: x["id"]), cls=CombinedEncoder).encode()
    ).hexdigest()
    instructions_sha256 = hashlib.sha256(
        json.dumps(instructions, sort_keys=True, cls=CombinedEncoder).encode()
    ).hexdigest()
    disclaimer_sha256 = hashlib.sha256(
        json.dumps(disclaimer, sort_keys=True, cls=CombinedEncoder).encode()
    ).hexdigest()
    core_details["assistant"] = assistant_name
    core_details["description"] = description
    full_sha256 = hashlib.sha256(
        json.dumps(core_details, sort_keys=True, cls=CombinedEncoder).encode()
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


@op(
    path="/assistant/add_path",
    name="addAssistantPath",
    method="POST",
    tags=["standaloneAst"],
    description="""Add or update a path for an Amplify assistant.

    Example request:
    {
        "data": {
            "assistantId": "astp/3209457834985793094",
            "astPath": "my/assistant/path",
        }
    }
    """,
    params={
        "assistantId": "String. Required. Unique identifier of the assistant. Example: 'astp/3209457834985793094'.",
        "astPath": "String. Required. Path to assign to the assistant. Example: 'my/assistant/path'.",
    }
)
@validated(op="add_assistant_path")
def add_assistant_path(event, context, current_user, name, data):
    is_group_sys_user = data["is_group_sys_user"]
    print(f"Adding path to assistant with data: {data}")
    
    # Extract the assistant ID and path from the data
    data = data['data']
    ast_path = data["astPath"]
    assistant_id = data["assistantId"]
    is_public = data['isPublic']
    access_to = data.get('accessTo',{})
    amplify_groups = access_to.get('amplifyGroups', [])
    users = access_to.get('users', [])
    
    print(f"Adding path '{ast_path}' to assistant '{assistant_id}'")
    
    # Get DynamoDB resources
    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
    lookup_table = dynamodb.Table(os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE"))
    
    try:
        # First, find the current version of the assistant
        existing_assistant = get_most_recent_assistant_version(assistants_table, assistant_id)
        
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
        prevAstPath = None #used to path history
        try:
            existing_path_response = lookup_table.get_item(Key={"astPath": ast_path})
            if "Item" in existing_path_response:
                existing_item = existing_path_response["Item"]
                existing_assistant_id = existing_item.get("assistantId")
                if existing_assistant_id:
                    if (existing_assistant_id != assistant_id):
                        return {
                            "success": False,
                            "message": f"Path '{ast_path}' is already in use by another assistant.",
                        }
                    prevAstPath = existing_item.get("astPath") 
                path_history = existing_item.get("pathHistory", [])

        except Exception as e:
            print(f"Error checking for existing path: {str(e)}")
     
        if (not prevAstPath): #prevent losing path history when path is updated
            try:
            # Query for the current path entry for this assistant
                response = lookup_table.query(
                    IndexName="AssistantIdIndex",
                    KeyConditionExpression=Key("assistantId").eq(assistant_id),
                    Limit=1  # We just need the most recent one
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
        
        if path_history and len(path_history) > 1 and \
           path_history[-1]["path"] == ast_path and \
           path_history[-1]["changedBy"] == current_user and \
           path_history[-1]["assistant_id"] == assistant_id:
            path_history[-1]["changedAt"] = created_at
        else:
            # Otherwise append a new entry to the history
            path_history.append({"path": ast_path, "assistant_id": assistant_id, "changedAt": created_at, "changedBy": current_user})

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
            }
            
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

        # Save the updated assistant
        new_item = save_assistant(
            assistants_table,
            existing_assistant["name"],
            existing_assistant["description"],
            existing_assistant["instructions"],
            assistant_data,
            existing_assistant.get("disclaimer", ""),
            existing_assistant.get("dataSources", []),
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
            principal_type = "group" if is_group_sys_user else "user"
            
            # Add direct permissions entry in DynamoDB for the new version ID
            object_access_table = dynamodb.Table(os.environ.get("OBJECT_ACCESS_DYNAMODB_TABLE"))
            object_access_table.put_item(
                Item={
                    "object_id": new_item["id"],  # The ID of the new assistant version
                    "principal_id": current_user,
                    "permission_level": "owner",  # Give the user full ownership rights
                    "principal_type": principal_type,  # For individual users or groups
                    "object_type": "assistant"    # The type of object being accessed
                }
            )
            print(f"Successfully added direct permissions for {principal_type} {current_user} on assistant version {new_item['id']}")
        except Exception as e:
            print(f"Error adding permissions for assistant version: {str(e)}")
        
        # Update the latest alias to point to the new version
        update_assistant_latest_alias(assistant_id, new_item["id"], new_version)
        
        # Now that we've successfully saved the new path, remove ALL previous paths for this assistant except the new one
        try:
            # Query for all paths belonging to this assistant
            response = lookup_table.query(
                IndexName="AssistantIdIndex",
                KeyConditionExpression=Key("assistantId").eq(assistant_id)
            )
            
            paths_to_release = []
            for item in response.get("Items", []):
                # Skip the new path we just added
                if item["astPath"] != ast_path:
                    paths_to_release.append(item["astPath"])
            
            # Remove all old paths
            for path_to_release in paths_to_release:
                release_assistant_path(path_to_release, assistant_id, current_user)
            
            print(f"Removed {len(paths_to_release)} previous path(s) associated with assistant {assistant_id}")
        except Exception as e:
            print(f"Error removing previous paths: {str(e)}")
            # Continue anyway - we've already saved the new path successfully
        
        return {
            "success": True,
            "message": "Assistant path updated successfully",
            "data": {
                "assistantId": assistant_id,
                "astPath": ast_path,
                "version": new_version
            }
        }
    
    except Exception as e:
        print(f"Error adding assistant path: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Failed to add path to assistant: {str(e)}"
        }


@op(
    path="/assistant/lookup",
    name="lookupAssistant",
    method="POST",
    tags=["standaloneAst"],
    description="""Look up an Amplify assistant by path.

    Example request:
    {
        "data": {
            "astPath": "my/assistant/path"
        }
    }

    Example response:
    {
        "success": true,
        "message": "Assistant found",
        "data": {
            "assistantId": "astp/34098509834509809348",
            "astPath": "my/assistant/path",
            "public": true,
            "pathFromDefinition": "my/assistant/path"
        }
    }
    """,
    params={
        "astPath": "String. Required. Path to look up the assistant. Example: 'my/assistant/path'."
    }
)
@validated(op="lookup")
def lookup_assistant_path(event, context, current_user, name, data):
    token = data['access_token']
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
                    cls=CombinedEncoder,
                )
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
        print(f"DynamoDB response: {json.dumps(response, cls=CombinedEncoder)}")
        
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
                    cls=CombinedEncoder,
                )
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
                    cls=CombinedEncoder,
                )
            }
        
        # Update lastAccessed 
        current_time = datetime.now().isoformat()
        try:    
            # Update the item with access tracking information
            lookup_table.update_item(
                Key={"astPath": ast_path},
                UpdateExpression="SET lastAccessed = :time",
                ExpressionAttributeValues={
                    ":time": current_time
                }
            )
            print(f"Updated access tracking for path '{ast_path}': lastAccessed={current_time}, lastAccessedBy={current_user}")
        except Exception as update_error:
            # Log the error but continue - don't fail the lookup just because tracking failed
            print(f"Error updating access tracking: {str(update_error)}")
        
        # Initialize accessTo outside the conditional block
        accessTo = item.get("accessTo", {})
        
        # Check if the assistant is public or if the user has access
        if not item.get("public", False):
            # check if user is listed in the entry or are part of the amplify groups
            if (current_user != item.get("createdBy") and \
                current_user not in accessTo.get("users", [])) and \
                not verify_user_in_amp_group(token, accessTo.get("amplifyGroups", [])):
                    return {"statusCode": 403,
                            "body": json.dumps(
                                {
                                    "success": False,
                                    "message": "User is not authorized to access this assistant",
                                    "data": None,
                                },
                                cls=CombinedEncoder,
                            )}
        

        # Get the assistant definition to include the astPath
        assistant_id = item.get("assistantId")
        assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
        assistant_definition = get_most_recent_assistant_version(assistants_table, assistant_id)

        group_id = assistant_definition.get("data", {}).get("groupId", None)
        if (group_id and group_id != current_user):
            #check group membership for group ast admin group
            print("Checking if user is a member of the ast group: ", group_id)
            is_member = verify_member_of_ast_admin_group(token, group_id)
            if (not is_member):
                return {"statusCode": 403,
                            "body": json.dumps(
                                {
                                    "success": False,
                                    "message": "User is not authorized to access the group associated with this assistant",
                                    "data": None,
                                },
                                cls=CombinedEncoder,
                            )}
            print("User is a member of the ast group: ", group_id)


        
        # Create response with path information from both lookup table and assistant definition
        response_data = {
            "assistantId": assistant_id,
            "astPath": ast_path,
            "public": item.get("public", False),
            "accessTo": accessTo
        }
        
        # Add path from assistant definition if available
        if assistant_definition and assistant_definition.get("data") and assistant_definition["data"].get("astPath"):
            response_data["pathFromDefinition"] = assistant_definition["data"]["astPath"]
        
        # Add assistant name to the response if available
        if assistant_definition:
            # First check if name is at the top level of the definition
            if "name" in assistant_definition:
                response_data["name"] = assistant_definition["name"]
            # As a fallback, check if name exists in data
            elif assistant_definition.get("data") and "name" in assistant_definition["data"]:
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
                cls=CombinedEncoder,
            )
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
                cls=CombinedEncoder,
            )
        }

def release_assistant_path(ast_path, assistant_id, current_user):
    print(f"Attempting to release path '{ast_path}' for {assistant_id} from lookup table")
    if (not ast_path or not assistant_id):
        print("No ast_path or assistant_id provided... no action taken")
        return
    dynamodb = boto3.resource("dynamodb")
    lookup_table = dynamodb.Table(os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE"))
    
    try:
        print(f"Realeasing '{ast_path}' from lookup table")
        existing_path_response = lookup_table.get_item(Key={"astPath": ast_path})
        if "Item" in existing_path_response:
            existing_item = existing_path_response["Item"]
            
            # verify data matches our records before releasing
            if existing_item.get("assistantId") != assistant_id:
                print(f"Path '{ast_path}' is not associated with assistant {assistant_id}... no action taken")
                return
            
            path_history = existing_item.get("pathHistory", [])
            path_history.append({"path": ast_path, "assistant_id": None, 
                                 "changedAt": datetime.now().isoformat(), 
                                 "changedBy": current_user})
            
            # Update the lookup table to REMOVE the assistantId attribute entirely
            # This is better than setting to empty string which causes index issues
            lookup_table.update_item(
                Key={"astPath": ast_path},
                UpdateExpression="REMOVE assistantId SET pathHistory = :history",
                ExpressionAttributeValues={
                    ":history": path_history
                }
            )
                   
            print(f"Path successfully released from lookup table")
        else:
            print(f"Path '{ast_path}' not found in lookup table... no action taken")
    except Exception as e:
        print(f"Error removing path '{ast_path}' from lookup table: {str(e)}")


@validated(op="share_assistant")  
def request_assistant_to_public_ast(event, context, current_user, name, data):
    data = data['data']
    assistant_id = data['assistantId']

    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
    object_access_table = dynamodb.Table(os.environ.get("OBJECT_ACCESS_DYNAMODB_TABLE"))
    
    try:
        # First, find the current version of the assistant
        print("Looking up assistant: ", assistant_id)
        existing_assistant = get_most_recent_assistant_version(assistants_table, assistant_id)
        
        if not existing_assistant:
            return {
                "success": False,
                "message": f"Assistant not found: {assistant_id}",
            }
        
        if (not existing_assistant.get("data", {}).get("availableOnRequest")):
                print("Assistant is not available for public request: ", assistant_id)
                return {
                    "success": False,
                    "message": f"Assistant is not available for public request: {assistant_id}",
                }
    
        print("Updating assistant permissions for user: ", current_user)
        object_access_table.put_item(Item={
                    'object_id': assistant_id,
                    'principal_id': current_user,
                    'principal_type':  'user',
                    'object_type': 'assistant',
                    'permission_level': 'read',  
                    'policy': None
            })
    
            
        data_sources = get_data_source_keys(existing_assistant["dataSources"])
        print("Updating permissions for ast datasources")
        
        for ds in data_sources:
            object_access_table.put_item(Item={
                    'object_id': ds,
                    'principal_id': current_user,
                    'principal_type':  'user',
                    'object_type': 'datasource',
                    'permission_level': 'read',  
                    'policy': None
            })
            
        print(f"Creating alias for user {current_user} for assistant {assistant_id}")
        create_assistant_alias(
            current_user,
            assistant_id,
            existing_assistant["id"],
            existing_assistant["version"],
            "latest",
            )
        print(f"Successfully created alias for user {current_user}")

        return {
            "success": True,
            "message": f"Assistant id is now available for chat requests via api access: {assistant_id}",
        }
            
        
    except Exception as e:
        print(f"Error verifying assistant id: {str(e)}")
        return {
            "success": False,
            "message": f"Error verifying assistant id: {str(e)}",
        }


@validated(op="lookup")
def validate_assistant_id(event, context, current_user, name, data):
    data = data['data']
    assistant_id = data['assistantId']

    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
    
    try:
        # First, find the current version of the assistant
        existing_assistant = get_most_recent_assistant_version(assistants_table, assistant_id)
        
        if not existing_assistant:
            return {
                "success": False,
                "message": f"Assistant not found: {assistant_id}",
            }
        
        return {
            "success": True,
            "message": f"Assistant id is a valid assistant: {assistant_id}",
        }
    except Exception as e:
        print(f"Error verifying assistant id: {str(e)}")
        return {
            "success": False,
            "message": f"Error verifying assistant id: {str(e)}",
        }


def scrape_website_content(url, is_sitemap=False, user_id=None, max_pages=10):
    """Helper function to scrape a website and return the data source key"""
    try:
        print(f"Attempting to scrape {'sitemap' if is_sitemap else 'website'}: {url}")

        # Determine if single URL or sitemap
        urls_to_scrape = []
        if is_sitemap:
            urls_to_scrape = extract_urls_from_sitemap(url, max_pages)
            print(f"Extracted {len(urls_to_scrape)} URLs from sitemap")
            if not urls_to_scrape:
                return {
                    "success": False,
                    "message": f"Could not extract any URLs from sitemap at {url}",
                    "error": "Empty sitemap or parsing error",
                }
        else:
            urls_to_scrape = [url]
            print(f"Set up to scrape single URL: {url}")

        # Scrape content from URLs
        scraped_data = []
        for url in urls_to_scrape:
            print(f"Fetching and parsing URL: {url}")
            content = fetch_and_parse_url(url)
            if content:
                print(f"Successfully parsed content from {url}")
                scraped_data.append(
                    {
                        "url": url,
                        "content": content,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            else:
                print(f"Failed to parse content from {url}")

        # Check if any URLs were successfully scraped
        if not scraped_data:
            print("No content was successfully scraped from any URL")
            return {
                "success": False,
                "message": "Failed to scrape any content from the provided URLs",
                "error": "All URL requests failed or returned no content",
            }

        print(f"Successfully scraped {len(scraped_data)} URLs, saving content")

        # Create data source
        try:
            data_source_key = save_scraped_content(scraped_data, user_id)
            print(f"Saved scraped content with key: {data_source_key}")

            return {
                "success": True,
                "message": f"Successfully scraped {len(scraped_data)} URLs",
                "data": {
                    "dataSourceKey": data_source_key,  # This is now file_key
                    "urlsScraped": len(scraped_data),
                    "scrapedUrls": [item["url"] for item in scraped_data],
                },
            }
        except Exception as save_error:
            print(f"Error saving scraped content: {save_error}")
            return {
                "success": False,
                "message": f"Error saving scraped content: {str(save_error)}",
                "error": str(save_error),
            }

    except Exception as e:
        print(f"Error scraping website: {e}")
        return {
            "success": False,
            "message": f"Failed to scrape website: {str(e)}",
            "error": str(e),
        }


def extract_urls_from_sitemap(sitemap_url, max_pages=10):
    """Extract URLs from a sitemap XML file."""
    try:
        response = requests.get(sitemap_url, timeout=30)
        response.raise_for_status()

        sitemap_content = response.content
        sitemap_dict = xmltodict.parse(sitemap_content)

        # Handle nested sitemaps
        if "sitemapindex" in sitemap_dict:
            all_urls = []
            for sitemap in sitemap_dict["sitemapindex"]["sitemap"][:max_pages]:
                sitemap_loc = sitemap["loc"]
                sub_urls = extract_urls_from_sitemap(sitemap_loc, max_pages)
                all_urls.extend(sub_urls)
                if len(all_urls) >= max_pages:
                    return all_urls[:max_pages]
            return all_urls

        # Extract URLs from urlset
        urls = []
        if "urlset" in sitemap_dict and "url" in sitemap_dict["urlset"]:
            url_entries = sitemap_dict["urlset"]["url"]
            # Handle single URL case
            if isinstance(url_entries, dict):
                urls.append(url_entries["loc"])
            else:
                for url_entry in url_entries[:max_pages]:
                    urls.append(url_entry["loc"])

        return urls[:max_pages]

    except Exception as e:
        print(f"Error extracting URLs from sitemap: {e}")
        return []


def fetch_and_parse_url(url):
    """Fetch and parse content from a URL."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        # Parse URL to get the fragment
        parsed_url = urlparse(url)
        fragment = parsed_url.fragment
        base_url = url.replace(f"#{fragment}", "") if fragment else url

        response = requests.get(base_url, headers=headers, timeout=30)

        # Handle HTTP errors explicitly
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"Error fetching URL {url}: {e}")
            return None

        # Check if content is HTML
        content_type = response.headers.get("Content-Type", "")
        if (
            "text/html" not in content_type
            and "application/xhtml+xml" not in content_type
        ):
            print(f"URL {url} returned non-HTML content: {content_type}")

            # For non-HTML content like PDFs, handle differently
            if "application/pdf" in content_type:
                return {
                    "metadata": {
                        "title": url.split("/")[-1],
                        "url": url,
                        "contentType": content_type,
                        "scrapedAt": datetime.now().isoformat(),
                    },
                    "text": f"[PDF Content from {url}]",
                }

            # Generic handling for other types
            return {
                "metadata": {
                    "title": url.split("/")[-1],
                    "url": url,
                    "contentType": content_type,
                    "scrapedAt": datetime.now().isoformat(),
                },
                "text": f"[Content from {url} with type {content_type}]",
            }

        # Parse HTML
        soup = BeautifulSoup(response.content, "lxml")

        # Remove script, style, and other non-content elements
        for element in soup(["script", "style", "meta", "noscript", "iframe"]):
            element.decompose()

        # Extract title
        title = soup.title.string if soup.title else url.split("/")[-1]

        # Build a more structured extraction of content
        main_content = ""
        section_title = ""

        # If we have a fragment, try to find the specific section
        if fragment:
            # Try different ways to find the section
            section = (
                soup.find(id=fragment)
                or soup.find(attrs={"name": fragment})
                or soup.find(id=lambda x: x and fragment in x)
                or soup.find(class_=lambda x: x and fragment in x)
            )

            if section:
                # Get the section title if available
                heading = section.find(["h1", "h2", "h3", "h4", "h5", "h6"])
                if heading:
                    section_title = heading.get_text(strip=True)

                # Get the content of the section
                main_content = section.get_text(separator=" ", strip=True)
            else:
                print(f"Could not find section with fragment: {fragment}")

        # If no specific section was found or no fragment was provided, get the main content
        if not main_content:
            # Try to find main content containers
            main_elements = soup.find_all(["main", "article", "div", "section"])
            if main_elements:
                for element in main_elements:
                    if (
                        len(element.get_text(strip=True)) > 200
                    ):  # Only substantial blocks
                        main_content += (
                            element.get_text(separator=" ", strip=True) + "\n\n"
                        )

        # If no main content found, just get the body text
        if not main_content:
            main_content = soup.get_text(separator=" ", strip=True)

        # Clean up whitespace
        main_content = re.sub(r"\s+", " ", main_content).strip()

        # Add a headline with the title and section title if available
        formatted_text = (
            f"# {title} - {section_title}\n\n{main_content}"
            if section_title
            else f"# {title}\n\n{main_content}"
        )

        # Extract metadata
        metadata = {
            "title": title,
            "url": url,
            "contentType": "text/html",
            "scrapedAt": datetime.now().isoformat(),
        }

        # Return structured content
        return {
            "metadata": metadata,
            "text": formatted_text,
        }

    except Exception as e:
        print(f"Error processing URL {url}: {e}")
        return None


def save_scraped_content(scraped_data, user_id):
    """Save scraped content to S3 and create entry in DynamoDB."""
    # Generate unique ID
    timestamp = datetime.now().strftime("%Y-%m-%d")
    unique_id = str(uuid.uuid4())

    # Original file key for the raw data
    file_key = f"{user_id}/{timestamp}/{unique_id}.json"

    # Content key for the processed chunks data
    content_key = f"{user_id}/{timestamp}/{unique_id}.content.json"

    # Save to S3
    bucket_name = os.environ["ASSISTANTS_FILES_BUCKET_NAME"]

    # Create metadata with account information
    account_data = {
        "user": user_id,
        "account": "general_account",  # Default account
        "api_key": None,
    }

    # Get encryption key and encrypt metadata
    encrypted_metadata = ""
    try:
        ssm_client = boto3.client("ssm")
        parameter_name = os.environ.get("FILE_UPLOAD_ENCRYPTION_PARAMETER")
        if parameter_name:
            response = ssm_client.get_parameter(
                Name=parameter_name, WithDecryption=True
            )
            key = response["Parameter"]["Value"].encode()
            from cryptography.fernet import Fernet
            import base64

            fernet = Fernet(key)
            data_str = json.dumps(account_data)
            encrypted_data = fernet.encrypt(data_str.encode())
            encrypted_metadata = base64.b64encode(encrypted_data).decode("utf-8")
    except Exception as e:
        print(f"Warning: Error encrypting metadata: {e}")

    # Save raw data to S3 - FIXED: Use CombinedEncoder directly
    s3.put_object(
        Bucket=bucket_name,
        Key=file_key,
        Body=json.dumps(scraped_data, cls=CombinedEncoder),  # Use CombinedEncoder here
        ContentType="application/json",
        Metadata={"encrypted_metadata": encrypted_metadata},
    )
    print(f"Raw data saved to S3: {bucket_name}/{file_key}")

    # Format scraped content for the RAG system
    formatted_content = {"chunks": [], "src": file_key}

    for i, item in enumerate(scraped_data):
        content_text = ""
        if isinstance(item["content"], dict) and "text" in item["content"]:
            content_text = item["content"]["text"]
        elif isinstance(item["content"], str):
            content_text = item["content"]
        else:
            content_text = str(item["content"])

        formatted_content["chunks"].append(
            {
                "content": content_text,
                "locations": [{"url": item["url"], "scrapedAt": item["timestamp"]}],
                "indexes": [i],
                "char_index": 0,
            }
        )

    # Save the RAG-formatted content to S3 - FIXED: Use CombinedEncoder here too
    s3.put_object(
        Bucket=bucket_name,
        Key=content_key,
        Body=json.dumps(
            formatted_content, cls=CombinedEncoder
        ),  # Use CombinedEncoder here
        ContentType="application/json",
        Metadata={"encrypted_metadata": encrypted_metadata},
    )
    print(f"Formatted content saved to S3: {bucket_name}/{content_key}")

    # Create hash entry - CRITICAL for RAG pipeline
    hash_files_table = dynamodb.Table(os.environ["HASH_FILES_DYNAMO_TABLE"])
    hash_entry = {
        "id": unique_id,
        "user": user_id,
        "originalKey": file_key,
        "textLocationKey": content_key,
        "createdAt": int(time.time() * 1000),
        "content_type": "application/json",
        "filename": f"scraped_websites_{timestamp}.json",
        "metadata": {
            "scrapedUrls": [item["url"] for item in scraped_data],
            "scrapedAt": datetime.now().isoformat(),
            # "urlCount": int(len(scraped_data)),
        },
    }
    hash_files_table.put_item(Item=hash_entry)
    print(f"Hash entry created pointing {unique_id} → {content_key}")

    # Create entry in FILES_DYNAMO_TABLE - this is critical for the RAG system to find the file
    files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
    file_metadata = {
        "id": file_key,  # CRITICAL: Use file_key, not content_key for this entry
        "name": f"scraped_websites_{timestamp}.json",
        "type": "application/json",
        "tags": ["website", "scraped"],
        "data": {
            "sourceUrls": [item["url"] for item in scraped_data],
            "scrapedAt": datetime.now().isoformat(),
            # "urlCount": int(len(scraped_data)),
        },
        "knowledgeBase": "default",
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
        "createdBy": user_id,
        "updatedBy": user_id,
    }

    # Always create the FILES_DYNAMO_TABLE entry and fail if it doesn't work
    files_table.put_item(Item=file_metadata)
    print(f"Files table entry created for {file_key}")

    # Return the file_key that should be used as the data source ID
    return file_key  # IMPORTANT: Return file_key, not content_key


@op(
    path="/assistant/rescan_websites",
    name="rescanWebsites",
    method="POST",
    tags=["apiDocumentation"],
    description="""Rescan websites associated with an assistant.

    Example request:
    {
        "data": {
            "assistantId": "ast/38940562397049823"
        }
    }
    """,
    params={
        "assistantId": "String. Required. ID of the assistant to update website content for."
    },
)
@validated(op="rescan_websites")
def rescan_websites(event, context, current_user, name, data=None):
    """
    Lambda function to rescan websites associated with assistants.
    If data is provided, only rescan websites for the specified assistant.
    If no data, scan all assistants that are due for update.
    """
    try:
        assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

        # If assistantId is provided, rescan that specific assistant
        if data and "data" in data and "assistantId" in data["data"]:
            assistant_id = data["data"]["assistantId"]
            response = assistants_table.get_item(Key={"id": assistant_id})

            if "Item" not in response:
                return {"success": False, "message": "Assistant not found"}

            assistant = response["Item"]
            result = process_assistant_websites(assistant, current_user)

            return {
                "success": result["success"],
                "message": result["message"],
                "data": result.get("data", {}),
            }

        # Otherwise, scan all assistants that are due for update
        else:
            # Query for assistants with websiteUrls
            response = assistants_table.scan(
                FilterExpression="attribute_exists(websiteUrls)"
            )

            assistants = response["Items"]
            results = []

            for assistant in assistants:
                # Check if due for update
                last_scan = assistant.get("lastWebsiteScan")
                frequency = assistant.get("websiteScanFrequency", 7)  # Default: 7 days

                if last_scan:
                    last_scan_date = datetime.fromisoformat(last_scan)
                    if datetime.now() - last_scan_date < timedelta(days=frequency):
                        # Not due for update
                        continue

                # Process websites for this assistant
                result = process_assistant_websites(assistant, assistant["user"])
                results.append({"assistantId": assistant["id"], "result": result})

            return {
                "success": True,
                "message": f"Processed {len(results)} assistants",
                "data": {"results": results},
            }

    except Exception as e:
        print(f"Error rescanning websites: {e}")
        return {"success": False, "message": f"Failed to rescan websites: {str(e)}"}


def process_assistant_websites(assistant, user_id):
    """Process websites for an assistant and update data sources."""
    try:
        website_urls = assistant.get("websiteUrls", [])
        if not website_urls and not assistant.get("data", {}).get("websiteUrls", []):
            if assistant.get("data", {}) and "websiteUrls" in assistant["data"]:
                website_urls = assistant["data"]["websiteUrls"]
            else:
                return {
                    "success": True,
                    "message": "No websites to process for this assistant",
                }

        # Scrape content from URLs
        scraped_data = []
        for website_data in website_urls:
            url = website_data.get("url", "")
            is_sitemap = website_data.get("isSitemap", False)
            max_pages = website_data.get("maxPages", 10)

            if is_sitemap:
                urls = extract_urls_from_sitemap(url, max_pages)
                for sub_url in urls:
                    content = fetch_and_parse_url(sub_url)
                    if content:
                        scraped_data.append(
                            {
                                "url": sub_url,
                                "content": content,
                                "timestamp": datetime.now().isoformat(),
                            }
                        )
            else:
                content = fetch_and_parse_url(url)
                if content:
                    scraped_data.append(
                        {
                            "url": url,
                            "content": content,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

        if not scraped_data:
            return {
                "success": False,
                "message": "Failed to scrape any content from the websites",
            }

        # Create data source
        data_source_key = save_scraped_content(scraped_data, user_id)

        # Update file metadata in FILES_DYNAMO_TABLE
        files_table = dynamodb.Table(os.environ.get("FILES_DYNAMO_TABLE"))

        timestamp = datetime.now().strftime("%Y-%m-%d")
        files_table.put_item(
            Item={
                "id": data_source_key,
                "name": f"scraped_websites_{timestamp}.json",
                "type": "application/json",
                "tags": ["website", "scraped"],
                "data": {
                    "sourceUrls": [item["url"] for item in scraped_data],
                    "scrapedAt": datetime.now().isoformat(),
                    # "urlCount": int(len(scraped_data)),
                },
                "knowledgeBase": "default",
                "createdAt": datetime.now().isoformat(),
                "updatedAt": datetime.now().isoformat(),
                "createdBy": user_id,
                "updatedBy": user_id,
            }
        )

        # Update assistant with the new data source
        assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

        # Get the most recent version
        assistant_id = assistant["id"]
        assistant_public_id = assistant.get("assistantId")

        if assistant_public_id:
            from service.core import get_most_recent_assistant_version

            latest_assistant = get_most_recent_assistant_version(
                assistants_table, assistant_public_id
            )

            if latest_assistant:
                # Create a new version with updated dataSources
                data_sources = latest_assistant.get("dataSources", [])

                # Add new scraped content
                data_sources.append(
                    {
                        "id": data_source_key,  # This is the file_key now, not content_key
                        "name": f"Scraped content from {len(scraped_data)} URLs",
                        "type": "application/json",
                        "metadata": {
                            "sourceUrls": [item["url"] for item in scraped_data],
                            "scrapedAt": datetime.now().isoformat(),
                            # "urlCount": int(len(scraped_data)),
                            "isScrapedContent": True,
                        },
                    }
                )

                # Update lastWebsiteScan timestamp
                assistants_table.update_item(
                    Key={"id": latest_assistant["id"]},
                    UpdateExpression="set lastWebsiteScan = :scan, dataSources = :ds",
                    ExpressionAttributeValues={
                        ":scan": datetime.now().isoformat(),
                        ":ds": data_sources,
                    },
                )

                return {
                    "success": True,
                    "message": f"Successfully updated assistant with scraped content from {len(scraped_data)} URLs",
                    "data": {
                        "dataSourceKey": data_source_key,
                        "urlsScraped": len(scraped_data),
                    },
                }

        return {
            "success": False,
            "message": "Failed to update assistant with scraped content",
        }

    except Exception as e:
        print(f"Error processing assistant websites: {e}")
        return {
            "success": False,
            "message": f"Failed to process assistant websites: {str(e)}",
        }


def process_assistant_websites(assistant, user_id):
    """Process websites for an assistant and update data sources."""
    try:
        website_urls = assistant.get("websiteUrls", [])
        if not website_urls:
            return {
                "success": True,
                "message": "No websites to process for this assistant",
            }

        # Scrape content from URLs
        scraped_data = []
        for url in website_urls:
            is_sitemap = url.endswith(".xml") or "sitemap" in url.lower()

            if is_sitemap:
                urls = extract_urls_from_sitemap(url)
                for sub_url in urls:
                    content = fetch_and_parse_url(sub_url)
                    if content:
                        scraped_data.append(
                            {
                                "url": sub_url,
                                "content": content,
                                "timestamp": datetime.now().isoformat(),
                            }
                        )
            else:
                content = fetch_and_parse_url(url)
                if content:
                    scraped_data.append(
                        {
                            "url": url,
                            "content": content,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

        if not scraped_data:
            return {
                "success": False,
                "message": "Failed to scrape any content from the websites",
            }

        # Create data source
        data_source_key = save_scraped_content(scraped_data, user_id)

        # Update assistant with the new data source
        assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

        # Get the most recent version
        assistant_id = assistant["id"]
        assistant_public_id = assistant.get("assistantId")

        if assistant_public_id:
            from service.core import get_most_recent_assistant_version

            latest_assistant = get_most_recent_assistant_version(
                assistants_table, assistant_public_id
            )

            if latest_assistant:
                # Create a new version with updated dataSources
                data_sources = latest_assistant.get("dataSources", [])

                # Add new scraped content
                data_sources.append(
                    {
                        "id": data_source_key,
                        "name": "Scraped Websites",
                        "type": "application/json",
                    }
                )

                # Update lastWebsiteScan timestamp
                assistants_table.update_item(
                    Key={"id": latest_assistant["id"]},
                    UpdateExpression="set lastWebsiteScan = :scan, dataSources = :ds",
                    ExpressionAttributeValues={
                        ":scan": datetime.now().isoformat(),
                        ":ds": data_sources,
                    },
                )

                return {
                    "success": True,
                    "message": f"Successfully updated assistant with scraped content from {len(scraped_data)} URLs",
                    "data": {
                        "dataSourceKey": data_source_key,
                        "urlsScraped": len(scraped_data),
                    },
                }

        return {
            "success": False,
            "message": "Failed to update assistant with scraped content",
        }

    except Exception as e:
        print(f"Error processing assistant websites: {e}")
        return {
            "success": False,
            "message": f"Failed to process assistant websites: {str(e)}",
        }


@validated(op="scrape_website")
def scrape_website(event, context, current_user, name, data):
    """
    Lambda function to scrape a website and create a data source.
    """
    url = data["data"]["url"]
    is_sitemap = data["data"].get("isSitemap", False)
    max_pages = data["data"].get("maxPages", 10)

    return scrape_website_content(url, is_sitemap, current_user, max_pages)
