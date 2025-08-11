from datetime import datetime, timezone
from enum import Enum
import re
from botocore.exceptions import ClientError
from decimal import Decimal
import boto3
import os
import uuid
import random
import boto3
import re
from pycommon.api_utils import TokenV1
from pycommon.api.amplify_users import are_valid_amplify_users
from pycommon.api.ops import api_tool
from pycommon.api.auth_admin import verify_user_as_admin
from botocore.config import Config
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.API_KEY.value])

s3 = boto3.client("s3")
bucket_name = os.environ["S3_API_DOCUMENTATION_BUCKET"]
dynamodb = boto3.resource("dynamodb")
api_keys_table_name = os.environ["API_KEYS_DYNAMODB_TABLE"]
table = dynamodb.Table(api_keys_table_name)


class APIFile(Enum):
    PDF = "Amplify_API_Documentation.pdf"
    CSV = "Amplify_API_Documentation.csv"
    JSON = "Postman_Amplify_API_Collection.json"


@validated("read")
def get_api_keys_for_user(event, context, user, name, data):
    return get_api_keys(user)


def get_api_keys(user):
    print("Getting keys from dyanmo")
    try:
        # Use a Scan operation with a FilterExpression to find items where the user is the owner or the delegate
        # Use a ProjectionExpression to specify the attributes to retrieve (excluding 'apiKey')
        response = table.scan(
            FilterExpression="#owner = :user or delegate = :user",
            ExpressionAttributeNames={
                "#owner": "owner"  # Use '#owner' to substitute 'owner'
            },
            ExpressionAttributeValues={":user": user},
            ProjectionExpression="api_owner_id, #owner, delegate, applicationName, applicationDescription, createdAt, lastAccessed, rateLimit, expirationDate, accessTypes, active, account, systemId, purpose, apiKey",
        )
        # Check if any items were found
        if "Items" in response and response["Items"]:
            print(f"API keys found for user {user}")
            for item in response["Items"]:
                item['needs_rotation'] = item.get("active", False) and (not item.get("purpose")) and (item.get('apiKey', '').startswith('amp-') or item.get('apiKey') == 'MIGRATED')
                del item['apiKey']
                # delegate will not be able to see account coa
                if user == item.get("delegate"):
                    item["account"] = None

                # check if key is expired, if so deactivate key
                expiration = item.get("expirationDate")
                if item.get("active") and expiration and is_expired(expiration):
                    print( f"Key {item.get('applicationName')} is expired and will be deactivated ")
                    deactivate_key_in_dynamo(user, item.get("api_owner_id"))
                    item["active"] = False

            return {"success": True, "data": response["Items"]}
        else:
            print(f"User {user} has no API keys. ")
            return {"success": True, "data": [], "message": "User has no API keys."}
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred while retrieving API keys for user {user}: {e}")
        return {"success": False, "data": [], "message": str(e)}


