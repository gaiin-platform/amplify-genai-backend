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
from pycommon.lzw import is_lzw_compressed_format, lzw_uncompress
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

# USER_STORAGE_TABLE for direct access (shared artifacts only)
user_storage_table_name = os.environ.get("USER_STORAGE_TABLE")
user_storage_table = dynamodb.Table(user_storage_table_name) if user_storage_table_name else None
print(f"DEBUG: USER_STORAGE_TABLE environment variable = {user_storage_table_name}")

def get_app_id(current_user: str) -> str:
    return "amplify-artifacts"

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
    "USER_STORAGE_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("read")
def get_artifact(event, context, current_user, name, data):
    validated_key = validate_query_param(event.get("queryStringParameters", {}))
    if not validated_key["success"]:
        return validated_key

    artifact_key = validated_key["key"]

    user_artifact_info = validate_user_and_get_artifact_info(current_user, artifact_key)
    if not user_artifact_info:
        return {
            "success": False,
            "message": "You do not have permission to access this artifact.",
        }

    try:
        if is_migrated_artifact(artifact_key):
            # Check if this is a shared artifact
            print(f"DEBUG: user_artifact_info = {user_artifact_info}")
            shared_by = user_artifact_info.get("sharedBy")
            print(f"DEBUG: sharedBy field = {shared_by}")
            
            if shared_by:
                # DIRECT DYNAMODB ACCESS: Required because pycommon functions use access token's user
                # for PK creation. Shared artifacts are stored under sharer's PK but we have recipient's token.
                # This is the ONLY case where we bypass pycommon to avoid cross-user access limitations.
                print(f"SHARED ARTIFACT DETECTED: Retrieving from {shared_by}'s storage: {artifact_key}")
                
                # Construct PK/SK directly to access sharer's storage  
                # Use recipient-specific SK that matches pycommon's actual structure
                pk = f"{shared_by}#amplify-artifacts#artifact-content"  # pycommon includes entity in PK
                print(f"DEBUG: Constructed PK for shared retrieval: {pk}")
                shared_artifact_key = f"shared-with-{current_user}#{artifact_key}"
                sk = shared_artifact_key  # pycommon uses just item_id as SK, no entity prefix
                print(f"DEBUG: Searching for PK={pk}, SK={sk}")
                
                try:
                    print(f"DEBUG: Searching in table: {user_storage_table_name}")
                    response = user_storage_table.get_item(Key={"PK": pk, "SK": sk})
                    print(f"DEBUG: DynamoDB response = {response}")
                    artifact_content = response.get("Item") if "Item" in response else None
                    print(f"DEBUG: Extracted artifact_content = {artifact_content is not None}")
                except Exception as e:
                    print(f"ERROR: Failed to retrieve shared artifact: {e}")
                    artifact_content = None
            else:
                # NORMAL CASE: Use pycommon for user's own artifacts
                print(f"Retrieving artifact from USER_STORAGE_TABLE: {artifact_key}")
                app_id = "amplify-artifacts"
                artifact_content = load_user_data(data["access_token"], app_id, "artifact-content", artifact_key)
            
            if artifact_content is None:
                return {"success": False, "message": "Migrated artifact not found."}
            
            # Extract from nested structure if needed
            if "data" in artifact_content:
                actual_artifact = artifact_content["data"]
                
                # Check if contents field is compressed and decompress if needed
                if "contents" in actual_artifact and is_lzw_compressed_format(actual_artifact["contents"]):
                    print("Decompressing artifact contents")
                    actual_artifact["contents"] = lzw_uncompress(actual_artifact["contents"])
            else:
                # Fallback: return the whole content if structure is different
                actual_artifact = artifact_content
                
            return {"success": True, "data": actual_artifact}
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
    "USER_STORAGE_TABLE": [DynamoDBOperation.DELETE_ITEM],
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
        # Delete artifact content
        if is_migrated_artifact(artifact_key):
            # Get artifact info to determine storage location
            user_artifact_info = validate_user_and_get_artifact_info(current_user, artifact_key)
            if user_artifact_info and user_artifact_info.get("sharedBy"):
                # SHARED ARTIFACT DELETE: Remove recipient's specific copy AND metadata
                # Each share creates a unique copy, so safe to delete this specific one
                shared_by_user = user_artifact_info.get("sharedBy")
                print(f"Deleting shared artifact copy for {current_user} from {shared_by_user}'s storage: {artifact_key}")
                
                # Delete the recipient-specific copy
                pk = f"{shared_by_user}#amplify-artifacts#artifact-content"  # pycommon includes entity in PK
                shared_artifact_key = f"shared-with-{current_user}#{artifact_key}"
                sk = shared_artifact_key  # pycommon uses just item_id as SK, no entity prefix
                
                try:
                    user_storage_table.delete_item(Key={"PK": pk, "SK": sk})
                    print(f"DEBUG: Deleted shared copy at PK={pk}, SK={sk}")
                    result = True
                except Exception as e:
                    print(f"Error deleting shared artifact copy: {e}")
                    result = None
            else:
                # NORMAL CASE: Use pycommon for user's own artifacts
                print(f"Deleting artifact from USER_STORAGE_TABLE: {artifact_key}")
                app_id = "amplify-artifacts"
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
                    "lastAccessed": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
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
        print(f"DEBUG: Adding sharedBy field: {sharedBy}")
        artifact_table_data["sharedBy"] = sharedBy
        print(f"DEBUG: Complete metadata will be: {artifact_table_data}")
        
    try:
        print("Adding artifact details to the table")
        createdAt = ""
        response = artifact_table.get_item(Key={"user_id": current_user})
        if "Item" in response:
            item = response["Item"]
            artifacts = item.get("artifacts", [])
            current_timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            createdAt = item.get("createdAt", current_timestamp)
        else:
            artifacts = []
            createdAt = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

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

        # Store artifact content in USER_STORAGE_TABLE
        print("Store artifact content in USER_STORAGE_TABLE")
        artifact["artifactId"] = artifact_id
        
        if sharedBy:
            # SHARED ARTIFACT: Store in sharer's storage space using sharer's access token
            # Use recipient-specific SK to avoid overwrites when sharing with multiple users
            print(f"SHARED ARTIFACT STORAGE: Storing in {sharedBy}'s storage space for recipient {current_user}")
            app_id = "amplify-artifacts"
            # Create unique key per share to avoid overwrites
            shared_artifact_key = f"shared-with-{current_user}#{artifact_key}"
            print(f"DEBUG: Using app_id = {app_id} for shared storage")
            print(f"DEBUG: Shared artifact key = {shared_artifact_key}")
            print(f"DEBUG: This will create PK = {sharedBy}#{app_id} = {sharedBy}#amplify-artifacts")
            try:
                result = save_user_data(access_token, app_id, "artifact-content", shared_artifact_key, artifact)
                print(f"DEBUG: save_user_data result = {result}")
            except Exception as e:
                print(f"ERROR: save_user_data failed: {e}")
                result = None
        else:
            # REGULAR ARTIFACT: Store in current user's storage space  
            app_id = "amplify-artifacts"
            result = save_user_data(access_token, app_id, "artifact-content", artifact_key, artifact)
        
        if result is None:
            return {"success": False, "message": "Failed to save artifact content"}
        
        # Verify the save was successful
        if not result.get("success", True):  # Some pycommon functions return success flag
            print(f"ERROR: save_user_data reported failure: {result}")
            return {"success": False, "message": f"Failed to save artifact content: {result.get('message', 'Unknown error')}"}

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
            result = save_artifact_for_user(email, artifact, access_token, current_user)
            if not result["success"]:
                print(f"Failed to save artifact for {email}: {result['message']}")
                errors.append({"email": email, "message": result["message"]})
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


