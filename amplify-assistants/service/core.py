# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from datetime import datetime, timedelta
import hashlib
import os
import time
import boto3
import json
import uuid
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from decimal import Decimal
from pycommon.const import APIAccessType
from pycommon.api.amplify_users import are_valid_amplify_users
from pycommon.api.files import delete_file

# Initialize AWS services
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

from pycommon.api.data_sources import (
    get_data_source_keys,
    translate_user_data_sources_to_hash_data_sources,
    extract_key
)


from pycommon.api.object_permissions import (
    update_object_permissions,
    can_access_objects,
    simulate_can_access_objects,
)

from pycommon.api.ops import api_tool
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ASSISTANTS.value, APIAccessType.SHARE.value])

from pycommon.encoders import CustomPydanticJSONEncoder


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


def is_group_sys_user(data):
    return data.get("purpose", '') == "group"

def check_can_do(assistant, user_id):
    if assistant:
        return assistant["user"] == user_id
    return False

def check_user_can_share_assistant(assistant, user_id):
    return check_can_do(assistant, user_id)


def check_user_can_delete_assistant(assistant, user_id):
    return check_can_do(assistant, user_id)         


def check_user_can_update_assistant(assistant, user_id):
    return check_can_do(assistant, user_id)


@api_tool(
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
    parameters={
        "type": "object",
        "properties": {
            "assistantId": {
                "type": "string",
                "description": "Unique identifier of the assistant to delete. Example: 'astp/3209457834985793094'.",
            }
        },
        "required": ["assistantId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the assistant deletion was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result of the deletion",
            },
        },
        "required": ["success", "message"],
    },
)
@validated(op="delete")
def delete_assistant(event, context, current_user, name, data):
    access = data["allowed_access"]
    access_token = data["access_token"]
    if APIAccessType.ASSISTANTS.value not in access and APIAccessType.FULL_ACCESS.value not in access:
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
    print(f"Deleting assistant")

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
            print(f"User {current_user} is not authorized to delete assistant {assistant_public_id}")
            return {
                "success": False,
                "message": "You are not authorized to delete this assistant.",
            }

            # First, delete any paths associated with this assistant
        lookup_table = dynamodb.Table(os.environ.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE"))
        # Query for all paths belonging to this assistant
        response = lookup_table.query(
            IndexName="AssistantIdIndex",
            KeyConditionExpression=Key("assistantId").eq(assistant_public_id),
        )

        # Delete each path entry
        for item in response.get("Items", []):
            # imported here to avoid circular import
            from service.standalone_ast_path import release_assistant_path
            release_assistant_path(item["astPath"], assistant_public_id, current_user)

        astIconDs = existing_assistant.get("data", {}).get("astIcon")
        if (astIconDs):
            print(f"Deleting assistant Icon file: {astIconDs}")
            metadata = astIconDs.get("metadata")
            key = astIconDs.get("key") or (metadata.get("contentKey") if metadata else None)
            print(f"Deleting assistant Icon file: {key}")
            delete_file(access_token, key)

        integration_drive_data = existing_assistant.get("data", {}).get("integrationDriveData", {})
        if (integration_drive_data):
            from service.drive_datasources import extract_drive_datasources
            drive_data_sources = extract_drive_datasources(integration_drive_data)
            print(f"Deleting {len(drive_data_sources)} drive data sources")
            for ds in drive_data_sources:
                print(f"Deleting drive data source: {ds.get('id')}")
                delete_file(access_token, ds.get("id"))

        # delete asistant specific data sources - those with ds with ds.metadata.type starts with "assistant-
        dataSources = existing_assistant.get("dataSources", [])
        print(f"DataSources: {dataSources}")

        for i, ds in enumerate(dataSources):
            print(f"Processing datasource {i}: {ds}")
            metadata = ds.get("metadata") if ds else None
            if metadata and metadata.get("type", "").startswith("assistant"):
                print(f"Found assistant-specific datasource")
                key = extract_key(ds.get("key")) if ds.get("key") else ds.get("id")
                print(f"Deleting assistant specific data source: {key}")
                delete_file(access_token, key)

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


@api_tool(
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
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the assistants retrieval was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "array",
                "description": "Array of assistant objects",
                "items": {
                    "type": "object",
                    "properties": {
                        "assistantId": {
                            "type": "string",
                            "description": "Unique identifier of the assistant",
                        },
                        "version": {
                            "type": "integer",
                            "description": "Version number of the assistant",
                        },
                        "instructions": {
                            "type": "string",
                            "description": "Instructions for the assistant",
                        },
                        "disclaimerHash": {
                            "type": "string",
                            "description": "Hash of the disclaimer",
                        },
                        "coreHash": {
                            "type": "string",
                            "description": "Core hash of the assistant",
                        },
                        "user": {
                            "type": "string",
                            "description": "User who created the assistant",
                        },
                        "uri": {
                            "type": ["string", "null"],
                            "description": "URI associated with the assistant",
                        },
                        "createdAt": {
                            "type": "string",
                            "description": "Creation timestamp",
                        },
                        "dataSources": {
                            "type": "array",
                            "description": "Data sources associated with the assistant",
                            "items": {"type": "object"},
                        },
                    },
                },
            },
        },
        "required": ["success", "message", "data"],
    },
)
@validated(op="list")
def list_assistants(event, context, current_user, name, data):
    access = data["allowed_access"]
    if APIAccessType.ASSISTANTS.value not in access and APIAccessType.FULL_ACCESS.value not in access:
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
    if not is_group_sys_user(data):  # saves us the call, access is determined by group members access list
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
            "IndexName": "UserNameIndex",
            "KeyConditionExpression": Key("user").eq(user_id),
        }

        # If there is a last evaluated key, include it in the query
        if last_evaluated_key:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        response = assistants_table.query(**query_params)

        assistants.extend(response.get("Items", []))

        # Check if there's more data to retrieve
        last_evaluated_key = response.get("LastEvaluatedKey")

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
        assistant.setdefault("version", 1)
        assistant_id = assistant.get("assistantId", None)
        # will exclude system ast since they dont have assistantId
        if assistant_id and (
            assistant_id not in latest_assistants
            or latest_assistants[assistant_id]["version"] < assistant["version"]
        ):
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