@api_tool(
    path="/state/accounts/get",
    name="getUserAccounts",
    method="GET",
    tags=["apiKeysAst"],
    description="Get a list of the user's accounts that costs are charged to.",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "data": {"type": "array", "description": "Array of user account objects"},
            "message": {"type": "string", "description": "Optional message"},
        },
        "required": ["success"],
    },
)
@api_tool(
    path="/apiKeys/get_keys_ast",
    name="getApiKeysForAst",
    method="GET",
    tags=["apiKeysAst"],
    description="Get user's amplify api keys filtered for assistant use.",
    parameters={"type": "object", "properties": {}, "required": []},
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the operation was successful",
            },
            "All_API_Keys": {
                "type": "string",
                "description": "String representation of all API keys or message if none exist",
            },
            "Additional_Instructions": {
                "type": "string",
                "description": "Additional instructions for handling the API keys",
            },
        },
        "required": ["success", "All_API_Keys", "Additional_Instructions"],
    },
)
# for api key ast 
@validated("read")
def get_api_keys_for_assistant(event, context, user, name, data):
    # Fetch API keys
    api_keys_res = get_api_keys(user)
    if not api_keys_res["success"]:
        return {"success": False, "message": "API KEYS: UNAVAILABLE"}

    keys = api_keys_res["data"]
    append_msg = "Always display all the Keys."
    if len(keys) > 0:
        append_msg += f"\n\n There are a total of {len(keys)}. When asked to list keys, always list ALL {len(keys)} of the 'API KEYS'"
        delegate_keys = []
        delegated_keys = []
        for k in keys:
            if k.get("delegate"):
                if k["delegate"] != user:  # user is owner
                    delegate_keys.append(k)
                elif k["delegate"] == user and k["owner"] != user:  # user is not owner
                    delegated_keys.append(k)

        if delegate_keys:
            append_msg += (
                "\n\n GET OP is NOT allowed for the following Delegate keys (unauthorized): "
                + ", ".join([k["applicationName"] for k in delegate_keys])
            )
        if delegated_keys:
            append_msg += (
                "\n\n UPDATE OP is NOT allowed for the following Delegated keys (unauthorized): "
                + ", ".join([k["applicationName"] for k in delegated_keys])
            )

    return {
        "success": True,
        "All_API_Keys": f"{keys} " if keys else "No current existing keys",
        "Additional_Instructions": append_msg,
    }


def can_create_api_key(delegate, account, access_token):
    if not is_valid_account(account["id"]):
        return False, "Invalid COA string"
    # if delegate,  check user table for valid delegate email
    if delegate:
        valid_users, _ = are_valid_amplify_users(access_token, [delegate])
        return (True, "") if delegate in valid_users else (False, "User is not a valid Amplify user")
    
    return True, ""

# all accounts are valid for now
def is_valid_account(coa):
    return True
    # here we want to check valid coa string,
    pattern = re.compile(
        r"^(\w{3}\.\w{2}\.\w{5}\.\w{4}\.\w{3}\.\w{3}\.\w{3}\.\w{3}\.\w{1})$"
    )
    return bool(pattern.match(coa))


@validated("create")
def create_api_keys(event, context, user, name, data):
    # api keys/get
    api_key_data = data["data"]

    can_create, message = can_create_api_key(api_key_data.get("delegate"), api_key_data["account"], data["access_token"])
    if not can_create:
        print(f"Error: {message}")
        return {"success": False, "message": message}
    return create_api_key_for_user(user, api_key_data)


def create_api_key_for_user(user, api_key):
    print("Validated and now creating api key")
    api_keys_table_name = os.environ["API_KEYS_DYNAMODB_TABLE"]
    table = dynamodb.Table(api_keys_table_name)
    delegate = api_key.get("delegate")
    isSystem = api_key.get("systemUse", False)

    key_type = "delegate" if delegate else ("system" if isSystem else "owner")
    id = f"{user}/{key_type}Key/{str(uuid.uuid4())}"
    timestamp = datetime.now(timezone.utc).isoformat()

    # Generate TokenV1 API key
    token = TokenV1()
    api_key_raw = token.raw_key  
    api_key_hash = token.key     # hash

    app_name = api_key["appName"]

    sys_id = None
    if isSystem:
        sys_id = f"{app_name.strip().replace(' ', '-')}-{''.join(random.choices('0123456789', k=6))}"  # system Id

    try:
        print("Put entry in api keys table")

        # For delegate keys, don't store the hash initially (one-time retrieval)
        hash_to_store = "MIGRATED" if delegate else api_key_hash

        # Put (or update) the item for the specified user in the DynamoDB table
        response = table.put_item(
            Item={
                "api_owner_id": id,
                "owner": user,
                "apiKey": hash_to_store,
                "account": api_key["account"],
                "delegate": delegate,
                "systemId": sys_id,
                "active": True,
                "applicationName": app_name,
                "applicationDescription": api_key["appDescription"],
                "createdAt": timestamp,
                "lastAccessed": timestamp,
                "rateLimit": formatRateLimit(api_key["rateLimit"]),
                "expirationDate": api_key.get("expirationDate", None),
                "accessTypes": api_key["accessTypes"],
                "purpose": api_key.get("purpose", None),
            }
        )

        # Check if the response was successful
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
            print(f"API key for user {user} created successfully")
            return {
                "success": True,
                "data": {"id": id, 
                         "apiKey": None if delegate else api_key_raw,
                         "delegate": delegate},
                "message": "API key created successfully",
            }
        else:
            print(f"Failed to create API key for user {user}")
            return {"success": False, "message": "Failed to create API key"}
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred while saving API key for user {user}: {e}")
        return {
            "success": False,
            "message": f"An error occurred while saving API key: {str(e)}",
        }


