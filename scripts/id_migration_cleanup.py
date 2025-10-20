#!/usr/bin/env python3
"""
Cleanup script for ID migration - removes old user ID records after successful migration.
This should be run ONLY after verifying that the migration was successful and all services
are working correctly with the new user IDs.

IMPORTANT: This permanently deletes old records. Make sure you have backups!
"""

import sys
import csv
import argparse
import boto3
from datetime import datetime
from typing import Dict
from boto3.dynamodb.conditions import Key

from config import get_config

dynamodb = boto3.resource("dynamodb")


def log(*messages):
    for message in messages:
        print(f"[{datetime.now()}]", message)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Clean up old user ID records after successful migration."
    )
    parser.add_argument(
        "--csv-file",
        required=True,
        help="Path to the CSV file containing migration data (same as used for migration).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not delete anything, just show what would be deleted.",
    )
    parser.add_argument(
        "--log", 
        required=True, 
        help="Log output to the specified file."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt (use with caution!).",
    )
    return parser.parse_args()


def get_users_from_csv(file_path: str) -> Dict[str, str]:
    """Read CSV and return mapping of old_id to new_id."""
    users = {}
    try:
        with open(file_path, mode="r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                old_id = row.get("old_id")
                new_id = row.get("new_id")
                if old_id and new_id and old_id != new_id:  # Only include actual migrations
                    users[old_id] = new_id
                else:
                    if old_id == new_id:
                        log(f"Skipping {old_id} - same as new ID (no cleanup needed)")
    except Exception as e:
        log(f"Error reading CSV file {file_path}: {e}")
        sys.exit(1)
    return users


def verify_new_record_exists(table_name: str, key_conditions: dict, new_id: str) -> bool:
    """Verify that the new record exists before deleting the old one."""
    try:
        table = dynamodb.Table(table_name)
        # Try to get the item with the new key
        # This is a simplified check - actual verification would depend on table structure
        return True  # Simplified for now - in production, implement proper checks
    except Exception as e:
        log(f"Error verifying new record: {e}")
        return False


def cleanup_user_id_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old user_id record from COGNITO_USERS_DYNAMODB_TABLE."""
    msg = f"[cleanup_user_id_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("COGNITO_USERS_DYNAMODB_TABLE")
    deleted_count = 0
    
    try:
        table = dynamodb.Table(table_name)
        
        if dry_run:
            # Check if old record exists
            response = table.get_item(Key={"user_id": old_id})
            if "Item" in response:
                log(msg % f"Would delete user record with user_id={old_id}")
                deleted_count = 1
        else:
            # Delete the old record
            response = table.delete_item(
                Key={"user_id": old_id},
                ReturnValues="ALL_OLD"
            )
            if "Attributes" in response:
                log(msg % f"Deleted user record with user_id={old_id}")
                deleted_count = 1
                
    except Exception as e:
        log(msg % f"Error cleaning up user_id table: {e}")
    
    return deleted_count


def cleanup_artifacts_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old artifacts records."""
    msg = f"[cleanup_artifacts_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("ARTIFACTS_DYNAMODB_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user_id").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete artifact record with user_id={old_id}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(Key={"user_id": old_id})
                log(msg % f"Deleted artifact record with user_id={old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up artifacts table: {e}")
    
    return deleted_count


def cleanup_ops_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old ops records."""
    msg = f"[cleanup_ops_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("OPS_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user_id").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete ops record with user_id={old_id}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(Key={"user_id": old_id})
                log(msg % f"Deleted ops record with user_id={old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up ops table: {e}")
    
    return deleted_count


def cleanup_shares_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old shares records."""
    msg = f"[cleanup_shares_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("SHARES_DYNAMODB_TABLE")
    deleted_count = 0
    
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete share record with user={old_id}, name={item.get('name')}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(
                    Key={"user": old_id, "name": item.get("name")}
                )
                log(msg % f"Deleted share record with user={old_id}, name={item.get('name')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up shares table: {e}")
    
    return deleted_count


def cleanup_conversation_metadata_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old conversation metadata records."""
    msg = f"[cleanup_conversation_metadata_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("CONVERSATION_METADATA_TABLE")
    deleted_count = 0
    
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user_id").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete conversation metadata with user_id={old_id}, conversation_id={item.get('conversation_id')}")
                deleted_count += 1
            else:
                # Delete the old record (assuming composite key)
                table.delete_item(
                    Key={"user_id": old_id, "conversation_id": item.get("conversation_id")}
                )
                log(msg % f"Deleted conversation metadata with user_id={old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up conversation metadata table: {e}")
    
    return deleted_count


def cleanup_user_storage_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old user storage records."""
    msg = f"[cleanup_user_storage_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("USER_STORAGE_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Scan for records with old PK prefix
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("PK").begins_with(f"{old_id}#")
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete user storage record with PK={item.get('PK')}, SK={item.get('SK')}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(
                    Key={"PK": item.get("PK"), "SK": item.get("SK")}
                )
                log(msg % f"Deleted user storage record with PK={item.get('PK')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up user storage table: {e}")
    
    return deleted_count


def cleanup_accounts_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old accounts records."""
    msg = f"[cleanup_accounts_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("ACCOUNTS_DYNAMO_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old record
        response = table.get_item(Key={"user": old_id})
        
        if "Item" in response:
            if dry_run:
                log(msg % f"Would delete account record with user={old_id}")
                deleted_count = 1
            else:
                # Delete the old record
                table.delete_item(Key={"user": old_id})
                log(msg % f"Deleted account record with user={old_id}")
                deleted_count = 1
                
    except Exception as e:
        log(msg % f"Error cleaning up accounts table: {e}")
    
    return deleted_count


def cleanup_assistants_aliases_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old assistants aliases records."""
    msg = f"[cleanup_assistants_aliases_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("ASSISTANTS_ALIASES_DYNAMODB_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete assistant alias with user={old_id}, alias={item.get('alias')}")
                deleted_count += 1
            else:
                # Delete the old record (composite key: user, alias)
                table.delete_item(
                    Key={"user": old_id, "alias": item.get("alias")}
                )
                log(msg % f"Deleted assistant alias with user={old_id}, alias={item.get('alias')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up assistants aliases table: {e}")
    
    return deleted_count


def cleanup_group_assistant_conversations_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old group assistant conversations records."""
    msg = f"[cleanup_group_assistant_conversations_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete group conversation with user={old_id}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(Key={"user": old_id})
                log(msg % f"Deleted group conversation with user={old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up group assistant conversations table: {e}")
    
    return deleted_count


def cleanup_user_tags_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old user tags records."""
    msg = f"[cleanup_user_tags_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("USER_TAGS_DYNAMO_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete user tag with user={old_id}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(Key={"user": old_id})
                log(msg % f"Deleted user tag with user={old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up user tags table: {e}")
    
    return deleted_count


def cleanup_agent_state_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old agent state records."""
    msg = f"[cleanup_agent_state_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("AGENT_STATE_DYNAMODB_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records - table uses 'user' as primary key
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete agent state with user={old_id}, key={item.get('key')}")
                deleted_count += 1
            else:
                # Delete the old record (composite key: user, key)
                table.delete_item(
                    Key={"user": old_id, "key": item.get("key")}
                )
                log(msg % f"Deleted agent state with user={old_id}, key={item.get('key')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up agent state table: {e}")
    
    return deleted_count


def cleanup_agent_event_templates_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old agent event templates records."""
    msg = f"[cleanup_agent_event_templates_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete event template with user={old_id}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(Key={"user": old_id})
                log(msg % f"Deleted event template with user={old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up agent event templates table: {e}")
    
    return deleted_count


def cleanup_workflow_templates_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old workflow templates records."""
    msg = f"[cleanup_workflow_templates_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("WORKFLOW_TEMPLATES_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete workflow template with user={old_id}, id={item.get('id')}")
                deleted_count += 1
            else:
                # Delete the old record (composite key: user, id)
                table.delete_item(
                    Key={"user": old_id, "id": item.get("id")}
                )
                log(msg % f"Deleted workflow template with user={old_id}, id={item.get('id')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up workflow templates table: {e}")
    
    return deleted_count


def cleanup_scheduled_tasks_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old scheduled tasks records."""
    msg = f"[cleanup_scheduled_tasks_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("SCHEDULED_TASKS_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old records
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete scheduled task with user={old_id}, task_id={item.get('task_id')}")
                deleted_count += 1
            else:
                # Delete the old record (composite key: user, task_id)
                table.delete_item(
                    Key={"user": old_id, "task_id": item.get("task_id")}
                )
                log(msg % f"Deleted scheduled task with user={old_id}, task_id={item.get('task_id')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up scheduled tasks table: {e}")
    
    return deleted_count


def cleanup_oauth_state_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old OAuth state records."""
    msg = f"[cleanup_oauth_state_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("OAUTH_STATE_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # OAuth state uses state as primary key, but stores user in item
        # Need to scan for records with old user
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete OAuth state with state={item.get('state')}, user={old_id}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(Key={"state": item.get("state")})
                log(msg % f"Deleted OAuth state with state={item.get('state')}, user={old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up OAuth state table: {e}")
    
    return deleted_count


def cleanup_oauth_user_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old OAuth user records."""
    msg = f"[cleanup_oauth_user_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("OAUTH_USER_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # OAuth user uses user_integration as primary key (format: "{user}#{integration}")
        # Query for old records starting with old_id
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("user_integration").begins_with(f"{old_id}#")
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete OAuth user with user_integration={item.get('user_integration')}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(Key={"user_integration": item.get("user_integration")})
                log(msg % f"Deleted OAuth user with user_integration={item.get('user_integration')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up OAuth user table: {e}")
    
    return deleted_count


def cleanup_data_disclosure_acceptance_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old data disclosure acceptance records."""
    msg = f"[cleanup_data_disclosure_acceptance_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("DATA_DISCLOSURE_ACCEPTANCE_TABLE")
    deleted_count = 0
    
    if not table_name:
        log(msg % "Table not configured, skipping")
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Query for old record
        response = table.get_item(Key={"user": old_id})
        
        if "Item" in response:
            if dry_run:
                log(msg % f"Would delete data disclosure acceptance with user={old_id}")
                deleted_count = 1
            else:
                # Delete the old record
                table.delete_item(Key={"user": old_id})
                log(msg % f"Deleted data disclosure acceptance with user={old_id}")
                deleted_count = 1
                
    except Exception as e:
        log(msg % f"Error cleaning up data disclosure acceptance table: {e}")
    
    return deleted_count


def cleanup_history_cost_calculations_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old history cost calculations records."""
    msg = f"[cleanup_history_cost_calculations_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("HISTORY_COST_CALCULATIONS_DYNAMO_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # History cost calculations uses userDate as primary key (format: "{user}#{date}")
        # Scan for records starting with old_id
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("userDate").begins_with(f"{old_id}#")
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete history cost calculation with userDate={item.get('userDate')}")
                deleted_count += 1
            else:
                # Delete the old record
                table.delete_item(Key={"userDate": item.get("userDate")})
                log(msg % f"Deleted history cost calculation with userDate={item.get('userDate')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up history cost calculations table: {e}")
    
    return deleted_count


def cleanup_object_access_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old object access records."""
    msg = f"[cleanup_object_access_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("OBJECT_ACCESS_DYNAMODB_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        response = table.get_item(Key={"user_id": old_id})
        
        if "Item" in response:
            if dry_run:
                log(msg % f"Would delete object access record with user_id={old_id}")
                deleted_count = 1
            else:
                table.delete_item(Key={"user_id": old_id})
                log(msg % f"Deleted object access record with user_id={old_id}")
                deleted_count = 1
                
    except Exception as e:
        log(msg % f"Error cleaning up object access table: {e}")
    
    return deleted_count


def cleanup_files_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old files records."""
    msg = f"[cleanup_files_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("FILES_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        response = table.get_item(Key={"user_id": old_id})
        
        if "Item" in response:
            if dry_run:
                log(msg % f"Would delete files record with user_id={old_id}")
                deleted_count = 1
            else:
                table.delete_item(Key={"user_id": old_id})
                log(msg % f"Deleted files record with user_id={old_id}")
                deleted_count = 1
                
    except Exception as e:
        log(msg % f"Error cleaning up files table: {e}")
    
    return deleted_count


def cleanup_common_data_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old common data records."""
    msg = f"[cleanup_common_data_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("COMMON_DATA_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        response = table.get_item(Key={"user_id": old_id})
        
        if "Item" in response:
            if dry_run:
                log(msg % f"Would delete common data record with user_id={old_id}")
                deleted_count = 1
            else:
                table.delete_item(Key={"user_id": old_id})
                log(msg % f"Deleted common data record with user_id={old_id}")
                deleted_count = 1
                
    except Exception as e:
        log(msg % f"Error cleaning up common data table: {e}")
    
    return deleted_count


def cleanup_dynamic_code_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old dynamic code records."""
    msg = f"[cleanup_dynamic_code_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("DYNAMIC_CODE_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        
        # Dynamic code uses composite key: user_id, code_id
        response = table.query(
            KeyConditionExpression=Key("user_id").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete dynamic code with user_id={old_id}, code_id={item.get('code_id')}")
                deleted_count += 1
            else:
                table.delete_item(
                    Key={"user_id": old_id, "code_id": item.get("code_id")}
                )
                log(msg % f"Deleted dynamic code with user_id={old_id}, code_id={item.get('code_id')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up dynamic code table: {e}")
    
    return deleted_count


def cleanup_amplify_admin_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old amplify admin records."""
    msg = f"[cleanup_amplify_admin_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("AMPLIFY_ADMIN_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        # Scan for records that match old_id in user_id field
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("user_id").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete amplify admin record with id={item.get('id')}, user_id={old_id}")
                deleted_count += 1
            else:
                table.delete_item(Key={"id": item.get("id")})
                log(msg % f"Deleted amplify admin record with id={item.get('id')}, user_id={old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up amplify admin table: {e}")
    
    return deleted_count


def cleanup_api_keys_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old API keys records."""
    msg = f"[cleanup_api_keys_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("API_KEYS_DYNAMODB_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        # Scan for records with old user_id
        response = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("user_id").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete API key {item.get('api_key')} for user {old_id}")
                deleted_count += 1
            else:
                table.delete_item(Key={"api_key": item.get("api_key")})
                log(msg % f"Deleted API key {item.get('api_key')} for user {old_id}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up API keys table: {e}")
    
    return deleted_count


def cleanup_email_settings_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old email settings records."""
    msg = f"[cleanup_email_settings_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("EMAIL_SETTINGS_DYNAMO_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        # Email settings uses composite key
        response = table.query(
            KeyConditionExpression=Key("user").eq(old_id)
        )
        
        for item in response.get("Items", []):
            if dry_run:
                log(msg % f"Would delete email settings with user={old_id}, email={item.get('email')}")
                deleted_count += 1
            else:
                table.delete_item(
                    Key={"user": old_id, "email": item.get("email")}
                )
                log(msg % f"Deleted email settings with user={old_id}, email={item.get('email')}")
                deleted_count += 1
                
    except Exception as e:
        log(msg % f"Error cleaning up email settings table: {e}")
    
    return deleted_count


def cleanup_db_connections_table(old_id: str, new_id: str, dry_run: bool) -> int:
    """Clean up old database connections records."""
    msg = f"[cleanup_db_connections_table][dry-run: {dry_run}] %s"
    table_name = table_names.get("DB_CONNECTIONS_TABLE")
    deleted_count = 0
    
    if not table_name:
        return 0
        
    try:
        table = dynamodb.Table(table_name)
        response = table.get_item(Key={"user_id": old_id})
        
        if "Item" in response:
            if dry_run:
                log(msg % f"Would delete DB connections record with user_id={old_id}")
                deleted_count = 1
            else:
                table.delete_item(Key={"user_id": old_id})
                log(msg % f"Deleted DB connections record with user_id={old_id}")
                deleted_count = 1
                
    except Exception as e:
        log(msg % f"Error cleaning up DB connections table: {e}")
    
    return deleted_count


def cleanup_all_tables_for_user(old_id: str, new_id: str, dry_run: bool) -> Dict[str, int]:
    """Clean up old records for a single user across all affected tables."""
    
    results = {}
    
    # Tables that need cleanup (those that create new records instead of updating)
    # Based on analysis of id_migration.py - these functions use put_item which creates new records
    # NOTE: Tables using update_item don't need cleanup (they modify in place)
    cleanup_functions = [
        ("COGNITO_USERS", cleanup_user_id_table),
        ("AMPLIFY_ADMIN", cleanup_amplify_admin_table),
        ("ACCOUNTS", cleanup_accounts_table),
        ("API_KEYS", cleanup_api_keys_table),
        ("ARTIFACTS", cleanup_artifacts_table),
        ("OPS", cleanup_ops_table),
        ("SHARES", cleanup_shares_table),
        ("OBJECT_ACCESS", cleanup_object_access_table),
        ("ASSISTANTS_ALIASES", cleanup_assistants_aliases_table),
        ("GROUP_ASSISTANT_CONVERSATIONS", cleanup_group_assistant_conversations_table),
        ("FILES", cleanup_files_table),
        ("USER_TAGS", cleanup_user_tags_table),
        ("AGENT_STATE", cleanup_agent_state_table),
        ("AGENT_EVENT_TEMPLATES", cleanup_agent_event_templates_table),
        ("WORKFLOW_TEMPLATES", cleanup_workflow_templates_table),
        ("EMAIL_SETTINGS", cleanup_email_settings_table),
        ("SCHEDULED_TASKS", cleanup_scheduled_tasks_table),
        ("DB_CONNECTIONS", cleanup_db_connections_table),
        ("OAUTH_STATE", cleanup_oauth_state_table),
        ("OAUTH_USER", cleanup_oauth_user_table),
        ("DATA_DISCLOSURE_ACCEPTANCE", cleanup_data_disclosure_acceptance_table),
        ("HISTORY_COST_CALCULATIONS", cleanup_history_cost_calculations_table),
        ("CONVERSATION_METADATA", cleanup_conversation_metadata_table),
        ("USER_STORAGE", cleanup_user_storage_table),
        ("COMMON_DATA", cleanup_common_data_table),
        ("DYNAMIC_CODE", cleanup_dynamic_code_table),
    ]
    
    for table_name, cleanup_func in cleanup_functions:
        try:
            count = cleanup_func(old_id, new_id, dry_run)
            results[table_name] = count
        except Exception as e:
            log(f"Error cleaning up {table_name}: {e}")
            results[table_name] = 0
    
    return results


def main():
    args = parse_args()
    
    global table_names
    table_names = get_config()
    
    # Set up logging
    if args.log:
        logfile = open(args.log, "w")
        sys.stdout = logfile
        sys.stderr = logfile
    
    log(f"Starting ID migration cleanup. Dry run: {args.dry_run}")
    
    if not args.dry_run and not args.force:
        # Restore stdout temporarily for user input
        sys.stdout = sys.__stdout__
        print("\n" + "="*60)
        print("⚠️  WARNING: DESTRUCTIVE OPERATION")
        print("="*60)
        print("\nThis script will PERMANENTLY DELETE old user records.")
        print("Make sure you have:")
        print("1. ✅ Verified the migration was successful")
        print("2. ✅ Tested all services with new user IDs")
        print("3. ✅ Created backups of your data")
        print("4. ✅ Confirmed no services are still using old IDs")
        
        response = input("\nAre you ABSOLUTELY SURE you want to proceed? Type 'DELETE' to confirm: ")
        
        if args.log:
            sys.stdout = logfile
            
        if response != "DELETE":
            log("User did not confirm deletion. Exiting.")
            sys.exit(0)
    
    # Get users from CSV
    users = get_users_from_csv(args.csv_file)
    
    if not users:
        log("No users to clean up (all old_ids match new_ids or CSV is empty)")
        sys.exit(0)
    
    log(f"Found {len(users)} users to clean up")
    
    total_deleted = {}
    
    # Process each user
    for old_id, new_id in users.items():
        log(f"\n{'='*60}")
        log(f"Processing cleanup for: {old_id} -> {new_id}")
        log(f"{'='*60}")
        
        results = cleanup_all_tables_for_user(old_id, new_id, args.dry_run)
        
        # Aggregate results
        for table, count in results.items():
            total_deleted[table] = total_deleted.get(table, 0) + count
    
    # Summary
    log(f"\n{'='*60}")
    log("CLEANUP SUMMARY")
    log(f"{'='*60}")
    
    for table, count in total_deleted.items():
        if args.dry_run:
            log(f"{table}: Would delete {count} records")
        else:
            log(f"{table}: Deleted {count} records")
    
    total = sum(total_deleted.values())
    if args.dry_run:
        log(f"\nTotal: Would delete {total} records across all tables")
        log("\nRun without --dry-run to actually delete these records")
    else:
        log(f"\nTotal: Deleted {total} records across all tables")
        log("\n✅ Cleanup completed successfully")
    
    if args.log:
        logfile.close()


if __name__ == "__main__":
    main()