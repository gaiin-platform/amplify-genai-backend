import json
import os
import uuid
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
import boto3
from decimal import Decimal

from enum import Enum

from service.supported_models import update_supported_models, get_supported_models
from pycommon.api.ast_admin_groups import (
    get_all_ast_admin_groups,
    update_ast_admin_groups,
)
from pycommon.api.amplify_users import are_valid_amplify_users
from pycommon.api.ops_reqs import get_all_op
from base_feature_flags import feature_flags
from datetime import datetime
from botocore.config import Config
from service.user_services import is_in_amp_group
from pycommon.const import NO_RATE_LIMIT, APIAccessType
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ADMIN.value])

# Setup AWS DynamoDB access
dynamodb = boto3.resource("dynamodb")
admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])


class AdminConfigTypes(Enum):
    ADMINS = "admins"
    EMBEDDINGS = "embeddings"
    FEATURE_FLAGS = "featureFlags"
    APP_VARS = "applicationVariables"
    APP_SECRETS = "applicationSecrets"
    OPENAI_ENDPOINTS = "openaiEndpoints"
    AVAILABLE_MODELS = "supportedModels"
    DEFAULT_MODELS = "defaultModels"
    AST_ADMIN_GROUPS = "assistantAdminGroups"
    PPTX_TEMPLATES = "powerPointTemplates"
    AMPLIFY_GROUPS = "amplifyGroups"
    RATE_LIMIT = "rateLimit"
    PROMPT_COST_ALERT = "promtCostAlert"
    OPS = "ops"
    INTEGRATIONS = "integrations"
    EMAIL_SUPPORT = "emailSupport"
    DEFAULT_CONVERSATION_STORAGE = "defaultConversationStorage"
    AI_EMAIL_DOMAIN = 'aiEmailDomain'


# Map config_type to the corresponding secret name in Secrets Manager
secret_name_map = {
    AdminConfigTypes.APP_VARS: os.environ["APP_ARN_NAME"],
    AdminConfigTypes.APP_SECRETS: os.environ["SECRETS_ARN_NAME"],
    AdminConfigTypes.OPENAI_ENDPOINTS: os.environ["LLM_ENDPOINTS_SECRETS_NAME_ARN"],
}


# flow, list of feature flags that are user based
@validated(op="update")
def update_configs(event, context, current_user, name, data):
    if not authorized_admin(current_user):
        return {"success": False, "message": "User is not an authorized admin"}
    token = data["access_token"]
    data = data["data"]
    configs = data["configurations"]

    # Pre-validate all users across all configurations with a single API call
    invalid_users_set = validate_users_across_configs(configs, token)

    response_data = {}
    for config in configs:
        config_type_val = config["type"]
        config_type = AdminConfigTypes(config_type_val)
        update_data = config["data"]  # The data to update
        print(f"\nUpdating: {config_type}")
        print(f"Data: {update_data}\n")

        update_result = handle_update_config(config_type, update_data, token, invalid_users_set)

        if update_result.get("success"):
            # Log the change
            log_item(
                config_type=config_type_val,
                username=current_user,
                details={"updated_data": json.dumps(update_data)},
            )
        response_data[config_type_val] = update_result

    if all(value.get("success") == True for value in response_data.values()):
        print("All successful")
        print(response_data)
        return {"success": True, "data": response_data}
    return {
        "success": False,
        "data": response_data,
        "message": "Some or all configuration updates were unsuccessful",
    }

def validate_users_across_configs(configs, token):
    """
    Efficiently validate users across multiple configurations with a single API call.
    
    Args:
        configs: List of configuration objects with 'type' and 'data' fields
        token: Authentication token
    
    Returns:
        set: Set of invalid users to avoid repeated API calls
    """
    all_users = set()
    
    # Collect all users from all configurations that need validation
    for config in configs:
        config_type = AdminConfigTypes(config["type"])
        update_data = config["data"]
        
        if config_type == AdminConfigTypes.ADMINS:
            all_users.update(update_data)
        elif config_type == AdminConfigTypes.AMPLIFY_GROUPS:
            for group_data in update_data.values():
                all_users.update(group_data.get("members", []))
        elif config_type == AdminConfigTypes.FEATURE_FLAGS:
            for feature_data in update_data.values():
                all_users.update(feature_data.get("userExceptions", []))
    
    # Make single API call if there are users to validate
    if all_users:
        _, invalid_users = are_valid_amplify_users(token, list(all_users))
        return set(invalid_users)
    
    return set()