@validated("update")
def update_api_keys_for_user(event, context, user, name, data):
    failed = []
    for item in data["data"]:
        response = update_api_key(item["apiKeyId"], item["updates"], user)
        if not response["success"]:
            failed.append(response["key_name"])

    return {"success": len(failed) == 0, "failedKeys": failed}


def update_api_key(item_id, updates, user):
    # Fetch the current state of the API key to ensure it is active and not expired
    key_name = "unknown"
    try:
        current_key = table.get_item(Key={"api_owner_id": item_id})
        key_data = current_key.get("Item", None)
        key_name = key_data["applicationName"]
        if (
            "Item" not in current_key
            or not key_data["active"]
            or is_expired(key_data["expirationDate"])
            or user != key_data["owner"]
        ):
            print("Failed at initial screening")
            return {
                "success": False,
                "error": "API key is inactive or expired or does not exist or you are unauthorized to make updates",
                "key_name": key_name,
            }
    except ClientError as e:
        return {"success": False, "error": str(e), "key_name": key_name}

    updatable_fields = {"rateLimit", "expirationDate", "account", "accessTypes"}
    update_expression = []
    expression_attribute_values = {}
    expression_attribute_names = {}
    print("Updates to perform on key: ", key_name)
    for field, value in updates.items():
        if field in updatable_fields:
            print("updates: ", field, "-", value)
        if field == "account":
            if not is_valid_account(value["id"]):
                warning = "Warning: Invalid COA string attached to account"
                print(warning)  # or use a logging mechanism
            # Continue with key creation despite the warning
        if field == "rateLimit":
            value = formatRateLimit(value)
        # Use attribute names to avoid conflicts with DynamoDB reserved keywords
        placeholder = f"#{field}"
        value_placeholder = f":{field}"
        update_expression.append(f"{placeholder} = {value_placeholder}")
        expression_attribute_names[placeholder] = field
        expression_attribute_values[value_placeholder] = value

    # Join the update expression and check if it's empty
    if not update_expression:
        return {
            "success": False,
            "error": "No valid fields provided for update",
            "key_name": key_name,
        }

    # Construct the full update expression
    final_update_expression = "SET " + ", ".join(update_expression)

    try:
        response = table.update_item(
            Key={"api_owner_id": item_id},
            UpdateExpression=final_update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ExpressionAttributeNames=expression_attribute_names,
            ReturnValues="UPDATED_NEW",
        )
        return {"success": True, "updated_attributes": response["Attributes"]}
    except ClientError as e:
        print("Updates save error: ", e)
        return {"success": False, "error": str(e), "key_name": key_name}


@validated("deactivate")
def deactivate_key(event, context, user, name, data):
    item_id = data["data"]["apiKeyId"]
    if not is_valid_id_format(item_id):
        return {"success": False, "error": "Invalid or missing API key ID parameter"}
    return deactivate_key_in_dynamo(user, item_id)


def deactivate_key_in_dynamo(user, key_id):
    try:
        response = table.get_item(Key={"api_owner_id": key_id})
        if "Item" in response:
            item = response["Item"]

            if item["owner"] == user or item["delegate"] == user:
                print("updating key")
                update_response = table.update_item(
                    Key={"api_owner_id": key_id},
                    UpdateExpression="SET active = :val",
                    ExpressionAttributeValues={":val": False},
                    ReturnValues="UPDATED_NEW",
                )

                # Check if the item was successfully updated
                if not update_response["Attributes"]["active"]:
                    print("successfully deactivated")
                    return {
                        "success": True,
                        "message": "API key successfully deactivated.",
                    }
                else:
                    return {
                        "success": False,
                        "error": "Unable to update value to False.",
                    }
            else:
                return {
                    "success": False,
                    "error": "Unauthorized to deactivate this API key",
                }
        else:
            return {"success": False, "error": "API key not found"}

    except Exception as e:
        # Handle potential errors
        print(f"An error occurred: {e}")
        return {"success": False, "error": "Failed to deactivate API key"}


