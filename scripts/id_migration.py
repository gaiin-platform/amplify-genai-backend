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
import json
from datetime import datetime
from typing import Dict, Tuple
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.conditions import Attr

from config import CONFIG as tables
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
    main as s3_migration_main
)
from user_storage_backup import backup_user_storage_table

dynamodb = boto3.resource("dynamodb")

# Import USER_STORAGE_TABLE functions
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'amplify-lambda'))
from data.user import CommonData


def paginated_query(table_name: str, key_name: str, value: str, index_name: str = None):
    """
    Generator for paginated DynamoDB query results.
    Yields items matching Key(key_name).eq(value).
    """
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
        required=True,
        help="Path to the CSV file containing migration data.",
    )
    parser.add_argument(
        "--log", required=True, help="Log output to the specified file."
    )
    parser.add_argument(
        "--old-table", 
        default="amplify-v6-lambda-basic-ops-dev-user-storage",
        help="Source user storage table name (default: amplify-v6-lambda-basic-ops-dev-user-storage)"
    )
    parser.add_argument(
        "--new-table", 
        default="amplify-v6-lambda-dev-user-data-storage",
        help="Target user storage table name (default: amplify-v6-lambda-dev-user-data-storage)"
    )
    return parser.parse_args()


def get_tables_from_config_file() -> Dict[str, str]:
    """
    Reads the configuration file and returns a dictionary of table names,
    excluding the entry with the key 'needs_edit'.

    Returns:
        Dict[str, str]: A dictionary containing table names from the configuration file,
        with 'needs_edit' removed.
    """
    t = tables.copy()
    del t["needs_edit"]
    return t


def tables_ok(table_names: Dict[str, str], continue_anyway: bool = False) -> bool:
    """
    Checks if the required DynamoDB tables exist and prompts the user for confirmation to proceed.

    Args:
        table_names (Dict[str, str]): A dictionary mapping logical table names to DynamoDB table names.
        continue_anyway (bool, optional): If True, continues execution even if some tables do not exist,
            setting their values to None. Defaults to False.

    Returns:
        bool: True if all required tables exist (or continue_anyway is True) and the user confirms to proceed;
            False otherwise.

    Side Effects:
        - Logs messages about missing tables and table mappings.
        - Prompts the user for confirmation via input.
    """
    try:
        existing_tables = dynamodb.meta.client.list_tables()["TableNames"]
        for table_key, table_value in table_names.items():
            if table_value not in existing_tables:
                if continue_anyway:
                    log(f"Table {table_value} does not exist, but continuing anyway.")
                    table_names[table_key] = None
                    continue
                else:
                    log(f"Table {table_value} does not exist.")
                    return False
        table_names = {k: v for k, v in table_names.items() if v is not None}
        # Print the table mapping and ask the user to
        # confirm they have accepted the terms
        log("The following tables will be used:")
        for k, v in table_names.items():
            log(f"\t{k}: {v}")
        response = input("Have you accepted the terms and wish to proceed? (yes/no): ")
        if response.lower() != "yes":
            log("User did not accept the terms. Exiting.")
            return False
        return True

    except Exception as e:
        log(f"Error checking tables: {e}")
        return False


def get_user(old_id: str) -> dict | None:
    """Fetch user by old ID."""
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
                        log(
                            f"Warning: Duplicate new_id {new_id} found. Clean up CSV and try again."
                        )
                        sys.exit(1)
                    users[old_id] = new_id
                else:
                    log(f"Skipping invalid row: {row}")
    except Exception as e:
        log(f"Error reading CSV file {file_path}: {e}")
        sys.exit(1)
    return users


## Starting Here is all the functions that actually update the data ###
## ----------------------------------------------------------------- ##