def validate_user_and_get_artifact_info(current_user, artifact_key):
    print("Validating user and getting artifact info")
    try:
        # Retrieve the user's entry from the DynamoDB table
        response = artifact_table.get_item(Key={"user_id": current_user})

        if "Item" in response:
            artifacts = response["Item"].get("artifacts", [])

            # Check if the artifact_key matches any entry in the user's artifacts
            for artifact in artifacts:
                if artifact["key"] == artifact_key:
                    return artifact  # Return full artifact info
        return None  # No matching artifact found
    except Exception as e:
        print(f"Error validating user permissions: {e}")
        return None

def validate_user(current_user, artifact_key):
    """Legacy function for backward compatibility"""
    artifact_info = validate_user_and_get_artifact_info(current_user, artifact_key)
    return artifact_info is not None


def validate_query_param(query_params):
    print("Query params: ", query_params)
    if not query_params or not query_params.get("artifact_id"):
        return {"success": False, "message": "Missing artifact_id parameter"}
    artifact_key = query_params.get("artifact_id")

    # Support both old and new artifact key formats:
    # Old format: user@email.com/20250904/Artifact:v1
    # New format: 20250904/Artifact:v1
    old_key_pattern = r"^[^/]+/\d{8}/[^/]+:v\d+$"
    new_key_pattern = r"^\d{8}/[^/]+:v\d+$"

    if not (re.match(old_key_pattern, artifact_key) or re.match(new_key_pattern, artifact_key)):
        return {"success": False, "message": "Invalid artifact_key format"}

    return {"success": True, "key": artifact_key}