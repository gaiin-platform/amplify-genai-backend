# This script is useful in migrating user ID's from one format to another.
# It is especially helpful in the event that, during SSO, the `username` needs
# to be changed.
# One example might be that SSO was initially set up to use email for username
# and then later changed some thing immutable so that email address changes don't
# impact the user.
import sys
import csv
import argparse
import boto3
import os
import time
import uuid as uuid_lib
from datetime import datetime
from typing import Dict
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.conditions import Attr

from config import get_config
from s3_data_migration import (
    migrate_workflow_templates_bucket_for_user, 
    migrate_scheduled_tasks_logs_bucket_for_user,
    migrate_artifacts_bucket_for_user,
    migrate_user_settings_for_user,
    migrate_conversations_bucket_for_user,
    migrate_shares_bucket_for_user,
    migrate_code_interpreter_files_bucket_for_user,
    migrate_agent_state_bucket_for_user,
    migrate_group_assistant_conversations_bucket_for_user,
    cleanup_orphaned_workflow_templates_for_user,
    main as s3_migration_main
)
from user_storage_backup import backup_user_storage_table

# AWS region configuration (will be set from command line args in main)
AWS_REGION = None

# AWS clients (will be initialized in main after args are parsed)
dynamodb = None
s3_client = None
dynamodb_client = None

# Note: CommonData import removed - using direct DynamoDB writes for self-contained migration


def check_table_exists(table_name: str) -> bool:
    """Check if a DynamoDB table exists and is accessible."""
    global dynamodb_client
    if not table_name:
        return False
    try:
        dynamodb_client.describe_table(TableName=table_name)
        return True
    except Exception:
        return False


def check_bucket_exists(bucket_name: str) -> bool:
    """Check if an S3 bucket exists and is accessible."""
    global s3_client
    if not bucket_name:
        return False
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        return True
    except Exception:
        return False


def validate_migration_resources(table_names: Dict[str, str], args) -> Dict[str, Dict[str, bool]]:
    """Validate which tables and buckets exist for migration planning."""
    validation_results = {
        "tables": {},
        "buckets": {},
        "migration_steps_needed": {}
    }
    
    # Automatically categorize all config keys by type and check existence
    for key, resource_name in table_names.items():
        if "TABLE" in key:
            # It's a DynamoDB table
            exists = check_table_exists(resource_name)
            validation_results["tables"][key] = exists
            log(f"Table check: {key} ({resource_name}) - {'✅ EXISTS' if exists else '❌ NOT FOUND'}")
            
        elif "BUCKET" in key:
            # It's an S3 bucket
            exists = check_bucket_exists(resource_name)
            validation_results["buckets"][key] = exists
            log(f"Bucket check: {key} ({resource_name}) - {'✅ EXISTS' if exists else '❌ NOT FOUND'}")
    
    # Determine which migration steps are needed
    old_table_exists = validation_results["tables"]["OLD_USER_STORAGE_TABLE"]
    new_table_exists = validation_results["tables"]["USER_DATA_STORAGE_TABLE"]
    cognito_exists = validation_results["tables"]["COGNITO_USERS_DYNAMODB_TABLE"]
    
    # Check if any buckets exist for S3 migrations
    s3_migrations_needed = any(validation_results["buckets"].values())
    
    validation_results["migration_steps_needed"] = {
        "user_data_storage_id_migration": new_table_exists,
        "old_to_new_table_migration": old_table_exists and new_table_exists,
        "per_user_table_updates": cognito_exists,
        "s3_migrations": s3_migrations_needed
    }
    
    return validation_results


def paginated_query(table_name: str, key_name: str, value: str, index_name: str = None):
    """
    Generator for paginated DynamoDB query results.
    Yields items matching Key(key_name).eq(value).
    """
    global dynamodb
    table = dynamodb.Table(table_name)
    kwargs = {"KeyConditionExpression": Key(key_name).eq(value)}
    if index_name:
        kwargs["IndexName"] = index_name

    while True:
        response = table.query(**kwargs)
        for item in response.get("Items", []):
            yield item
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def paginated_scan(
    table_name: str, attr_name: str, value: str, begins_with: bool = False
):
    """
    Generator for paginated DynamoDB scan results.
    Yields items matching Attr(attr_name).eq(value).
    """
    global dynamodb
    table = dynamodb.Table(table_name)
    if begins_with:
        kwargs = {"FilterExpression": Attr(attr_name).begins_with(value)}
    else:
        kwargs = {"FilterExpression": Attr(attr_name).eq(value)}

    while True:
        response = table.scan(**kwargs)
        for item in response.get("Items", []):
            yield item
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def log(*messages):
    for message in messages:
        print(f"[{datetime.now()}]", message)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Migrate user IDs from one format to another."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not make any changes, just show what would happen.",
    )
    parser.add_argument(
        "--csv-file",
        required=False,  # Made optional since we can generate it
        default="migration_users.csv",
        help="Path to the CSV file containing migration data (default: migration_users.csv).",
    )
    parser.add_argument(
        "--log", 
        required=False, 
        help="Log output to the specified file (auto-generated if not provided)."
    )
    parser.add_argument(
        "--no-id-change",
        action="store_true",
        help="Generate migration_users.csv with same old_id and new_id for S3 consolidation only (no username changes).",
    )
    parser.add_argument(
        "--dont-backup",
        action="store_true",
        help="Skip both backup creation and verification (for users who already have backups)"
    )
    parser.add_argument(
        "--no-confirmation",
        action="store_true",
        help=argparse.SUPPRESS  # Hidden from help - for automation/testing
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region for DynamoDB and S3 operations (default: us-east-1)"
    )
    return parser.parse_args()


def get_tables_from_config_file() -> Dict[str, str]:
    """
    Reads the configuration file and returns a dictionary of table names.
    
    Returns:
        Dict[str, str]: A dictionary containing table names from the configuration file.
    """
    config = get_config()
    # Remove needs_edit if it exists (legacy compatibility)
    config.pop("needs_edit", None)
    return config


def get_user(old_id: str) -> dict | None:
    """Fetch user by old ID."""
    global dynamodb
    table = table_names.get("COGNITO_USERS_DYNAMODB_TABLE")
    try:
        account = dynamodb.Table(table)
        response = account.query(KeyConditionExpression=Key("user_id").eq(old_id))
        if "Items" in response and response["Items"]:
            return response["Items"][0]
        else:
            return None
    except Exception as e:
        return None