@api_tool(
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
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the assistant. Example: 'Sample Assistant 3'.",
            },
            "description": {
                "type": "string",
                "description": "Description of the assistant's purpose.",
            },
            "assistantId": {
                "type": "string",
                "description": "If provided, updates an existing assistant. Example: 'astp/3io4u5ipy34jkelkdfweiorwur'. prefixed with astp",
            },
            "tags": {
                "type": "array",
                "description": "Tags to categorize the assistant.",
                "items": {"type": "string"},
            },
            "instructions": {
                "type": "string",
                "description": "Detailed instructions on how the assistant should respond.",
            },
            "disclaimer": {
                "type": "string",
                "description": "Disclaimer for the assistant's responses.",
            },
            "dataSources": {
                "type": "array",
                "description": "List of data sources the assistant can use. You can obtain ful data source objects by calling the /files/query endpoint",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "key": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                },
            },
        },
        "required": ["name", "description", "tags", "instructions", "dataSources"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the assistant creation/update was successful",
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
                    "id": {
                        "type": "string",
                        "description": "Internal database ID of the assistant",
                    },
                    "version": {
                        "type": "integer",
                        "description": "Version number of the assistant",
                    },
                    "data_sources": {
                        "type": "array",
                        "description": "List of data sources the assistant can use. You can obtain ful data source objects by calling the /files/query endpoint",
                        "items": {"type": "object"},
                    },
                },
                "required": ["assistantId", "id", "version"],
            },
        },
        "required": ["success", "message", "data"],
    },
)
@validated(op="create")
def create_assistant(event, context, current_user, name, data):
    access = data["allowed_access"]
    access_token = data["access_token"]
    if APIAccessType.ASSISTANTS.value not in access and APIAccessType.FULL_ACCESS.value not in access:
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
    is_group_user = is_group_sys_user(data)

    # Identify and store website URLs
    website_data_sources = []
    standard_data_sources = []

    all_website_urls = assistant_data.get('websiteUrls', [])
    print(f"Starting with {len(all_website_urls)} existing website URLs")
    print(f"all_website_urls: {all_website_urls}")


    for source in data_sources:
        # Check if this is a website-related data source
        is_website_type = source.get("type") in ["website/url", "website/sitemap"]
        is_from_sitemap = source.get("metadata", {}).get("fromSitemap") is not None
        
        if is_website_type or is_from_sitemap:
            # Check if this is a new URL to scrape (no key) or existing scraped content (has key)
            if not source.get("key"):
                # This is a NEW website URL that needs scraping
                url = source.get("metadata", {}).get("sourceUrl", "")
                if not url:
                    url = source.get("id", '')
                    if (url and "metadata" in source):
                        source["metadata"]["sourceUrl"] = url
                
                # Check if this URL is already being tracked
                existing_entry = next((entry for entry in all_website_urls if entry.get("url") == url), None)
                if not existing_entry:
                    # Add new website URL to tracking
                    website_url_entry = {
                        "url": url,
                        "sourceUrl": url,
                        "isSitemap": source.get("type") == "website/sitemap",
                        "type": source.get("type"),
                        **source.get("metadata", {}),  # Take frontend metadata as-is
                        "lastScanned": source.get("metadata", {}).get("lastScanned")
                    }
                    all_website_urls.append(website_url_entry)
                    print(f"Added new website URL to tracking: {url}")
                
                print(f"Website data source needs scraping: {source}")
                website_data_sources.append(source)
            else:
                # This is EXISTING scraped content - preserve as data source
                print(f"Preserving existing scraped website data source: {source.get('id')}")
                standard_data_sources.append(source)
        else:
            standard_data_sources.append(source)

    assistant_data["websiteUrls"] = all_website_urls

    # Process website URLs for scraping (only unscraped ones)
    scraped_data_sources = []
    if website_data_sources:
        for website_source in website_data_sources:
            # Extract URL and metadata
            metadata = website_source.get("metadata", {})
            url = metadata.get("sourceUrl", "") or website_source.get("id", "")
            is_sitemap = website_source.get("type") == "website/sitemap"
            scan_frequency = metadata.get("scanFrequency")

            try:
                # imported here to avoid circular import
                from service.scrape_websites import scrape_website_content
                # Attempt immediate scraping
                max_pages = metadata.get("maxPages")
                exclusions = metadata.get("exclusions")
                scraped_data = scrape_website_content(url, access_token, is_sitemap, max_pages, exclusions)
                scraped_web_ds = scraped_data.get("data", {}).get("dataSources")
                if scraped_data.get("success") and scraped_web_ds:
                    for ds in scraped_web_ds:
                        ds.get("metadata").update({"scanFrequency": scan_frequency, "contentKey": ds['id']})
                        if (is_sitemap):
                            ds.get("metadata").update({"maxPages": max_pages})
                        ds['key'] = ds['id']

                    scraped_data_sources += scraped_web_ds
                    
                    # Update lastScanned timestamp for this URL
                    for website_entry in assistant_data["websiteUrls"]:
                        if website_entry["url"] == url:
                            website_entry["lastScanned"] = scraped_web_ds[0].get("metadata", {}).get("scrapedAt", datetime.now().isoformat())
                            break

            except Exception as e:
                print(f"Error initially scraping website {url}: {str(e)}")

    # imported here to avoid circular import
    from service.drive_datasources import process_assistant_drive_sources
    integration_drive_ds_response = process_assistant_drive_sources(assistant_data, access_token)
    if not integration_drive_ds_response.get("success", False):
        return integration_drive_ds_response
    integration_drive_ds_data = integration_drive_ds_response.get("data", {})
    # update assistant_data with integration drive data
    assistant_data["integrationDriveData"] = integration_drive_ds_data.get("integrationDriveData", {})

    # Permissions handling for non-group users
    if not is_group_user:
        # Process standard data sources (excluding website URLs which don't need permission checks)
        filtered_ds = []
        tag_data_sources = []

        for source in standard_data_sources:
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
                    filtered_ds[i]["id"] = source.get("key", source.get("id", ""))

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
        final_data_sources = filtered_ds + tag_data_sources
    else:
        # For group system users, use all data sources as-is
        final_data_sources = standard_data_sources
    
    # merge additional ds
    final_data_sources += scraped_data_sources 
    # + drive_data_sources

    print(f"final_data_sources: {final_data_sources}")

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
        is_group_user=is_group_user,
    )