def is_valid_id_format(id):
    print("Validating key id: ", id)
    regex = r"^[^/]+/(ownerKey|delegateKey|systemKey)/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    match = re.fullmatch(regex, id, re.IGNORECASE)
    return bool(match)


def is_expired(date_str):
    if not date_str:
        return False
    date = datetime.strptime(date_str, "%Y-%m-%d")
    return date <= datetime.now()


@validated("read")
def get_system_ids(event, context, current_user, name, data):
    print("Getting system-specific API keys from DynamoDB")
    try:
        # Use a Scan operation with a FilterExpression to find items where the user is the owner and systemId is not null
        response = table.scan(
            FilterExpression="#owner = :user and attribute_type(systemId, :type) and active = :active",
            ExpressionAttributeNames={
                "#owner": "owner"  # Use '#owner' to avoid reserved keyword conflicts
            },
            ExpressionAttributeValues={
                ":user": current_user,
                ":type": "S",  # Assuming systemId is a string attribute
                ":active": True,
            },
            ProjectionExpression="#owner, applicationName, lastAccessed, rateLimit, expirationDate, accessTypes, systemId",
        )
        # Check if any items were found
        if "Items" in response and response["Items"]:
            print(f"System API keys found for owner {current_user}")
            print(f"items {response['Items']}")
            return {"success": True, "data": response["Items"]}
        else:
            print(f"No active system API keys found for owner {current_user}")
            return {
                "success": True,
                "data": [],
                "message": "No active system API keys found.",
            }
    except Exception as e:
        # Handle potential errors
        print(
            f"An error occurred while retrieving system API keys for owner {current_user}: {e}"
        )
        return {"success": False, "message": str(e)}


@validated("read")
def get_documentation(event, context, current_user, name, data):
    print(f"Getting presigned download URL for user {current_user}")

    doc_presigned_url = generate_presigned_url(APIFile.PDF.value)
    csv_presigned_url = generate_presigned_url(APIFile.CSV.value)
    postman_presigned_url = generate_presigned_url(APIFile.JSON.value)

    res = {"success": True}
    if doc_presigned_url:
        res["doc_url"] = doc_presigned_url
    if csv_presigned_url:
        res["csv_url"] = csv_presigned_url
    if postman_presigned_url:
        res["postman_url"] = postman_presigned_url

    if len(res) > 1:
        return res
    else:
        print("Failed to retrieve a new presigned url")
        return {"success": False, "message": "Files not found"}


def generate_presigned_url(file):
    s3 = boto3.client("s3")
    try:
        return s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket_name,
                "Key": file,
                "ResponseContentDisposition": f"attachment; filename={file}",
            },
            ExpiresIn=7200,  # Expires in 3 hrs
        )
    except ClientError as e:
        print(f"Error generating presigned download URL for file {file}: {e}")
        return None


def formatRateLimit(rateLimit):
    if rateLimit.get("rate", None):
        rateLimit["rate"] = Decimal(str(rateLimit["rate"]))
    return rateLimit