def filter_valid_users(users, invalid_users_set):
    """Helper function to filter out invalid users from a list."""
    return [user for user in users if user not in invalid_users_set]


def validate_and_filter_users_in_config(config_type, update_data, invalid_users_set):
    """
    Filter users in a configuration object using pre-computed invalid users set.
    
    Args:
        config_type: Type of configuration
        update_data: Data to update
        invalid_users_set: Pre-computed set of invalid users
    """
    if config_type == AdminConfigTypes.ADMINS:
        return filter_valid_users(update_data, invalid_users_set)
            
    elif config_type == AdminConfigTypes.AMPLIFY_GROUPS:
        for group_data in update_data.values():
            users = group_data.get("members", [])
            if users:
                group_data["members"] = filter_valid_users(users, invalid_users_set)
        return update_data
        
    elif config_type == AdminConfigTypes.FEATURE_FLAGS:
        for feature_data in update_data.values():
            users = feature_data.get("userExceptions", [])
            if users:
                feature_data["userExceptions"] = filter_valid_users(users, invalid_users_set)
        return update_data
    
    # For config types that don't need user validation
    return update_data


def handle_update_config(config_type, update_data, token, invalid_users_set):
    """
    Handle configuration updates with pre-computed invalid users set.
    """

    # Handle non-user validation configs
    match config_type:
        case ( AdminConfigTypes.ADMINS
              | AdminConfigTypes.AMPLIFY_GROUPS
              | AdminConfigTypes.FEATURE_FLAGS ):
            processed_data = validate_and_filter_users_in_config(config_type, update_data, invalid_users_set)
            
            # Special handling for ADMINS - check if we have valid users
            if config_type == AdminConfigTypes.ADMINS and len(processed_data) == 0:
                return {"success": False, "message": "No valid users as admins."}
            
            return update_admin_config_data(config_type.value, processed_data)
        
        case ( AdminConfigTypes.RATE_LIMIT
            | AdminConfigTypes.PROMPT_COST_ALERT
            | AdminConfigTypes.INTEGRATIONS
            | AdminConfigTypes.EMAIL_SUPPORT
            | AdminConfigTypes.AI_EMAIL_DOMAIN
            | AdminConfigTypes.DEFAULT_CONVERSATION_STORAGE
            | AdminConfigTypes.DEFAULT_MODELS ):
            print(f"Updating {config_type.value} - {update_data}")
            return update_admin_config_data(config_type.value, update_data)

        case AdminConfigTypes.AVAILABLE_MODELS:
            return update_supported_models(token, {"models": update_data})

        case AdminConfigTypes.AST_ADMIN_GROUPS:
            return update_ast_admin_groups(token, {"groups": update_data})

        case AdminConfigTypes.PPTX_TEMPLATES:
            return update_pptx_data(config_type.value, update_data)

        case (AdminConfigTypes.APP_VARS
            | AdminConfigTypes.APP_SECRETS
            | AdminConfigTypes.OPENAI_ENDPOINTS):
            region_name = os.environ.get("AWS_REGION", "us-east-1")
            if config_type in secret_name_map:
                secret_name = secret_name_map[config_type]
                return update_secret(secret_name, region_name, update_data)
            return {"success": False, "message": "Invalid Secret Name"}

        case _:
            print("Unknown configuration type")
            return {"success": False, "message": "Unknown configuration type"}


