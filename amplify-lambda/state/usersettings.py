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

from pycommon.logger import getLogger
logger = getLogger("user-settings")

tableName = os.environ["SHARES_DYNAMODB_TABLE"] #Marked for future deletion
dynamodb = boto3.resource("dynamodb")
users_table = dynamodb.Table(tableName)

def get_app_id() -> str:
    return "amplify-user-settings"

def _normalize_settings(settings):
    """
    CONSERVATIVE normalization - only fix ACTUAL corruption, not normal data.
    Prevents TypeError: property 0 is non-configurable and can't be deleted.
    Only applies fixes when there's clear evidence of migration corruption.
    """
    logger.info("_normalize_settings called with: %s", settings)
    
    if not isinstance(settings, dict):
        logger.info("Settings is not dict, returning as-is: %s", type(settings))
        return settings
    
    normalized = settings.copy()
    corruption_detected = False
    
    # ONLY fix featureOptions if it's clearly corrupted (numeric keys)
    if "featureOptions" in normalized:
        feature_options = normalized["featureOptions"]
        logger.info("Processing featureOptions - type: %s, value: %s", type(feature_options), feature_options)
        
        if isinstance(feature_options, dict):
            # Check for clear corruption: ALL keys are numeric strings (array-like)
            all_keys = list(feature_options.keys())
            logger.info("featureOptions keys: %s", all_keys)
            if all_keys and all(str(k).isdigit() for k in all_keys):
                logger.warning("CORRUPTION DETECTED: featureOptions has only numeric keys %s, resetting...", all_keys)
                normalized["featureOptions"] = {
                    "includeArtifacts": True,
                    "includeFocusedMessages": True, 
                    "includePluginSelector": True,
                    "includeHighlighter": True,
                    "includeMemory": True
                }
                corruption_detected = True
            else:
                logger.info("featureOptions looks normal, keeping as-is")
        elif isinstance(feature_options, list):
            logger.warning("CORRUPTION DETECTED: featureOptions is array %s, converting to object...", feature_options)
            normalized["featureOptions"] = {
                "includeArtifacts": True,
                "includeFocusedMessages": True,
                "includePluginSelector": True, 
                "includeHighlighter": True,
                "includeMemory": True
            }
            corruption_detected = True
        elif isinstance(feature_options, str):
            logger.warning("CORRUPTION DETECTED: featureOptions is string %s, attempting to parse...", feature_options)
            # Try to parse the Python dict string
            try:
                import ast
                parsed_features = ast.literal_eval(feature_options)
                if isinstance(parsed_features, dict):
                    normalized["featureOptions"] = parsed_features
                    corruption_detected = True
                    logger.info("Successfully parsed featureOptions string to dict: %s", parsed_features)
                else:
                    logger.warning("Parsed featureOptions is not a dict: %s", type(parsed_features))
            except Exception as e:
                logger.error("Failed to parse featureOptions string: %s", e)
        else:
            logger.warning("featureOptions has unexpected type %s: %s", type(feature_options), feature_options)
    
    # ONLY fix hiddenModelIds if it's not an array
    if "hiddenModelIds" in normalized:
        hidden_models = normalized["hiddenModelIds"]
        logger.info("Processing hiddenModelIds - type: %s, value: %s", type(hidden_models), hidden_models)
        if not isinstance(hidden_models, list):
            logger.warning("CORRUPTION DETECTED: hiddenModelIds is not array: %s, converting...", type(hidden_models))
            normalized["hiddenModelIds"] = []
            corruption_detected = True
        else:
            logger.info("hiddenModelIds looks normal, keeping as-is")
    
    if not corruption_detected:
        logger.info("No corruption detected in settings, returning original data")
        return settings  # Return original to preserve exact format
    
    logger.info("Corruption detected, returning normalized: %s", normalized)
    return normalized


@required_env_vars({
    "SHARES_DYNAMODB_TABLE": [DynamoDBOperation.SCAN], #Marked for future deletion
})
@validated("get")
def get_settings(event, context, current_user, name, data):
    try:
        # Check USER_STORAGE_TABLE first (migrated settings)
        try:
            app_id = get_app_id()
            logger.info("Fetching settings for user %s with app_id %s", current_user, app_id)
            user_storage_data = handle_get_item(current_user, app_id, "user-settings", "user-settings")
            
            logger.info("Raw user_storage_data: %s", user_storage_data)
            
            if user_storage_data and "data" in user_storage_data and "settings" in user_storage_data["data"]:
                logger.info("Settings found for user %s in USER_STORAGE_TABLE", current_user)
                settings = user_storage_data["data"]["settings"]
                logger.info("Raw settings before normalization: %s", settings)
                logger.info("Settings featureOptions type: %s, value: %s", 
                           type(settings.get("featureOptions")), settings.get("featureOptions"))
                logger.info("Settings hiddenModelIds type: %s, value: %s", 
                           type(settings.get("hiddenModelIds")), settings.get("hiddenModelIds"))
                
                normalized_settings = _normalize_settings(settings)
                logger.info("Normalized settings: %s", normalized_settings)
                return {"success": True, "data": normalized_settings}
        except Exception as e:
            logger.debug("No migrated settings found for user %s: %s", current_user, e)
        
        # Fallback to SHARES_DYNAMODB_TABLE (legacy settings)
        response = users_table.scan(FilterExpression=Attr("user").eq(current_user))
        items = response.get("Items", [])

        if items:
            # Assuming the first match is the correct one
            settings_item = items[0]
            logger.info("Settings found for user %s in SHARES_DYNAMODB_TABLE", current_user)
            settings = settings_item.get("settings", None)
            normalized_settings = _normalize_settings(settings) if settings else None
            return {"success": True, "data": normalized_settings}
        else:
            # No settings found for the user
            logger.debug("No settings found for user %s", current_user)
            return {"success": True, "data": None}
    except Exception as e:
        # Handle potential errors
        logger.error(
            "An error occurred while retrieving settings for user %s: %s",
            current_user, e
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
        
        app_id = get_app_id()
        settings_data = {"settings": settings}
        
        result = handle_put_item(current_user, app_id, "user-settings", "user-settings", settings_data)
        if result and "uuid" in result:
            logger.info("Settings for user %s saved successfully", current_user)
            return {"success": True, "message": "Settings saved successfully"}
        else:
            logger.error("Failed to save settings for user %s", current_user)
            return {"success": False, "message": "Failed to save settings"}
    except Exception as e:
        logger.error("An error occurred while saving settings for user %s: %s", current_user, e)
        return {"success": False, "error": f"Error occurred while saving settings: {e}"}