@validated("upload")
def get_api_doc_presigned_urls(event, context, current_user, name, data):
    # verify they are an admin
    if not verify_user_as_admin(data["access_token"], "Upload API Documentation"):
        return {"success": False, "error": "Unable to authenticate user as admin"}
    data = data["data"]
    filename = data.get("filename", "")
    md5_content = data.get("content_md5", "")
    file_names = {
        APIFile.PDF.value: "application/pdf",
        APIFile.CSV.value: "text/csv",
        APIFile.JSON.value: "application/json",
    }
    print("Uploading: ", file_names[filename])
    if not filename in file_names.keys():
        return {"success": False, "error": "File name does not match the preset names."}

    try:
        config = Config(signature_version="s3v4")  # Force AWS Signature Version 4
        s3 = boto3.client("s3", config=config)
        presigned = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket_name,
                "Key": filename,
                "ContentType": file_names[filename],
                "ContentMD5": md5_content,
            },
            ExpiresIn=3600,
        )
        print("Presigned url generated")
        return {"success": True, "presigned_url": presigned}
    except ClientError as e:
        print(f"Error generating presigned upload URL: {e}")
        return {
            "success": False,
            "error": f"Error generating presigned upload URL: {e}",
        }


@validated("read")
def get_api_document_templates(event, context, current_user, name, data):
    templates_key = "Amplify_API_Templates.zip"

    try:
        # List objects in the bucket and check if templates file exists
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=templates_key)
        file_exists = response.get("Contents") and any(
            obj["Key"] == templates_key for obj in response.get("Contents", [])
        )

        if not file_exists:
            print("templates.zip does not exist in S3. Uploading now...")
            # Upload from local to S3
            try:

                file_path = os.path.abspath(
                    os.path.join(
                        os.path.dirname(__file__),
                        "..",
                        "api_documentation",
                        "templates",
                        "Amplify_API_Templates.zip",
                    )
                )

                with open(file_path, "rb") as f:
                    s3.put_object(
                        Bucket=bucket_name,
                        Key=templates_key,
                        Body=f,
                        ContentType="application/zip",  # Content type for a zip file
                    )
                print("Succesfully put templates.zip in the s3 bucket")
            except FileNotFoundError:
                print("Local templates.zip file not found in the Lambda package")
                return {
                    "success": False,
                    "message": "Local templates.zip file not found in the Lambda package",
                }
            except ClientError as e:
                print(f"Error uploading {templates_key} to S3: {e}")
                return {
                    "success": False,
                    "message": f"Error uploading {templates_key} to S3: {e}",
                }
        else:
            print("templates.zip exists in S3")

    except ClientError as e:
        print(f"Error checking for template in S3: {e}")
        return {"success": False, "message": f"Failed to check for template file: {e}"}

    # Now that the file should be in S3, generate the presigned URL
    presigned_url = generate_presigned_url(templates_key)
    if presigned_url:
        return {"success": True, "presigned_url": presigned_url}
    else:
        return {"success": False, "message": "Failed to generate presigned URL"}


@validated("rotate")
def rotate_api_key(event, context, user, name, data):
    """
    Rotate an API key by generating a new TokenV1 key and updating the hash in the database.
    """
    api_key_id = data["data"]["apiKeyId"]
    
    if not is_valid_id_format(api_key_id):
        return {"success": False, "error": "Invalid or missing API key ID parameter"}
    
    try:
        # First, retrieve the current key from DynamoDB
        print('Retrieve the item from DynamoDB for rotation')
        current_key = table.get_item(Key={"api_owner_id": api_key_id})
        
        if "Item" not in current_key:
            return {"success": False, "error": "API key not found"}
        
        key_data = current_key["Item"]
        delegate = key_data.get("delegate")
        
        # Authorization checks matching the old get_api_key logic:
        # - If it's a delegate key (has delegate), only the delegate can rotate it
        # - If it's not a delegate key (no delegate), only the owner can rotate it
        # - Owner cannot rotate delegate keys (keys they created for others)        
        if not ((delegate and delegate == user) or (not delegate and key_data["owner"] == user)):
            return {"success": False, "error": "Unauthorized to rotate this API key"}

        # Check if key is active and not expired
        if not key_data.get("active", False):
            return {"success": False, "error": "Cannot rotate inactive API key"}
        
        if is_expired(key_data.get("expirationDate")):
            return {"success": False, "error": "Cannot rotate expired API key"}
        
        # Generate new TokenV1 key
        new_token = TokenV1()
        new_api_key_raw = new_token.raw_key
        new_api_key_hash = new_token.key
        
        # Update the hash in the database
        update_response = table.update_item(
            Key={"api_owner_id": api_key_id},
            UpdateExpression="SET #apiKey = :apiKey",
            ExpressionAttributeNames={"#apiKey": "apiKey"},
            ExpressionAttributeValues={":apiKey": new_api_key_hash},
            ReturnValues="UPDATED_NEW",
        )
        
        print(f"API key rotated successfully for key ID: {api_key_id}")
        return {
            "success": True,
            "data": {"apiKey": new_api_key_raw},
            "message": "API key rotated successfully",
        }
        
    except Exception as e:
        print(f"An error occurred while rotating API key {api_key_id}: {e}")
        return {
            "success": False,
            "error": f"An error occurred while rotating API key: {str(e)}",
        }


