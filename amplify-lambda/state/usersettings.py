import time
import boto3
import os
import uuid
from boto3.dynamodb.conditions import Key, Attr

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

tableName = os.environ["DYNAMODB_TABLE"]
dynamodb = boto3.resource("dynamodb")
users_table = dynamodb.Table(tableName)


@validated("get")
def get_settings(event, context, current_user, name, data):
    try:
        # Step 1: Query using the secondary index to get the primary key
        response = users_table.scan(FilterExpression=Attr("user").eq(current_user))

        items = response.get("Items", [])

        if items:
            # Assuming the first match is the correct one
            settings_item = items[0]
            print(f"Settings found for user {current_user}")
            return {"success": True, "data": settings_item.get("settings", None)}
        else:
            # No settings found for the user
            print(f"No settings found for user {current_user}")
            return {"success": True, "data": None}
    except Exception as e:
        # Handle potential errors
        print(
            f"An error occurred while retrieving settings for user {current_user}: {e}"
        )
        return {"success": False, "error": f"Error occurred: {e}"}


@validated("save")
def save_settings(event, context, user, name, data):
    # settings/save
    settings_data = data["data"]
    return save_settings_for_user(user, settings_data["settings"])


def save_settings_for_user(current_user, settings):
    try:
        # Step 1: Query using the secondary index to get the primary key
        response = users_table.scan(FilterExpression=Attr("user").eq(current_user))

        items = response.get("Items", [])
        timestamp = int(time.time() * 1000)

        if not items:
            # No item found with user and name, create a new item
            id_key = "{}/{}".format(
                current_user, str(uuid.uuid4())
            )  # add the user's name to the key in DynamoDB
            new_item = {
                "id": id_key,
                "user": current_user,
                "name": "/state/share",
                "data": [],
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
            response = users_table.put_item(Item=new_item)

        else:
            # Otherwise, update the existing item
            user_id = items[0]["id"]

            response = users_table.update_item(
                Key={"id": user_id},
                UpdateExpression="set settings = :s",
                ExpressionAttributeValues={":s": settings},
                ReturnValues="UPDATED_NEW",
            )

        # Check if the response was successful
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") in [200, 204]:
            print(f"Settings for user {current_user} saved successfully")
            return {"success": True, "message": "Settings saved successfully"}
        else:
            print(f"Failed to save settings for user {current_user}")
            return {"success": False, "message": "Failed to save settings"}
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred while saving settings for user {current_user}: {e}")
        return {"success": False, "error": "Error occured while saving settings: {e}"}
