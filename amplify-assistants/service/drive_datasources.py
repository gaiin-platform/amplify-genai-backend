# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
import boto3
import json
import requests
from pycommon.const import APIAccessType

# Initialize AWS services
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")


from pycommon.api.ops import api_tool
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker


setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ASSISTANTS.value, APIAccessType.SHARE.value])

from service.core import get_most_recent_assistant_version


def process_assistant_drive_sources(assistant_data, access_token):
    """Process integration drive data for an assistant and update data sources."""
    try:
        drive_data = assistant_data.get("integrationDriveData", {})
        if not drive_data or not has_drive_data(drive_data):
            print("No integration drive data to process for this assistant")
            return {
                "success": True,
                "message": "No integration drive data to process for this assistant",
            }   
        # print("Processing integration drive data for this assistant: ", drive_data)
        response = upload_integration_files_to_datasources(drive_data, access_token)
        if not response.get("success", False):
            return response
        updated_drive_data = response.get("data", {})
        return { "success": True,
                 "message": f"Successfully updated assistant with integration drive files",
                 "data": {
                        "integrationDriveData": updated_drive_data,
                  },
                }
    
    except Exception as e:
        print(f"Error processing assistant drive data: {e}")
        return {"success": False, "message": f"Failed to process assistant integration drive data: {str(e)}"}


def extract_drive_datasources(data):
    datasources = []
    
    # Iterate through each integration provider (google, microsoft, etc.)
    for provider_key, provider_data in data.items():
        if not isinstance(provider_data, dict):
            continue
            
        # Process files
        if "files" in provider_data:
            files_data = provider_data["files"]
            for file_id, file_metadata in files_data.items():
                datasource = file_metadata.get("datasource")
                if datasource and isinstance(datasource, dict) and datasource.get("id"):
                    datasources.append(datasource)
        
        # Process folders
        if "folders" in provider_data:
            folders_data = provider_data["folders"]
            for folder_id, folder_files in folders_data.items():
                for file_id, file_metadata in folder_files.items():
                    datasource = file_metadata.get("datasource")
                    if datasource and isinstance(datasource, dict) and datasource.get("id"):
                        datasources.append(datasource)
    
    return datasources


def upload_integration_files_to_datasources(drive_files_data: dict, access_token: str) -> dict:
    """
    Upload selected drive integration files to data sources.

    Args:
        access_token: Bearer token for authentication
        drive_files_data: Dictionary containing the drive files payload structure
                         following DriveFilesDataSourcesPayload format

    Returns:
        dict: Response containing success status and updated payload data,
              or error information if unsuccessful
    """
    print("Initiate upload integration files to datasources call")

    upload_endpoint = os.environ["API_BASE_URL"] + "/integrations/user/files/upload"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    data = {"data": drive_files_data}

    try:
        response = requests.post(
            upload_endpoint, headers=headers, data=json.dumps(data)
        )
        response_content = response.json()
        print("Response: ", response_content)

        if response.status_code == 200 and response_content.get("success", False):
            print("Integration Drive files uploaded successfully")
            return {
                "success": True,
                "data": response_content.get("data"),
                "message": "Integration Drive files uploaded successfully"
            }
        print("Failed to upload integration drive files")
        return {
            "success": False,
            "error": response_content.get("error", "Unknown error occurred"),
            "message": "Failed to upload integration drive files"
        }

    except Exception as e:
        print(f"Error uploading integration files: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Exception occurred during integration drive data upload"
        }