#########################################################

# perfer to convert keys instead of rotating 
def backfill_api_keys_to_token_v1():
    """
    TERMINAL ONLY FUNCTION - Backfill old API keys to TokenV1 hash format.
    
    This function:
    1. Scans the entire API keys table
    2. Finds items that have apiKey values starting with 'amp-'
    3. Creates TokenV1 hash from the existing apiKey value
    4. Updates the apiKey column with the hash (keeping the same column name)
    
    This should only be run once during migration from old to new key format.
    """
    print("Starting backfill process for API keys to TokenV1 format...")
    
    try:
        # Scan the entire table to find items with apiKey column that starts with 'amp-'
        scan_kwargs = {
            'FilterExpression': 'begins_with(apiKey, :prefix)',
            'ExpressionAttributeValues': {':prefix': 'amp-'},
            'ProjectionExpression': 'api_owner_id, apiKey, applicationName, #owner',
            'ExpressionAttributeNames': {'#owner': 'owner'}
        }
        
        items_processed = 0
        items_updated = 0
        items_failed = 0
        
        while True:
            response = table.scan(**scan_kwargs)
            items = response.get('Items', [])
            
            if not items:
                print("No more items with apiKey starting with 'amp-' found.")
                break
            
            print(f"Processing batch of {len(items)} items...")
            
            for item in items:
                items_processed += 1
                api_key_id = item['api_owner_id']
                old_api_key = item['apiKey']
                app_name = item.get('applicationName', 'Unknown')
                owner = item.get('owner', 'Unknown')
                
                try:
                    print(f"Processing key: {app_name} (Owner: {owner}, ID: {api_key_id})")
                    
                    # Create TokenV1 from existing API key
                    token = TokenV1(old_api_key)
                    new_hash = token.key
                    
                    
                    # Update the apiKey column with the hash
                    update_response = table.update_item(
                        Key={'api_owner_id': api_key_id},
                        UpdateExpression='SET apiKey = :hash',
                        ExpressionAttributeValues={':hash': new_hash},
                        ReturnValues='UPDATED_NEW'
                    )
                    
                    items_updated += 1
                    print(f"✅ Successfully updated key: {app_name}")
                    
                except Exception as e:
                    items_failed += 1
                    print(f"❌ Failed to update key: {app_name} - Error: {str(e)}")
                    continue
            
            # Check if there are more items to scan
            if 'LastEvaluatedKey' not in response:
                break
            
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        
        print("\n" + "="*50)
        print("BACKFILL PROCESS COMPLETED")
        print("="*50)
        print(f"Total items processed: {items_processed}")
        print(f"Successfully updated: {items_updated}")
        print(f"Failed updates: {items_failed}")
        print("="*50)
        
        return {
            "success": True,
            "processed": items_processed,
            "updated": items_updated,
            "failed": items_failed
        }
        
    except Exception as e:
        print(f"❌ Critical error during backfill process: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "processed": items_processed,
            "updated": items_updated,
            "failed": items_failed
        }