def convert_floats_to_decimal(obj):
    """
    Recursively convert float values to Decimal for DynamoDB compatibility.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {key: convert_floats_to_decimal(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    else:
        return obj


def update_admin_config_data(config_type, update_data):
    try:
        type_value = config_type
        # Convert any float values to Decimal for DynamoDB compatibility
        processed_data = convert_floats_to_decimal(update_data)
        
        admin_table.put_item(
            Item={
                "config_id": type_value,
                "data": processed_data,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {"success": True, "data": f"{type_value} updated successfully."}
    except Exception as e:
        print(f"Error updating {type_value}: {str(e)}")
        return {"success": False, "message": f"Error updating {type_value}: {str(e)}"}


def get_secret(secret_name, region_name):
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region_name)

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        secret_string = get_secret_value_response["SecretString"]
        secret_dict = json.loads(secret_string)
        return secret_dict
    except ClientError as e:
        print(f"Error getting secret: {e}")
        return None


def update_secret(secret_name, region_name, update_dict):
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region_name)

    try:
        # Get the current secret value
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        secret_string = get_secret_value_response["SecretString"]
        secret_dict = json.loads(secret_string)

        # Update the secret dict with the new values
        secret_dict.update(update_dict)

        # Convert back to JSON string
        new_secret_string = json.dumps(secret_dict)

        # Put the updated secret
        client.put_secret_value(SecretId=secret_name, SecretString=new_secret_string)

        return {"success": True}
    except ClientError as e:
        # Handle exceptions
        return {"success": False, "message": str(e)}


def update_pptx_data(pptx_type, update_data):
    try:
        config_item = admin_table.get_item(Key={"config_id": pptx_type})
        if not "Item" in config_item:
            return {
                "success": False,
                "message": "Error getting {pptx_type} form the table",
            }

        existing_templates = config_item["Item"]["data"]
        # Create a Dictionary of Existing Templates
        existing_templates_dict = {
            template["name"]: template for template in existing_templates
        }

        # Update Templates
        for updated_template in update_data:
            name = updated_template["name"]
            if name in existing_templates_dict:
                existing_template = existing_templates_dict[name]
                # Update only the specified fields
                for key in ["isAvailable", "amplifyGroups"]:
                    if key in updated_template:
                        existing_template[key] = updated_template[key]
            else:
                existing_templates_dict[name] = {
                    "name": name,
                    "isAvailable": updated_template.get("isAvailable", False),
                    "amplifyGroups": updated_template.get("amplifyGroups", []),
                }

        # Save Updated Templates Back to the admin_table
        updated_templates = list(existing_templates_dict.values())
        return update_admin_config_data(pptx_type, updated_templates)

    except Exception as e:
        return {"success": False, "message": f"Error updating {pptx_type}: {str(e)}"}


@validated(op="read")
def get_configs(event, context, current_user, name, data):
    if not authorized_admin(current_user):
        return {"success": False, "message": "User is not an authorized admin"}

    query_params = event.get("queryStringParameters", {})
    lazy_load = int(query_params.get("lazy_load", "0"))

    configurations = {}

    if lazy_load:
        print("Loading admin table configs only")
        dynamo_config_types = [
            AdminConfigTypes.FEATURE_FLAGS,
            AdminConfigTypes.ADMINS,
            AdminConfigTypes.PPTX_TEMPLATES,
            AdminConfigTypes.AMPLIFY_GROUPS,
            AdminConfigTypes.RATE_LIMIT,
            AdminConfigTypes.PROMPT_COST_ALERT,
            AdminConfigTypes.INTEGRATIONS,
            AdminConfigTypes.EMAIL_SUPPORT,
            AdminConfigTypes.AI_EMAIL_DOMAIN,
            AdminConfigTypes.DEFAULT_CONVERSATION_STORAGE,
            AdminConfigTypes.DEFAULT_MODELS,
        ]

        for config_type in dynamo_config_types:
            try:
                # Attempt to retrieve the configuration from DynamoDB
                config_item = admin_table.get_item(Key={"config_id": config_type.value})
                new_data = None
                if "Item" in config_item:
                    new_data = config_item["Item"]["data"]
                    if config_type == AdminConfigTypes.FEATURE_FLAGS:
                        # Check if any base feature flags are missing and add them
                        missing_base_flags = check_and_update_missing_base_flags(
                            new_data
                        )
                        if missing_base_flags:
                            new_data.update(missing_base_flags)
                            print(
                                f"Added missing base feature flags: {list(missing_base_flags.keys())}"
                            )
                else:
                    # Configuration does not exist, initialize it
                    new_data = initialize_config(config_type)
                configurations[config_type.value] = new_data

            except Exception as e:
                print(f"Error retrieving or initializing {config_type.value}: {str(e)}")
                return {
                    "success": False,
                    "message": f"Error retrieving or initializing {config_type.value}: {str(e)}",
                }

    else:
        print("Loading remaining configs only")

        region_name = os.environ.get("AWS_REGION", "us-east-1")
        # secrets manager info
        for config_type, secret_name in secret_name_map.items():
            try:
                secret_value = get_secret(secret_name, region_name)
                configurations[config_type.value] = secret_value
            except ClientError as e:
                print(f"Error retrieving {config_type.value}: {str(e)}")
        # dyanmo table rows

        # print(data)
        token = data["access_token"]

        supported_models_result = get_supported_models(token)
        configurations[AdminConfigTypes.AVAILABLE_MODELS.value] = (
            supported_models_result["data"]
            if supported_models_result.get("success")
            else None
        )

        ast_admin_groups_result = get_all_ast_admin_groups(token)
        configurations[AdminConfigTypes.AST_ADMIN_GROUPS.value] = (
            ast_admin_groups_result["data"]
            if ast_admin_groups_result.get("success")
            else None
        )

        ops_result = get_all_op(token)
        configurations[AdminConfigTypes.OPS.value] = (
            ops_result["data"] if ops_result.get("success") else None
        )

    return {"success": True, "data": configurations}


def initialize_config(config_type):
    print("Initializing data: ", config_type.value)
    item = {
        "config_id": config_type.value,
        "data": None,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    if config_type == AdminConfigTypes.ADMINS:
        # Get ADMINS env var
        admins_env = os.environ.get("ADMINS", "")
        # Parse it into a list of strings
        admins_list = [
            admin.strip() for admin in admins_env.split(",") if admin.strip()
        ]

        item["data"] = admins_list

    elif config_type == AdminConfigTypes.FEATURE_FLAGS:
        # Transform feature_flags.FEATURE_FLAGS into required format
        transformed_flags = {}
        for feature_name, enabled in feature_flags.FEATURE_FLAGS.items():
            transformed_flags[feature_name] = {
                "enabled": enabled,
                "userExceptions": [],
                "amplifyGroupExceptions": [],
            }

        item["data"] = transformed_flags

    elif config_type == AdminConfigTypes.PPTX_TEMPLATES:
        # Initialize PPTX_TEMPLATES
        output_bucket_name = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]
        s3_client = boto3.client("s3")

        try:
            # List objects in the 'templates/' prefix
            paginator = s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=output_bucket_name, Prefix="templates/")

            templates = []
            for page in pages:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith("/"):  # Skip folders
                        continue
                    # Remove 'templates/' prefix to get the name
                    name = key[len("templates/") :]
                    if name:
                        templates.append(
                            {"name": name, "isAvailable": False, "amplifyGroups": []}
                        )

            item["data"] = templates

        except Exception as e:
            item["data"] = []
            print(f"Error listing PPTX templates from S3: {str(e)}")

    elif config_type == AdminConfigTypes.AMPLIFY_GROUPS:
        item["data"] = (
            {}
        )  # no groups means none have been added through cognito now the admin interface

    elif config_type == AdminConfigTypes.RATE_LIMIT:
        item["data"] = NO_RATE_LIMIT

    elif config_type == AdminConfigTypes.PROMPT_COST_ALERT:
        item["data"] = {
            "isActive": False,
            "cost": 5,
            "alertMessage": "This request will cost an estimated $<totalCost> (the actual cost may be more) and require <prompts> prompt(s).",
        }
    elif config_type == AdminConfigTypes.DEFAULT_MODELS:
        item["data"] = {
            "user": None,
            "advanced": None,
            "cheapest": None,
            "documentCaching": None,
            "agent": None,
            "embeddings": None
        }
    elif config_type == AdminConfigTypes.DEFAULT_CONVERSATION_STORAGE:
        item["data"] = "future-local"
    elif config_type == AdminConfigTypes.EMAIL_SUPPORT:
        item["data"] = {"isActive": False, "email": ""}
    elif config_type == AdminConfigTypes.AI_EMAIL_DOMAIN:
        item["data"] = ""
    elif config_type == AdminConfigTypes.INTEGRATIONS:
        item["data"] = {}  # No integrtaions have been initialized from the admin panel
    else:
        raise ValueError(f"Unknown config type: {config_type}")
    try:
        admin_table.put_item(Item=item)
        print(f"Config Item Initialized: {config_type.value}")
    except Exception as e:
        print(f"Error initializing AMPLIFY_GROUPS config: {str(e)}")

    return item["data"]


@validated(op="read")
def get_user_app_configs(event, context, current_user, name, data):
    # For quick table data only, anything with more complex logic should be its own endpoint
    app_configs = [
        AdminConfigTypes.EMAIL_SUPPORT,
        AdminConfigTypes.DEFAULT_CONVERSATION_STORAGE,
        AdminConfigTypes.AI_EMAIL_DOMAIN,
    ]
    configs = {}
    for config_type in app_configs:
        try:
            response = admin_table.get_item(Key={"config_id": config_type.value})
            if "Item" in response:
                configs[config_type.value] = response["Item"]["data"]
            else:
                print(f"No {config_type.value} Data Found, skipping...")
        except Exception as e:
            return {
                "success": False,
                "message": f"Error retrieving {config_type.value} Data: {str(e)}",
            }

    return {"success": True, "data": configs}


@validated(op="read")
def get_user_feature_flags(event, context, current_user, name, data):
    # Retrieve feature flags from DynamoDB
    try:
        response = admin_table.get_item(
            Key={"config_id": AdminConfigTypes.FEATURE_FLAGS.value}
        )
        if "Item" in response:
            feature_flags = response["Item"].get("data", {})
            # Check if any base feature flags are missing and add them
            missing_base_flags = check_and_update_missing_base_flags(feature_flags)
            if missing_base_flags:
                feature_flags.update(missing_base_flags)
                print(
                    f"Added missing base feature flags: {list(missing_base_flags.keys())}"
                )
        else:
            feature_flags = initialize_config(AdminConfigTypes.FEATURE_FLAGS)
    except Exception as e:
        return {
            "success": False,
            "message": f"Error retrieving feature flags: {str(e)}",
        }

    # Compute user-specific feature flags
    user_feature_flags = {}

    for feature_name, config in feature_flags.items():
        enabled = config.get("enabled", False)
        user_exceptions = config.get("userExceptions", [])

        # Flip the 'enabled' value if user is in exceptions or a member of an  amplifyGroupExceptions
        if current_user in user_exceptions or is_in_amp_group(
            current_user, config.get("amplifyGroupExceptions", [])
        ):
            enabled = not enabled

        user_feature_flags[feature_name] = enabled

    # Add Admin Interface Access
    user_feature_flags["adminInterface"] = authorized_admin(current_user, True)
    # print("users: ", user_feature_flags)
    return {"success": True, "data": user_feature_flags}


def check_and_update_missing_base_flags(stored_flags):
    """
    Check if any base feature flags are missing from the stored flags.
    If missing flags are found, update the DynamoDB table and return the missing flags.

    Args:
        stored_flags (dict): The feature flags stored in DynamoDB

    Returns:
        dict: Missing feature flags that were added, or empty dict if none were missing
    """
    # Find flags in base_flags that are not in stored_flags
    missing_flags = {}
    for flag_name, enabled in feature_flags.FEATURE_FLAGS.items():
        if flag_name not in stored_flags:
            # Create the default structure for a new feature flag
            missing_flags[flag_name] = {
                "enabled": enabled,
                "userExceptions": [],
                "amplifyGroupExceptions": [],
            }

    # If we found missing flags, update the DynamoDB table
    if missing_flags:
        print("Updating Missing flags: ", missing_flags.keys())
        # Create a new dictionary with all flags (existing + missing)
        updated_flags = {**stored_flags, **missing_flags}

        try:
            # Update the DynamoDB table with the merged flags
            admin_table.put_item(
                Item={
                    "config_id": AdminConfigTypes.FEATURE_FLAGS.value,
                    "data": updated_flags,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            )
            print(
                f"Updated feature flags in DynamoDB with {len(missing_flags)} new base flags"
            )
        except Exception as e:
            print(f"Error updating feature flags in DynamoDB: {str(e)}")
            # We'll return an empty dict if we couldn't update the table
            return {}

    return missing_flags


@validated(op="read")
def get_pptx_for_users(event, context, current_user, name, data):
    try:
        # Attempt to retrieve the PPTX_TEMPLATES configuration from DynamoDB
        config_item = admin_table.get_item(
            Key={"config_id": AdminConfigTypes.PPTX_TEMPLATES.value}
        )
        if "Item" in config_item:
            templates = config_item["Item"]["data"]
            # Filter templates that are available
            available_templates = [
                template["name"]
                for template in templates
                if template.get("isAvailable", False)
                or is_in_amp_group(current_user, template.get("amplifyGroups", []))
            ]
            return {"success": True, "data": available_templates}
        else:
            # Configuration does not exist, return empty list
            return {"success": True, "data": []}

    except Exception as e:
        print(f"Error retrieving PPTX_TEMPLATES: {str(e)}")
        return {
            "success": False,
            "message": f"Error retrieving PPTX_TEMPLATES: {str(e)}",
        }


@validated(op="delete")
def delete_pptx_by_admin(event, context, current_user, name, data):
    query_params = event.get("queryStringParameters", {})
    print("Query params: ", query_params)
    template_name = query_params.get("template_name", "")
    if not template_name or not template_name.endswith(".pptx"):
        return {
            "success": False,
            "message": "Invalid or missing template name parameter",
        }

    if not template_name:
        return {"success": False, "message": "Template name is required for deletion."}

    if not authorized_admin(current_user):
        return {"success": False, "message": "User is not an authorized admin."}

    s3_client = boto3.client("s3")
    output_bucket_name = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]

    try:
        # Retrieve Existing PPTX_TEMPLATES Configuration
        config_item = admin_table.get_item(
            Key={"config_id": AdminConfigTypes.PPTX_TEMPLATES.value}
        )
        if "Item" in config_item:
            existing_templates = config_item["Item"]["data"]
        else:
            # Configuration does not exist, cannot delete
            return {
                "success": False,
                "message": "No PPTX templates configuration found.",
            }

        updated_templates = []
        for template in existing_templates:
            if template["name"] != template_name:
                updated_templates.append(template)

        # Update the Configuration in DynamoDB
        admin_table.put_item(
            Item={
                "config_id": AdminConfigTypes.PPTX_TEMPLATES.value,
                "data": updated_templates,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
        )

        #  Delete the PPTX File from S3
        pptx_key = f"templates/{template_name}"
        try:
            s3_client.delete_object(Bucket=output_bucket_name, Key=pptx_key)
        except Exception as e:
            print(f"Error deleting PPTX file from S3: {str(e)}")
            return {
                "success": False,
                "message": f"Error deleting PPTX file from S3: {str(e)}",
            }

        return {
            "success": True,
            "message": f"Template '{template_name}' deleted successfully.",
        }

    except Exception as e:
        print(f"Error deleting template: {str(e)}")
        return {"success": False, "message": f"Error deleting template: {str(e)}"}


@validated(op="upload")
def generate_presigned_url_for_upload(event, context, current_user, name, data):
    data = data["data"]
    template_name = data.get("fileName", "")
    if not template_name or not template_name.endswith(".pptx"):
        return {
            "success": False,
            "message": "A valid PPTX template name is required for upload.",
        }

    is_available = data.get("isAvailable", True)
    amplify_groups = data.get("amplifyGroups", [])
    content_type = data.get("contentType", "")
    content_md5 = data.get("md5", "")

    is_available_str = "true" if is_available else "false"
    amplify_groups_str = ",".join(amplify_groups)

    if not template_name:
        return {"success": False, "message": "Template name is required for upload."}

    # Authorize the User
    log_item(
        None,
        current_user,
        f"Authentication user for the purpose of: Upload PowerPoint Template",
    )
    if not authorized_admin(current_user):
        return {"success": False, "message": "User is not an authorized admin."}

    output_bucket_name = os.environ["S3_CONVERSION_OUTPUT_BUCKET_NAME"]
    pptx_key = f"templates/{template_name}"

    try:
        config = Config(signature_version="s3v4")  # Force AWS Signature Version 4
        s3_client = boto3.client("s3", config=config)
        # Generate a presigned URL for put_object
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": output_bucket_name,
                "Key": pptx_key,
                "ContentType": content_type,
                "Metadata": {
                    "isavailable": is_available_str,
                    "amplifygroups": amplify_groups_str,
                },
                "ContentMD5": content_md5,
            },
            ExpiresIn=3600,  # URL expires in 1 hour
        )

        print("\n", presigned_url)

        return {"success": True, "presigned_url": presigned_url}
    except ClientError as e:
        print(f"Error generating presigned URL: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating presigned URL: {str(e)}",
        }



@validated(op="read")
def verify_valid_admin(event, context, current_user, name, data):
    purpose = data["data"]["purpose"]
    print(f"{current_user} is being verified for the purpose of: {purpose}")
    log_item(None, current_user, f"Authentication user for the purpose of: {purpose}")
    return {"success": True, "isAdmin": authorized_admin(current_user)}



def authorized_admin(current_user, forFeatureFlags=False):
    try:
        # Get the 'admins' configuration item
        response = admin_table.get_item(
            Key={"config_id": AdminConfigTypes.ADMINS.value}
        )
        if "Item" in response:
            admins_list = response["Item"].get("data", [])
            if current_user in admins_list:
                print(current_user + " is authorized to make changes.")
                return True
        else:
            print("No admins list in the admins table...")
            init_admins = initialize_config(AdminConfigTypes.ADMINS)
            return current_user in init_admins

    except Exception as e:
        print(f"Error in authorized_admin: {str(e)}")
    print(current_user + " is not authorized to make changes.")

    # using for authentication
    if not forFeatureFlags:
        log_item(
            "AUTH FAIL",
            current_user,
            current_user + " is not authorized to make changes.",
        )
    return False


def log_item(config_type, username, details):
    audit_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_LOGS_DYNAMODB_TABLE"])
    audit_table.put_item(
        Item={
            "log_id": f"{username}/{ str(uuid.uuid4())}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "configuration": config_type,
            "user": username,
            "details": details,
        }
    )


def sync_assistant_admins(event, context):
    print("Syncing Assistant Admin Interface Users...")
    AST_ADMIN_UI_FLAG = "assistantAdminInterface"
    groups_table = dynamodb.Table(os.environ["AMPLIFY_GROUPS_DYNAMODB_TABLE"])
    # Retrieve feature flags from DynamoDB
    feature_flags = None
    try:
        response = admin_table.get_item(
            Key={"config_id": AdminConfigTypes.FEATURE_FLAGS.value}
        )
        if "Item" in response:
            feature_flags = response["Item"].get("data", {})
        else:
            print("Feature flags are being initialized..")
            # ast admin is set to false so it will get updated to the table
            feature_flags = initialize_config(AdminConfigTypes.FEATURE_FLAGS)
    except Exception as e:
        print(f"Error retrieving feature flags: {str(e)}")
        return {"statusCode": 500, "body": f"Error retrieving feature flags: {str(e)}"}

    if not feature_flags or AST_ADMIN_UI_FLAG not in feature_flags:
        print(f"Error retrieving feature flags")
        return {"statusCode": 500, "body": f"Error retrieving feature flags"}

    admin_feature = feature_flags[AST_ADMIN_UI_FLAG]

    # everyone has access, no need to continue
    if admin_feature.get("enabled", False):
        return {
            "statusCode": 200,
            "body": f"The admin interface is public. No changes needed.",
        }

    groups = []
    try:
        response = groups_table.scan()
        groups.extend(response.get("Items", []))

        # Handle pagination if there are more records
        while "LastEvaluatedKey" in response:
            response = groups_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            groups.extend(response.get("Items", []))

    except Exception as e:
        print(f"An error occurred while retrieving groupss: {e}")
        return {
            "statusCode": 500,
            "body": f"An error occurred while retrieving groupss: {e}",
        }

    # go through all groups and collect the admins and writes
    access_to_users_set = set()
    for group_item in groups:
        # members should be a dict like: {'username': 'admin' or 'write' or 'read'}
        members = group_item.get("members", {})
        for username, perm in members.items():
            if perm in ("admin", "write"):
                access_to_users_set.add(username)

    user_exceptions = admin_feature.get("userExceptions", [])
    current_exceptions_set = set(user_exceptions)

    if current_exceptions_set == access_to_users_set:
        print("No updates needed. ")
        return {"statusCode": 200, "body": "No updates needed. "}
    print("\nUsers needing access", access_to_users_set)

    adding = access_to_users_set - current_exceptions_set
    if adding:
        print("\nNew Users added:", adding)
    removing = current_exceptions_set - access_to_users_set
    if removing:
        print("Existing Users removed:", removing)

    admin_feature["userExceptions"] = list(
        access_to_users_set
    )  # overwrite, this capture when someone loses access to the admin interface

    feature_flags[AST_ADMIN_UI_FLAG] = admin_feature

    update_res = update_admin_config_data(
        AdminConfigTypes.FEATURE_FLAGS.value, feature_flags
    )
    if not update_res["success"]:
        print(f"Error updating feature flags: {update_res['message']}")
        return {
            "statusCode": 500,
            "body": f"Error updating feature flags: {update_res['message']}",
        }
    else:
        print("Feature flags updated successfully.")
        return {"statusCode": 200, "body": "Feature flags updated successfully."}
