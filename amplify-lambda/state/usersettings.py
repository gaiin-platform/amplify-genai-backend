import time
import boto3
import os
import uuid
from boto3.dynamodb.conditions import Key, Attr

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation
)
from state.user_data import handle_get_item, handle_put_item

setup_validated(rules, get_permission_checker)

tableName = os.environ["SHARES_DYNAMODB_TABLE"]
dynamodb = boto3.resource("dynamodb")
users_table = dynamodb.Table(tableName)

def get_app_id(current_user: str) -> str:
    return f"{current_user}#amplify-user-settings"


@required_env_vars({
    "SHARES_DYNAMODB_TABLE": [DynamoDBOperation.SCAN],
})
@validated("get")
def get_settings(event, context, current_user, name, data):
    try:
        # Check USER_STORAGE_TABLE first (migrated settings)
        try:
            app_id = get_app_id(current_user)
            user_storage_data = handle_get_item(current_user, app_id, "user-settings", "user-settings")
            
            if user_storage_data and "data" in user_storage_data and "settings" in user_storage_data["data"]:
                print(f"Settings found for user {current_user} in USER_STORAGE_TABLE")
                return {"success": True, "data": user_storage_data["data"]["settings"]}
        except Exception as e:
            print(f"No migrated settings found for user {current_user}: {e}")
        
        # Fallback to SHARES_DYNAMODB_TABLE (legacy settings)
        response = users_table.scan(FilterExpression=Attr("user").eq(current_user))
        items = response.get("Items", [])

        if items:
            # Assuming the first match is the correct one
            settings_item = items[0]
            print(f"Settings found for user {current_user} in SHARES_DYNAMODB_TABLE")
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


# @required_env_vars({
#     "SHARES_DYNAMODB_TABLE": [DynamoDBOperation.SCAN, DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM],
# })
@validated("save")
def save_settings(event, context, user, name, data):
    # settings/save
    settings_data = data["data"]
    access_token = data["access_token"]
    return save_settings_for_user(user, settings_data["settings"], access_token)


def save_settings_for_user(current_user, settings, access_token=None):
    try:
        if not access_token:
            return {"success": False, "error": "Access token required"}
        
        app_id = get_app_id(current_user)
        settings_data = {"settings": settings}
        
        result = handle_put_item(current_user, app_id, "user-settings", "user-settings", settings_data)
        if result and "uuid" in result:
            print(f"Settings for user {current_user} saved successfully")
            return {"success": True, "message": "Settings saved successfully"}
        else:
            print(f"Failed to save settings for user {current_user}")
            return {"success": False, "message": "Failed to save settings"}
    except Exception as e:
        print(f"An error occurred while saving settings for user {current_user}: {e}")
        return {"success": False, "error": f"Error occurred while saving settings: {e}"}