def clear_amp_api_keys():
    """
    TERMINAL ONLY FUNCTION - Clear API keys that start with 'amp-'.
    
    This function:
    1. Scans the entire API keys table
    2. Finds items that have apiKey values starting with 'amp-'
    3. Sets the apiKey column to "MIGRATED" to indicate these keys have been processed
    
    Use this to clean up any remaining 'amp-' prefixed keys after migration.
    """
    print("Starting process to clear API keys that start with 'amp-'...")
    
    try:
        # Scan the entire table to find items with apiKey column that starts with 'amp-'
        scan_kwargs = {
            'FilterExpression': 'begins_with(apiKey, :prefix)',
            'ExpressionAttributeValues': {':prefix': 'amp-'},
            'ProjectionExpression': 'api_owner_id, apiKey, applicationName, #owner',
            'ExpressionAttributeNames': {'#owner': 'owner'}
        }
        
        items_processed = 0
        items_updated = 0
        items_failed = 0
        
        while True:
            response = table.scan(**scan_kwargs)
            items = response.get('Items', [])
            
            if not items:
                print("No more items with apiKey starting with 'amp-' found.")
                break
            
            print(f"Processing batch of {len(items)} items...")
            
            for item in items:
                items_processed += 1
                api_key_id = item['api_owner_id']
                old_api_key = item['apiKey']
                app_name = item.get('applicationName', 'Unknown')
                owner = item.get('owner', 'Unknown')
                
                try:
                    print(f"Clearing key: {app_name} (Owner: {owner}, ID: {api_key_id})")
                    print(f"  Current apiKey: {old_api_key[:10]}...")
                    
                    # Set the apiKey to "MIGRATED" to indicate it has been processed
                    update_response = table.update_item(
                        Key={'api_owner_id': api_key_id},
                        UpdateExpression='SET apiKey = :migrated',
                        ExpressionAttributeValues={':migrated': 'MIGRATED'},
                        ReturnValues='UPDATED_NEW'
                    )
                    
                    items_updated += 1
                    print(f"✅ Successfully cleared key: {app_name}")
                    
                except Exception as e:
                    items_failed += 1
                    print(f"❌ Failed to clear key: {app_name} - Error: {str(e)}")
                    continue
            
            # Check if there are more items to scan
            if 'LastEvaluatedKey' not in response:
                break
            
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        
        print("\n" + "="*50)
        print("CLEAR AMP- KEYS PROCESS COMPLETED")
        print("="*50)
        print(f"Total items processed: {items_processed}")
        print(f"Successfully cleared: {items_updated}")
        print(f"Failed clears: {items_failed}")
        print("="*50)
        
        return {
            "success": True,
            "processed": items_processed,
            "updated": items_updated,
            "failed": items_failed
        }
        
    except Exception as e:
        print(f"❌ Critical error during clear process: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "processed": items_processed,
            "updated": items_updated,
            "failed": items_failed
        }


def check_for_amp_api_keys():
    """
    TERMINAL ONLY FUNCTION - Check if there are still API keys starting with 'amp-'.
    
    This function:
    1. Scans the entire API keys table
    2. Looks for items that have apiKey values starting with 'amp-'
    3. Returns True if any are found, False otherwise
    
    Use this to verify that migration/cleanup is complete.
    """
    print("Checking for API keys that start with 'amp-'...")
    
    try:
        # Scan the table to find items with apiKey column that starts with 'amp-'
        response = table.scan(
            FilterExpression='begins_with(apiKey, :prefix)',
            ExpressionAttributeValues={':prefix': 'amp-'},
            ProjectionExpression='api_owner_id, apiKey, applicationName, #owner',
            ExpressionAttributeNames={'#owner': 'owner'},
            Limit=1  # We only need to know if any exist
        )
        
        items = response.get('Items', [])
        
        if items:
            print(f"❌ Found {len(items)} item(s) with apiKey starting with 'amp-'")
            for item in items:
                app_name = item.get('applicationName', 'Unknown')
                owner = item.get('owner', 'Unknown')
                api_key = item.get('apiKey', '')
                print(f"  - {app_name} (Owner: {owner}) - Key: {api_key[:10]}...")
            
            # Check if there are more items
            if 'LastEvaluatedKey' in response:
                print("  ... and potentially more items exist")
            
            return True
        else:
            print("✅ No API keys starting with 'amp-' found")
            return False
            
    except Exception as e:
        print(f"❌ Error checking for amp- keys: {str(e)}")
        return False