def get_users_from_csv(file_path: str) -> Dict[str, str]:
    """Read CSV and return mapping of old_id to new_id."""
    users = {}
    try:
        with open(file_path, mode="r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                old_id = row.get("old_id")
                new_id = row.get("new_id")
                if old_id and new_id:
                    if new_id in users.values():
                        log(f"Warning: Duplicate new_id {new_id} found. Clean up CSV and try again.")
                        sys.exit(1)
                    users[old_id] = new_id
                else:
                    log(f"Skipping invalid row: {row}")
    except Exception as e:
        log(f"Error reading CSV file {file_path}: {e}")
        sys.exit(1)
    return users


def generate_no_change_csv(file_path: str, dry_run: bool) -> bool:
    """
    Generate migration_users.csv by pulling all users from COGNITO_USERS_DYNAMODB_TABLE.
    Each user will have the same old_id and new_id (no username change, just data migration).
    
    Args:
        file_path: Path to write the CSV file
        dry_run: If True, only show what would be done
        
    Returns:
        bool: Success status
    """
    msg = f"[generate_no_change_csv][dry-run: {dry_run}] %s"
    
    try:
        table_name = table_names.get("COGNITO_USERS_DYNAMODB_TABLE")
        if not table_name:
            log(msg % "COGNITO_USERS_DYNAMODB_TABLE not found in config")
            return False
            
        log(msg % f"Fetching all users from {table_name}...")
        
        cognito_table = dynamodb.Table(table_name)
        
        # Collect all users
        all_users = []
        last_evaluated_key = None
        
        while True:
            # Prepare scan parameters - only need user_id
            scan_params = {"ProjectionExpression": "user_id"}
            
            # Add pagination token if we have one
            if last_evaluated_key:
                scan_params["ExclusiveStartKey"] = last_evaluated_key
            
            # Execute scan
            response = cognito_table.scan(**scan_params)
            
            # Check if we got items
            if "Items" not in response:
                break
                
            # Add items to our collection
            for item in response.get("Items", []):
                user_id = item.get("user_id")
                if user_id:
                    all_users.append(user_id)
            
            # Check if there are more pages
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break  # No more pages
                
        log(msg % f"Found {len(all_users)} users in Cognito table")
        
        if not all_users:
            log(msg % "No users found in Cognito table")
            return False
        
        # Sort users for consistency
        all_users.sort()
        
        if dry_run:
            log(msg % f"Would generate {file_path} with {len(all_users)} users (same old_id and new_id)")
            log(msg % "Sample entries that would be created:")
            for user in all_users[:5]:  # Show first 5 as sample
                log(msg % f"  {user},{user}")
            if len(all_users) > 5:
                log(msg % f"  ... and {len(all_users) - 5} more users")
            return True
        else:
            # Write CSV file
            with open(file_path, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['old_id', 'new_id'])  # Header
                
                for user_id in all_users:
                    writer.writerow([user_id, user_id])  # Same ID for both columns
                    
            log(msg % f"Generated {file_path} with {len(all_users)} users")
            log(msg % "All users will keep their existing IDs (data migration only)")
            return True
            
    except Exception as e:
        log(msg % f"Error generating CSV file: {e}")
        return False


## Starting Here is all the functions that actually update the data ###
## ----------------------------------------------------------------- ##


# "COGNITO_USERS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-cognito-users",
def skip_if_table_missing(table_name: str, operation_name: str) -> bool:
    """Helper function to skip operations if table doesn't exist."""
    if not check_table_exists(table_name):
        log(f"[{operation_name}] Skipping - table {table_name} does not exist")
        return True
    return False


def update_user_id(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update the user ID of the given user."""
    msg = f"[update_user_id][dry-run: {dry_run}] %s"
    try:
        user = get_user(old_id)
        user_table = table_names.get("COGNITO_USERS_DYNAMODB_TABLE")
        # update username
        if not user:
            log(msg % f"User with old ID {old_id} not found.")
            return False
        log(msg % f"Found user with old ID {old_id}.\n\tExisting Data: {user}")
        # Store old key for cleanup (cognito users table has hash key: user_id)
        old_key = {
            "user_id": old_id
        }
        
        user["user_id"] = new_id
        if dry_run:
            log(
                msg
                % f"Would update user ID from {old_id} to {new_id}.\n\tNew Data: {user}"
            )
            log(msg % f"Would delete old user record with key: {old_key}")
            return True
        else:
            # save the user back to the table with new user_id
            log(
                msg % f"Updating user ID from {old_id} to {new_id}.\n\tNew Data: {user}"
            )
            cognito_table = dynamodb.Table(user_table)
            cognito_table.put_item(Item=user)
            
            # Delete old record with old user_id
            try:
                cognito_table.delete_item(Key=old_key)
                log(msg % f"Deleted old user record with key: {old_key}")
            except Exception as delete_e:
                log(msg % f"Warning: Failed to delete old user record {old_key}: {delete_e}")
                # Don't fail the migration for cleanup errors
            return True
    except Exception as e:
        log(msg % f"Error updating user ID from {old_id} to {new_id}: {e}")
        return False


# "AMPLIFY_ADMIN_DYNAMODB_TABLE" : "amplify-v6-admin-dev-admin-configs",
def update_amplify_admin_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all admin configs associated with the old user ID to the new user ID."""
    msg = f"[update_amplify_admin_table][dry-run: {dry_run}] %s"
    table = table_names.get("AMPLIFY_ADMIN_DYNAMODB_TABLE")
    amplify_admin_table = dynamodb.Table(table)

    # For the 'admins' config, we could just do this record a single time
    # by using the CSV file and constructing the proper list. This would
    # be the most efficient way to do it - but its a one time shot and so
    # I'm doing it the easy way for now.
    admin_config = amplify_admin_table.get_item(Key={"config_id": "admins"})
    if not "Item" in admin_config:
        log(msg % f"No admin config found in {table}.")
    else:
        admin_data = list(set(admin_config["Item"].get("data", [])))
        if old_id in admin_data:
            log(
                msg
                % f"Found admin config with old ID {old_id}.\n\tExisting Data: {admin_data}"
            )
            new_admin_data = list(
                set([new_id if uid == old_id else uid for uid in admin_data])
            )
            if dry_run:
                log(msg
                    % f"Would update admin config data to:\n\tNew Data: {new_admin_data}"
)
            else:
                log(msg
                    % f"Updating admin config data to:\n\tNew Data: {new_admin_data}"
)
                amplify_admin_table.put_item(Item={"config_id": "admins", "data": new_admin_data}
)

    # A similar dilemma is presented here and I maintain that this table desperately needs a restructure.
    group_config = amplify_admin_table.get_item(Key={"config_id": "amplifyGroups"})
    if not "Item" in group_config:
        log(msg % f"No amplifyGroups config found in {table}.")
    else:
        group_data = group_config["Item"].get("data", {})
        updated = False
        for group_name, group_info in group_data.items():
            group_updated = False
            
            # Check and update members list
            members = list(set(group_info.get("members", [])))
            if old_id in members:
                log(msg
                    % f"Found amplifyGroups config with old ID {old_id} in group {group_name} members.\n\tExisting Data: {members}"
)
                new_members = list(set([new_id if uid == old_id else uid for uid in members])
)
                group_info["members"] = new_members
                group_updated = True
                if dry_run:
                    log(    msg
                        % f"Would update members of group {group_name} to:\n\tNew Data: {new_members}"
    )
                else:
                    log(    msg
                        % f"Updating members of group {group_name} to:\n\tNew Data: {new_members}"
    )
            
            # Check and update createdBy field
            created_by = group_info.get("createdBy")
            if created_by == old_id:
                log(msg
                    % f"Found amplifyGroups config with old ID {old_id} as createdBy in group {group_name}.\n\tExisting createdBy: {created_by}"
)
                group_info["createdBy"] = new_id
                group_updated = True
                if dry_run:
                    log(    msg
                        % f"Would update createdBy of group {group_name} to:\n\tNew createdBy: {new_id}"
    )
                else:
                    log(    msg
                        % f"Updating createdBy of group {group_name} to:\n\tNew createdBy: {new_id}"
    )
            
            if group_updated:
                updated = True
        if updated and not dry_run:
            amplify_admin_table.put_item(
                Item={"config_id": "amplifyGroups", "data": group_data}
            )

    # And the most complicated of the tables....
    feature_flags_config = amplify_admin_table.get_item(
        Key={"config_id": "featureFlags"}
    )
    if not "Item" in feature_flags_config:
        log(msg % f"No featureFlags config found in {table}.")
    else:
        feature_flags_data = feature_flags_config["Item"].get("data", {})
        updated = False
        for flag_name, flag_info in feature_flags_data.items():
            user_exceptions = list(set(flag_info.get("userExceptions", [])))
            if old_id in user_exceptions:
                log(msg
                    % f"Found featureFlags config with old ID {old_id} in flag {flag_name}.\n\tExisting Data: {user_exceptions}"
)
                new_user_exceptions = list(set([new_id if uid == old_id else uid for uid in user_exceptions])
)
                flag_info["userExceptions"] = new_user_exceptions
                updated = True
                if dry_run:
                    log(    msg
                        % f"Would update userExceptions of flag {flag_name} to:\n\tNew Data: {new_user_exceptions}"
    )
                else:
                    log(    msg
                        % f"Updating userExceptions of flag {flag_name} to:\n\tNew Data: {new_user_exceptions}"
    )
        if updated and not dry_run:
            amplify_admin_table.put_item(
                Item={"config_id": "featureFlags", "data": feature_flags_data}
            )


### User object related tables ###
# "ACCOUNTS_DYNAMO_TABLE": "amplify-v6-lambda-dev-accounts",
# DONE
def update_accounts(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all accounts associated with the old user ID to the new user ID."""
    msg = f"[update_accounts][dry-run: {dry_run}] %s"
    table = table_names.get("ACCOUNTS_DYNAMO_TABLE")
    
    # Skip if table doesn't exist
    if skip_if_table_missing(table, "update_accounts"):
        return True
        
    try:

        ret = False
        accounts_table = dynamodb.Table(table)
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found accounts record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (accounts table only has hash key: user)
            old_key = {
                "user": old_id
            }
            
            # Update to new ID
            item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would update account item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old account record with key: {old_key}")
            else:
                log(msg % f"Updating account item to:\n\tNew Data: {item}")
                # Put new record with new user ID
                accounts_table.put_item(Item=item)
                
                # Delete old record with old user ID
                try:
                    accounts_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old account record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old account record {old_key}: {delete_e}")
                    # Don't fail the migration for cleanup errors
            ret = True
        return ret

    except Exception as e:
        log(msg % f"Error updating accounts for user ID from {old_id} to {new_id}: {e}")
        return False


# "API_KEYS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-api-keys",
def update_api_keys(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all API keys associated with the old user ID to the new user ID."""
    # NOTE: This table does not allow us to query by the old user ID, so we
    # have to scan the entire table for both owner and delegate fields. 
    # This is not efficient, but it is a one-time operation.
    # CRITICAL: api_owner_id is NOT updated per user requirements.
    msg = f"[update_api_keys][dry-run: {dry_run}] %s"
    table = table_names.get("API_KEYS_DYNAMODB_TABLE")
    if not table:
        log(msg % f"Table API_KEYS_DYNAMODB_TABLE not found, skipping")
        return True
        
    try:
        api_keys_table = dynamodb.Table(table)
        ret = False

        # Single scan of entire table checking both owner and delegate fields
        scanner = api_keys_table.scan()
        while True:
            for item in scanner.get('Items', []):
                updated = False
                
                # Check and update owner field
                if "owner" in item and item["owner"] == old_id:
                    log(    msg
                        % f"Found API keys record with owner {old_id}.\n\tExisting Data: {item}"
    )
                    item["owner"] = new_id
                    updated = True
                
                # Check and update delegate field
                if "delegate" in item and item["delegate"] == old_id:
                    log(    msg
                        % f"Found API keys record with delegate {old_id}.\n\tExisting Data: {item}"
    )
                    item["delegate"] = new_id
                    updated = True
                
                # Only save if we made changes
                if updated:
                    if dry_run:
                        log(msg % f"Would update API key item to:\n\tNew Data: {item}")
                    else:
                        log(msg % f"Updating API key item to:\n\tNew Data: {item}")
                        api_keys_table.put_item(Item=item)
                    ret = True
            
            if 'LastEvaluatedKey' not in scanner:
                break
            scanner = api_keys_table.scan(ExclusiveStartKey=scanner['LastEvaluatedKey'])

        return ret

    except Exception as e:
        log(msg % f"Error updating API keys for user ID from {old_id} to {new_id}: {e}")
        return False


# "ARTIFACTS_DYNAMODB_TABLE" : "amplify-v6-artifacts-dev-user-artifacts",
# "S3_ARTIFACTS_BUCKET": "amplify-v6-artifacts-dev-bucket"
# DONE
def update_artifacts_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all artifacts records associated with the old user ID to the new user ID.
    ENHANCED: Handles partial migrations where artifacts may be in split states.
    """
    msg = f"[update_artifacts_table][dry-run: {dry_run}] %s"
    table = table_names.get("ARTIFACTS_DYNAMODB_TABLE")
    
    # Skip if table doesn't exist
    if skip_if_table_missing(table, "update_artifacts_table"):
        return True
        
    artifacts_table = dynamodb.Table(table)
    ret = False
    
    try:
        items = list(paginated_query(table, "user_id", old_id))
        
        if not items:
            log(msg % f"No artifacts found for user {old_id}")
            return True
            
        for item in items:
            log(
                msg
                % f"Found artifacts record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (primary key: user_id)
            old_key = {
                "user_id": old_id
            }
            
            # ENHANCED: Handle split data states
            # 1. Some artifacts may already be migrated (clean key format)
            # 2. Some artifacts may still be in legacy format  
            # 3. Some artifacts may exist in both S3 and USER_DATA_STORAGE_TABLE
            #
            # The migrate_artifacts_bucket_for_user function now handles:
            # - Detection of already migrated artifacts
            # - Skipping of duplicates in USER_DATA_STORAGE_TABLE
            # - Transformation of all keys to new format
            # - Preservation of already migrated artifacts
            success, updated_artifacts = migrate_artifacts_bucket_for_user(old_id, new_id, dry_run, item, AWS_REGION)
            
            if not success:
                log(msg % f"Migration failed for user {old_id}, but continuing with ID update")
                # Still update the user_id even if migration fails
                # This ensures split state can be resolved later
            
            # Update user_id and artifacts array while preserving ALL other columns
            item["user_id"] = new_id
            if success and updated_artifacts:
                item["artifacts"] = updated_artifacts
            elif not success:
                # If migration failed, at least transform the keys in metadata
                # This allows the application to find artifacts even in split state
                if "artifacts" in item and isinstance(item["artifacts"], list):
                    transformed_artifacts = []
                    for artifact in item["artifacts"]:
                        updated_artifact = artifact.copy()
                        old_key = artifact.get("key", "")
                        # Transform key even if content migration failed
                        if old_key.startswith(f"{old_id}/"):
                            updated_artifact["key"] = old_key[len(f"{old_id}/"):]
                        transformed_artifacts.append(updated_artifact)
                    item["artifacts"] = transformed_artifacts
            
            if dry_run:
                log(msg % f"Would update artifact item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old artifact record with key: {old_key}")
            else:
                log(msg % f"Updating artifact item to:\n\tNew Data: {item}")
                # Put new record with new user ID
                artifacts_table.put_item(Item=item)
                
                # Delete old record with old user ID
                try:
                    artifacts_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old artifact record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old artifact record {old_key}: {delete_e}")
            
            ret = True
            
        return ret
        
    except Exception as e:
        log(msg % f"Error updating artifacts for user ID from {old_id} to {new_id}: {e}")
        return False

     

# "OPS_DYNAMODB_TABLE" : "amplify-v6-lambda-ops-dev-ops",
def update_ops_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all ops records associated with the old user ID to the new user ID."""
    msg = f"[update_ops_table][dry-run: {dry_run}] %s"
    table = table_names.get("OPS_DYNAMODB_TABLE")
    try:
        ops_table = dynamodb.Table(table)

        ret = False
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found ops records for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (composite key: user + tag)
            old_key = {
                "user": old_id,
                "tag": item.get("tag")  # tag is the sort key
            }
            
            # Update to new user ID
            item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would update ops item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old ops record with key: {old_key}")
            else:
                log(msg % f"Updating ops item to:\n\tNew Data: {item}")
                # Put new record with new user ID
                ops_table.put_item(Item=item)
                
                # Delete old record with old user ID
                try:
                    ops_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old ops record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old ops record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating ops records for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "SHARES_DYNAMODB_TABLE" : "amplify-v6-lambda-dev",
# "S3_SHARE_BUCKET_NAME": "amplify-v6-lambda-dev-share", #Marked for deletion
def update_shares_table(old_id: str, new_id: str, all_users_map: dict, dry_run: bool) -> bool:
    """
    Migrate shares for a user from SHARES_DYNAMODB_TABLE to USER_DATA_STORAGE_TABLE.
    
    Processing flow:
    1. Migrate S3 shares files to consolidation bucket
    2. Transform and migrate DynamoDB records to USER_DATA_STORAGE_TABLE with new schema
    3. Delete old records from SHARES_DYNAMODB_TABLE
    
    Args:
        old_id: The old user ID being migrated
        new_id: The new user ID
        all_users_map: Complete mapping of all old_id -> new_id (for cross-user S3 key updates)
        dry_run: If True, only show what would be done
    
    New schema in USER_DATA_STORAGE_TABLE:
    - PK: "{new_user_id}#amplify-shares#received"  
    - SK: "{new_sharer_id}#{date}#{uuid}"
    - data: share metadata (sharedBy, note, sharedAt, key)
    """
    msg = f"[update_shares_table][dry-run: {dry_run}] %s"
    table = table_names.get("SHARES_DYNAMODB_TABLE")
    
    if not table:
        log(msg % "SHARES_DYNAMODB_TABLE not found in config, skipping")
        return True
    
    # Step 1: Migrate S3 shares files
    success = migrate_shares_bucket_for_user(old_id, new_id, dry_run)
    if not success:
        log(msg % f"Failed to migrate S3 shares for user {old_id}")
        return False
    
    shares_table = dynamodb.Table(table)
    
    # Get USER_DATA_STORAGE_TABLE for migration
    user_storage_table_name = table_names.get("USER_DATA_STORAGE_TABLE")
    if not user_storage_table_name:
        log(msg % "USER_DATA_STORAGE_TABLE not found in config")
        return False
    user_storage_table = dynamodb.Table(user_storage_table_name)
    
    # Step 2: Process and migrate DynamoDB records
    ret = True
    try:
        for item in paginated_scan(table, "user", old_id):
            
            log(msg % f"Found shares record for user ID {old_id}")
            log(msg % f"    Existing Data: {item}")
            
            # Migrate user settings from SHARES_DYNAMODB_TABLE settings column to USER_DATA_STORAGE_TABLE
            # Only migrate if settings don't already exist in USER_DATA_STORAGE_TABLE
            migrate_user_settings_for_user(old_id, new_id, dry_run, item, AWS_REGION)
            
            share_name = item.get('name', '/state/share')
            share_data_array = item.get('data', [])
            shares_record_id = item.get('id')  # Get the actual primary key
            
            if not isinstance(share_data_array, list):
                log(msg % f"Invalid share data format for user {old_id}, skipping")
                continue
            
            # Check if shares already exist in USER_DATA_STORAGE_TABLE to avoid duplicates
            hash_key = f"{new_id}#amplify-shares"
            existing_shares = set()
            if not dry_run:
                try:
                    # Query existing shares to avoid duplicates
                    response = user_storage_table.query(
                        KeyConditionExpression=Key('PK').eq(f"{hash_key}#received")
                    )
                    for existing_item in response.get('Items', []):
                        existing_data = existing_item.get('data', {})
                        existing_key = existing_data.get('key', '')
                        existing_shares.add(existing_key)
                    
                    if existing_shares:
                        log(msg % f"Found {len(existing_shares)} shares already migrated for user {new_id}")
                        
                except Exception as e:
                    log(msg % f"Warning: Could not check existing shares for user {new_id}: {e}")
            
            log(msg % f"Processing {len(share_data_array)} shares for user {old_id} -> {new_id}")
            
            # Process each share in the data array and migrate to USER_DATA_STORAGE_TABLE
            migrated_count = 0
            skipped_count = 0
            for share_entry in share_data_array:
                try:
                    # Extract share metadata
                    old_shared_by = share_entry.get('sharedBy', '')
                    shared_at = share_entry.get('sharedAt', 0)
                    note = share_entry.get('note', '')
                    old_key = share_entry.get('key', '')
                    
                    if not old_shared_by or not old_key:
                        log(msg % f"Skipping share entry missing required fields")
                        continue
                    
                    # Translate sharer ID using complete user mappings (not just current migration)
                    new_shared_by = all_users_map.get(old_shared_by, old_shared_by)
                    
                    # Update key to match actual S3 location using ALL user mappings
                    # Both DB key and S3 path will be: shares/recipient/sharer/date/file.json
                    key_parts = old_key.split('/')
                    if len(key_parts) >= 2:
                        # Transform the key to match ALL new user IDs (cross-user aware)
                        new_key_parts = []
                        for part in key_parts:
                            # Replace ANY user ID from the complete mapping
                            new_key_parts.append(all_users_map.get(part, part))
                        # Construct new key WITH shares/ prefix to match S3 structure
                        new_key = f"shares/{'/'.join(new_key_parts)}"
                    else:
                        # Fallback: ensure shares/ prefix and update ALL user IDs
                        new_key = old_key
                        for map_old_id, map_new_id in all_users_map.items():
                            if map_old_id in new_key:
                                new_key = new_key.replace(map_old_id, map_new_id)
                        if not new_key.startswith("shares/"):
                            new_key = f"shares/{new_key}"
                    
                    log(msg % f"Cross-user key update: {old_key} -> {new_key}")
                    
                    # Check if this share already exists in USER_DATA_STORAGE_TABLE
                    if new_key in existing_shares:
                        log(msg % f"Skipping already migrated share: {new_key}")
                        skipped_count += 1
                        continue
                    
                    # Generate date from timestamp
                    from datetime import datetime
                    if shared_at:
                        try:
                            dt_obj = datetime.fromtimestamp(shared_at / 1000)  # Convert ms to seconds
                            date_str = dt_obj.strftime("%Y-%m-%d")
                        except:
                            date_str = datetime.now().strftime("%Y-%m-%d")
                    else:
                        date_str = datetime.now().strftime("%Y-%m-%d")
                    
                    # Generate unique share ID
                    share_id = f"{new_shared_by}#{date_str}#{str(uuid_lib.uuid4())}"
                    
                    # Prepare USER_DATA_STORAGE_TABLE data
                    user_storage_data = {
                        "sharedBy": new_shared_by,
                        "note": note,
                        "sharedAt": shared_at,
                        "key": new_key
                    }
                    
                    # Create hash key for USER_DATA_STORAGE_TABLE
                    hash_key = f"{new_id}#amplify-shares"
                    
                    # Create the item for USER_DATA_STORAGE_TABLE
                    new_item = {
                        "PK": f"{hash_key}#received",
                        "SK": share_id,
                        "UUID": str(uuid_lib.uuid4()),
                        "data": user_storage_data,
                        "appId": hash_key,
                        "entityType": "received",
                        "createdAt": int(time.time())
                    }
                    
                    if dry_run:
                        log(msg % f"Would migrate share: user={old_id}->{new_id}, sharer={old_shared_by}->{new_shared_by}, key={old_key}->{new_key}")
                    else:
                        user_storage_table.put_item(Item=new_item)
                        log(msg % f"Migrated share to USER_DATA_STORAGE_TABLE: user={new_id}, sharer={new_shared_by}")
                        migrated_count += 1
                        
                except Exception as e:
                    log(msg % f"Error processing share entry: {e}")
                    continue
            
            # Step 3: Delete old record from SHARES_DYNAMODB_TABLE after successful migration
            if not dry_run and migrated_count > 0 and shares_record_id:
                try:
                    shares_table.delete_item(
                        Key={
                            'id': shares_record_id  # Use correct primary key 'id'
                        }
                    )
                    log(msg % f"Deleted legacy shares record with id: {shares_record_id}")
                except Exception as e:
                    log(msg % f"Error deleting legacy shares record: {e}")
                    # Don't fail the migration for delete errors
            
            if dry_run:
                log(msg % f"Would migrate {len(share_data_array)} shares for user {old_id}")
            else:
                log(msg % f"Successfully migrated {migrated_count} shares, skipped {skipped_count} existing shares for user {old_id}")
            
            ret = True
            
        return ret
    except Exception as e:
        log(msg % f"Error updating shares for user ID from {old_id} to {new_id}: {e}")
        return False
    
# "OBJECT_ACCESS_DYNAMODB_TABLE" : "amplify-v6-object-access-dev-object-access",
# DONE
def update_object_access_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all object access records associated with the old user ID to the new user ID."""
    msg = f"[update_object_access_table][dry-run: {dry_run}] %s"
    table = table_names.get("OBJECT_ACCESS_DYNAMODB_TABLE")
    object_access_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(
            table, "principal_id", old_id, index_name="PrincipalIdIndex"
        ):
            log(
                msg
                % f"Found object access record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (composite key: object_id + principal_id)
            old_key = {
                "object_id": item.get("object_id"),
                "principal_id": old_id
            }
            
            # Update to new principal ID
            item["principal_id"] = new_id
            
            if dry_run:
                log(msg % f"Would update object access item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old object access record with key: {old_key}")
            else:
                log(msg % f"Updating object access item to:\n\tNew Data: {item}")
                # Put new record with new principal ID
                object_access_table.put_item(Item=item)
                
                # Delete old record with old principal ID
                try:
                    object_access_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old object access record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old object access record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating object access for user ID from {old_id} to {new_id}: {e}"
        )
        return False


### Assistants Tables ###
# "ASSISTANTS_ALIASES_DYNAMODB_TABLE": "amplify-v6-assistants-dev-assistant-aliases",
# DONE
def update_assistants_aliases_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistants aliases records associated with the old user ID to the new user ID."""
    msg = f"[update_assistants_aliases_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANTS_ALIASES_DYNAMODB_TABLE")
    assistants_aliases_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found assistants aliases record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (composite key: user + assistantId)
            old_key = {
                "user": old_id,
                "assistantId": item.get("assistantId")
            }
            
            # Update to new user ID
            item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would update assistants aliases item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old assistants aliases record with key: {old_key}")
            else:
                log(msg % f"Updating assistants aliases item to:\n\tNew Data: {item}")
                # Put new record with new user ID
                assistants_aliases_table.put_item(Item=item)
                
                # Delete old record with old user ID
                try:
                    assistants_aliases_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old assistants aliases record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old assistants aliases record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating assistants aliases for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "ASSISTANTS_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-assistants",
# DONE
# NOTE: The semantics of this are different than most other functions - it _UPDATES_ the
# record rathre than creating a new one. Why? Because the PK must be unique so we cannot
# just copy it. So our choices are: delete & recreate OR update. I chose update.
def update_assistants_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistants records associated with the old user ID to the new user ID."""
    msg = f"[update_assistants_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANTS_DYNAMODB_TABLE")
    assistants_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user", old_id, index_name="UserNameIndex"):
            log(
                msg
                % f"Found assistants record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update assistants item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating assistants item to:\n\tNew Data: {item}")
                # update item instead of creating new because the PK must be unique
                # in cases where there is no SK, like this one
                assistants_table.update_item(Key={"id": item["id"]},
                    UpdateExpression="SET #user = :new_id",
                    ExpressionAttributeNames={"#user": "user"},
                    ExpressionAttributeValues={":new_id": new_id},
)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating assistants for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-code-interpreter-assistants",
# "ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME": "amplify-v6-assistants-dev-code-interpreter-files", #Marked for deletion
# DONE
# Change Type: UPDATE
# True Tested (with Change): False
def update_assistant_code_interpreter_table(
    old_id: str, new_id: str, dry_run: bool
) -> bool:
    """Update all assistant code interpreter records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_code_interpreter_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE")
    
    # IMPLEMENTED: Code interpreter files S3 to S3_CONSOLIDATION_BUCKET migration
    # - Files: Migrated from ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME to S3_CONSOLIDATION_BUCKET_NAME  
    # - Old format: "{user_id}/{message_id}-{file_id}-FN-{filename}"
    # - New format: "codeInterpreter/{user_id}/{message_id}-{file_id}-FN-{filename}"
    # - User ID updates: old_id replaced with new_id in file paths
    # - DynamoDB updates: "user" field updated from old_id to new_id
    #
    # Processing flow:
    # 1. Call migrate_code_interpreter_files_bucket_for_user() to migrate S3 files
    # 2. Update "user" field from old_id to new_id in ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE
    success = migrate_code_interpreter_files_bucket_for_user(old_id, new_id, dry_run)
    
    if not success:
        log(msg % f"Failed to migrate code interpreter files for user {old_id}")
        return False
    
    if not table:
        log(msg % f"Table ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE not found, skipping DynamoDB updates")
        return success  # S3 migration was successful, so return that status
    
    assistant_code_interpreter_table = dynamodb.Table(table)
    try:
        ret = False
        for item in paginated_query(table, "user", old_id, index_name="UserIndex"):
            log(
                msg
                % f"Found assistant code interpreter record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # CRITICAL: Must use delete-old + create-new pattern since "id" is primary key containing old user ID
            old_id_field = item["id"]
            
            # Transform id field: replace old user ID with new user ID  
            # Pattern: "old_user_id/ast/uuid" -> "new_user_id/ast/uuid"
            new_id_field = old_id_field.replace(old_id, new_id, 1)  # Replace only first occurrence
            
            # Create updated item with new id and user fields
            updated_item = item.copy()
            updated_item["id"] = new_id_field
            updated_item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would delete old record with id: {old_id_field}")
                log(msg % f"Would create new record with id: {new_id_field}")
                log(msg % f"New Data: {updated_item}")
            else:
                log(msg % f"Creating new assistant code interpreter record with id: {new_id_field}")
                assistant_code_interpreter_table.put_item(Item=updated_item)
                
                log(msg % f"Deleting old assistant code interpreter record with id: {old_id_field}")
                assistant_code_interpreter_table.delete_item(Key={"id": old_id_field})
                
                log(msg % f"Successfully recreated assistant code interpreter record: {old_id_field} -> {new_id_field}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating assistant code interpreter for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "ASSISTANT_THREADS_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-assistant-threads",
# DONE
# Update Type: UPDATE
# True Tested (with Change): False
def update_assistant_threads_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistant threads records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_threads_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_THREADS_DYNAMODB_TABLE")
    if not table:
        log(msg % f"Table ASSISTANT_THREADS_DYNAMODB_TABLE not found, skipping")
        return True
        
    assistant_threads_table = dynamodb.Table(table)
    try:
        ret = False
        for item in paginated_scan(table, "user", old_id):
            log(
                msg
                % f"Found assistant threads record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup
            old_key = {
                "id": item["id"]
            }
            
            # Update user field
            item["user"] = new_id
            
            # Update id field if it contains the old user ID
            if "id" in item and item["id"].startswith(f"{old_id}/"):
                thread_suffix = item["id"][len(f"{old_id}/"):]  # Get everything after "old_id/"
                item["id"] = f"{new_id}/{thread_suffix}"
                log(msg % f"Updated id: {old_key['id']} -> {item['id']}")
            
            if dry_run:
                log(msg % f"Would update assistant threads item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old assistant threads record with key: {old_key}")
            else:
                log(msg % f"Updating assistant threads item to:\n\tNew Data: {item}")
                # Put new record with updated id and user
                assistant_threads_table.put_item(Item=item)
                
                # Delete old record with old id
                try:
                    assistant_threads_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old assistant threads record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old assistant threads record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating assistant threads for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-assistant-thread-runs",
def update_assistant_thread_runs_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistant thread runs records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_thread_runs_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE")
    if not table:
        log(msg % f"Table ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE not found, skipping")
        return True
        
    assistant_thread_runs_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan by user field (GSI might not exist)
        for item in paginated_scan(table, "user", old_id):
            log(
                msg
                % f"Found assistant thread runs record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup
            old_key = {
                "id": item["id"]
            }
            
            # Update user field
            item["user"] = new_id
            
            # Update id field if it contains the old user ID (pattern: {user_id}/run/{uuid})
            if "id" in item and item["id"].startswith(f"{old_id}/run/"):
                run_suffix = item["id"][len(f"{old_id}/"):]  # Get everything after "old_id/"
                item["id"] = f"{new_id}/{run_suffix}"
                log(msg % f"Updated id: {old_key['id']} -> {item['id']}")
            
            # Update assistant_key field if it contains the old user ID (pattern: {user_id}/ast/{uuid})
            if "assistant_key" in item and item["assistant_key"].startswith(f"{old_id}/ast/"):
                ast_suffix = item["assistant_key"][len(f"{old_id}/"):]  # Get everything after "old_id/"
                item["assistant_key"] = f"{new_id}/{ast_suffix}"
                log(msg % f"Updated assistant_key: {old_id}/... -> {item['assistant_key']}")
            
            # Update thread_key field if it contains the old user ID (pattern: {user_id}/thr/{uuid})
            if "thread_key" in item and item["thread_key"].startswith(f"{old_id}/thr/"):
                thr_suffix = item["thread_key"][len(f"{old_id}/"):]  # Get everything after "old_id/"
                item["thread_key"] = f"{new_id}/{thr_suffix}"
                log(msg % f"Updated thread_key: {old_id}/... -> {item['thread_key']}")
            
            if dry_run:
                log(msg % f"Would update assistant thread runs item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old assistant thread runs record with key: {old_key}")
            else:
                log(msg % f"Updating assistant thread runs item to:\n\tNew Data: {item}")
                # Put new record with updated fields
                assistant_thread_runs_table.put_item(Item=item)
                
                # Delete old record with old id
                try:
                    assistant_thread_runs_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old assistant thread runs record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old assistant thread runs record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating assistant thread runs for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "ASSISTANT_GROUPS_DYNAMO_TABLE" : "amplify-v6-object-access-dev-amplify-groups",
def update_assistant_groups_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistant groups records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_groups_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_GROUPS_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table ASSISTANT_GROUPS_DYNAMO_TABLE not found, skipping")
        return True
        
    assistant_groups_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan entire table once - check both createdBy field and members dict
        scanner = assistant_groups_table.scan()
        while True:
            for item in scanner.get('Items', []):
                updated = False
                
                # Update createdBy field if user is the creator
                if "createdBy" in item and item["createdBy"] == old_id:
                    item["createdBy"] = new_id
                    updated = True
                    log(msg % f"Updated createdBy: {old_id} -> {new_id}")
                
                # Update members dict if user is a member
                if "members" in item and isinstance(item["members"], dict):
                    members = item["members"]
                    if old_id in members:
                        permission = members[old_id]
                        del members[old_id]
                        members[new_id] = permission
                        item["members"] = members
                        updated = True
                        log(msg % f"Updated member: {old_id} -> {new_id} with permission {permission}")
                
                # Only process if we made changes
                if updated:
                    log(
                        msg
                        % f"Found assistant groups record for user ID {old_id}.\\n\\tExisting Data: {item}"
                    )
                    
                    if dry_run:
                        log(msg % f"Would update assistant groups item to:\\n\\tNew Data: {item}")
                    else:
                        log(msg % f"Updating assistant groups item to:\\n\\tNew Data: {item}")
                        assistant_groups_table.put_item(Item=item)
                    ret = True
            
            if 'LastEvaluatedKey' not in scanner:
                break
            scanner = assistant_groups_table.scan(ExclusiveStartKey=scanner['LastEvaluatedKey'])
            
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating assistant groups for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "ASSISTANT_LOOKUP_DYNAMODB_TABLE" : "amplify-v6-assistants-dev-assistant-lookup",
def update_assistant_lookup_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all assistant lookup records associated with the old user ID to the new user ID."""
    msg = f"[update_assistant_lookup_table][dry-run: {dry_run}] %s"
    table = table_names.get("ASSISTANT_LOOKUP_DYNAMODB_TABLE")
    if not table:
        log(msg % f"Table ASSISTANT_LOOKUP_DYNAMODB_TABLE not found, skipping")
        return True
        
    assistant_lookup_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan required - no GSI for user fields
        for item in paginated_scan(table, "createdBy", old_id):
            log(
                msg
                % f"Found assistant lookup record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Update createdBy field
            if "createdBy" in item and item["createdBy"] == old_id:
                item["createdBy"] = new_id
            
            # Update accessTo dict - contains "users" key with list of user IDs
            if "accessTo" in item and isinstance(item["accessTo"], dict):
                access_to = item["accessTo"]
                if "users" in access_to and isinstance(access_to["users"], list):
                    users_list = access_to["users"]
                    if old_id in users_list:
                        # Replace old_id with new_id in the users list
                        users_list = [new_id if user_id == old_id else user_id for user_id in users_list]
                        access_to["users"] = users_list
                        log(msg % f"Updated accessTo users: replaced {old_id} -> {new_id}")
                item["accessTo"] = access_to
            
            # Update pathHistory array - each entry has a "changedBy" field
            if "pathHistory" in item and isinstance(item["pathHistory"], list):
                path_history = item["pathHistory"]
                for history_entry in path_history:
                    if isinstance(history_entry, dict) and "changedBy" in history_entry:
                        if history_entry["changedBy"] == old_id:
                            history_entry["changedBy"] = new_id
                            log(msg % f"Updated pathHistory changedBy: {old_id} -> {new_id}")
                item["pathHistory"] = path_history
            
            if dry_run:
                log(msg % f"Would update assistant lookup item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating assistant lookup item to:\\n\\tNew Data: {item}")
                assistant_lookup_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating assistant lookup for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE" : "amplify-v6-assistants-dev-group-assistant-conversations",
# "S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME": "amplify-v6-assistants-dev-group-conversations-content" #Marked for deletion
def update_group_assistant_conversations_table(
    old_id: str, new_id: str, dry_run: bool
) -> bool:
    """Update all group assistant conversations records associated with the old user ID to the new user ID."""
    msg = f"[update_group_assistant_conversations_table][dry-run: {dry_run}] %s"
    table = table_names.get("GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE")
    
    # IMPLEMENTED: Group assistant conversation files S3 to S3_CONSOLIDATION_BUCKET migration
    # - Files: Migrated from S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME to S3_CONSOLIDATION_BUCKET_NAME  
    # - Old format: "astgp/{assistant-id}/{conversation-id}.txt"
    # - New format: "agentConversations/astgp/{assistant-id}/{conversation-id}.txt"
    #
    # FIXED Processing flow:
    # 1. ALWAYS: Call migrate_group_assistant_conversations_bucket_for_user() to migrate S3 files (idempotent)
    # 2. CONDITIONAL: Update ALL s3Location fields system-wide (only if legacy s3:// entries still exist)
    # 3. ALWAYS: Update "user" field from old_id to new_id for current user's records
    
    # Step 1: Always migrate S3 files (idempotent - will skip if already done)
    success = migrate_group_assistant_conversations_bucket_for_user(old_id, new_id, dry_run)
    
    if not success:
        log(msg % f"Failed to migrate group assistant conversation files for user {old_id}")
        return False
    
    if not table:
        log(msg % f"Table GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE not found, skipping DynamoDB updates")
        return success  # S3 migration was successful, so return that status
    
    try:
        group_assistant_conversations_table = dynamodb.Table(table)

        # Step 2: CONDITIONAL system-wide s3Location updates
        # Check if any records still have legacy s3:// format
        log(msg % f"Checking if system-wide s3Location updates are needed...")
        needs_s3_updates = _check_for_legacy_s3_locations(table, dry_run, AWS_REGION)
        
        if needs_s3_updates:
            log(msg % f"Legacy s3:// locations detected. Performing system-wide s3Location updates...")
            s3_update_success = _update_all_s3_locations_system_wide(table, dry_run, AWS_REGION)
            if not s3_update_success:
                log(msg % f"Warning: Failed to update some s3Location fields")
        else:
            log(msg % f"All s3Location fields already migrated, skipping system-wide updates")

        # Step 3: ALWAYS update user field for current user
        log(msg % f"Updating user field for {old_id} -> {new_id}")
        ret = False
        
        # Use paginated_scan since UserIndex GSI doesn't exist on this table
        for item in paginated_scan(table, "user", old_id):
            log(
                msg
                % f"Found group assistant conversation record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            if dry_run:
                log(msg % f"Would update group assistant conversation user field: {old_id} -> {new_id}")
            else:
                # Update the user field using update_item (more efficient than put_item)
                group_assistant_conversations_table.update_item(
                    Key={'conversationId': item['conversationId']},
                    UpdateExpression="SET #user = :new_id",
                    ExpressionAttributeNames={'#user': 'user'},
                    ExpressionAttributeValues={':new_id': new_id}
                )
                log(msg % f"Updated group assistant conversation user field: {old_id} -> {new_id}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating group assistant conversation records for user ID from {old_id} to {new_id}: {e}"
        )
        return False


def _check_for_legacy_s3_locations(table_name: str, dry_run: bool, region: str) -> bool:
    """Check if any records in the table still have legacy s3:// locations."""
    try:
        dynamodb = boto3.resource('dynamodb', region_name=region)
        table = dynamodb.Table(table_name)
        
        # Scan for records with s3Location containing s3://
        response = table.scan(
            FilterExpression="contains(s3Location, :s3_prefix)",
            ExpressionAttributeValues={":s3_prefix": "s3://"},
            Select='COUNT'  # Only count, don't return items
        )
        
        count = response.get('Count', 0)
        if dry_run:
            print(f"[DRY RUN] Found {count} records with legacy s3:// locations")
        else:
            print(f"Found {count} records with legacy s3:// locations")
        
        return count > 0
        
    except Exception as e:
        print(f"Warning: Could not check for legacy s3Location entries: {e}")
        # If we can't check, assume we need updates to be safe
        return True


def _update_all_s3_locations_system_wide(table_name: str, dry_run: bool, region: str) -> bool:
    """Update ALL s3Location fields that reference the old bucket format."""
    try:
        dynamodb = boto3.resource('dynamodb', region_name=region)
        table = dynamodb.Table(table_name)
        
        # Scan for records with s3Location containing s3://
        scan_kwargs = {
            'FilterExpression': "contains(s3Location, :s3_prefix)",
            'ExpressionAttributeValues': {":s3_prefix": "s3://"}
        }
        
        updated_count = 0
        failed_count = 0
        
        while True:
            response = table.scan(**scan_kwargs)
            
            for item in response.get('Items', []):
                try:
                    s3_location = item.get("s3Location", "")
                    if not s3_location.startswith("s3://"):
                        continue
                    
                    # Extract key from s3Location (remove s3://bucket-name/ prefix)
                    import re
                    match = re.search(r's3://[^/]+/(.+)', s3_location)
                    if match:
                        key_path = match.group(1)  # Extract "astgp/..." 
                        if key_path.startswith("astgp/"):
                            new_s3_location = f"agentConversations/{key_path}"
                            
                            if dry_run:
                                print(f"[DRY RUN] Would update s3Location: {s3_location} -> {new_s3_location}")
                                updated_count += 1
                            else:
                                # Update the record (conversationId is the only primary key)
                                table.update_item(
                                    Key={
                                        'conversationId': item['conversationId']
                                    },
                                    UpdateExpression="SET s3Location = :new_location",
                                    ExpressionAttributeValues={":new_location": new_s3_location}
                                )
                                print(f"Updated s3Location: {s3_location} -> {new_s3_location}")
                                updated_count += 1
                        else:
                            print(f"Warning: Unexpected s3Location format: {s3_location}")
                    else:
                        print(f"Warning: Could not parse s3Location: {s3_location}")
                        
                except Exception as item_error:
                    print(f"Warning: Failed to update record: {item_error}")
                    failed_count += 1
            
            # Handle pagination
            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        
        if dry_run:
            print(f"[DRY RUN] Would update {updated_count} s3Location fields system-wide")
        else:
            print(f"Updated {updated_count} s3Location fields system-wide")
        
        if failed_count > 0:
            print(f"Warning: Failed to update {failed_count} records")
        
        return failed_count == 0
        
    except Exception as e:
        print(f"Error during system-wide s3Location updates: {e}")
        return False



### Data source related tables ###
# "FILES_DYNAMO_TABLE" : "amplify-v6-lambda-dev-user-files",
def update_files_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all files records associated with the old user ID to the new user ID."""
    msg = f"[update_files_table][dry-run: {dry_run}] %s"
    table = table_names.get("FILES_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table FILES_DYNAMO_TABLE not found, skipping")
        return True
        
    files_table = dynamodb.Table(table)
    ret = False
    try:
        # Query by createdBy via createdBy GSI
        for item in paginated_query(table, "createdBy", old_id, index_name="createdBy"):
            log(
                msg
                % f"Found files record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["createdBy"] = new_id
            if item.get("updatedBy") == old_id:
                item["updatedBy"] = new_id
            if dry_run:
                log(msg % f"Would update files item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating files item to:\n\tNew Data: {item}")
                files_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating files for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "HASH_FILES_DYNAMO_TABLE" : "amplify-v6-lambda-dev-hash-files",
def update_hash_files_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all hash files records associated with the old user ID to the new user ID."""
    msg = f"[update_hash_files_table][dry-run: {dry_run}] %s"
    table = table_names.get("HASH_FILES_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table HASH_FILES_DYNAMO_TABLE not found, skipping")
        return True
        
    hash_files_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan required - no GSI for originalCreator
        for item in paginated_scan(table, "originalCreator", old_id):
            log(
                msg
                % f"Found hash files record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["originalCreator"] = new_id
            if dry_run:
                log(msg % f"Would update hash files item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating hash files item to:\n\tNew Data: {item}")
                hash_files_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating hash files for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "EMBEDDING_PROGRESS_TABLE" : "amplify-v6-embedding-dev-embedding-progress",
def update_embedding_progress_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all embedding progress records associated with the old user ID to the new user ID."""
    msg = f"[update_embedding_progress_table][dry-run: {dry_run}] %s"
    table = table_names.get("EMBEDDING_PROGRESS_TABLE")
    if not table:
        log(msg % f"Table EMBEDDING_PROGRESS_TABLE not found, skipping")
        return True
        
    embedding_progress_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan required - no GSI for originalCreator
        for item in paginated_scan(table, "originalCreator", old_id):
            log(
                msg
                % f"Found embedding progress record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["originalCreator"] = new_id
            if dry_run:
                log(msg % f"Would update embedding progress item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating embedding progress item to:\n\tNew Data: {item}")
                embedding_progress_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating embedding progress for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "USER_TAGS_DYNAMO_TABLE" : "amplify-v6-lambda-dev-user-tags",
def update_user_tags_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all user tags records associated with the old user ID to the new user ID."""
    msg = f"[update_user_tags_table][dry-run: {dry_run}] %s"
    table = table_names.get("USER_TAGS_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table USER_TAGS_DYNAMO_TABLE not found, skipping")
        return True
        
    user_tags_table = dynamodb.Table(table)
    ret = False
    try:
        # Query directly via primary key - "user" is the hash key
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found user tags record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (primary key: user)
            old_key = {
                "user": old_id
            }
            
            # Update to new user ID
            item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would update user tags item to:\\n\\tNew Data: {item}")
                log(msg % f"Would delete old user tags record with key: {old_key}")
            else:
                log(msg % f"Updating user tags item to:\\n\\tNew Data: {item}")
                # Put new record with new user ID
                user_tags_table.put_item(Item=item)
                
                # Delete old record with old user ID
                try:
                    user_tags_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old user tags record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old user tags record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating user tags for user ID from {old_id} to {new_id}: {e}"
        )
        return False


### AGENT LOOP TABLES ###
# "AGENT_STATE_DYNAMODB_TABLE": "amplify-v6-agent-loop-dev-agent-state"   *LESS IMPORTANT*
# "AGENT_STATE_BUCKET": "amplify-v6-agent-loop-dev-agent-state" #Marked for deletion
def update_agent_state_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all agent state records associated with the old user ID to the new user ID."""
    msg = f"[update_agent_state_table][dry-run: {dry_run}] %s"
    table = table_names.get("AGENT_STATE_DYNAMODB_TABLE")
    
    # IMPLEMENTED: Agent state files S3 to S3_CONSOLIDATION_BUCKET migration
    # - Files: Migrated from AGENT_STATE_BUCKET to S3_CONSOLIDATION_BUCKET_NAME  
    # - Old format: "{user_id}/{session_id}/agent_state.json" and "{user_id}/{session_id}/index.json"
    # - New format: "agentState/{user_id}/{session_id}/agent_state.json" and "agentState/{user_id}/{session_id}/index.json"
    # - User ID updates: old_id replaced with new_id in file paths
    # - DynamoDB updates: "user" field updated from old_id to new_id
    # - Memory field updates: Remove "bucket" field to indicate migrated state (use consolidation bucket)
    #
    # Processing flow:
    # 1. Call migrate_agent_state_bucket_for_user() to migrate S3 files
    # 2. Update "user" field from old_id to new_id in AGENT_STATE_DYNAMODB_TABLE
    # 3. Remove "memory.bucket" field to indicate migration (backward compatibility detection)
    # 4. Update "memory.key" to use new agentState/ prefix path
    success = migrate_agent_state_bucket_for_user(old_id, new_id, dry_run)
    
    if not success:
        log(msg % f"Failed to migrate agent state files for user {old_id}")
        return False
    
    if not table:
        log(msg % f"Table AGENT_STATE_DYNAMODB_TABLE not found, skipping DynamoDB updates")
        return success  # S3 migration was successful, so return that status
    
    try:
        agent_state_table = dynamodb.Table(table)

        ret = False
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found agent state records for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (composite key: user + sessionId)
            old_key = {
                "user": old_id,
                "sessionId": item.get("sessionId")
            }
            
            # Update user field from old_id to new_id
            item["user"] = new_id
            
            # Update memory field if present - remove bucket and update key path
            if "memory" in item and isinstance(item["memory"], dict):
                # Convert memory field from DynamoDB format to Python objects
                raw_memory = item["memory"]
                memory = {}
                
                # Convert each field in memory from DynamoDB type descriptors
                for field_name, field_value in raw_memory.items():
                    if isinstance(field_value, dict) and len(field_value) == 1:
                        type_key = list(field_value.keys())[0]
                        if type_key == 'S':  # String
                            memory[field_name] = field_value['S']
                        elif type_key == 'N':  # Number
                            memory[field_name] = float(field_value['N']) if '.' in field_value['N'] else int(field_value['N'])
                        elif type_key == 'M':  # Map - recursively convert
                            memory[field_name] = field_value['M']  # Keep as dict for now
                        else:
                            memory[field_name] = field_value  # Keep as-is for other types
                    else:
                        memory[field_name] = field_value  # Already converted or not DynamoDB format
                
                # Remove bucket field to indicate migrated state
                if "bucket" in memory:
                    # Handle corrupted bucket field structure
                    if isinstance(memory["bucket"], dict):
                        log(msg % f"WARNING: Corrupted memory.bucket field detected")
                        log(msg % f"Corrupted bucket structure: {memory['bucket']}")
                        
                        # Try to extract the actual bucket value before deleting (for logging purposes)
                        try:
                            if "S" in memory["bucket"] and isinstance(memory["bucket"]["S"], dict) and "S" in memory["bucket"]["S"]:
                                extracted_bucket = memory["bucket"]["S"]["S"]
                                log(msg % f"Extracted bucket value: {extracted_bucket}")
                        except Exception:
                            log(msg % f"Could not extract bucket value from corrupted structure")
                    
                    del memory["bucket"]
                
                # Update key path to use new agentState/ prefix
                if "key" in memory:
                    old_memory_key = memory["key"]
                    
                    # Handle corrupted records with remaining nested DynamoDB descriptors
                    if isinstance(old_memory_key, dict):
                        log(msg % f"WARNING: Corrupted memory.key field detected - attempting to extract value")
                        log(msg % f"Corrupted key structure: {old_memory_key}")
                        
                        # Try to extract the actual string value from remaining nested structure
                        try:
                            if "S" in old_memory_key and isinstance(old_memory_key["S"], dict) and "S" in old_memory_key["S"]:
                                extracted_key = old_memory_key["S"]["S"]
                                log(msg % f"Extracted key value: {extracted_key}")
                                old_memory_key = extracted_key  # Use extracted value
                            elif "S" in old_memory_key and isinstance(old_memory_key["S"], str):
                                # Single nested case
                                extracted_key = old_memory_key["S"]
                                log(msg % f"Extracted key value (single nested): {extracted_key}")
                                old_memory_key = extracted_key
                            else:
                                log(msg % f"Cannot extract value from corrupted structure - skipping key transformation")
                                old_memory_key = None
                        except Exception as extract_e:
                            log(msg % f"Failed to extract value from corrupted key: {extract_e}")
                            old_memory_key = None
                    
                    if isinstance(old_memory_key, str) and old_memory_key.startswith(f"{old_id}/"):
                        # Transform: "{old_id}/{session_id}/..." -> "agentState/{new_id}/{session_id}/..."
                        key_suffix = old_memory_key[len(f"{old_id}/"):]  # Remove old user prefix
                        memory["key"] = f"agentState/{new_id}/{key_suffix}"
                        log(msg % f"Transformed key: {old_memory_key} -> agentState/{new_id}/{key_suffix}")
                    elif isinstance(old_memory_key, str) and old_memory_key.startswith(f"agentState/{old_id}/"):
                        # Handle keys already in agentState format but with old user ID
                        key_suffix = old_memory_key[len(f"agentState/{old_id}/"):]
                        memory["key"] = f"agentState/{new_id}/{key_suffix}"
                        log(msg % f"Updated agentState key: {old_memory_key} -> agentState/{new_id}/{key_suffix}")
                    elif isinstance(old_memory_key, str):
                        log(msg % f"Key doesn't match expected patterns, leaving unchanged: {old_memory_key}")
                
                item["memory"] = memory
            
            if dry_run:
                log(msg % f"Would update agent state item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old agent state record with key: {old_key}")
            else:
                log(msg % f"Updating agent state item to:\n\tNew Data: {item}")
                # Put new record with new user ID
                agent_state_table.put_item(Item=item)
                
                # Delete old record with old user ID
                try:
                    agent_state_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old agent state record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old agent state record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating agent state records for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE": "amplify-v6-agent-loop-dev-agent-event-templates",
# DONE
def update_agent_event_templates_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all agent event templates records associated with the old user ID to the new user ID."""
    msg = f"[update_agent_event_templates_table][dry-run: {dry_run}] %s"
    table = table_names.get("AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE")
    agent_event_templates_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found agent event templates record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (composite key: user + tag)
            old_key = {
                "user": old_id,
                "tag": item.get("tag")
            }
            
            # Update to new user ID
            item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would update agent event template item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old agent event template record with key: {old_key}")
            else:
                log(msg % f"Updating agent event template item to:\n\tNew Data: {item}")
                # Put new record with new user ID
                agent_event_templates_table.put_item(Item=item)
                
                # Delete old record with old user ID
                try:
                    agent_event_templates_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old agent event template record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old agent event template record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating agent event templates for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "WORKFLOW_TEMPLATES_TABLE" : "amplify-v6-agent-loop-dev-workflow-registry",
# "WORKFLOW_TEMPLATES_BUCKET": "amplify-v6-agent-loop-dev-workflow-templates",
def update_workflow_templates_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all workflow templates records associated with the old user ID to the new user ID."""
    msg = f"[update_workflow_templates_table][dry-run: {dry_run}] %s"
    table = table_names.get("WORKFLOW_TEMPLATES_TABLE")
    workflow_templates_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found workflow templates record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # IMPLEMENTED: Workflow templates S3 to USER_DATA_STORAGE_TABLE migration
            # - "s3_key": Downloaded from S3 and migrated to USER_DATA_STORAGE_TABLE, then removed from record
            # - "template_uuid": Used directly as USER_DATA_STORAGE_TABLE SK (no transformation needed)
            # - "user": Updated from old_id to new_id (part of USER_DATA_STORAGE_TABLE PK)
            # Migration detection: Records without s3_key are considered migrated
            # 
            # Processing flow:
            # 1. Call migrate_workflow_templates_bucket_for_user() to migrate S3 content
            # 2. Update "user" attribute from old_id to new_id
            # 3. Remove "s3_key" from record (handled by migration function)
            success, updated_item = migrate_workflow_templates_bucket_for_user(old_id, new_id, dry_run, item, AWS_REGION)
            
            if not success:
                log(msg % f"Failed to migrate workflow template S3 content for user {old_id}, template: {item.get('templateId', 'unknown')}")
            
            # Store old key for cleanup (primary key: user + templateId)
            old_key = {
                "user": old_id,
                "templateId": item["templateId"]
            }
            
            # Update user_id and remove s3_key while preserving ALL other columns
            if updated_item:
                item = updated_item  # Use the updated item from migration
            # Remove s3Key after successful migration (should not exist in migrated records)
            if "s3Key" in item:
                del item["s3Key"]
                
            item["user"] = new_id
            
            # Fix isPublic type for GSI TemplateIdPublicIndex - expects Number not Boolean
            if "isPublic" in item and isinstance(item["isPublic"], bool):
                item["isPublic"] = 1 if item["isPublic"] else 0
            
            if dry_run:
                log(msg % f"Would update workflow template item to:\n\tNew Data: {item}"
)
            else:
                log(msg % f"Updating workflow template item to:\n\tNew Data: {item}")
                workflow_templates_table.put_item(Item=item)
                # Delete old record after successful creation
                try:
                    workflow_templates_table.delete_item(Key=old_key)
                    log(msg % f"Successfully deleted old workflow template record: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old workflow template record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating workflow templates for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "EMAIL_SETTINGS_DYNAMO_TABLE" : "amplify-v6-agent-loop-dev-email-allowed-senders",
def update_email_settings_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all email settings records associated with the old user ID to the new user ID."""
    msg = f"[update_email_settings_table][dry-run: {dry_run}] %s"
    table = table_names.get("EMAIL_SETTINGS_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table EMAIL_SETTINGS_DYNAMO_TABLE not found, skipping")
        return True
        
    email_settings_table = dynamodb.Table(table)
    ret = False
    try:
        # Single optimized scan - check both email field and allowedSenders list
        scanner = email_settings_table.scan()
        while True:
            for item in scanner.get('Items', []):
                updated = False
                is_primary_email = item.get("email") == old_id
                
                # Update email field if this user owns the email settings
                if is_primary_email:
                    log(msg % f"Found email settings record with email {old_id}.\\n\\tExisting Data: {item}")
                    
                    # Store old key for cleanup (primary key: email + tag)
                    old_key = {
                        "email": old_id,
                        "tag": item["tag"]
                    }
                    
                    item["email"] = new_id
                    updated = True
                
                # Update allowedSenders list if old_id appears in the list
                if "allowedSenders" in item and isinstance(item["allowedSenders"], list):
                    updated_senders = []
                    senders_changed = False
                    
                    for sender_pattern in item["allowedSenders"]:
                        # Only do replacement if old_id is actually in the string
                        if old_id in sender_pattern:
                            updated_pattern = sender_pattern.replace(old_id, new_id)
                            updated_senders.append(updated_pattern)
                            senders_changed = True
                            log(msg % f"Updated allowedSender: {sender_pattern} -> {updated_pattern}")
                        else:
                            updated_senders.append(sender_pattern)
                    
                    if senders_changed:
                        item["allowedSenders"] = updated_senders
                        updated = True
                        if not is_primary_email:
                            log(msg % f"Found email settings record with old_id in allowedSenders.\\n\\tExisting Data: {item}")
                
                # Save if any updates were made
                if updated:
                    if dry_run:
                        log(msg % f"Would update email settings item to:\\n\\tNew Data: {item}")
                        if is_primary_email:
                            log(msg % f"Would delete old email settings record with key: {old_key}")
                    else:
                        log(msg % f"Updating email settings item to:\\n\\tNew Data: {item}")
                        email_settings_table.put_item(Item=item)
                        
                        # Delete old record if primary email changed (requires new primary key)
                        if is_primary_email:
                            try:
                                email_settings_table.delete_item(Key=old_key)
                                log(msg % f"Successfully deleted old email settings record: {old_key}")
                            except Exception as delete_e:
                                log(msg % f"Warning: Failed to delete old email settings record {old_key}: {delete_e}")
                    ret = True
            
            if 'LastEvaluatedKey' not in scanner:
                break
            scanner = email_settings_table.scan(ExclusiveStartKey=scanner['LastEvaluatedKey'])
        
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating email settings for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "SCHEDULED_TASKS_TABLE" : "amplify-v6-agent-loop-dev-scheduled-tasks",
# "SCHEDULED_TASKS_LOGS_BUCKET": "amplify-v6-agent-loop-dev-scheduled-tasks-logs"
def update_scheduled_tasks_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all scheduled tasks records associated with the old user ID to the new user ID."""
    msg = f"[update_scheduled_tasks_table][dry-run: {dry_run}] %s"
    table = table_names.get("SCHEDULED_TASKS_TABLE")
    scheduled_tasks_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found scheduled tasks record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # IMPLEMENTED: Scheduled tasks logs S3 to USER_DATA_STORAGE_TABLE migration
            # - S3 logs: Consolidated from SCHEDULED_TASKS_LOGS_BUCKET/{old_id}/{task_id}/logs/*.json
            # - Target: USER_DATA_STORAGE_TABLE with PK: "{user_id}#amplify-agent-logs#scheduled-task-logs", SK: "{task_id}"
            # - appId: "{user_id}#amplify-agent-logs", entityType: "scheduled-task-logs"
            # - "detailsKey": Removed from SCHEDULED_TASKS_TABLE logs array entries after migration
            # Migration detection: Logs without detailsKey entries are considered migrated
            # 
            # Processing flow:
            # 1. Call migrate_scheduled_tasks_logs_bucket_for_user() to consolidate S3 logs → USER_DATA_STORAGE_TABLE
            # 2. Update "user" attribute from old_id to new_id in SCHEDULED_TASKS_TABLE
            # 3. Remove "detailsKey" from logs array entries (handled by migration function)
            success, updated_item = migrate_scheduled_tasks_logs_bucket_for_user(old_id, new_id, dry_run, item, AWS_REGION)
            
            if not success:
                log(msg % f"S3 migration failed for user {old_id}, task: {item.get('taskId', 'unknown')} - continuing with table update")
                # Continue with table update even if S3 migration fails (partial migration recovery)
            else:
                log(msg % f"S3 logs successfully consolidated to USER_DATA_STORAGE_TABLE for task: {item.get('taskId', 'unknown')}")
            
            # Store old key for cleanup (primary key: user + taskId)
            old_key = {
                "user": old_id,
                "taskId": item["taskId"]
            }
            
            # Update user_id and logs array while preserving ALL other columns
            if updated_item:
                item = updated_item  # Use the updated item from migration (with cleaned logs array)
                log(msg % f"Using updated item from migration - logs array cleaned of detailsKey entries")
            
            item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would update scheduled task item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating scheduled task item to:\n\tNew Data: {item}")
                scheduled_tasks_table.put_item(Item=item)
                # Delete old record after successful creation
                try:
                    scheduled_tasks_table.delete_item(Key=old_key)
                    log(msg % f"Successfully deleted old scheduled task record: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old scheduled task record {old_key}: {delete_e}")
            ret = True
            
        # After successful migration, clean up any orphaned logs in the tasks logs bucket
        log(msg % f"Checking for orphaned scheduled task logs for user {old_id}")
        try:
            from s3_data_migration import cleanup_orphaned_scheduled_task_logs
            cleanup_orphaned_scheduled_task_logs(old_id, dry_run, AWS_REGION)
        except Exception as cleanup_e:
            log(msg % f"Warning: Failed to clean up orphaned logs for user {old_id}: {cleanup_e}")
            # Don't fail the migration for cleanup errors
            
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating scheduled tasks for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "DB_CONNECTIONS_TABLE" : "amplify-v6-lambda-dev-db-connections",
def update_db_connections_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all db connections records associated with the old user ID to the new user ID."""
    msg = f"[update_db_connections_table][dry-run: {dry_run}] %s"
    table = table_names.get("DB_CONNECTIONS_TABLE")
    if not table:
        log(msg % f"Table DB_CONNECTIONS_TABLE not found, skipping")
        return True
        
    db_connections_table = dynamodb.Table(table)
    ret = False
    try:
        # Query by user via UserIndex GSI
        for item in paginated_query(table, "user", old_id, index_name="UserIndex"):
            log(
                msg
                % f"Found db connections record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update db connections item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating db connections item to:\\n\\tNew Data: {item}")
                db_connections_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating db connections for user ID from {old_id} to {new_id}: {e}"
        )
        return False


### INTEGRATION TABLES ###
# "OAUTH_STATE_TABLE" : "amplify-v6-assistants-api-dev-oauth-state",
def update_oauth_state_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all OAuth state records associated with the old user ID to the new user ID."""
    msg = f"[update_oauth_state_table][dry-run: {dry_run}] %s"
    table = table_names.get("OAUTH_STATE_TABLE")
    try:
        oauth_state_table = dynamodb.Table(table)

        # Note: Using in-place updates since primary key 'state' doesn't change
        # Only the 'user' attribute needs to be updated

        ret = False
        for item in paginated_scan(table, "user", old_id):
            log(
                msg
                % f"Found OAuth state records for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            state_value = item.get("state")
            
            if dry_run:
                log(msg % f"Would update OAuth state record {state_value}: user {old_id} -> {new_id}")
            else:
                # In-place update of user field (primary key 'state' stays the same)
                try:
                    oauth_state_table.update_item(
                        Key={"state": state_value},
                        UpdateExpression="SET #user = :new_user",
                        ExpressionAttributeNames={"#user": "user"},
                        ExpressionAttributeValues={":new_user": new_id}
                    )
                    log(msg % f"Updated OAuth state record {state_value}: user {old_id} -> {new_id}")
                except Exception as update_e:
                    log(msg % f"Error updating OAuth state record {state_value}: {update_e}")
                    continue
            ret = True
        return ret

    except Exception as e:
        log(
            msg
            % f"Error updating OAuth state records for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "OAUTH_USER_TABLE" : "amplify-v6-assistants-api-dev-user-oauth-integrations",
def update_oauth_user_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all OAuth user records associated with the old user ID to the new user ID."""
    msg = f"[update_oauth_user_table][dry-run: {dry_run}] %s"
    table = table_names.get("OAUTH_USER_TABLE")
    if not table:
        log(msg % f"Table OAUTH_USER_TABLE not found, skipping")
        return True
        
    oauth_user_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan required - composite primary key, need to check user_integration prefix
        for item in paginated_scan(table, "user_integration", old_id, begins_with=True):
            log(
                msg
                % f"Found OAuth user record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup before updating
            old_key = {
                "user_integration": item.get("user_integration")
            }
            
            # Update user_integration prefix: old_id/service -> new_id/service
            if "user_integration" in item:
                old_integration = item["user_integration"]
                if old_integration.startswith(f"{old_id}/"):
                    suffix = old_integration[len(f"{old_id}/"):]
                    item["user_integration"] = f"{new_id}/{suffix}"
                    log(msg % f"Updated user_integration: {old_integration} -> {item['user_integration']}")
            
            if dry_run:
                log(msg % f"Would update OAuth user item to:\n\tNew Data: {item}")
                log(msg % f"Would delete old OAuth user record with key: {old_key}")
            else:
                log(msg % f"Updating OAuth user item to:\n\tNew Data: {item}")
                # Put new record with new user_integration
                oauth_user_table.put_item(Item=item)
                
                # Delete old record with old user_integration
                try:
                    oauth_user_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old OAuth user record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old OAuth user record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating OAuth user for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "DATA_DISCLOSURE_ACCEPTANCE_TABLE" : "amplify-v6-data-disclosure-dev-acceptance",
def update_data_disclosure_acceptance_table(
    old_id: str, new_id: str, dry_run: bool
) -> bool:
    """Update all data disclosure acceptance records associated with the old user ID to the new user ID."""
    msg = f"[update_data_disclosure_acceptance_table][dry-run: {dry_run}] %s"
    table = table_names.get("DATA_DISCLOSURE_ACCEPTANCE_TABLE")
    if not table:
        log(msg % f"Table DATA_DISCLOSURE_ACCEPTANCE_TABLE not found, skipping")
        return True
        
    data_disclosure_acceptance_table = dynamodb.Table(table)
    ret = False
    try:
        # Query directly via primary key - "user" is the hash key
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found data disclosure acceptance record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (simple key: user)
            old_key = {
                "user": old_id
            }
            
            # Update to new user ID
            item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would update data disclosure acceptance item to:\\n\\tNew Data: {item}")
                log(msg % f"Would delete old data disclosure acceptance record with key: {old_key}")
            else:
                log(msg % f"Updating data disclosure acceptance item to:\\n\\tNew Data: {item}")
                # Put new record with new user ID
                data_disclosure_acceptance_table.put_item(Item=item)
                
                # Delete old record with old user ID
                try:
                    data_disclosure_acceptance_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old data disclosure acceptance record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old data disclosure acceptance record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating data disclosure acceptance for user ID from {old_id} to {new_id}: {e}"
        )
        return False


### Cost calculation related tables ###
# "COST_CALCULATIONS_DYNAMO_TABLE" : "amplify-v6-lambda-dev-cost-calculations",
def update_cost_calculations_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all cost calculations records associated with the old user ID to the new user ID."""
    msg = f"[update_cost_calculations_table][dry-run: {dry_run}] %s"
    table = table_names.get("COST_CALCULATIONS_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table COST_CALCULATIONS_DYNAMO_TABLE not found, skipping")
        return True
        
    cost_calculations_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan for all records where id == old_id
        for item in paginated_scan(table, "id", old_id):
            log(
                msg
                % f"Found cost calculations record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (primary key: id + accountInfo)
            old_key = {
                "id": old_id,
                "accountInfo": item["accountInfo"]
            }
            
            # Update id field from old_id to new_id
            item["id"] = new_id
            
            if dry_run:
                log(msg % f"Would update cost calculations item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating cost calculations item to:\\n\\tNew Data: {item}")
                cost_calculations_table.put_item(Item=item)
                # Delete old record after successful creation
                try:
                    cost_calculations_table.delete_item(Key=old_key)
                    log(msg % f"Successfully deleted old cost calculations record: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old cost calculations record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating cost calculations for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "HISTORY_COST_CALCULATIONS_DYNAMO_TABLE" : "amplify-v6-lambda-dev-history-cost-calculations",
def update_history_cost_calculations_table(
    old_id: str, new_id: str, dry_run: bool
) -> bool:
    """Update all history cost calculations records associated with the old user ID to the new user ID."""
    msg = f"[update_history_cost_calculations_table][dry-run: {dry_run}] %s"
    table = table_names.get("HISTORY_COST_CALCULATIONS_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table HISTORY_COST_CALCULATIONS_DYNAMO_TABLE not found, skipping")
        return True
        
    history_cost_calculations_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan for records with userDate prefix matching old_id
        for item in paginated_scan(table, "userDate", old_id, begins_with=True):
            log(
                msg
                % f"Found history cost calculations record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (primary key: userDate + accountInfo)
            old_key = {
                "userDate": item["userDate"],
                "accountInfo": item["accountInfo"]
            }
            
            # Update userDate prefix: old_id#date -> new_id#date
            if "userDate" in item:
                old_user_date = item["userDate"]
                if old_user_date.startswith(f"{old_id}#"):
                    date_suffix = old_user_date[len(f"{old_id}#"):]
                    item["userDate"] = f"{new_id}#{date_suffix}"
                    log(msg % f"Updated userDate: {old_user_date} -> {item['userDate']}")
            
            if dry_run:
                log(msg % f"Would update history cost calculations item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating history cost calculations item to:\n\tNew Data: {item}")
                history_cost_calculations_table.put_item(Item=item)
                # Delete old record after successful creation
                try:
                    history_cost_calculations_table.delete_item(Key=old_key)
                    log(msg % f"Successfully deleted old history cost calculations record: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old history cost calculations record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating history cost calculations for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "ADDITIONAL_CHARGES_TABLE": "amplify-v6-chat-billing-dev-additional-charges",
def update_additional_charges_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all additional charges records associated with the old user ID to the new user ID."""
    msg = f"[update_additional_charges_table][dry-run: {dry_run}] %s"
    table = table_names.get("ADDITIONAL_CHARGES_TABLE")
    if not table:
        log(msg % f"Table ADDITIONAL_CHARGES_TABLE not found, skipping")
        return True
        
    additional_charges_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan required - no GSI for user field
        for item in paginated_scan(table, "user", old_id):
            log(
                msg
                % f"Found additional charges record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (primary key: id)
            old_key = {
                "id": item["id"]
            }
            
            # Update user field
            item["user"] = new_id
            
            # Update id field if it contains the old user ID (pattern: {user_id}/thr/{uuid}/{user_id}/ast/{uuid})
            if "id" in item and old_id in item["id"]:
                # Replace all occurrences of old_id with new_id in the composite key
                item["id"] = item["id"].replace(old_id, new_id)
                log(msg % f"Updated id: {old_key['id']} -> {item['id']}")
            
            if dry_run:
                log(msg % f"Would update additional charges item to:\\n\\tNew Data: {item}")
                log(msg % f"Would delete old additional charges record with key: {old_key}")
            else:
                log(msg % f"Updating additional charges item to:\\n\\tNew Data: {item}")
                # Put new record with updated id and user
                additional_charges_table.put_item(Item=item)
                
                # Delete old record with old id
                try:
                    additional_charges_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old additional charges record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old additional charges record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating additional charges for user ID from {old_id} to {new_id}: {e}"
        )
        return False


### Chat related tables ###
# "CHAT_USAGE_DYNAMO_TABLE" : "amplify-v6-lambda-dev-chat-usage",
def update_chat_usage_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all chat usage records associated with the old user ID to the new user ID."""
    msg = f"[update_chat_usage_table][dry-run: {dry_run}] %s"
    table = table_names.get("CHAT_USAGE_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table CHAT_USAGE_DYNAMO_TABLE not found, skipping")
        return True
        
    chat_usage_table = dynamodb.Table(table)
    ret = False
    try:
        # Query by user via UserUsageTimeIndex GSI
        for item in paginated_query(table, "user", old_id, index_name="UserUsageTimeIndex"):
            log(
                msg
                % f"Found chat usage record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update chat usage item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating chat usage item to:\\n\\tNew Data: {item}")
                chat_usage_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating chat usage for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "CONVERSATION_METADATA_TABLE" : "amplify-v6-lambda-dev-conversation-metadata",
# "S3_CONVERSATIONS_BUCKET_NAME": "amplify-v6-lambda-dev-user-conversations", #Marked for deletion
def update_conversation_metadata_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all conversation metadata records associated with the old user ID to the new user ID."""
    msg = f"[update_conversation_metadata_table][dry-run: {dry_run}] %s"
    table = table_names.get("CONVERSATION_METADATA_TABLE")
    
    # Skip if table doesn't exist  
    if skip_if_table_missing(table, "update_conversation_metadata_table"):
        return True
    
    # IMPLEMENTED: Conversations S3 to S3_CONSOLIDATION_BUCKET migration
    # - Conversations: Migrated from S3_CONVERSATIONS_BUCKET_NAME to S3_CONSOLIDATION_BUCKET_NAME
    # - Old prefix: "{old_id}/" → New prefix: "conversations/{new_id}/"
    # - Migration detection: Not needed - all conversations move to consolidation bucket
    # - DynamoDB updates: "user_id" field and "s3_key" prefix updated
    #
    # Processing flow:
    # 1. Call migrate_conversations_bucket_for_user() to migrate S3 conversations
    # 2. Update "user_id" field from old_id to new_id in CONVERSATION_METADATA_TABLE
    # 3. Update "s3_key" prefix from old_id to new_id
    success = migrate_conversations_bucket_for_user(old_id, new_id, dry_run)
    
    if not success:
        log(msg % f"Failed to migrate conversations for user {old_id}")
        return False
    
    try:
        conversation_metadata_table = dynamodb.Table(table)
        ret = False
        
        for item in paginated_query(table, "user_id", old_id):
            log(
                msg
                % f"Found conversation metadata record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (primary key: user_id + conversation_id)
            old_key = {
                "user_id": old_id,
                "conversation_id": item["conversation_id"]
            }
            
            # Update user_id and s3_key prefix
            item["user_id"] = new_id
            if "s3_key" in item and item["s3_key"].startswith(f"{old_id}/"):
                old_s3_key = item["s3_key"]
                conversation_id = old_s3_key[len(f"{old_id}/"):]
                item["s3_key"] = f"conversations/{new_id}/{conversation_id}"
                
            if dry_run:
                log(msg % f"Would update conversation metadata item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating conversation metadata item to:\\n\\tNew Data: {item}")
                conversation_metadata_table.put_item(Item=item)
                # Delete old record after successful creation
                try:
                    conversation_metadata_table.delete_item(Key=old_key)
                    log(msg % f"Successfully deleted old conversation metadata record: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old conversation metadata record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating conversation metadata for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "USER_DATA_STORAGE_TABLE" : "amplify-v6-lambda-basic-ops-dev-user-storage",
def update_user_storage_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all user storage records associated with the old user ID to the new user ID."""
    msg = f"[update_user_storage_table][dry-run: {dry_run}] %s"
    table = table_names.get("USER_DATA_STORAGE_TABLE")
    if not table:
        log(msg % f"Table USER_DATA_STORAGE_TABLE not found, skipping")
        return True
        
    user_storage_table = dynamodb.Table(table)
    ret = False
    try:
        # Scan for records with PK prefix matching old_id
        for item in paginated_scan(table, "PK", old_id, begins_with=True):
            log(
                msg
                % f"Found user storage record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Update PK prefix: old_id#suffix -> new_id#suffix
            if "PK" in item:
                old_pk = item["PK"]
                if old_pk.startswith(f"{old_id}#"):
                    pk_suffix = old_pk[len(f"{old_id}#"):]
                    item["PK"] = f"{new_id}#{pk_suffix}"
                    log(msg % f"Updated PK: {old_pk} -> {item['PK']}")
            
            # Update appId prefix: old_id#suffix -> new_id#suffix
            if "appId" in item:
                old_app_id = item["appId"]
                if old_app_id.startswith(f"{old_id}#"):
                    app_id_suffix = old_app_id[len(f"{old_id}#"):]
                    item["appId"] = f"{new_id}#{app_id_suffix}"
                    log(msg % f"Updated appId: {old_app_id} -> {item['appId']}")
            
            if dry_run:
                log(msg % f"Would update user storage item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating user storage item to:\n\tNew Data: {item}")
                user_storage_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating user storage for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "MEMORY_DYNAMO_TABLE": "amplify-v6-memory-dev-memory",
def update_memory_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all memory records associated with the old user ID to the new user ID."""
    msg = f"[update_memory_table][dry-run: {dry_run}] %s"
    table = table_names.get("MEMORY_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table MEMORY_DYNAMO_TABLE not found, skipping")
        return True

    try:
        memory_table = dynamodb.Table(table)
        log(msg % f"Processing table {table}")

        # Query using UserIndex GSI to find all memories for this user
        items_updated = 0
        for item in paginated_query(table, "user", old_id, "UserIndex"):
            memory_id = item.get("id")
            current_user = item.get("user")
            
            if current_user == old_id:
                log(msg % f"Found memory record with old user ID. Memory ID: {memory_id}")
                
                if dry_run:
                    log(msg % f"Would update memory {memory_id} from user '{old_id}' to '{new_id}'")
                    items_updated += 1
                else:
                    # Update both user and memory_type_id fields for this memory
                    try:
                        memory_table.update_item(
                            Key={"id": memory_id},
                            UpdateExpression="SET #user = :new_user, #memory_type_id = :new_user",
                            ExpressionAttributeNames={
                                "#user": "user",
                                "#memory_type_id": "memory_type_id"
                            },
                            ExpressionAttributeValues={":new_user": new_id}
                        )
                        log(msg % f"Updated memory {memory_id}: user and memory_type_id '{old_id}' -> '{new_id}'")
                        items_updated += 1
                    except Exception as e:
                        log(msg % f"Failed to update memory {memory_id}: {str(e)}")
                        return False

        log(msg % f"Processed {items_updated} memory records for user {old_id}")
        return True

    except Exception as e:
        log(msg % f"Error processing memory table {table}: {str(e)}")
        return False

# "COMMON_DATA_DYNAMO_TABLE": Software Engineer service common data table
def update_common_data_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all common data records associated with the old user ID to the new user ID."""
    msg = f"[update_common_data_table][dry-run: {dry_run}] %s"
    table = table_names.get("COMMON_DATA_DYNAMO_TABLE")
    if not table:
        log(msg % f"Table COMMON_DATA_DYNAMO_TABLE not found, skipping")
        return True
        
    try:
        common_data_table = dynamodb.Table(table)
        ret = False
        
        # Scan for records where PK starts with old_id# (format: {user_id}#{entity_type})
        for item in paginated_scan(table, "PK", old_id, begins_with=True):
            log(
                msg
                % f"Found common data record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Store old key for cleanup (composite key: PK + SK)
            old_key = {
                "PK": item["PK"],
                "SK": item["SK"]
            }
            
            # Update PK: {old_id}#{entity_type} -> {new_id}#{entity_type}
            if "PK" in item and item["PK"].startswith(f"{old_id}#"):
                entity_type_suffix = item["PK"][len(f"{old_id}#"):]
                item["PK"] = f"{new_id}#{entity_type_suffix}"
                log(msg % f"Updated PK: {old_key['PK']} -> {item['PK']}")
            
            # Update app_id field (contains user_id)
            if "app_id" in item and item["app_id"] == old_id:
                item["app_id"] = new_id
                log(msg % f"Updated app_id: {old_id} -> {new_id}")
            
            if dry_run:
                log(msg % f"Would update common data item to:\\n\\tNew Data: {item}")
                log(msg % f"Would delete old common data record with key: {old_key}")
            else:
                log(msg % f"Updating common data item to:\\n\\tNew Data: {item}")
                # Put new record with updated PK and app_id
                common_data_table.put_item(Item=item)
                
                # Delete old record with old PK
                try:
                    common_data_table.delete_item(Key=old_key)
                    log(msg % f"Deleted old common data record with key: {old_key}")
                except Exception as delete_e:
                    log(msg % f"Warning: Failed to delete old common data record {old_key}: {delete_e}")
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating common data for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "DYNAMO_DYNAMIC_CODE_TABLE": Software Engineer service dynamic code table  
def update_dynamic_code_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all dynamic code records associated with the old user ID to the new user ID."""
    msg = f"[update_dynamic_code_table][dry-run: {dry_run}] %s"
    table = table_names.get("DYNAMO_DYNAMIC_CODE_TABLE")
    if not table:
        log(msg % f"Table DYNAMO_DYNAMIC_CODE_TABLE not found, skipping")
        return True
        
    try:
        dynamic_code_table = dynamodb.Table(table)
        ret = False
        
        # Single scan to check all records for any fields containing old_id
        scanner = dynamic_code_table.scan()
        while True:
            for item in scanner.get('Items', []):
                updates = {}
                update_expression_parts = []
                expression_names = {}
                expression_values = {}
                
                # Check creator field
                if "creator" in item and item["creator"] == old_id:
                    updates["creator"] = new_id
                    update_expression_parts.append("#creator = :creator")
                    expression_names["#creator"] = "creator"
                    expression_values[":creator"] = new_id
                
                # Check mapped_by field
                if "mapped_by" in item and item["mapped_by"] == old_id:
                    updates["mapped_by"] = new_id
                    update_expression_parts.append("#mapped_by = :mapped_by")
                    expression_names["#mapped_by"] = "mapped_by"
                    expression_values[":mapped_by"] = new_id
                
            
                # Update record if any changes needed
                if updates:
                    uuid_key = item["uuid"]
                    log(msg % f"Found dynamic code record for user ID {old_id}. UUID: {uuid_key}")
                    log(msg % f"Updates needed: {list(updates.keys())}")
                    
                    if dry_run:
                        log(msg % f"Would update dynamic code record {uuid_key} with: {updates}")
                    else:
                        update_expression = "SET " + ", ".join(update_expression_parts)
                        dynamic_code_table.update_item(
                            Key={"uuid": uuid_key},
                            UpdateExpression=update_expression,
                            ExpressionAttributeNames=expression_names,
                            ExpressionAttributeValues=expression_values
                        )
                        log(msg % f"Updated dynamic code record {uuid_key} with: {updates}")
                    ret = True
            
            if 'LastEvaluatedKey' not in scanner:
                break
            scanner = dynamic_code_table.scan(ExclusiveStartKey=scanner['LastEvaluatedKey'])
            
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating dynamic code for user ID from {old_id} to {new_id}: {e}"
        )
        return False


def migrate_all_user_data_storage_ids(users_map: dict, dry_run: bool) -> bool:
    """
    Step 1: Migrate ALL existing entries in USER_DATA_STORAGE table from old IDs to new IDs.
    This is done FIRST while the table is at its smallest state, before we migrate more data into it.
    
    Args:
        users_map: Dictionary mapping old_id -> new_id
        dry_run: If True, only show what would be done
    
    Returns:
        bool: Success status
    """
    msg = f"[migrate_all_user_data_storage_ids][dry-run: {dry_run}] %s"
    
    # Get the USER_DATA_STORAGE table (the new consolidated table)
    table_name = table_names.get("USER_DATA_STORAGE_TABLE")
    if not table_name:
        log(msg % "USER_DATA_STORAGE_TABLE not found in config, skipping")
        return True
    
    log(msg % f"Migrating all IDs in {table_name} from old to new...")
    
    try:
        table = dynamodb.Table(table_name)
        updated_count = 0
        
        # Scan entire table and update all records using proper pagination
        scan_kwargs = {}
        while True:
            response = table.scan(**scan_kwargs)
            for item in response.get('Items', []):
                # Check if PK or SK contains any old user IDs
                pk = item.get('PK', '')
                sk = item.get('SK', '')
                updated = False
                new_pk = pk
                new_sk = sk
                
                for old_id, new_id in users_map.items():
                    # Check and update PK (existing logic)
                    if pk.startswith(f"{old_id}#"):
                        pk_suffix = pk[len(f"{old_id}#"):]
                        new_pk = f"{new_id}#{pk_suffix}"
                        updated = True
                    
                    # MALFORMED ENTRY REPAIR: Check for entries with old user ID embedded in app_id portion
                    # Pattern: "new_id#old-user-id-sanitized-app-name#entity"
                    # Should be: "new_id#app-name#entity"
                    old_id_sanitized = old_id.replace("@", "-").replace(".", "-")
                    malformed_patterns = [
                        f"{new_id}#{old_id_sanitized}-amplify-workflows#workflow-templates",
                        f"{new_id}#{old_id_sanitized}-amplify-user-settings#user-settings", 
                        f"{new_id}#{old_id_sanitized}-amplify-artifacts#artifact-content",
                        f"{new_id}#{old_id_sanitized}-amplify-agent-logs#scheduled-task-logs"
                    ]
                    
                    correct_replacements = [
                        f"{new_id}#amplify-workflows#workflow-templates",
                        f"{new_id}#amplify-user-settings#user-settings",
                        f"{new_id}#amplify-artifacts#artifact-content", 
                        f"{new_id}#amplify-agent-logs#scheduled-task-logs"
                    ]
                    
                    # Check if PK matches any malformed pattern
                    for malformed_pk, correct_pk in zip(malformed_patterns, correct_replacements):
                        if pk == malformed_pk:
                            new_pk = correct_pk
                            updated = True
                            log(msg % f"DETECTED MALFORMED PK: {pk} -> {new_pk}")
                            break
                    
                    # Check and update SK for shared artifacts pattern: shared-with-{user_id}#
                    if sk.startswith(f"shared-with-{old_id}#"):
                        sk_suffix = sk[len(f"shared-with-{old_id}#"):]
                        new_sk = f"shared-with-{new_id}#{sk_suffix}"
                        updated = True
                    
                    # Check and update SK for shares pattern: {sharer_user_id}#{date}#{uuid}
                    elif sk.startswith(f"{old_id}#") and "#amplify-shares#" in pk:
                        sk_suffix = sk[len(f"{old_id}#"):]
                        new_sk = f"{new_id}#{sk_suffix}"
                        updated = True
                
                # Check data fields for ALL items (not just ones with PK/SK updates)
                data_key_updated = False
                
                # Update data fields in shares and other records
                if 'data' in item and isinstance(item['data'], dict):
                    # Update sharedBy field
                    if 'sharedBy' in item['data']:
                        shared_by = item['data']['sharedBy']
                        for old_id, new_id in users_map.items():
                            if shared_by == old_id:
                                item['data']['sharedBy'] = new_id
                                data_key_updated = True
                                break
                    
                    # Update key field for shares AND received (S3 paths containing user IDs)
                    # Handle both DynamoDB attribute format and clean format
                    data = item.get('data', {})
                    key_value = None
                    
                    # Extract key from DynamoDB format or clean format
                    if isinstance(data, dict):
                        if 'M' in data and isinstance(data['M'], dict) and 'key' in data['M']:
                            # DynamoDB attribute format: data.M.key.S
                            key_attr = data['M']['key']
                            if isinstance(key_attr, dict) and 'S' in key_attr:
                                key_value = key_attr['S']
                        elif 'key' in data:
                            # Clean format: data.key
                            key_value = data['key']
                    
                    if key_value:
                        entity_type = item.get('entityType', '')
                        
                        # Handle both shares and received entity types
                        if entity_type in ['received'] or "#amplify-shares#" in pk:
                            old_key = key_value
                            new_key = old_key
                            key_updated = False
                            
                            log(msg % f"DEBUG: Processing key update - PK: {pk}, entityType: {entity_type}, old_key: {old_key}")
                            log(msg % f"DEBUG: Available user mappings: {users_map}")
                            
                            # Replace old user IDs with new user IDs in S3 paths
                            for old_id, new_id in users_map.items():
                                if old_id in new_key:
                                    new_key = new_key.replace(old_id, new_id)
                                    key_updated = True
                                    data_key_updated = True
                                    log(msg % f"✅ Replacing '{old_id}' with '{new_id}' in S3 key (entityType: {entity_type})")
                            
                            # Ensure key has shares/ prefix for consistency with S3 structure
                            if not new_key.startswith("shares/") and key_updated:
                                new_key = f"shares/{new_key}"
                            
                            if new_key != old_key:
                                # Update key in correct format (DynamoDB attribute or clean)
                                if 'M' in item['data'] and isinstance(item['data']['M'], dict) and 'key' in item['data']['M']:
                                    # DynamoDB attribute format: update data.M.key.S
                                    item['data']['M']['key']['S'] = new_key
                                else:
                                    # Clean format: update data.key
                                    item['data']['key'] = new_key
                                log(msg % f"✅ Updated S3 key: {old_key} -> {new_key}")
                           
                
                # Now handle PK/SK updates if needed
                if updated or data_key_updated:
                    if dry_run:
                        if new_pk != pk:
                            log(msg % f"Would update PK: {pk} -> {new_pk}")
                        if new_sk != sk:
                            log(msg % f"Would update SK: {sk} -> {new_sk}")
                    else:
                        # Delete old record
                        table.delete_item(Key={'PK': item['PK'], 'SK': item['SK']})
                        
                        # Update item with new PK/SK
                        item['PK'] = new_pk
                        item['SK'] = new_sk
                        
                        # Also update appId if present (for both normal and malformed entries)
                        if 'appId' in item:
                            for old_id, new_id in users_map.items():
                                # Normal appId update
                                if item['appId'].startswith(f"{old_id}#"):
                                    app_id_suffix = item['appId'][len(f"{old_id}#"):]
                                    item['appId'] = f"{new_id}#{app_id_suffix}"
                                    break
                                
                                # MALFORMED appId REPAIR: Fix embedded old user ID in appId
                                old_id_sanitized = old_id.replace("@", "-").replace(".", "-")
                                malformed_app_patterns = [
                                    f"{new_id}#{old_id_sanitized}-amplify-workflows",
                                    f"{new_id}#{old_id_sanitized}-amplify-user-settings",
                                    f"{new_id}#{old_id_sanitized}-amplify-artifacts",
                                    f"{new_id}#{old_id_sanitized}-amplify-agent-logs"
                                ]
                                
                                correct_app_replacements = [
                                    f"{new_id}#amplify-workflows",
                                    f"{new_id}#amplify-user-settings", 
                                    f"{new_id}#amplify-artifacts",
                                    f"{new_id}#amplify-agent-logs"
                                ]
                                
                                for malformed_app, correct_app in zip(malformed_app_patterns, correct_app_replacements):
                                    if item['appId'] == malformed_app:
                                        item['appId'] = correct_app
                                        log(msg % f"REPAIRED MALFORMED appId: {malformed_app} -> {correct_app}")
                                        break
                        
                        # Put new record
                        table.put_item(Item=item)
                        
                        changes = []
                        if new_pk != pk:
                            changes.append(f"PK: {pk} -> {new_pk}")
                        if new_sk != sk:
                            changes.append(f"SK: {sk} -> {new_sk}")
                        log(msg % f"Updated {', '.join(changes)}")
                    
                    updated_count += 1
            
            # Check for more pages
            if 'LastEvaluatedKey' not in response:
                break
            
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        
        log(msg % f"Updated {updated_count} records in USER_DATA_STORAGE table")
        return True
        
    except Exception as e:
        log(msg % f"Error migrating USER_DATA_STORAGE table IDs: {e}")
        return False


def migrate_user_storage_to_user_data_storage(users_map: dict, dry_run: bool, old_table: str, new_table: str) -> bool:
    """
    Step 2: Migrate all data from USER_DATA_STORAGE_TABLE (basic-ops) to USER_DATA_STORAGE_TABLE,
    translating IDs on the fly as we copy.
    
    Args:
        users_map: Dictionary mapping old_id -> new_id
        dry_run: If True, only show what would be done
        old_table: Source table (USER_DATA_STORAGE_TABLE in basic-ops)
        new_table: Target table (USER_DATA_STORAGE_TABLE)
    
    Returns:
        bool: Success status
    """
    global dynamodb
    msg = f"[migrate_user_storage_to_user_data_storage][dry-run: {dry_run}] %s"
    
    log(msg % f"Migrating from {old_table} to {new_table} with ID translation...")
    
    try:
        # First create backup if not exists
        import os
        backup_csv = "user_storage_backup.csv"
        
        if not os.path.exists(backup_csv):
            log(msg % f"Creating backup of {old_table}...")
            if not dry_run:
                backup_file, item_count = backup_user_storage_table(old_table)
                if not backup_file:
                    log(msg % f"Failed to create backup of {old_table}")
                    return False
                os.rename(backup_file, backup_csv)
                log(msg % f"Created backup: {backup_csv} with {item_count} items")
        
        # Now migrate with ID translation
        source_table = dynamodb.Table(old_table)
        target_table = dynamodb.Table(new_table)
        
        migrated_count = 0
        paginator = source_table.scan()
        
        while True:
            for item in paginator.get('Items', []):
                # Translate IDs in PK
                pk = item.get('PK', '')
                updated = False
                
                for old_id, new_id in users_map.items():
                    if pk.startswith(f"{old_id}#"):
                        # Update PK from old_id to new_id
                        pk_suffix = pk[len(f"{old_id}#"):]
                        item['PK'] = f"{new_id}#{pk_suffix}"
                        
                        # Also update appId if present
                        if 'appId' in item and item['appId'].startswith(f"{old_id}#"):
                            app_id_suffix = item['appId'][len(f"{old_id}#"):]
                            item['appId'] = f"{new_id}#{app_id_suffix}"
                        
                        updated = True
                        break
                
                # Migrate to target table
                if dry_run:
                    if updated:
                        log(msg % f"Would migrate with ID translation: {pk} -> {item['PK']}")
                    else:
                        log(msg % f"Would migrate unchanged: {pk}")
                else:
                    target_table.put_item(Item=item)
                    if updated:
                        log(msg % f"Migrated with ID translation: {pk} -> {item['PK']}")
                    migrated_count += 1
            
            # Check for more pages
            if 'LastEvaluatedKey' not in paginator:
                break
            
            paginator = source_table.scan(ExclusiveStartKey=paginator['LastEvaluatedKey'])
        
        log(msg % f"Migrated {migrated_count} records from {old_table} to {new_table}")
        return True
        
    except Exception as e:
        log(msg % f"Error migrating user storage tables: {e}")
        return False


def ensure_user_storage_migration(dry_run: bool, old_table: str, new_table: str) -> bool:
    """
    Ensure user storage table migration from basic-ops to amplify-lambda.
    
    Steps:
    1. Check if backup CSV exists, if not create it
    2. Check if new table exists (user-data-storage suffix)  
    3. If new table exists, migrate data from CSV
    """
    global dynamodb_client
    msg = f"[ensure_user_storage_migration][dry-run: {dry_run}] %s"
    
    # Backup CSV filename
    backup_csv = "user_storage_backup.csv"
    
    log(msg % f"Checking user storage migration status...")
    
    try:
        # Check if backup CSV exists
        import os
        if not os.path.exists(backup_csv):
            log(msg % f"Backup CSV {backup_csv} not found. Creating backup...")
            
            if dry_run:
                log(msg % f"[DRY RUN] Would create backup of {old_table}")
                return True
            else:
                # Create backup
                backup_file, item_count = backup_user_storage_table(old_table)
                if not backup_file:
                    log(msg % f"Failed to create backup of {old_table}")
                    return False
                
                # Rename to expected filename
                os.rename(backup_file, backup_csv)
                log(msg % f"Created backup: {backup_csv} with {item_count} items")
        else:
            log(msg % f"Backup CSV {backup_csv} already exists")
        
        # Check if new table exists
        dynamodb_client = boto3.client('dynamodb')
        try:
            dynamodb_client.describe_table(TableName=new_table)
            log(msg % f"New table {new_table} exists, migrating data...")
            
            if dry_run:
                log(msg % f"[DRY RUN] Would migrate data from {backup_csv} to {new_table}")
                return True
            else:
                # Import data to new table
                success = import_user_storage_from_csv(backup_csv, new_table)
                if success:
                    log(msg % f"Successfully migrated user storage data to {new_table}")
                    return True
                else:
                    log(msg % f"Failed to migrate user storage data to {new_table}")
                    return False
                    
        except dynamodb_client.exceptions.ResourceNotFoundException:
            log(msg % f"New table {new_table} does not exist yet. Migration will occur after deployment.")
            return True
            
    except Exception as e:
        log(msg % f"Error during user storage migration check: {e}")
        return False


def import_user_storage_from_csv(csv_file: str, table_name: str) -> bool:
    """Import user storage data from CSV to new DynamoDB table."""
    global dynamodb_client
    
    try:
        import csv
        import json
        
        dynamodb_client = boto3.client('dynamodb')
        
        items = []
        with open(csv_file, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            
            for row in reader:
                # Convert CSV row back to DynamoDB format
                item = {}
                
                for key, value in row.items():
                    if key == '_backup_timestamp' or not value.strip():
                        continue
                        
                    # Convert based on field patterns
                    if key in ['createdAt', 'updatedAt'] and value.isdigit():
                        item[key] = {'N': value}
                    elif key == 'data' and value.startswith('{'):
                        # JSON data field
                        try:
                            parsed_data = json.loads(value)
                            item[key] = convert_dict_to_dynamodb_map(parsed_data)
                        except:
                            item[key] = {'S': value}
                    else:
                        item[key] = {'S': value}
                
                if item:
                    items.append(item)
        
        # Batch write items
        batch_size = 25
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            request_items = {
                table_name: [
                    {"PutRequest": {"Item": item}} for item in batch
                ]
            }
            
            dynamodb_client.batch_write_item(RequestItems=request_items)
        
        print(f"Imported {len(items)} items to {table_name}")
        return True
        
    except Exception as e:
        print(f"Error importing user storage data: {e}")
        return False


def convert_dict_to_dynamodb_map(obj):
    """Convert Python dict to DynamoDB Map format."""
    if isinstance(obj, dict):
        dynamodb_map = {}
        for k, v in obj.items():
            dynamodb_map[k] = convert_dict_to_dynamodb_map(v)
        return {'M': dynamodb_map}
    elif isinstance(obj, list):
        return {'L': [convert_dict_to_dynamodb_map(item) for item in obj]}
    elif isinstance(obj, str):
        return {'S': obj}
    elif isinstance(obj, (int, float)):
        return {'N': str(obj)}
    elif isinstance(obj, bool):
        return {'BOOL': obj}
    elif obj is None:
        return {'NULL': True}
    else:
        return {'S': str(obj)}





### VERY MUCH LESS IMPORTANT TABLES ###
# "AMPLIFY_ADMIN_LOGS_DYNAMODB_TABLE" : "amplify-v6-admin-dev-admin-logs",
# "user" attribute needs to be updated
# "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE" : "amplify-v6-object-access-dev-amplify-group-logs",
# "user" attribute needs to be updated (can query)
# "OP_LOG_DYNAMO_TABLE" : "amplify-v6-assistants-api-dev-op-log",
# "user" attribute needs to be updated
# "REQUEST_STATE_DYNAMO_TABLE" : "amplify-v6-amplify-js-dev-request-state",
# "user" attribute needs to be updated (can query)


if __name__ == "__main__":
    # Parse command line arguments first
    args = parse_args()
    
    # Initialize AWS region and clients from command line args
    AWS_REGION = args.region
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    s3_client = boto3.client("s3", region_name=AWS_REGION)
    dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
    
    table_names = get_tables_from_config_file()
    
    log(f"Using AWS region: {AWS_REGION}")
    log(f"Loaded {len(table_names)} table/bucket configurations from config.py")

    try:
        # Auto-generate log filename if not provided
        if not args.log:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode = "dry_run" if args.dry_run else "migration"
            args.log = f"id_migration_{mode}_{timestamp}.log"
        
        print(f"Logging to file: {args.log}")
        logfile = open(args.log, "w")
        
        # Use tee-like functionality to show output in both console and file
        import sys as sys_module
        
        class TeeOutput:
            def __init__(self, file1, file2):
                self.file1 = file1
                self.file2 = file2
            
            def write(self, data):
                self.file1.write(data)
                self.file2.write(data)
                self.file1.flush()
                self.file2.flush()
            
            def flush(self):
                self.file1.flush()
                self.file2.flush()
        
        # Keep original stdout/stderr for console output
        original_stdout = sys_module.stdout
        original_stderr = sys_module.stderr
        
        # Create tee output to both console and file
        sys_module.stdout = TeeOutput(original_stdout, logfile)
        sys_module.stderr = TeeOutput(original_stderr, logfile)
        
        log(f"Starting user ID migration. Dry run: {args.dry_run}")
        log(f"Log file: {args.log}")
        
        # Backup verification (unless skipped or dry run)
        if not args.dry_run and not args.dont_backup:
            log(f"\n=== BACKUP VERIFICATION ===")
            log(f"Checking for recent backups before proceeding with migration...")
            
            try:
                import subprocess
                from datetime import datetime
                
                # Generate backup name pattern to look for
                today = datetime.now().strftime("%Y%m%d")
                backup_pattern = f"id-migration-backup-{today}"
                
                # Run backup verification
                result = subprocess.run([
                    "python", "backup_prereq.py", 
                    "--verify-only", 
                    "--backup-name", backup_pattern
                ], capture_output=True, text=True, cwd=".")
                
                if result.returncode != 0:
                    log(f"❌ BACKUP VERIFICATION FAILED!")
                    log(f"Backup script output: {result.stdout}")
                    log(f"Backup script errors: {result.stderr}")
                    log(f"")
                    log(f"🚨 MIGRATION ABORTED FOR SAFETY!")
                    log(f"")
                    log(f"Please create backups first:")
                    log(f"  python scripts/backup_prereq.py --backup-name 'pre-migration-{datetime.now().strftime('%Y%m%d-%H%M%S')}'")
                    log(f"")
                    log(f"Or if you already have backups:")
                    log(f"  python scripts/id_migration.py --dont-backup {' --dry-run' if args.dry_run else ''}")
                    log(f"")
                    
                    # Auto-continue in debug mode or --no-confirmation for automation
                    if sys.gettrace() is not None:
                        log(f"⚠️  Debug mode detected - continuing despite backup verification failure...")
                    elif args.no_confirmation:
                        log(f"⚠️  No confirmation mode - continuing despite backup verification failure...")
                        log(f"⚠️  WARNING: Proceeding without verified backups!")
                    else:
                        response = input("\nDo you want to continue anyway? (WARNING: No verified backups!) (yes/no): ").lower().strip()
                        if response not in ['yes', 'y']:
                            log(f"Migration cancelled by user.")
                            sys.exit(0)
                        log(f"⚠️  User confirmed: continuing despite backup verification failure...")
                    
                    log(f"⚠️  PROCEEDING WITHOUT VERIFIED BACKUPS - USE AT YOUR OWN RISK!")
                    log(f"")
                else:
                    log(f"✅ Backup verification passed! Proceeding with migration...")
                    
            except Exception as e:
                log(f"Warning: Could not verify backups: {e}")
                log(f"Proceeding with migration (use --dont-backup to suppress this warning)")
        
        elif args.dont_backup and not args.dry_run:
            log(f"ℹ️  Backup process skipped (--dont-backup flag used)")
            log(f"ℹ️  Assuming you already have proper backups in place")
        
        # Generate CSV if --no-id-change flag is present
        if args.no_id_change:
            log(f"\n=== GENERATING MIGRATION CSV (No ID Changes) ===")
            log(f"Pulling all users from Cognito table for S3 consolidation migration...")
            
            import os
            if os.path.exists(args.csv_file) and not args.dry_run:
                # Backup existing file if it exists
                backup_name = f"{args.csv_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                os.rename(args.csv_file, backup_name)
                log(f"Existing {args.csv_file} backed up to {backup_name}")
            
            if not generate_no_change_csv(args.csv_file, args.dry_run):
                log(f"Failed to generate {args.csv_file}")
                sys.exit(1)
            log(f"Successfully generated {args.csv_file} with no ID changes (data migration only)")
        
        # CRITICAL PREREQUISITES CHECK
        log(f"\n=== PREREQUISITE VALIDATION ===")
        log(f"Checking critical infrastructure required for migration...")
        
        # Check consolidated bucket (required for S3 migrations)
        consolidation_bucket = table_names.get("S3_CONSOLIDATION_BUCKET_NAME")
        if not check_bucket_exists(consolidation_bucket):
            log(f"❌ CRITICAL ERROR: S3 consolidation bucket does not exist!")
            log(f"   Missing bucket: {consolidation_bucket}")
            log(f"")
            log(f"🚨 MIGRATION CANNOT PROCEED!")
            log(f"")
            log(f"SOLUTION: Deploy amplify-lambda service first to create required infrastructure:")
            from config import STAGE
            log(f"   serverless amplify-lambda:deploy --stage {STAGE}")
            log(f"")
            log(f"This will create:")
            log(f"   - S3 consolidation bucket: {consolidation_bucket}")
            log(f"   - User data storage table: {table_names.get('USER_DATA_STORAGE_TABLE')}")
            sys.exit(1)
        else:
            log(f"✅ S3 consolidation bucket exists: {consolidation_bucket}")
            
        # Check user data storage table (required for data consolidation)
        user_data_table = table_names.get("USER_DATA_STORAGE_TABLE")
        if not check_table_exists(user_data_table):
            log(f"❌ CRITICAL ERROR: User data storage table does not exist!")
            log(f"   Missing table: {user_data_table}")
            log(f"")
            log(f"🚨 MIGRATION CANNOT PROCEED!")
            log(f"")
            log(f"SOLUTION: Deploy amplify-lambda service first to create required infrastructure:")
            from config import STAGE
            log(f"   serverless amplify-lambda:deploy --stage {STAGE}")
            sys.exit(1)
        else:
            log(f"✅ User data storage table exists: {user_data_table}")
            
        log(f"✅ All critical prerequisites validated successfully!")
        
        # Get user mappings for batch operations
        users_map = get_users_from_csv(args.csv_file)
        
        # Early Validation: Check what resources exist
        log(f"\n=== RESOURCE VALIDATION ===")
        log(f"Checking which tables and buckets exist to determine migration scope...")
        validation_results = validate_migration_resources(table_names, args)
        
        # Show migration plan based on what exists
        steps_needed = validation_results["migration_steps_needed"]
        
        # Check if any user IDs are actually changing for step 1 planning
        ids_changing = any(old_id != new_id for old_id, new_id in users_map.items())
        
        log(f"\n=== MIGRATION PLAN ===")
        if steps_needed['user_data_storage_id_migration']:
            if ids_changing:
                log(f"Step 1 - USER_DATA_STORAGE ID migration: ✅ NEEDED")
            else:
                log(f"Step 1 - USER_DATA_STORAGE ID migration: ⏭️  SKIPPED (no ID changes, --no-id-change mode)")
        else:
            log(f"Step 1 - USER_DATA_STORAGE ID migration: ⏭️  SKIPPED (table missing)")
        log(f"Step 2 - Old→New table migration: {'✅ NEEDED' if steps_needed['old_to_new_table_migration'] else '⏭️  SKIPPED (tables missing)'}")
        log(f"Step 3 - Per-user table updates: {'✅ NEEDED' if steps_needed['per_user_table_updates'] else '⏭️  SKIPPED (Cognito table missing)'}")
        log(f"Step 4 - S3 migrations: {'✅ NEEDED' if steps_needed['s3_migrations'] else '⏭️  SKIPPED (buckets missing)'}")
        
        if not any(steps_needed.values()):
            log(f"\n⚠️  WARNING: No migration steps needed - all critical resources are missing!")
            log(f"This might indicate a configuration issue or wrong deployment stage.")
            if not args.dry_run:
                # Auto-continue in debug mode or --no-confirmation for automation
                if sys.gettrace() is not None:
                    log(f"Debug mode detected - automatically continuing...")
                elif args.no_confirmation:
                    log(f"No confirmation mode - automatically continuing...")
                else:
                    response = input("\nDo you want to continue anyway? (yes/no): ").lower().strip()
                    if response not in ['yes', 'y']:
                        log(f"Migration cancelled by user.")
                        sys.exit(0)
        
        # Step 1: Migrate all existing USER_DATA_STORAGE table IDs (while it's at its smallest)
        log(f"\n=== STEP 1: USER_DATA_STORAGE ID MIGRATION ===")
        
        # Check if any user IDs are actually changing
        ids_changing = any(old_id != new_id for old_id, new_id in users_map.items())
        
        if steps_needed["user_data_storage_id_migration"]:
            if ids_changing:
                log(f"Migrating all existing IDs in USER_DATA_STORAGE table...")
                if not migrate_all_user_data_storage_ids(users_map, args.dry_run):
                    log(f"USER_DATA_STORAGE ID migration failed. Continuing...")
                else:
                    log(f"USER_DATA_STORAGE ID migration completed successfully.")
            else:
                log(f"⏭️  Skipping USER_DATA_STORAGE ID migration - no user IDs are changing (--no-id-change mode)")
        else:
            log(f"⏭️  Skipping USER_DATA_STORAGE ID migration - table does not exist")
        
        # Step 2: Migrate OLD_USER_STORAGE_TABLE (basic-ops) to USER_DATA_STORAGE_TABLE with ID translation
        log(f"\n=== STEP 2: USER_STORAGE TO USER_DATA_STORAGE MIGRATION ===")
        
        # Get table names from config only
        old_table = table_names.get("OLD_USER_STORAGE_TABLE")
        new_table = table_names.get("USER_DATA_STORAGE_TABLE")
        
        log(f"Table configuration:")
        log(f"  Old table: {old_table} (from config)")
        log(f"  New table: {new_table} (from config)")
        
        if not steps_needed["old_to_new_table_migration"]:
            log(f"⏭️  Skipping USER_STORAGE to USER_DATA_STORAGE migration")
            if not old_table:
                log(f"   Reason: OLD_USER_STORAGE_TABLE not configured or missing")
            elif not new_table:
                log(f"   Reason: USER_DATA_STORAGE_TABLE not configured or missing")
            elif not validation_results["tables"]["OLD_USER_STORAGE_TABLE"]:
                log(f"   Reason: Old table ({old_table}) does not exist (OK)")
            elif not validation_results["tables"]["USER_DATA_STORAGE_TABLE"]:
                log(f"   Reason: New table ({new_table}) does not exist (DEPLOY AMPLIFY-LAMBDA FIRST)")
        else:
            log(f"Migrating from {old_table} to {new_table} with ID translation...")
            if not migrate_user_storage_to_user_data_storage(users_map, args.dry_run, old_table, new_table):
                log(f"User storage migration failed. Continuing...")
            else:
                log(f"User storage migration completed successfully.")

        # Step 3: Process each user for remaining table updates
        log(f"\n=== STEP 3: PER-USER TABLE UPDATES ===")
        if not steps_needed["per_user_table_updates"]:
            log(f"⏭️  Skipping per-user table updates - COGNITO_USERS_DYNAMODB_TABLE does not exist")
            log(f"Cannot proceed without user lookup capability")
        else:
            # loop through our users
            for u in users_map.items():
                log(f"\n\nProcessing user: old: {u[0]} new: {u[1]}")
                old_user_id = u[0]
                new_user_id = u[1]
                
                # this is a sanity check to make user exists
                user = get_user(old_user_id)

                if not user:
                    log(f"\tUser with old ID {old_user_id} not found. Skipping.")
                    continue

                if not update_user_id(old_user_id, new_user_id, args.dry_run):
                    log(f"Unable to update user ID for {old_user_id}. Skipping - Manual intervention required.")
                    continue
                
                ### ONLY RUN IF USER ID MIGRATION IS REQUIRED ###
                if old_user_id != new_user_id:

                    # # Update accounts table (has built-in existence checking)
                    if validation_results["tables"].get("ACCOUNTS_DYNAMO_TABLE", False):
                        if not update_accounts(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update accounts for {old_user_id}. Skipping - Manual intervention required.")
                    else:
                        log(f"[{old_user_id}] Skipping accounts table - does not exist")

                    # # Update API keys table (has built-in existence checking)  
                    if validation_results["tables"].get("API_KEYS_DYNAMODB_TABLE", False):
                        if not update_api_keys(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update API keys for {old_user_id}. This is assumed reasonable as not all users have API keys.")
                    else:
                        log(f"[{old_user_id}] Skipping API keys table - does not exist")

                    # # Update ops table
                    if validation_results["tables"].get("OPS_DYNAMODB_TABLE", False):
                        if not update_ops_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update ops records for {old_user_id}. This is assumed reasonable as not all users have ops records.")
                    else:
                        log(f"[{old_user_id}] Skipping ops table - does not exist")

                    # Update OAuth state table
                    if validation_results["tables"].get("OAUTH_STATE_TABLE", False):
                        if not update_oauth_state_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update OAuth state records for {old_user_id}. This is assumed reasonable as not all users have OAuth state records.")
                    else:
                        log(f"[{old_user_id}] Skipping OAuth state table - does not exist")

                    # Update amplify admin table
                    if validation_results["tables"].get("AMPLIFY_ADMIN_DYNAMODB_TABLE", False):
                        if not update_amplify_admin_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update Amplify Admin records for {old_user_id}. This is assumed reasonable as not all users are admins.")
                    else:
                        log(f"[{old_user_id}] Skipping amplify admin table - does not exist")

                    # Update agent event templates table
                    if validation_results["tables"].get("AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE", False):
                        if not update_agent_event_templates_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update agent event templates records for {old_user_id}. This is assumed reasonable as not all users have agent event templates.")
                    else:
                        log(f"[{old_user_id}] Skipping agent event templates table - does not exist")

                    # Update object access table
                    if validation_results["tables"].get("OBJECT_ACCESS_DYNAMODB_TABLE", False):
                        if not update_object_access_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update object access records for {old_user_id}. This is assumed reasonable as not all users have object access records.")
                    else:
                        log(f"[{old_user_id}] Skipping object access table - does not exist")

                    # Update assistants aliases table
                    if validation_results["tables"].get("ASSISTANTS_ALIASES_DYNAMODB_TABLE", False):
                        if not update_assistants_aliases_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update assistants aliases records for {old_user_id}. This is assumed reasonable as not all users have assistants aliases records.")
                    else:
                        log(f"[{old_user_id}] Skipping assistants aliases table - does not exist")

                    # Update assistants table
                    if validation_results["tables"].get("ASSISTANTS_DYNAMODB_TABLE", False):
                        if not update_assistants_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update assistants records for {old_user_id}. This is assumed reasonable as not all users have assistants records.")
                    else:
                        log(f"[{old_user_id}] Skipping assistants table - does not exist")

                    # Update assistant groups table
                    if validation_results["tables"].get("ASSISTANT_GROUPS_DYNAMO_TABLE", False):
                        if not update_assistant_groups_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update assistant groups records for {old_user_id}. This is assumed reasonable as not all users have assistant groups records.")
                    else:
                        log(f"[{old_user_id}] Skipping assistant groups table - does not exist")

                    # Update assistant lookup table
                    if validation_results["tables"].get("ASSISTANT_LOOKUP_DYNAMODB_TABLE", False):
                        if not update_assistant_lookup_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update assistant lookup records for {old_user_id}. This is assumed reasonable as not all users have assistant lookup records.")
                    else:
                        log(f"[{old_user_id}] Skipping assistant lookup table - does not exist")

                    # Update assistant threads table
                    if validation_results["tables"].get("ASSISTANT_THREADS_DYNAMODB_TABLE", False):
                        if not update_assistant_threads_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update assistant threads records for {old_user_id}. This is assumed reasonable as not all users have assistant threads records.")
                    else:
                        log(f"[{old_user_id}] Skipping assistant threads table - does not exist")

                    # Update assistant thread runs table
                    if validation_results["tables"].get("ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE", False):
                        if not update_assistant_thread_runs_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update assistant thread runs records for {old_user_id}. This is assumed reasonable as not all users have assistant thread runs records.")
                    else:
                        log(f"[{old_user_id}] Skipping assistant thread runs table - does not exist")

                    # Update chat usage table
                    if validation_results["tables"].get("CHAT_USAGE_DYNAMO_TABLE", False):
                        if not update_chat_usage_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update chat usage records for {old_user_id}. This is assumed reasonable as not all users have chat usage records.")
                    else:
                        log(f"[{old_user_id}] Skipping chat usage table - does not exist")

                    # Update cost calculations table
                    if validation_results["tables"].get("COST_CALCULATIONS_DYNAMO_TABLE", False):
                        if not update_cost_calculations_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update cost calculations records for {old_user_id}. This is assumed reasonable as not all users have cost calculations records.")
                    else:
                        log(f"[{old_user_id}] Skipping cost calculations table - does not exist")

                    # Update data disclosure acceptance table
                    if validation_results["tables"].get("DATA_DISCLOSURE_ACCEPTANCE_TABLE", False):
                        if not update_data_disclosure_acceptance_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update data disclosure acceptance records for {old_user_id}. This is assumed reasonable as not all users have data disclosure acceptance records.")
                    else:
                        log(f"[{old_user_id}] Skipping data disclosure acceptance table - does not exist")

                    # Update db connections table
                    if validation_results["tables"].get("DB_CONNECTIONS_TABLE", False):
                        if not update_db_connections_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update db connections records for {old_user_id}. This is assumed reasonable as not all users have db connections records.")
                    else:
                        log(f"[{old_user_id}] Skipping db connections table - does not exist")

                    # Update email settings table
                    if validation_results["tables"].get("EMAIL_SETTINGS_DYNAMO_TABLE", False):
                        if not update_email_settings_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update email settings records for {old_user_id}. This is assumed reasonable as not all users have email settings records.")
                    else:
                        log(f"[{old_user_id}] Skipping email settings table - does not exist")

                    # Update files table
                    if validation_results["tables"].get("FILES_DYNAMO_TABLE", False):
                        if not update_files_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update files records for {old_user_id}. This is assumed reasonable as not all users have files records.")
                    else:
                        log(f"[{old_user_id}] Skipping files table - does not exist")

                    # Update hash files table
                    if validation_results["tables"].get("HASH_FILES_DYNAMO_TABLE", False):
                        if not update_hash_files_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update hash files records for {old_user_id}. This is assumed reasonable as not all users have hash files records.")
                    else:
                        log(f"[{old_user_id}] Skipping hash files table - does not exist")

                    # Update embedding progress table
                    if validation_results["tables"].get("EMBEDDING_PROGRESS_TABLE", False):
                        if not update_embedding_progress_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update embedding progress records for {old_user_id}. This is assumed reasonable as not all users have embedding progress records.")
                    else:
                        log(f"[{old_user_id}] Skipping embedding progress table - does not exist")

                    # Update history cost calculations table
                    if validation_results["tables"].get("HISTORY_COST_CALCULATIONS_DYNAMO_TABLE", False):
                        if not update_history_cost_calculations_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update history cost calculations records for {old_user_id}. This is assumed reasonable as not all users have history cost calculations records.")
                    else:
                        log(f"[{old_user_id}] Skipping history cost calculations table - does not exist")

                    # Update additional charges table
                    if validation_results["tables"].get("ADDITIONAL_CHARGES_TABLE", False):
                        if not update_additional_charges_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update additional charges records for {old_user_id}. This is assumed reasonable as not all users have additional charges records.")
                    else:
                        log(f"[{old_user_id}] Skipping additional charges table - does not exist")

                    # Update oauth user table
                    if validation_results["tables"].get("OAUTH_USER_TABLE", False):
                        if not update_oauth_user_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update oauth user records for {old_user_id}. This is assumed reasonable as not all users have oauth user records.")
                    else:
                        log(f"[{old_user_id}] Skipping oauth user table - does not exist")

                    # Update user tags table
                    if validation_results["tables"].get("USER_TAGS_DYNAMO_TABLE", False):
                        if not update_user_tags_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update user tags records for {old_user_id}. This is assumed reasonable as not all users have user tags records.")
                    else:
                        log(f"[{old_user_id}] Skipping user tags table - does not exist")

                    # Update memory table
                    if validation_results["tables"].get("MEMORY_DYNAMO_TABLE", False):
                        if not update_memory_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update memory records for {old_user_id}. This is assumed reasonable as not all users have memory records.")
                    else:
                        log(f"[{old_user_id}] Skipping memory table - does not exist")

                    # Update common data table
                    if validation_results["tables"].get("COMMON_DATA_DYNAMO_TABLE", False):
                        if not update_common_data_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update common data records for {old_user_id}. This is assumed reasonable as not all users have common data records.")
                    else:
                        log(f"[{old_user_id}] Skipping common data table - does not exist")

                    # Update dynamic code table
                    if validation_results["tables"].get("DYNAMO_DYNAMIC_CODE_TABLE", False):
                        if not update_dynamic_code_table(old_user_id, new_user_id, args.dry_run):
                            log(f"Unable to update dynamic code records for {old_user_id}. This is assumed reasonable as not all users have dynamic code records.")
                    else:
                        log(f"[{old_user_id}] Skipping dynamic code table - does not exist")


                ### ALWAYS REQUIRED TO BE RUN REGARDLESS OF ID CHANGE ###
                ### (These handle S3 migrations and data consolidation) ###

                # Update agent state table + S3 bucket migration
                table_exists = validation_results["tables"].get("AGENT_STATE_DYNAMODB_TABLE", False)
                bucket_exists = validation_results["buckets"].get("AGENT_STATE_BUCKET", False)
                if table_exists or bucket_exists:
                    if not update_agent_state_table(old_user_id, new_user_id, args.dry_run):
                        log(f"Unable to update agent state records for {old_user_id}. This is assumed reasonable as not all users have agent state records.")
                else:
                    log(f"[{old_user_id}] Skipping agent state migration - table and bucket do not exist")

                # Update artifacts table + S3 bucket migration
                table_exists = validation_results["tables"].get("ARTIFACTS_DYNAMODB_TABLE", False)
                bucket_exists = validation_results["buckets"].get("S3_ARTIFACTS_BUCKET", False)
                if table_exists or bucket_exists:
                    if not update_artifacts_table(old_user_id, new_user_id, args.dry_run):
                        log(f"Unable to update artifacts records for {old_user_id}. This is assumed reasonable as not all users have artifacts.")
                else:
                    log(f"[{old_user_id}] Skipping artifacts migration - table and bucket do not exist")
                
                # Update workflow templates table + S3 bucket migration
                table_exists = validation_results["tables"].get("WORKFLOW_TEMPLATES_TABLE", False)
                bucket_exists = validation_results["buckets"].get("WORKFLOW_TEMPLATES_BUCKET", False)
                if table_exists or bucket_exists:
                    if not update_workflow_templates_table(old_user_id, new_user_id, args.dry_run):
                        log(f"Unable to update workflow templates records for {old_user_id}. This is assumed reasonable as not all users have workflow templates.")
                    
                    # CLEANUP: Check for orphaned workflow files in S3 (files without metadata entries)
                    if bucket_exists:
                        log(f"[{old_user_id}] Checking for orphaned workflow templates in S3...")
                        try:
                            cleanup_success = cleanup_orphaned_workflow_templates_for_user(old_user_id, new_user_id, args.dry_run, AWS_REGION)
                            if cleanup_success:
                                log(f"[{old_user_id}] Orphaned workflow templates cleanup completed successfully")
                            else:
                                log(f"[{old_user_id}] Warning: Some issues occurred during orphaned workflow cleanup")
                        except Exception as cleanup_e:
                            log(f"[{old_user_id}] Warning: Failed to cleanup orphaned workflows: {cleanup_e}")
                else:
                    log(f"[{old_user_id}] Skipping workflow templates migration - table and bucket do not exist")

                # Update assistant code interpreter table + S3 bucket migration
                table_exists = validation_results["tables"].get("ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE", False)
                bucket_exists = validation_results["buckets"].get("ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME", False)
                if table_exists or bucket_exists:
                    if not update_assistant_code_interpreter_table(old_user_id, new_user_id, args.dry_run):
                        log(f"Unable to update assistant code interpreter records for {old_user_id}. This is assumed reasonable as not all users have assistant code interpreter records.")
                else:
                    log(f"[{old_user_id}] Skipping assistant code interpreter migration - table and bucket do not exist")

                # Update conversation metadata table + S3 bucket migration
                table_exists = validation_results["tables"].get("CONVERSATION_METADATA_TABLE", False)
                bucket_exists = validation_results["buckets"].get("S3_CONVERSATIONS_BUCKET_NAME", False)
                if table_exists or bucket_exists:
                    if not update_conversation_metadata_table(old_user_id, new_user_id, args.dry_run):
                        log(f"Unable to update conversation metadata records for {old_user_id}. This is assumed reasonable as not all users have conversation metadata records.")
                else:
                    log(f"[{old_user_id}] Skipping conversation metadata migration - table and bucket do not exist")

                # Update group assistant conversations table + S3 bucket migration
                table_exists = validation_results["tables"].get("GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE", False)
                bucket_exists = validation_results["buckets"].get("S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME", False)
                if table_exists or bucket_exists:
                    if not update_group_assistant_conversations_table(old_user_id, new_user_id, args.dry_run):
                        log(f"Unable to update group assistant conversations records for {old_user_id}. This is assumed reasonable as not all users have group assistant conversations records.")
                else:
                    log(f"[{old_user_id}] Skipping group assistant conversations migration - table and bucket do not exist")

                # Update scheduled tasks table + S3 bucket migration
                table_exists = validation_results["tables"].get("SCHEDULED_TASKS_TABLE", False)
                bucket_exists = validation_results["buckets"].get("SCHEDULED_TASKS_LOGS_BUCKET", False)
                if table_exists or bucket_exists:
                    if not update_scheduled_tasks_table(old_user_id, new_user_id, args.dry_run):
                        log(f"Unable to update scheduled tasks records for {old_user_id}. This is assumed reasonable as not all users have scheduled tasks records.")
                else:
                    log(f"[{old_user_id}] Skipping scheduled tasks migration - table and bucket do not exist")

                # Update shares table + S3 bucket migration
                table_exists = validation_results["tables"].get("SHARES_DYNAMODB_TABLE", False)
                bucket_exists = validation_results["buckets"].get("S3_SHARE_BUCKET_NAME", False)
                if table_exists or bucket_exists:
                    if not update_shares_table(old_user_id, new_user_id, users_map, args.dry_run):
                        log(f"Unable to update shares records for {old_user_id}. This is assumed reasonable as not all users have shares records.")
                else:
                    log(f"[{old_user_id}] Skipping shares migration - table and bucket do not exist")
                
        # Step 2: Run standalone S3 bucket migrations (data disclosure, API docs)
        log(f"\n=== STANDALONE S3 BUCKET MIGRATION ===")
        log(f"Note: User-specific S3 migrations were already handled in the update functions above.")
        log(f"This step migrates standalone buckets (data disclosure, API documentation).")
        
        if args.dry_run:
            # In dry run mode, automatically run S3 migration dry run
            log(f"[DRY RUN] Running standalone S3 bucket migration in dry run mode...")
            try:
                import sys as sys_module
                original_argv = sys_module.argv
                
                # Set up argv for s3_migration_main with dry-run
                sys_module.argv = ['s3_data_migration.py', '--bucket', 'all', '--dry-run']
                
                # Call S3 migration main function
                s3_success = s3_migration_main()
                
                # Restore original argv
                sys_module.argv = original_argv
                
                if s3_success:
                    log(f"[DRY RUN] S3 bucket migration dry run completed successfully!")
                else:
                    log(f"[DRY RUN] S3 bucket migration dry run encountered issues!")
                    
            except Exception as s3_error:
                log(f"[DRY RUN] Error running S3 migration dry run: {s3_error}")
        else:
            # In real mode, check for confirmation or auto-continue
            log(f"\n" + "="*60)
            log(f"USER ID MIGRATION COMPLETED")
            log(f"="*60)
            log(f"Next step: Standalone S3 Bucket Migration")
            log(f"This will migrate data from legacy S3 buckets to consolidation bucket.")
            log(f"This includes:")
            log(f"- Data disclosure files")
            log(f"- API documentation")
            log(f"Note: User-specific S3 data was already migrated during the table updates above.")
            
            # Check for no-confirmation flag or debug mode
            if sys.gettrace() is not None:
                log(f"Debug mode detected - automatically continuing S3 migration...")
                run_s3_migration = True
            elif args.no_confirmation:
                log(f"No confirmation mode - automatically continuing S3 migration...")
                run_s3_migration = True
            else:
                # Restore stdout temporarily to ask user for input
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__
                
                print("\n" + "="*60)
                print("USER ID MIGRATION COMPLETED")
                print("="*60)
                print("\nNext step: Standalone S3 Bucket Migration")
                print("This will migrate data from legacy S3 buckets to consolidation bucket.")
                print("This includes:")
                print("- Data disclosure files")
                print("- API documentation")
                print("\nNote: User-specific S3 data was already migrated during the table updates above.")
                
                response = input("\nDo you want to run the standalone S3 bucket migration now? (yes/no): ").lower().strip()
                
                # Restore file logging
                sys.stdout = logfile
                sys.stderr = logfile
                
                run_s3_migration = response in ['yes', 'y']
            
            if run_s3_migration:
                log(f"User confirmed standalone S3 bucket migration. Starting...")
                try:
                    # Run S3 migration in real mode
                    import sys as sys_module
                    original_argv = sys_module.argv
                    
                    # Set up argv for s3_migration_main
                    sys_module.argv = ['s3_data_migration.py', '--bucket', 'all']
                    
                    # Call S3 migration main function
                    s3_success = s3_migration_main()
                    
                    # Restore original argv
                    sys_module.argv = original_argv
                    
                    if s3_success:
                        log(f"Standalone S3 bucket migration completed successfully!")
                    else:
                        log(f"Standalone S3 bucket migration failed!")
                        
                except Exception as s3_error:
                    log(f"Error running S3 migration: {s3_error}")
                    
            else:
                log(f"Standalone S3 bucket migration skipped. Run manually: python3 s3_data_migration.py --bucket all")

    except Exception as e:
        log(f"Error processing users: {e}")
    finally:
        # Restore original stdout/stderr
        sys_module.stdout = original_stdout
        sys_module.stderr = original_stderr
        logfile.close()
        print(f"Migration completed. Full log available in: {args.log}")