@api_tool(
    path="/assistant/process_drive_sources",
    name="processDriveSources",
    method="POST",
    tags=["default", "process-drive-sources"],
    description="""Process and update drive integration sources associated with an assistant.""",
    parameters={
        "type": "object",
        "properties": {
            "assistantId": {
                "type": "string",
                "description": "ID of the assistant to process drive sources for.",
            }
        },
        "required": ["assistantId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the drive sources processing was successful",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "patternProperties": {
                    "^(google|microsoft|.*?)$": {
                        "type": "object",
                        "properties": {
                            "folders": {
                                "type": "object",
                                "patternProperties": {
                                    ".*": {
                                        "type": "object",
                                        "patternProperties": {
                                            ".*": {
                                                "type": "object",
                                                "properties": {
                                                    "type": {"type": "string"},
                                                    "lastCaptured": {"type": "string", "format": "date-time"},
                                                    "datasource": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "raw": {"type": ["object", "null"]},
                                                            "type": {"type": "string"},
                                                            "data": {"type": ["object", "null"]},
                                                            "key": {"type": "string"},
                                                            "metadata": {"type": "object"},
                                                            "groupId": {"type": "string"}
                                                        },
                                                        "required": ["id", "name", "type"]
                                                    }
                                                },
                                                "required": ["type"]
                                            }
                                        }
                                    }
                                }
                            },
                            "files": {
                                "type": "object",
                                "patternProperties": {
                                    ".*": {
                                        "type": "object",
                                        "properties": {
                                            "type": {"type": "string"},
                                            "lastCaptured": {"type": "string", "format": "date-time"},
                                            "datasource": {
                                                "type": "object",
                                                "properties": {
                                                    "id": {"type": "string"},
                                                    "name": {"type": "string"},
                                                    "raw": {"type": ["object", "null"]},
                                                    "type": {"type": "string"},
                                                    "data": {"type": ["object", "null"]},
                                                    "key": {"type": "string"},
                                                    "metadata": {"type": "object"},
                                                    "groupId": {"type": "string"}
                                                },
                                                "required": ["id", "name", "type"]
                                            }
                                        },
                                        "required": ["type"]
                                    }
                                }
                            }
                        },
                        "required": ["folders", "files"]
                    }
                }
            },
        },
        "required": ["success", "message"],
    },
)
@validated(op="process_drive_sources")
def process_drive_sources(event, context, current_user, name, data=None):
    """
    Lambda function to process drive integration sources for an assistant.
    """
    access_token = data["access_token"]
    try:
        assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
        
        # Get assistantId from request data
        assistant_public_id = data["data"]["assistantId"]
        
        if not assistant_public_id:
            return {"success": False, "message": "Assistant public ID not found"}
        
        # Get the most recent version of the assistant
        latest_assistant = get_most_recent_assistant_version(
            assistants_table, assistant_public_id
        )
        
        if not latest_assistant:
            return {"success": False, "message": "Could not retrieve latest assistant version"}
        
        # Process the assistant drive sources
        result = process_assistant_drive_sources(latest_assistant.get("data", {}), access_token)
        
        if not result.get("success", False):
            return {
                "success": False,
                "message": result.get("message", "Failed to process drive sources")
            }
        
        # Update the assistant with the new integration drive data
        result_data = result.get("data", {})
        integration_drive_data = result_data.get("integrationDriveData", {})
        
        if integration_drive_data:
            # Update the latest assistant's integrationDriveData
            update_expression = "SET #data.#integrationDriveData = :integrationDriveData"
            expression_attribute_names = {
                "#data": "data",
                "#integrationDriveData": "integrationDriveData"
            }
            expression_attribute_values = {
                ":integrationDriveData": integration_drive_data
            }
            
            assistants_table.update_item(
                Key={"id": latest_assistant["id"]},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values
            )
        
        return {
            "success": True,
            "message": result.get("message", "Drive sources processed successfully"),
            "data": integration_drive_data
        }
        
    except Exception as e:
        print(f"Error processing drive sources: {e}")
        return {"success": False, "message": f"Failed to process drive sources: {str(e)}"}
    


def has_drive_data(payload):
    """Check if payload contains any actual drive data (files or folders)."""
    if not payload or not isinstance(payload, dict):
        return False
    
    for provider_name, provider_data in payload.items():
        if provider_data and isinstance(provider_data, dict):
            # Check if folders has any data
            folders = provider_data.get('folders', {})
            if isinstance(folders, dict) and len(folders) > 0:
                return True
            
            # Check if files has any data  
            files = provider_data.get('files', {})
            if isinstance(files, dict) and len(files) > 0:
                return True
    print(f"No drive files found")
    return False