def run_backfill_from_terminal():
    """
    Terminal entry point for running the backfill process.
    This function should only be called from terminal/CLI, not from API endpoints.
    """
    print("🚀 Starting API Key Migration to TokenV1...")
    print("⚠️  WARNING: This will modify your DynamoDB table!")
    print("⚠️  Make sure you have a backup before proceeding!")
    
    # Ask for confirmation
    confirmation = input("\nDo you want to proceed with the migration? (yes/no): ").lower().strip()
    
    if confirmation != 'yes':
        print("❌ Migration cancelled by user.")
        return
    
    print("\n🔄 Starting migration process...")
    result = backfill_api_keys_to_token_v1()
    
    if result["success"]:
        print(f"\n✅ Migration completed successfully!")
        print(f"   - Processed: {result['processed']} items")
        print(f"   - Updated: {result['updated']} items")
        print(f"   - Failed: {result['failed']} items")
    else:
        print(f"\n❌ Migration failed: {result['error']}")
        print(f"   - Processed: {result.get('processed', 0)} items")
        print(f"   - Updated: {result.get('updated', 0)} items")
        print(f"   - Failed: {result.get('failed', 0)} items")


def run_clear_amp_keys_from_terminal():
    """
    Terminal entry point for clearing amp- prefixed API keys.
    This function should only be called from terminal/CLI, not from API endpoints.
    """
    print("🧹 Starting API Key Cleanup (clearing amp- prefixed keys)...")
    print("⚠️  WARNING: This will modify your DynamoDB table!")
    print("⚠️  Make sure you have a backup before proceeding!")
    
    # Ask for confirmation
    confirmation = input("\nDo you want to proceed with clearing amp- keys? (yes/no): ").lower().strip()
    
    if confirmation != 'yes':
        print("❌ Cleanup cancelled by user.")
        return
    
    print("\n🔄 Starting cleanup process...")
    result = clear_amp_api_keys()
    
    if result["success"]:
        print(f"\n✅ Cleanup completed successfully!")
        print(f"   - Processed: {result['processed']} items")
        print(f"   - Cleared: {result['updated']} items")
        print(f"   - Failed: {result['failed']} items")
    else:
        print(f"\n❌ Cleanup failed: {result['error']}")
        print(f"   - Processed: {result.get('processed', 0)} items")
        print(f"   - Cleared: {result.get('updated', 0)} items")
        print(f"   - Failed: {result.get('failed', 0)} items")


def run_check_amp_keys_from_terminal():
    """
    Terminal entry point for checking if amp- prefixed API keys still exist.
    This function should only be called from terminal/CLI, not from API endpoints.
    """
    print("🔍 Checking for remaining amp- prefixed API keys...")
    
    has_amp_keys = check_for_amp_api_keys()
    
    if has_amp_keys:
        print("\n❌ There are still API keys starting with 'amp-' in the table.")
        print("   Consider running the migration or cleanup process.")
    else:
        print("\n✅ No API keys starting with 'amp-' found. Migration appears complete!")


# Terminal execution check
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        if command == "migrate":
            run_backfill_from_terminal()
        elif command == "clear":
            run_clear_amp_keys_from_terminal()
        elif command == "check":
            run_check_amp_keys_from_terminal()
        else:
            print("Usage: python core.py [migrate|clear|check]")
            print("  migrate - Convert amp- prefixed keys to TokenV1 hashes")
            print("  clear   - Clear amp- prefixed keys (set to empty string)")
            print("  check   - Check if amp- prefixed keys still exist")
    else:
        print("Usage: python core.py [migrate|clear|check]")
        print("  migrate - Convert amp- prefixed keys to TokenV1 hashes")
        print("  clear   - Clear amp- prefixed keys (set to empty string)")
        print("  check   - Check if amp- prefixed keys still exist")