# "COGNITO_USERS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-cognito-users",
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
        user["user_id"] = new_id
        if dry_run:
            log(
                msg
                % f"Would update user ID from {old_id} to {new_id}.\n\tNew Data: {user}"
            )
            return True
        else:
            # save the user back to the table
            log(
                msg % f"Updating user ID from {old_id} to {new_id}.\n\tNew Data: {user}"
            )
            dynamodb.Table(user_table).put_item(Item=user)
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
                log(
                    msg
                    % f"Would update admin config data to:\n\tNew Data: {new_admin_data}"
                )
            else:
                log(
                    msg
                    % f"Updating admin config data to:\n\tNew Data: {new_admin_data}"
                )
                amplify_admin_table.put_item(
                    Item={"config_id": "admins", "data": new_admin_data}
                )

    # A similar dilemma is presented here and I maintain that this table desperately needs a restructure.
    group_config = amplify_admin_table.get_item(Key={"config_id": "amplifyGroups"})
    if not "Item" in group_config:
        log(msg % f"No amplifyGroups config found in {table}.")
    else:
        group_data = group_config["Item"].get("data", {})
        updated = False
        for group_name, group_info in group_data.items():
            members = list(set(group_info.get("members", [])))
            if old_id in members:
                log(
                    msg
                    % f"Found amplifyGroups config with old ID {old_id} in group {group_name}.\n\tExisting Data: {members}"
                )
                new_members = list(
                    set([new_id if uid == old_id else uid for uid in members])
                )
                group_info["members"] = new_members
                updated = True
                if dry_run:
                    log(
                        msg
                        % f"Would update members of group {group_name} to:\n\tNew Data: {new_members}"
                    )
                else:
                    log(
                        msg
                        % f"Updating members of group {group_name} to:\n\tNew Data: {new_members}"
                    )
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
                log(
                    msg
                    % f"Found featureFlags config with old ID {old_id} in flag {flag_name}.\n\tExisting Data: {user_exceptions}"
                )
                new_user_exceptions = list(
                    set([new_id if uid == old_id else uid for uid in user_exceptions])
                )
                flag_info["userExceptions"] = new_user_exceptions
                updated = True
                if dry_run:
                    log(
                        msg
                        % f"Would update userExceptions of flag {flag_name} to:\n\tNew Data: {new_user_exceptions}"
                    )
                else:
                    log(
                        msg
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
    try:

        ret = False
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found accounts record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update account item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating account item to:\n\tNew Data: {item}")
                dynamodb.Table(table).put_item(Item=item)
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
                    log(
                        msg
                        % f"Found API keys record with owner {old_id}.\n\tExisting Data: {item}"
                    )
                    item["owner"] = new_id
                    updated = True
                
                # Check and update delegate field
                if "delegate" in item and item["delegate"] == old_id:
                    log(
                        msg
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
    """Update all artifacts records associated with the old user ID to the new user ID."""
    msg = f"[update_artifacts_table][dry-run: {dry_run}] %s"
    table = table_names.get("ARTIFACTS_DYNAMODB_TABLE")
    artifacts_table = dynamodb.Table(table)
    ret = False
    try:
        for item in paginated_query(table, "user_id", old_id):
            log(
                msg
                % f"Found artifacts record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # IMPLEMENTED: Artifacts S3 to USER_STORAGE_TABLE migration
            # - "key": Transformed from "user@email.com/20250305/Game:v3" to "20250305/Game:v3" format
            # - S3 content: Downloaded from S3_ARTIFACTS_BUCKET and migrated to USER_STORAGE_TABLE
            # - "user_id": Updated from old_id to new_id (part of USER_STORAGE_TABLE PK)
            # - "artifacts": Array updated with new clean key format for each artifact
            # Migration detection: Key format detection (date pattern vs user prefix)
            # 
            # Processing flow:
            # 1. Call migrate_artifacts_bucket_for_user() to migrate S3 content and transform keys
            # 2. Update "user_id" attribute from old_id to new_id  
            # 3. Update "artifacts" array with transformed clean keys (handled by migration function)
            success, updated_artifacts = migrate_artifacts_bucket_for_user(old_id, new_id, dry_run, item)
            
            # Update user_id and artifacts array while preserving ALL other columns
            item["user_id"] = new_id
            if success and updated_artifacts:
                item["artifacts"] = updated_artifacts
            
            if dry_run:
                log(msg % f"Would update artifact item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating artifact item to:\n\tNew Data: {item}")
                artifacts_table.put_item(Item=item)
            
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
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update ops item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating ops item to:\n\tNew Data: {item}")
                ops_table.put_item(Item=item)
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
def update_shares_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all shares records associated with the old user ID to the new user ID."""
    msg = f"[update_shares_table][dry-run: {dry_run}] %s"
    table = table_names.get("SHARES_DYNAMODB_TABLE")
    
    # IMPLEMENTED: Shares S3 to S3_CONSOLIDATION_BUCKET migration
    # - Shares: Migrated from S3_SHARE_BUCKET_NAME to S3_CONSOLIDATION_BUCKET_NAME  
    # - Old format: "recipient_user/sharer_user/date/file.json" 
    # - New format: "shares/recipient_user/sharer_user/date/file.json"
    # - User ID updates: old_id replaced with new_id in recipient/sharer positions
    # - DynamoDB updates: "user", "id", and "sharedBy" fields in data array updated
    #
    # Processing flow:
    # 1. Call migrate_shares_bucket_for_user() to migrate S3 shares files
    # 2. Update "user" field from old_id to new_id in SHARES_DYNAMODB_TABLE
    # 3. Update "id" prefix from old_id to new_id  
    # 4. Update "sharedBy" fields in data array from old_id to new_id
    success = migrate_shares_bucket_for_user(old_id, new_id, dry_run)
    
    if not success:
        log(msg % f"Failed to migrate shares for user {old_id}")
        return False
    
    shares_table = dynamodb.Table(table)

    # Process shares records to update user IDs and sharedBy fields
    # 1. "id" field: Update prefix from old_id to new_id
    # 2. "user" field: Update from old_id to new_id  
    # 3. "data" array -> "sharedBy" fields: Update from old_id to new_id
    
    ret = False
    try:
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found shares record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Migrate user settings from SHARES_DYNAMODB_TABLE settings column to USER_STORAGE_TABLE
            migrate_user_settings_for_user(old_id, new_id, dry_run, item)
            
            # Update user field from old_id to new_id
            item["user"] = new_id
            
            # Update id field: replace old_id with new_id in the ID
            if "id" in item and old_id in item["id"]:
                old_item_id = item["id"]
                item["id"] = item["id"].replace(old_id, new_id)
                log(msg % f"Updated id: {old_item_id} -> {item['id']}")
            
            # Update sharedBy fields in data array
            if "data" in item and isinstance(item["data"], list):
                for data_entry in item["data"]:
                    if isinstance(data_entry, dict) and "sharedBy" in data_entry:
                        if data_entry["sharedBy"] == old_id:
                            data_entry["sharedBy"] = new_id
                            log(msg % f"Updated sharedBy: {old_id} -> {new_id}")
                            
                    # Update key paths in data entries if they reference old_id
                    if isinstance(data_entry, dict) and "key" in data_entry:
                        old_key = data_entry["key"]
                        if old_id in old_key:
                            # Update key path to use new_id where old_id appears
                            key_parts = old_key.split('/')
                            new_key_parts = []
                            for part in key_parts:
                                new_key_parts.append(new_id if part == old_id else part)
                            data_entry["key"] = f"shares/{'/'.join(new_key_parts)}"
                            log(msg % f"Updated key: {old_key} -> {data_entry['key']}")
            
            if dry_run:
                log(msg % f"Would update shares item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating shares item to:\n\tNew Data: {item}")
                shares_table.put_item(Item=item)
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
            item["principal_id"] = new_id
            if dry_run:
                log(msg % f"Would update object access item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating object access item to:\n\tNew Data: {item}")
                object_access_table.put_item(Item=item)
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
            item["user"] = new_id
            if dry_run:
                log(
                    msg
                    % f"Would update assistants aliases item to:\n\tNew Data: {item}"
                )
            else:
                log(msg % f"Updating assistants aliases item to:\n\tNew Data: {item}")
                assistants_aliases_table.put_item(Item=item)
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
                assistants_table.update_item(
                    Key={"id": old_id},
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
            item["user"] = new_id
            if dry_run:
                log(
                    msg
                    % f"Would update assistant code interpreter item to:\n\tNew Data: {item}"
                )
            else:
                log(
                    msg
                    % f"Updating assistant code interpreter item to:\n\tNew Data: {item}"
                )
                assistant_code_interpreter_table.update_item(
                    Key={"id": item["id"]},
                    UpdateExpression="SET #user = :new_id",
                    ExpressionAttributeNames={"#user": "user"},
                    ExpressionAttributeValues={":new_id": new_id},
                )
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
        for item in paginated_query(table, "user", old_id, index_name="UserNameIndex"):
            log(
                msg
                % f"Found assistant threads record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(
                    msg % f"Would update assistant threads item to:\n\tNew Data: {item}"
                )
            else:
                log(msg % f"Updating assistant threads item to:\n\tNew Data: {item}")
                assistant_threads_table.update_item(
                    Key={"id": item["id"]},
                    UpdateExpression="SET #user = :new_id",
                    ExpressionAttributeNames={"#user": "user"},
                    ExpressionAttributeValues={":new_id": new_id},
                )
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
        # Query by user via UserIndex GSI
        for item in paginated_query(table, "user", old_id, index_name="UserIndex"):
            log(
                msg
                % f"Found assistant thread runs record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update assistant thread runs item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating assistant thread runs item to:\n\tNew Data: {item}")
                assistant_thread_runs_table.put_item(Item=item)
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
        # Scan required - no GSI for user fields
        for item in paginated_scan(table, "createdBy", old_id):
            log(
                msg
                % f"Found assistant groups record for user ID {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Update createdBy field
            if "createdBy" in item and item["createdBy"] == old_id:
                item["createdBy"] = new_id
            
            # Update members dict - keys are user_ids, values are permissions
            if "members" in item and isinstance(item["members"], dict):
                members = item["members"]
                if old_id in members:
                    permission = members[old_id]
                    del members[old_id]
                    members[new_id] = permission
                    log(msg % f"Updated member: {old_id} -> {new_id} with permission {permission}")
                item["members"] = members
            
            if dry_run:
                log(msg % f"Would update assistant groups item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating assistant groups item to:\\n\\tNew Data: {item}")
                assistant_groups_table.put_item(Item=item)
            ret = True
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
    # - DynamoDB updates: "user" field updated from old_id to new_id
    # - s3Location field: Remove s3:// prefix to indicate migration (use consolidation bucket)
    #
    # Processing flow:
    # 1. Call migrate_group_assistant_conversations_bucket_for_user() to migrate S3 files
    # 2. Update "user" field from old_id to new_id in GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE
    # 3. Update "s3Location" field to remove s3:// prefix (backward compatibility detection)
    success = migrate_group_assistant_conversations_bucket_for_user(old_id, new_id, dry_run)
    
    if not success:
        log(msg % f"Failed to migrate group assistant conversation files for user {old_id}")
        return False
    
    if not table:
        log(msg % f"Table GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE not found, skipping DynamoDB updates")
        return success  # S3 migration was successful, so return that status
    
    try:
        group_assistant_conversations_table = dynamodb.Table(table)

        ret = False
        for item in paginated_query(table, "user", old_id):
            log(
                msg
                % f"Found group assistant conversation record for user ID {old_id}.\n\tExisting Data: {item}"
            )
            
            # Update user field from old_id to new_id
            item["user"] = new_id
            
            # Update s3Location field if present - remove s3:// prefix to indicate migration
            if "s3Location" in item and isinstance(item["s3Location"], str):
                s3_location = item["s3Location"]
                
                # Check if it's still in legacy format with s3:// prefix
                if s3_location.startswith("s3://"):
                    # Extract key from s3Location (remove s3://bucket-name/ prefix)
                    # Example: "s3://amplify-v6-assistants-dev-group-conversations-content/astgp/..." -> "agentConversations/astgp/..."
                    import re
                    # Find the part after the bucket name (should start with "astgp/")
                    match = re.search(r's3://[^/]+/(.+)', s3_location)
                    if match:
                        key_path = match.group(1)  # Extract "astgp/..." 
                        if key_path.startswith("astgp/"):
                            # Transform to consolidated format
                            item["s3Location"] = f"agentConversations/{key_path}"
                        else:
                            log(msg % f"Unexpected s3Location format, keeping as-is: {s3_location}")
                    else:
                        log(msg % f"Could not parse s3Location format: {s3_location}")
                # If it doesn't start with s3://, it's already migrated - leave as-is
            
            if dry_run:
                log(msg % f"Would update group assistant conversation item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating group assistant conversation item to:\n\tNew Data: {item}")
                group_assistant_conversations_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating group assistant conversation records for user ID from {old_id} to {new_id}: {e}"
        )
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
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update user tags item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating user tags item to:\\n\\tNew Data: {item}")
                user_tags_table.put_item(Item=item)
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
            
            # Update user field from old_id to new_id
            item["user"] = new_id
            
            # Update memory field if present - remove bucket and update key path
            if "memory" in item and isinstance(item["memory"], dict):
                memory = item["memory"]
                
                # Remove bucket field to indicate migrated state
                if "bucket" in memory:
                    del memory["bucket"]
                
                # Update key path to use new agentState/ prefix
                if "key" in memory:
                    old_key = memory["key"]
                    if old_key.startswith(f"{old_id}/"):
                        # Transform: "{old_id}/{session_id}/..." -> "agentState/{new_id}/{session_id}/..."
                        key_suffix = old_key[len(f"{old_id}/"):]  # Remove old user prefix
                        memory["key"] = f"agentState/{new_id}/{key_suffix}"
                
                item["memory"] = memory
            
            if dry_run:
                log(msg % f"Would update agent state item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating agent state item to:\n\tNew Data: {item}")
                agent_state_table.put_item(Item=item)
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
            item["user"] = new_id
            if dry_run:
                log(
                    msg
                    % f"Would update agent event template item to:\n\tNew Data: {item}"
                )
            else:
                log(msg % f"Updating agent event template item to:\n\tNew Data: {item}")
                agent_event_templates_table.put_item(Item=item)
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
            
            # IMPLEMENTED: Workflow templates S3 to USER_STORAGE_TABLE migration
            # - "s3_key": Downloaded from S3 and migrated to USER_STORAGE_TABLE, then removed from record
            # - "template_uuid": Used directly as USER_STORAGE_TABLE SK (no transformation needed)
            # - "user": Updated from old_id to new_id (part of USER_STORAGE_TABLE PK)
            # Migration detection: Records without s3_key are considered migrated
            # 
            # Processing flow:
            # 1. Call migrate_workflow_templates_bucket_for_user() to migrate S3 content
            # 2. Update "user" attribute from old_id to new_id
            # 3. Remove "s3_key" from record (handled by migration function)
            success, updated_item = migrate_workflow_templates_bucket_for_user(old_id, new_id, dry_run, item)
            
            # Update user_id and remove s3_key while preserving ALL other columns
            if updated_item:
                item = updated_item  # Use the updated item from migration
            item["user"] = new_id
            
            if dry_run:
                log(
                    msg % f"Would update workflow template item to:\n\tNew Data: {item}"
                )
            else:
                log(msg % f"Updating workflow template item to:\n\tNew Data: {item}")
                workflow_templates_table.put_item(Item=item)
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
        # Scan entire table - need to check both email field and allowedSenders list
        table_obj = dynamodb.Table(table)
        
        # Scan for records where email == old_id
        for item in paginated_scan(table, "email", old_id):
            log(
                msg
                % f"Found email settings record with email {old_id}.\\n\\tExisting Data: {item}"
            )
            
            # Update email field
            item["email"] = new_id
            
            # Update allowedSenders list - replace old_id in any patterns
            if "allowedSenders" in item and isinstance(item["allowedSenders"], list):
                updated_senders = []
                for sender_pattern in item["allowedSenders"]:
                    # Replace old_id with new_id in the pattern string
                    updated_pattern = sender_pattern.replace(old_id, new_id)
                    updated_senders.append(updated_pattern)
                    if updated_pattern != sender_pattern:
                        log(msg % f"Updated allowedSender: {sender_pattern} -> {updated_pattern}")
                item["allowedSenders"] = updated_senders
            
            if dry_run:
                log(msg % f"Would update email settings item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating email settings item to:\\n\\tNew Data: {item}")
                email_settings_table.put_item(Item=item)
            ret = True
        
        # Also scan for records that have old_id in allowedSenders but different email
        scanner = table_obj.scan()
        while True:
            for item in scanner.get('Items', []):
                # Skip if we already processed this item (email == old_id)
                if item.get("email") == old_id:
                    continue
                    
                # Check allowedSenders list for old_id
                if "allowedSenders" in item and isinstance(item["allowedSenders"], list):
                    updated_senders = []
                    has_updates = False
                    for sender_pattern in item["allowedSenders"]:
                        # Replace old_id with new_id in the pattern string
                        updated_pattern = sender_pattern.replace(old_id, new_id)
                        updated_senders.append(updated_pattern)
                        if updated_pattern != sender_pattern:
                            has_updates = True
                            log(msg % f"Updated allowedSender: {sender_pattern} -> {updated_pattern}")
                    
                    if has_updates:
                        log(
                            msg
                            % f"Found email settings record with old_id in allowedSenders.\\n\\tExisting Data: {item}"
                        )
                        item["allowedSenders"] = updated_senders
                        
                        if dry_run:
                            log(msg % f"Would update email settings item to:\\n\\tNew Data: {item}")
                        else:
                            log(msg % f"Updating email settings item to:\\n\\tNew Data: {item}")
                            email_settings_table.put_item(Item=item)
                        ret = True
            
            if 'LastEvaluatedKey' not in scanner:
                break
            scanner = table_obj.scan(ExclusiveStartKey=scanner['LastEvaluatedKey'])
        
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
            
            # IMPLEMENTED: Scheduled tasks logs S3 to USER_STORAGE_TABLE migration
            # - "logs": Array consolidated from multiple S3 detailsKey files to single USER_STORAGE_TABLE entry
            # - "taskId": Used directly as USER_STORAGE_TABLE SK (no transformation needed)
            # - "user": Updated from old_id to new_id (part of USER_STORAGE_TABLE PK)  
            # - "detailsKey": Removed from logs array entries after migration (this is how we detect migrated logs)
            # Migration detection: Logs without detailsKey entries are considered migrated
            # Size monitoring: 350KB threshold warning for DynamoDB 400KB limit
            # 
            # Processing flow:
            # 1. Call migrate_scheduled_tasks_logs_bucket_for_user() to consolidate S3 logs
            # 2. Update "user" attribute from old_id to new_id
            # 3. Remove "detailsKey" from logs array entries (handled by migration function)
            success, updated_item = migrate_scheduled_tasks_logs_bucket_for_user(old_id, new_id, dry_run, item)
            
            # Update user_id and logs array while preserving ALL other columns
            if updated_item:
                item = updated_item  # Use the updated item from migration
            item["user"] = new_id
            
            if dry_run:
                log(msg % f"Would update scheduled task item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating scheduled task item to:\n\tNew Data: {item}")
                scheduled_tasks_table.put_item(Item=item)
            ret = True
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

        # TODO(Karely): It'd be nice to be able to query this rather than scan.
        # Also, we're creating new records here when, strictly speaking, we could
        # just update the existing ones in place. Still, this is consistent with what
        # we're doing elsewhere. So, we'll need to delete the old records later.

        ret = False
        for item in paginated_scan(table, "user", old_id):
            log(
                msg
                % f"Found OAuth state records for user ID {old_id}.\n\tExisting Data: {item}"
            )
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update OAuth state item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating OAuth state item to:\n\tNew Data: {item}")
                oauth_state_table.put_item(Item=item)
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
            
            # Update user_integration prefix: old_id/service -> new_id/service
            if "user_integration" in item:
                old_integration = item["user_integration"]
                if old_integration.startswith(f"{old_id}/"):
                    suffix = old_integration[len(f"{old_id}/"):]
                    item["user_integration"] = f"{new_id}/{suffix}"
                    log(msg % f"Updated user_integration: {old_integration} -> {item['user_integration']}")
            
            if dry_run:
                log(msg % f"Would update OAuth user item to:\n\tNew Data: {item}")
            else:
                log(msg % f"Updating OAuth user item to:\n\tNew Data: {item}")
                oauth_user_table.put_item(Item=item)
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
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update data disclosure acceptance item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating data disclosure acceptance item to:\\n\\tNew Data: {item}")
                data_disclosure_acceptance_table.put_item(Item=item)
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
            
            # Update id field from old_id to new_id
            item["id"] = new_id
            
            if dry_run:
                log(msg % f"Would update cost calculations item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating cost calculations item to:\\n\\tNew Data: {item}")
                cost_calculations_table.put_item(Item=item)
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
            item["user"] = new_id
            if dry_run:
                log(msg % f"Would update additional charges item to:\\n\\tNew Data: {item}")
            else:
                log(msg % f"Updating additional charges item to:\\n\\tNew Data: {item}")
                additional_charges_table.put_item(Item=item)
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating additional charges for user ID from {old_id} to {new_id}: {e}"
        )
        return False


### Chat related tables ###
# "CHAT_USAGE_DYNAMO_TABLE" : "amplify-v6-lambda-dev-chat-usages",
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
            ret = True
        return ret
    except Exception as e:
        log(
            msg
            % f"Error updating conversation metadata for user ID from {old_id} to {new_id}: {e}"
        )
        return False


# "USER_STORAGE_TABLE" : "amplify-v6-lambda-basic-ops-dev-user-storage",
def update_user_storage_table(old_id: str, new_id: str, dry_run: bool) -> bool:
    """Update all user storage records associated with the old user ID to the new user ID."""
    msg = f"[update_user_storage_table][dry-run: {dry_run}] %s"
    table = table_names.get("USER_STORAGE_TABLE")
    if not table:
        log(msg % f"Table USER_STORAGE_TABLE not found, skipping")
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
                    # Update the user field for this memory
                    try:
                        memory_table.update_item(
                            Key={"id": memory_id},
                            UpdateExpression="SET #user = :new_user",
                            ExpressionAttributeNames={"#user": "user"},
                            ExpressionAttributeValues={":new_user": new_id}
                        )
                        log(msg % f"Updated memory {memory_id} from user '{old_id}' to '{new_id}'")
                        items_updated += 1
                    except Exception as e:
                        log(msg % f"Failed to update memory {memory_id}: {str(e)}")
                        return False

        log(msg % f"Processed {items_updated} memory records for user {old_id}")
        return True

    except Exception as e:
        log(msg % f"Error processing memory table {table}: {str(e)}")
        return False

def ensure_user_storage_migration(dry_run: bool, old_table: str, new_table: str) -> bool:
    """
    Ensure user storage table migration from basic-ops to amplify-lambda.
    
    Steps:
    1. Check if backup CSV exists, if not create it
    2. Check if new table exists (user-data-storage suffix)  
    3. If new table exists, migrate data from CSV
    """
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


def migrate_shares_to_user_storage_table(dry_run: bool) -> bool:
    """
    Migrate SHARES_DYNAMODB_TABLE data to USER_STORAGE_TABLE.
    
    Transform schema from legacy shares format to:
    - PK: "{user_id}#amplify-shares#received"  
    - SK: "{sharer_id}#{date}#{uuid}"
    - data: share metadata (sharedBy, note, sharedAt, key)
    """
    msg = f"[migrate_shares_to_user_storage_table][dry-run: {dry_run}] %s"
    shares_table_name = table_names.get("SHARES_DYNAMODB_TABLE")
    
    if not shares_table_name:
        log(msg % "SHARES_DYNAMODB_TABLE not found in config, skipping migration")
        return True
    
    try:
        shares_table = dynamodb.Table(shares_table_name)
        migrated_count = 0
        
        # Scan the entire SHARES_DYNAMODB_TABLE
        log(msg % f"Starting migration from {shares_table_name}")
        
        paginator = shares_table.scan()
        while True:
            for item in paginator.get('Items', []):
                user_id = item.get('user')
                share_name = item.get('name')  # Usually '/state/share'
                share_data_array = item.get('data', [])
                
                if not user_id or not isinstance(share_data_array, list):
                    log(msg % f"Skipping invalid share record: {item}")
                    continue
                
                log(msg % f"Processing shares for user {user_id}, found {len(share_data_array)} shares")
                
                # Process each share in the data array
                for share_entry in share_data_array:
                    try:
                        # Extract share metadata
                        shared_by = share_entry.get('sharedBy', '')
                        shared_at = share_entry.get('sharedAt', 0)
                        note = share_entry.get('note', '')
                        key = share_entry.get('key', '')
                        
                        if not shared_by or not key:
                            log(msg % f"Skipping share entry missing required fields: {share_entry}")
                            continue
                        
                        # Generate new schema components
                        # Extract date from timestamp or use current date
                        if shared_at:
                            try:
                                from datetime import datetime
                                dt_obj = datetime.fromtimestamp(shared_at / 1000)  # Convert ms to seconds
                                date_str = dt_obj.strftime("%Y-%m-%d")
                            except:
                                date_str = datetime.now().strftime("%Y-%m-%d")
                        else:
                            date_str = datetime.now().strftime("%Y-%m-%d")
                        
                        # Generate unique share ID: "{sharer_id}#{date}#{uuid}"
                        import uuid as uuid_lib
                        share_id = f"{shared_by}#{date_str}#{str(uuid_lib.uuid4())}"
                        
                        # Prepare USER_STORAGE_TABLE data
                        user_storage_data = {
                            "sharedBy": shared_by,
                            "note": note,
                            "sharedAt": shared_at,
                            "key": key
                        }
                        
                        if dry_run:
                            log(msg % f"Would migrate share: user={user_id}, sharer={shared_by}, key={key}")
                        else:
                            # Use CommonData to store in USER_STORAGE_TABLE
                            try:
                                common_data = CommonData()
                                result = common_data.put_item(
                                    user_id=user_id,
                                    app_id="amplify-shares",
                                    entity_type="received",
                                    item_id=share_id,
                                    data=user_storage_data
                                )
                                
                                if result:
                                    log(msg % f"Successfully migrated share: user={user_id}, sharer={shared_by}")
                                    migrated_count += 1
                                else:
                                    log(msg % f"Failed to migrate share for user {user_id}, sharer {shared_by}")
                                    return False
                                    
                            except Exception as e:
                                log(msg % f"Error migrating share for user {user_id}: {e}")
                                return False
                        
                    except Exception as e:
                        log(msg % f"Error processing share entry {share_entry}: {e}")
                        continue
                
                # After successful migration of all shares for this user, delete the legacy record
                if not dry_run and share_data_array:
                    try:
                        shares_table.delete_item(
                            Key={
                                'user': user_id,
                                'name': share_name
                            }
                        )
                        log(msg % f"Deleted legacy share record for user {user_id}")
                    except Exception as e:
                        log(msg % f"Error deleting legacy share record for user {user_id}: {e}")
                        # Don't fail the migration for delete errors
            
            # Check for more pages
            if 'LastEvaluatedKey' not in paginator:
                break
            paginator = shares_table.scan(ExclusiveStartKey=paginator['LastEvaluatedKey'])
        
        if dry_run:
            log(msg % f"Migration dry run completed. Would migrate shares data to USER_STORAGE_TABLE")
        else:
            log(msg % f"Migration completed successfully. Migrated {migrated_count} shares to USER_STORAGE_TABLE")
        
        return True
        
    except Exception as e:
        log(msg % f"Error during shares migration: {e}")
        return False



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
    args = parse_args()

    global table_names
    table_names = get_tables_from_config_file()

    if not tables_ok(table_names, continue_anyway=True):
        sys.exit(1)

    try:
        print("Setting logging to file...")
        logfile = open(args.log, "w")
        sys.stdout = logfile
        sys.stderr = logfile
        log(f"Starting user ID migration. Dry run: {args.dry_run}")
        
        # Step 1: Ensure user storage table migration from basic-ops to amplify-lambda
        log(f"\n=== USER STORAGE TABLE MIGRATION ===")
        if not ensure_user_storage_migration(args.dry_run, args.old_table, args.new_table):
            log(f"User storage migration check failed. Continuing with user ID migration...")
        else:
            log(f"User storage migration check completed successfully.")
        
        # Step 1.5: Migrate SHARES_DYNAMODB_TABLE to USER_STORAGE_TABLE
        log(f"\n=== SHARES TABLE CONSOLIDATION ===")
        if not migrate_shares_to_user_storage_table(args.dry_run):
            log(f"Shares table migration failed. Continuing with user ID migration...")
        else:
            log(f"Shares table migration completed successfully.")

        # loop through our users
        for u in get_users_from_csv(args.csv_file).items():
            log(f"\n\nProcessing user: old: {u[0]} new: {u[1]}")
            old_user_id = u[0]
            new_user_id = u[1]
            # this is a sanity check to make user exists
            user = get_user(old_user_id)

            # if not user:
            #     log(f"\tUser with old ID {old_user_id} not found. Skipping.")
            #     continue

            # if not update_user_id(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update user ID for {old_user_id}. Skipping - Manual intervention required."
            #     )
            #     continue

            # if not update_accounts(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update accounts for {old_user_id}. Skipping - Manual intervention required."
            #     )
            #     # continue

            # if not update_api_keys(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update API keys for {old_user_id}. This is assumed reasonable as not all users have API keys."
            #     )

            # if not update_ops_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update ops records for {old_user_id}. This is assumed reasonable as not all users have ops records."
            #     )

            # if not update_agent_state_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update agent state records for {old_user_id}. This is assumed reasonable as not all users have agent state records."
            #     )

            # if not update_oauth_state_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update OAuth state records for {old_user_id}. This is assumed reasonable as not all users have OAuth state records."
            #     )

            # if not update_amplify_admin_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update Amplify Admin records for {old_user_id}. This is assumed reasonable as not all users are admins."
            #     )

            # if not update_artifacts_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update artifacts records for {old_user_id}. This is assumed reasonable as not all users have artifacts."
            #     )

            # if not update_agent_event_templates_table(
            #     old_user_id, new_user_id, args.dry_run
            # ):
            #     log(
            #         f"Unable to update agent event templates records for {old_user_id}. This is assumed reasonable as not all users have agent event templates."
            #     )

            # if not update_workflow_templates_table(
            #     old_user_id, new_user_id, args.dry_run
            # ):
            #     log(
            #         f"Unable to update workflow templates records for {old_user_id}. This is assumed reasonable as not all users have workflow templates."
            #     )

            # if not update_object_access_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update object access records for {old_user_id}. This is assumed reasonable as not all users have object access records."
            #     )

            # if not update_assistants_aliases_table(
            #     old_user_id, new_user_id, args.dry_run
            # ):
            #     log(
            #         f"Unable to update assistants aliases records for {old_user_id}. This is assumed reasonable as not all users have assistants aliases records."
            #     )

            # if not update_assistants_table(old_user_id, new_user_id, args.dry_run):
            #     log(
            #         f"Unable to update assistants records for {old_user_id}. This is assumed reasonable as not all users have assistants records."
            #     )

            if not update_assistant_code_interpreter_table(
                old_user_id, new_user_id, args.dry_run
            ):
                log(
                    f"Unable to update assistant code interpreter records for {old_user_id}. This is assumed reasonable as not all users have assistant code interpreter records."
                )

            if not update_assistant_threads_table(
                old_user_id, new_user_id, args.dry_run
            ):
                log(
                    f"Unable to update assistant threads records for {old_user_id}. This is assumed reasonable as not all users have assistant threads records."
                )

            if not update_memory_table(old_user_id, new_user_id, args.dry_run):
                log(
                    f"Unable to update memory records for {old_user_id}. This is assumed reasonable as not all users have memory records."
                )
        
        # Step 2: Ask user if they want to run S3 bucket migration
        log(f"\n=== S3 BUCKET MIGRATION ===")
        
        if not args.dry_run:
            # Restore stdout temporarily to ask user for input
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            
            print("\n" + "="*60)
            print("USER ID MIGRATION COMPLETED")
            print("="*60)
            print("\nNext step: S3 Bucket Migration")
            print("This will migrate data from legacy S3 buckets to consolidation bucket.")
            print("This includes:")
            print("- Data disclosure files")
            print("- API documentation")
            print("- Group assistant conversations (not user-specific)")
            print("\nWARNING: Ensure environment variables are set for S3 migration!")
            
            response = input("\nDo you want to run the S3 bucket migration now? (yes/no): ").lower().strip()
            
            # Restore file logging
            sys.stdout = logfile
            sys.stderr = logfile
            
            if response in ['yes', 'y']:
                log(f"User confirmed S3 bucket migration. Starting...")
                try:
                    # Run S3 migration with same dry_run setting
                    import sys as sys_module
                    original_argv = sys_module.argv
                    
                    # Set up argv for s3_migration_main
                    sys_module.argv = ['s3_data_migration.py', '--bucket', 'all']
                    if args.dry_run:
                        sys_module.argv.append('--dry-run')
                    
                    # Call S3 migration main function
                    s3_success = s3_migration_main()
                    
                    # Restore original argv
                    sys_module.argv = original_argv
                    
                    if s3_success:
                        log(f"S3 bucket migration completed successfully!")
                    else:
                        log(f"S3 bucket migration failed!")
                        
                except Exception as s3_error:
                    log(f"Error running S3 migration: {s3_error}")
                    
            else:
                log(f"User declined S3 bucket migration. Run manually: python3 s3_data_migration.py --bucket all")
        else:
            log(f"[DRY RUN] S3 bucket migration would be offered to user after real migration")

    except Exception as e:
        log(f"Error processing users: {e}")
    finally:
        logfile.close()
