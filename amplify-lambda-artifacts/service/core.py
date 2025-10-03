from datetime import datetime
import json
import re
import time
import boto3
import os
import boto3
import json
import re
from pycommon.api.amplify_users import are_valid_amplify_users
from pycommon.api.user_data import load_user_data, save_user_data, delete_user_data
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation, S3Operation
)
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ARTIFACTS.value])

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
artifacts_table_name = os.environ["ARTIFACTS_DYNAMODB_TABLE"]
artifact_table = dynamodb.Table(artifacts_table_name)
artifact_bucket = os.environ["S3_ARTIFACTS_BUCKET"] #Marked for future deletion

def get_app_id(current_user: str) -> str:
    return f"{current_user}#amplify-artifacts"

def is_migrated_artifact(artifact_key: str) -> bool:
    """
    Determine if an artifact is migrated based on key format.
    Pre-migration: "user@email.com/20250305/Game:v3" 
    Post-migration: "20250305/Game:v3"
    """
    migrated_pattern = r"^\d{8}/"
    return bool(re.match(migrated_pattern, artifact_key))




@required_env_vars({
    "S3_ARTIFACTS_BUCKET": [S3Operation.GET_OBJECT], #Marked for future deletion
    "ARTIFACTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("read")
def get_artifact(event, context, current_user, name, data):
    validated_key = validate_query_param(event.get("queryStringParameters", {}))
    if not validated_key["success"]:
        return validated_key

    artifact_key = validated_key["key"]

    if not validate_user(current_user, artifact_key):
        return {
            "success": False,
            "message": "You do not have permission to access this artifact.",
        }

    try:
        if is_migrated_artifact(artifact_key):
            # New format: Get from USER_STORAGE_TABLE
            print(f"Retrieving migrated artifact from USER_STORAGE_TABLE: {artifact_key}")
            
            app_id = get_app_id(current_user)
            artifact_content = load_user_data(data["access_token"], app_id, "artifact-content", artifact_key)
            
            if artifact_content is None:
                return {"success": False, "message": "Migrated artifact not found."}
                
            return {"success": True, "data": artifact_content}
        else:
            # Old format: Get from S3
            print(f"Retrieving legacy artifact from S3: {artifact_key}")
            
            response = s3.get_object(Bucket=artifact_bucket, Key=artifact_key)
            contents = response["Body"].read().decode("utf-8")
            return {"success": True, "data": json.loads(contents)}

    except Exception as e:
        print(f"Error retrieving artifact: {e}")
        return {"success": False, "message": "Failed to retrieve artifact."}


@required_env_vars({
    "ARTIFACTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("read")
def get_artifacts_info(event, context, current_user, name, data):
    try:
        print("retrieving entry from the table")
        response = artifact_table.get_item(Key={"user_id": current_user})

        if "Item" in response:
            # Extract the artifacts column from the user's entry
            artifacts = response["Item"].get("artifacts", [])
            return {"success": True, "data": artifacts}
        else:
            # If no entry is found for the user
            return {"success": True, "message": []}
    except Exception as e:
        # Handle any potential errors during the operation
        print(f"Error retrieving artifacts for user {current_user}: {e}")
        return {"success": False, "message": "Failed to retrieve artifacts."}


@required_env_vars({
    "S3_ARTIFACTS_BUCKET": [S3Operation.DELETE_OBJECT], #Marked for future deletion
    "ARTIFACTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("delete")
def delete_artifact(event, context, current_user, name, data):
    validated_key = validate_query_param(event.get("queryStringParameters", {}))
    if not validated_key["success"]:
        return validated_key

    artifact_key = validated_key["key"]

    if not validate_user(current_user, artifact_key):
        return {
            "success": False,
            "message": "You do not have permission to delete this artifact.",
        }

    try:
        # Delete artifact content from either S3 or USER_STORAGE_TABLE
        if is_migrated_artifact(artifact_key):
            # New format: Delete from USER_STORAGE_TABLE
            print(f"Deleting migrated artifact from USER_STORAGE_TABLE: {artifact_key}")
            
            app_id = get_app_id(current_user)
            result = delete_user_data(data["access_token"], app_id, "artifact-content", artifact_key)
            
            if result is None:
                return {"success": False, "message": "Failed to delete migrated artifact."}
        else:
            # Old format: Delete from S3
            print(f"Deleting legacy artifact from S3: {artifact_key}")
            s3.delete_object(Bucket=artifact_bucket, Key=artifact_key)

        # After successfully deleting content, remove the artifact from the DynamoDB metadata table
        print("Remove artifact from metadata table")
        response = artifact_table.get_item(Key={"user_id": current_user})
        if "Item" in response:
            artifacts = response["Item"].get("artifacts", [])
            createdAt = response["Item"].get("createdAt")
            updated_artifacts = [
                artifact for artifact in artifacts if artifact["key"] != artifact_key
            ]

            # Update the DynamoDB table with the new artifact list
            artifact_table.put_item(
                Item={
                    "user_id": current_user,
                    "artifacts": updated_artifacts,
                    "createdAt": createdAt,
                    "lastAccessed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
        return {"success": True, "message": "Artifact deleted successfully."}

    except Exception as e:
        print(f"Error deleting artifact: {e}")
        return {"success": False, "message": "Failed to delete artifact."}


def create_artifact_keys(current_user, artifact):
    print("creating artifact key data")
    created_at_str = artifact["createdAt"]
    created_at_dt = datetime.strptime(created_at_str, "%b %d, %Y")
    created_at_num = created_at_dt.strftime("%Y%m%d")
    name = artifact["name"].replace(" ", "_")
    # New format: clean key without user prefix
    artifact_key = f"{created_at_num}/{name}:v{artifact['version']}"
    artifact_id = f"{name}:v{artifact['version']}-{created_at_num}"

    return artifact_key, artifact_id, created_at_str


@required_env_vars({
    # "S3_ARTIFACTS_BUCKET": [S3Operation.PUT_OBJECT], #Marked for future deletion
    "ARTIFACTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("save")
def save_artifact(event, context, current_user, name, data):
    return save_artifact_for_user(current_user, data["data"]["artifact"], data["access_token"])


def save_artifact_for_user(current_user, artifact, access_token, sharedBy=None):
    print("saving artifact for user ", current_user)
    
    # Use updated create_artifact_keys which now returns clean format
    artifact_key, artifact_id, created_at_str = create_artifact_keys(current_user, artifact)

    artifact_table_data = {
        "key": artifact_key,
        "artifactId": artifact_id,
        "name": artifact["name"],
        "type": artifact["type"],
        "description": artifact["description"],
        "createdAt": created_at_str,
        "tags": artifact.get("tags", []),
    }
    if sharedBy:
        print("sharedBy: ", sharedBy)
        artifact_table_data["sharedBy"] = sharedBy
        
    try:
        print("Adding artifact details to the table")
        createdAt = ""
        response = artifact_table.get_item(Key={"user_id": current_user})
        if "Item" in response:
            item = response["Item"]
            artifacts = item.get("artifacts", [])
            createdAt = item.get("createdAt", time.strftime("%Y-%m-%dT%H:%M:%S"))
        else:
            artifacts = []
            createdAt = time.strftime("%Y-%m-%dT%H:%M:%S")

        # Append the new artifact data
        artifacts.append(artifact_table_data)

        # Update DynamoDB with new artifact metadata
        artifact_table.put_item(
            Item={
                "user_id": current_user,
                "artifacts": artifacts,
                "createdAt": createdAt,
                "lastAccessed": createdAt,
            }
        )

        print("Store artifact content in USER_STORAGE_TABLE")

        # Store the contents in USER_STORAGE_TABLE (new approach)
        artifact["artifactId"] = artifact_id
        app_id = get_app_id(current_user)
        result = save_user_data(access_token, app_id, "artifact-content", artifact_key, artifact)
        
        if result is None:
            return {"success": False, "message": "Failed to save artifact content"}

        # Return success with appended artifact data
        return {"success": True, "data": artifact_table_data}

    except Exception as e:
        print(f"Error saving artifact: {e}")
        return {"success": False, "message": "Failed to save artifact"}


@required_env_vars({
    "S3_ARTIFACTS_BUCKET": [S3Operation.PUT_OBJECT], #Marked for future deletion
    "ARTIFACTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("share")
def share_artifact(event, context, current_user, name, data):
    access_token = data["access_token"]
    data = data["data"]
    artifact = data["artifact"]
    email_list = data["shareWith"]

    if len(email_list) == 0:
        return {"success": False, "message": "No users to share with."}
    
    valid_users, invalid_users = are_valid_amplify_users(access_token, email_list)

    if len(valid_users) == 0:
        return {"success": False, "message": "No valid users to share with."}
    
    errors = []
    for email in invalid_users:
        errors.append({"email": email, "message": "User is not a valid Amplify user"})
    # Iterate over each email in the email list and save the artifact for each user
    for email in valid_users:
        try:
            print(f"Sharing artifact with user {email}")
            save_artifact_for_user(email, artifact, access_token, current_user)
        except Exception as e:
            print(f"Error sharing artifact with {email}: {e}")
            errors.append({"email": email, "message": str(e)})

    # If there were no errors, return success
    if not errors:
        return {"success": True, "message": "Artifact shared successfully."}

    if len(errors) == len(email_list):
        return {"success": False, "message": "Artifact failed to share."}

    # Return success but report any errors
    return {
        "success": True,
        "message": "Artifact shared with some users, but errors occurred for others.",
        "failed": errors,
    }


def validate_user(current_user, artifact_key):
    print("Validating user")
    try:
        # Retrieve the user's entry from the DynamoDB table
        response = artifact_table.get_item(Key={"user_id": current_user})

        if "Item" in response:
            artifacts = response["Item"].get("artifacts", [])

            # Check if the artifact_key matches any entry in the user's artifacts
            for artifact in artifacts:
                if artifact["key"] == artifact_key:
                    return True  # The user has permission to delete this artifact
        return False  # No matching artifact found
    except Exception as e:
        print(f"Error validating user permissions: {e}")
        return False


def validate_query_param(query_params):
    print("Query params: ", query_params)
    if not query_params or not query_params.get("artifact_id"):
        return {"success": False, "message": "Missing artifact_id parameter"}
    artifact_key = query_params.get("artifact_id")

    # Expected artifact_key format: current_user/created_at_num/name:vversion
    key_pattern = r"^[^/]+/\d{8}/[^/]+:v\d+$"

    if not re.match(key_pattern, artifact_key):
        return {"success": False, "message": "Invalid artifact_key format"}

    return {"success": True, "key": artifact_key}