@api_tool(
    path="/assistant/share",
    name="shareAssistant",
    method="POST",
    tags=["apiDocumentation"],
    description="""Share an assistant with another user.

    Example request:
    {
        "assistantId": "astp/3io4u5ipy34jkelkdfweiorwur",
        "userEmail": "user@example.com",
        "permissions": "read"
    }

    Example response:
    {
        "success": true,
        "message": "Assistant shared successfully with user@example.com.",
        "data": {
            "sharedWith": "user@example.com",
            "permissions": "read",
            "sharedAt": "2024-07-15T18:58:24.912235"
        }
    }
    """,
    parameters={
        "type": "object",
        "properties": {
            "assistantId": {
                "type": "string",
                "description": "The ID of the assistant to share. Example: 'astp/3io4u5ipy34jkelkdfweiorwur'",
            },
            "userEmail": {
                "type": "string",
                "description": "Email address of the user to share the assistant with",
            },
            "permissions": {
                "type": "string",
                "description": "Permission level for the shared assistant. Example: 'read' or 'write'",
            },
        },
        "required": ["assistantId", "userEmail", "permissions"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the sharing operation was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "properties": {
                    "sharedWith": {
                        "type": "string",
                        "description": "Email address of the user the assistant was shared with",
                    },
                    "permissions": {
                        "type": "string",
                        "description": "Permission level granted to the user",
                    },
                    "sharedAt": {
                        "type": "string",
                        "description": "Timestamp when the assistant was shared",
                    },
                },
                "required": ["sharedWith", "permissions", "sharedAt"],
            },
        },
        "required": ["success", "message", "data"],
    },
)
@validated(op="share_assistant")
def share_assistant(event, context, current_user, name, data):
    access = data["allowed_access"]
    access_token = data["access_token"]
    if APIAccessType.SHARE.value not in access and APIAccessType.FULL_ACCESS.value not in access:
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

    valid_users, _ = are_valid_amplify_users(access_token, recipient_users)

    if len(valid_users) == 0:
        return {"success": False, "message": "No valid users to share with."}
    

    return share_assistant_with(
        access_token=access_token,
        current_user=current_user,
        assistant_key=assistant_key,
        recipient_users=valid_users,
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
    
    from service.drive_datasources import extract_drive_datasources
    drive_data_sources = extract_drive_datasources(assistant_entry.get("data", {}).get("integrationDriveData", {}))
    # if (drive_data_sources):
    #     print(f"Drive data sources: {drive_data_sources}")

    data_sources = get_data_source_keys(assistant_entry["dataSources"] + drive_data_sources)
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
    is_group_user=False,
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
    ast_prefix = "astg" if is_group_user else "ast"
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
    is_group_user=False,
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

    principal_type = "group" if is_group_user else "user"

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
            # imported here to avoid circular import
            from service.standalone_ast_path import release_assistant_path
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
            is_group_user,
        )
        new_item["version"] = new_version

        # Collect all data source keys, including scraped content
        # Note: scraped content could still be processing through the rag pipeline
        # therefore we dont have the globals to update the permissions, 
        # we dont need to anyway
        all_data_source_keys = [
            source["id"] for source in data_sources 
            if not source["id"].startswith("s3://") and 
               source.get("metadata", {}).get("type") != "assistant-web-content"
        ]
        
        # Set permissions for the assistant
        if not update_object_permissions(
            access_token,
            [user_that_owns_the_assistant],
            [new_item["id"], new_item["assistantId"]],
            "assistant",
            principal_type,
            "owner"):
            print(f"Error updating permissions for assistant {new_item['id']}")

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
                print(f"Error updating permissions for data sources: {all_data_source_keys}")
            else:
                print(f"Successfully updated permissions for data sources: {all_data_source_keys}")

        # Update permissions for the new version to ensure the user retains edit rights
        try:
            # Add direct permissions entry in DynamoDB for the new version ID
            object_access_table.put_item(
                Item={
                    "object_id": new_item["id"],  # The ID of the new assistant version
                    "principal_id": user_that_owns_the_assistant,
                    "permission_level": "owner",  # Give the user full ownership rights
                    "principal_type": principal_type,  # For individual users or groups
                    "object_type": "assistant",  # The type of object being accessed
                }
            )
            print( f"Successfully added direct permissions for {principal_type} {user_that_owns_the_assistant} on assistant version {new_item['id']}" )
        except Exception as e:
            print(f"Error adding permissions for assistant version: {str(e)}")

        # Update the latest alias to point to the new version
        update_assistant_latest_alias(assistant_public_id, new_item["id"], new_version)

        # print(f"Indexing assistant {new_item['id']} for RAG")

        print(f"Added RAG entry for {new_item['id']}")

        # Return success response
        return {
            "success": True,
            "message": "Assistant created successfully",
            "data": {
                "assistantId": assistant_public_id,
                "id": new_item["id"],
                "version": new_version,
                "data_sources": new_item["dataSources"],
                "ast_data": new_item["data"]
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
            is_group_user,
        )

        # Set permissions for all data sources, including scraped content
        all_data_source_keys = [source["id"] for source in data_sources]

        if not update_object_permissions(
            access_token,
            [user_that_owns_the_assistant],
            [new_item["assistantId"], new_item["id"]],
            "assistant",
            principal_type,
            "owner",
        ):
            print(f"Error updating permissions for assistant {new_item['id']}")

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

        try:
            for object_id in [new_item["id"], new_item[ "assistantId"]]:
                object_access_table.put_item(
                Item={
                    "object_id": object_id,  
                    "principal_id": user_that_owns_the_assistant,
                    "permission_level": "owner",  # Give the user full ownership rights
                    "principal_type": principal_type,  # For individual users or groups
                    "object_type": "assistant",  # The type of object being accessed
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

        # print(f"Indexing assistant {new_item['id']} for RAG")
        # save_assistant_for_rag(new_item)
        print(f"Added RAG entry for {new_item['id']}")

        # Return success response
        return {
            "success": True,
            "message": "Assistant created successfully",
            "data": {
                "assistantId": new_item["assistantId"],
                "id": new_item["id"],
                "version": new_item["version"],
                "data_sources": new_item["dataSources"],
                "ast_data": new_item["data"]
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
        json.dumps(core_details, sort_keys=True, cls=CustomPydanticJSONEncoder).encode()
    ).hexdigest()
    datasources_sha256 = hashlib.sha256(
        json.dumps(
            data_sources.sort(key=lambda x: x["id"]), cls=CustomPydanticJSONEncoder
        ).encode()
    ).hexdigest()
    instructions_sha256 = hashlib.sha256(
        json.dumps(instructions, sort_keys=True, cls=CustomPydanticJSONEncoder).encode()
    ).hexdigest()
    disclaimer_sha256 = hashlib.sha256(
        json.dumps(disclaimer, sort_keys=True, cls=CustomPydanticJSONEncoder).encode()
    ).hexdigest()
    core_details["assistant"] = assistant_name
    core_details["description"] = description
    full_sha256 = hashlib.sha256(
        json.dumps(core_details, sort_keys=True, cls=CustomPydanticJSONEncoder).encode()
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



@validated(op="share_assistant")
def request_assistant_to_public_ast(event, context, current_user, name, data):
    data = data["data"]
    assistant_id = data["assistantId"]

    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
    object_access_table = dynamodb.Table(os.environ.get("OBJECT_ACCESS_DYNAMODB_TABLE"))

    try:
        # First, find the current version of the assistant
        print("Looking up assistant: ", assistant_id)
        existing_assistant = get_most_recent_assistant_version(
            assistants_table, assistant_id
        )

        if not existing_assistant:
            return {
                "success": False,
                "message": f"Assistant not found: {assistant_id}",
            }

        if not existing_assistant.get("data", {}).get("availableOnRequest"):
            print("Assistant is not available for public request: ", assistant_id)
            return {
                "success": False,
                "message": f"Assistant is not available for public request: {assistant_id}",
            }

        print("Updating assistant permissions for user: ", current_user)
        object_access_table.put_item(
            Item={
                "object_id": assistant_id,
                "principal_id": current_user,
                "principal_type": "user",
                "object_type": "assistant",
                "permission_level": "read",
                "policy": None,
            }
        )

        data_sources = get_data_source_keys(existing_assistant["dataSources"])
        print("Updating permissions for ast datasources")

        for ds in data_sources:
            object_access_table.put_item(
                Item={
                    "object_id": ds,
                    "principal_id": current_user,
                    "principal_type": "user",
                    "object_type": "datasource",
                    "permission_level": "read",
                    "policy": None,
                }
            )

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
    data = data["data"]
    assistant_id = data["assistantId"]

    dynamodb = boto3.resource("dynamodb")
    assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

    try:
        # First, find the current version of the assistant
        existing_assistant = get_most_recent_assistant_version(
            assistants_table, assistant_id
        )

        if not existing_assistant:
            print(f"Assistant not found: {assistant_id}")
            return {
                "success": False,
                "message": f"Assistant not found: {assistant_id}",
            }

        print(f"Assistant id is a valid assistant: {assistant_id}")
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

