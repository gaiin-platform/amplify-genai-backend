import os
import json
import boto3
import re
import time
import uuid
from typing import Optional, Dict, Any
from botocore.exceptions import ClientError
from decimal import Decimal
from boto3.dynamodb.conditions import Key
from pycommon.lzw import safe_compress
from config import get_config

# Load configuration using config.py
CONFIG = get_config()

# DynamoDB table for user storage
USER_STORAGE_TABLE = CONFIG.get("USER_DATA_STORAGE_TABLE")

# S3 bucket names from flattened config structure
S3_CONSOLIDATION_BUCKET_NAME = CONFIG.get("S3_CONSOLIDATION_BUCKET_NAME")
S3_CONVERSATIONS_BUCKET_NAME = CONFIG.get("S3_CONVERSATIONS_BUCKET_NAME")
S3_SHARE_BUCKET_NAME = CONFIG.get("S3_SHARE_BUCKET_NAME")
ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME = CONFIG.get("ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME")
AGENT_STATE_BUCKET = CONFIG.get("AGENT_STATE_BUCKET")
S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME = CONFIG.get("S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME")
DATA_DISCLOSURE_STORAGE_BUCKET = CONFIG.get("DATA_DISCLOSURE_STORAGE_BUCKET")
S3_API_DOCUMENTATION_BUCKET = CONFIG.get("S3_API_DOCUMENTATION_BUCKET")
S3_CONVERSION_INPUT_BUCKET_NAME = CONFIG.get("S3_CONVERSION_INPUT_BUCKET_NAME")
S3_CONVERSION_OUTPUT_BUCKET_NAME = CONFIG.get("S3_CONVERSION_OUTPUT_BUCKET_NAME")
S3_ZIP_FILE_BUCKET_NAME = CONFIG.get("S3_ZIP_FILE_BUCKET_NAME")
# Buckets that migrate to USER_STORAGE_TABLE
WORKFLOW_TEMPLATES_BUCKET = CONFIG.get("WORKFLOW_TEMPLATES_BUCKET")
SCHEDULED_TASKS_LOGS_BUCKET = CONFIG.get("SCHEDULED_TASKS_LOGS_BUCKET")
SCHEDULED_TASKS_TABLE = CONFIG.get("SCHEDULED_TASKS_TABLE")
S3_ARTIFACTS_BUCKET = CONFIG.get("S3_ARTIFACTS_BUCKET")

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


def _float_to_decimal(data):
    """Convert floats to Decimal in data structure"""
    return json.loads(json.dumps(data), parse_float=Decimal)


def _fix_python_dict_strings_in_data(data):
    """
    MIGRATION CORRUPTION FIX: Recursively detect and parse Python dict strings in data structure.
    This prevents Python dict strings from being stored as strings in USER_STORAGE_TABLE.
    
    Detects patterns like: "{'key': 'value'}" and converts them to proper dict objects.
    """
    if isinstance(data, dict):
        # Recursively process all values in dictionary
        fixed_dict = {}
        for key, value in data.items():
            fixed_dict[key] = _fix_python_dict_strings_in_data(value)
        return fixed_dict
    elif isinstance(data, list):
        # Process each item in list (this is where workflow steps corruption happens)
        fixed_list = []
        for item in data:
            if isinstance(item, str):
                # Check if this string looks like a Python dict representation
                item_stripped = item.strip()
                if (item_stripped.startswith("{'") and item_stripped.endswith("'}") and 
                    "': " in item_stripped and item_stripped.count("'") >= 4):
                    try:
                        import ast
                        # Safely parse the Python dict string to an actual dict
                        parsed_item = ast.literal_eval(item_stripped)
                        if isinstance(parsed_item, dict):
                            print(f"[MIGRATION FIX] Converted Python dict string to object: {item_stripped[:50]}...")
                            # Recursively fix any nested Python dict strings
                            fixed_list.append(_fix_python_dict_strings_in_data(parsed_item))
                        else:
                            fixed_list.append(item)  # Keep original if not dict
                    except (ValueError, SyntaxError) as e:
                        print(f"[MIGRATION WARNING] Failed to parse potential dict string: {str(e)[:50]}...")
                        fixed_list.append(item)  # Keep original on parse error
                else:
                    # Regular string, keep as-is
                    fixed_list.append(item)
            else:
                # Non-string item, recursively process
                fixed_list.append(_fix_python_dict_strings_in_data(item))
        return fixed_list
    else:
        # Primitive type (string, number, boolean, null), return as-is
        return data


def _create_hash_key(current_user, app_id):
    """Create a secure hash key combining user and app_id"""
    if not current_user or not app_id:
        raise ValueError("Both current_user and app_id are required")

    if not isinstance(current_user, str) or not isinstance(app_id, str):
        raise ValueError("Both current_user and app_id must be strings")

    # CRITICAL BUG PREVENTION: Detect if we're being called with old email-format user ID
    # This prevents malformed entries like "allen-karns-vanderbilt-edu-amplify-workflows#workflow-templates"
    if "@" in current_user and any(char in current_user for char in [".", "+"]):
        print(f"[WARNING] _create_hash_key called with email-format user ID: {current_user}")
        print(f"[WARNING] This may indicate a migration bug - user IDs should be converted to new format first")
        print(f"[WARNING] app_id: {app_id}")
        # Still proceed but log the issue for investigation
    
    # CRITICAL BUG PREVENTION: Detect if app_id contains old user ID format
    # Pattern: "old-user-id-sanitized-app-name" instead of just "app-name"
    if any(email_pattern in app_id for email_pattern in ["-vanderbilt-edu", "-gmail-com", "-edu", "@"]):
        print(f"[ERROR] _create_hash_key called with corrupted app_id containing user ID: {app_id}")
        print(f"[ERROR] current_user: {current_user}")
        print(f"[ERROR] This indicates a serious migration bug - app_id should only be the app name")
        raise ValueError(f"Corrupted app_id detected: {app_id}. app_id should only contain the app name (e.g., 'amplify-workflows'), not user ID information.")

    # Allow underscore in email part, replace other unsafe chars with dash
    sanitized_user = re.sub(r"[^a-zA-Z0-9@._-]", "-", current_user)
    sanitized_app = re.sub(r"[^a-zA-Z0-9-]", "-", app_id)

    # Use # as delimiter to match DynamoDB convention
    return f"{sanitized_user}#{sanitized_app}"


def is_migrated_artifact(artifact_key: str) -> bool:
    """
    Determine if an artifact is in migrated format based on key pattern.
    Pre-migration: "user@email.com/20250305/Game:v3" 
    Post-migration: "20250305/Game:v3"
    """
    migrated_pattern = r"^\d{8}/"
    return bool(re.match(migrated_pattern, artifact_key))








# Integration functions for id_migration.py

def migrate_conversations_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False) -> bool:
    """
    Migrate conversation files from S3_CONVERSATIONS_BUCKET_NAME to S3_CONSOLIDATION_BUCKET_NAME.
    
    Args:
        old_id: Old user identifier (used as S3 prefix)
        new_id: New user identifier (for new S3 prefix) 
        dry_run: If True, analyze and show what would be migrated
        
    Returns:
        bool: Success status of the migration
    """
    from datetime import datetime
    
    msg = f"[migrate_conversations_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # Get bucket names from config
        conversations_bucket = S3_CONVERSATIONS_BUCKET_NAME
        consolidation_bucket = S3_CONSOLIDATION_BUCKET_NAME
        
        if not conversations_bucket or not consolidation_bucket:
            log(f"Missing required bucket configuration: S3_CONVERSATIONS_BUCKET_NAME or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all conversation objects for the old user
        old_prefix = f"{old_id}/"
        new_prefix = f"conversations/{new_id}/"
        old_consolidation_prefix = f"conversations/{old_id}/"  # Split state: files already in consolidation with old ID
        
        log(f"Scanning for conversation files with prefix: {old_prefix}")
        
        try:
            # First check if files already exist in consolidation bucket
            log(f"Checking for existing files in consolidation bucket with prefix: {new_prefix}")
            try:
                existing_paginator = s3_client.get_paginator('list_objects_v2')
                existing_iterator = existing_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix=new_prefix
                )
                
                existing_files = set()
                for page in existing_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            # Extract conversation ID from consolidation bucket key
                            conversation_id = obj['Key'][len(new_prefix):]
                            existing_files.add(conversation_id)
                
                if existing_files:
                    log(f"Found {len(existing_files)} files already migrated in consolidation bucket")
                
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check consolidation bucket: {str(e)}")
                existing_files = set()
            
            # CRITICAL: Check for split state - files in consolidation bucket with OLD ID
            split_state_files = []
            log(f"Checking for split state: conversations in consolidation bucket with old ID prefix: {old_consolidation_prefix}")
            try:
                split_paginator = s3_client.get_paginator('list_objects_v2')
                split_iterator = split_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix=old_consolidation_prefix
                )
                
                for page in split_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            conversation_id = obj['Key'][len(old_consolidation_prefix):]
                            if conversation_id not in existing_files:  # Only migrate if not already at new location
                                split_state_files.append({
                                    'Key': obj['Key'],
                                    'Size': obj['Size'],
                                    'Source': 'consolidation_split'
                                })
                                log(f"Found split state conversation to migrate: {obj['Key']} -> {new_prefix}{conversation_id}")
                
                if split_state_files:
                    log(f"SPLIT STATE DETECTED: Found {len(split_state_files)} conversations in consolidation bucket with old ID")
                    
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check for split state conversations: {str(e)}")
            
            # Get list of objects with old user prefix from source bucket
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(
                Bucket=conversations_bucket,
                Prefix=old_prefix
            )
            
            conversation_files = []
            skipped_files = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        conversation_id = obj['Key'][len(old_prefix):]
                        if conversation_id in existing_files:
                            skipped_files.append(obj)
                            log(f"Skipping already migrated conversation: {conversation_id}")
                        else:
                            obj['Source'] = 'conversations_bucket'
                            conversation_files.append(obj)
            
            if skipped_files:
                log(f"Skipped {len(skipped_files)} already migrated conversation files")
            
            # Combine files from both sources (source bucket and split state)
            all_files_to_migrate = conversation_files + split_state_files
            
            if not all_files_to_migrate:
                if skipped_files or existing_files:
                    log(f"All conversation files already migrated for user {old_id}")
                else:
                    log(f"No conversation files found for user {old_id}")
                return True
                
            log(f"Found {len(all_files_to_migrate)} conversation files to migrate")
            if split_state_files:
                log(f"  - {len(conversation_files)} from source bucket")
                log(f"  - {len(split_state_files)} from consolidation bucket (split state)")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in all_files_to_migrate)
                log(f"Would migrate {len(all_files_to_migrate)} files ({total_size:,} bytes)")
                
                if conversation_files:
                    log(f"From source: s3://{conversations_bucket}/{old_prefix}")
                if split_state_files:
                    log(f"From split state: s3://{consolidation_bucket}/{old_consolidation_prefix}")
                log(f"Target: s3://{consolidation_bucket}/{new_prefix}")
                
                for obj in all_files_to_migrate[:5]:  # Show first 5 files as examples
                    if obj.get('Source') == 'consolidation_split':
                        conversation_id = obj['Key'][len(old_consolidation_prefix):]
                        log(f"  Would migrate (split state): {conversation_id} ({obj['Size']} bytes)")
                    else:
                        conversation_id = obj['Key'][len(old_prefix):]
                        log(f"  Would migrate: {conversation_id} ({obj['Size']} bytes)")
                
                if len(all_files_to_migrate) > 5:
                    log(f"  ... and {len(all_files_to_migrate) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in all_files_to_migrate:
                if obj.get('Source') == 'consolidation_split':
                    # Handle split state files (already in consolidation bucket with old ID)
                    old_key = obj['Key']
                    conversation_id = old_key[len(old_consolidation_prefix):]
                    new_key = f"{new_prefix}{conversation_id}"
                    source_bucket = consolidation_bucket
                    log_prefix = "[SPLIT STATE] "
                else:
                    # Handle files from source bucket
                    old_key = obj['Key']
                    conversation_id = old_key[len(old_prefix):]
                    new_key = f"{new_prefix}{conversation_id}"
                    source_bucket = conversations_bucket
                    log_prefix = ""
                
                try:
                    # Copy object to new location
                    copy_source = {
                        'Bucket': source_bucket,
                        'Key': old_key
                    }
                    
                    s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=consolidation_bucket,
                        Key=new_key,
                        MetadataDirective='COPY'
                    )
                    
                    # Verify the copy was successful by checking if object exists
                    try:
                        s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                        
                        # After successful copy, delete the original file to complete migration
                        try:
                            s3_client.delete_object(Bucket=source_bucket, Key=old_key)
                            log(f"{log_prefix}Successfully migrated and cleaned up conversation: {conversation_id}")
                        except ClientError as delete_e:
                            log(f"{log_prefix}Warning: Failed to delete original file {old_key} from {source_bucket}: {str(delete_e)}")
                            # Don't fail the migration for cleanup errors, but log it
                        
                        successful_migrations += 1
                    except ClientError:
                        log(f"{log_prefix}Failed to verify migrated conversation: {conversation_id}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"{log_prefix}Failed to migrate conversation {conversation_id}: {str(e)}")
                    failed_migrations += 1
            
            log(f"Migration completed: {successful_migrations} successful, {failed_migrations} failed")
            
            if failed_migrations > 0:
                log(f"WARNING: {failed_migrations} conversations failed to migrate")
                return False
            
            return True
            
        except ClientError as e:
            log(f"Error listing conversation objects: {str(e)}")
            return False
            
    except Exception as e:
        log(f"Unexpected error during conversation migration: {str(e)}")
        return False

def migrate_shares_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False) -> bool:
    """
    Migrate share files from S3_SHARE_BUCKET_NAME to S3_CONSOLIDATION_BUCKET_NAME.
    Also handles split state: updates existing files in consolidation bucket that contain old user IDs.
    
    Args:
        old_id: Old user identifier (used as S3 prefix)
        new_id: New user identifier (for new S3 prefix) 
        dry_run: If True, analyze and show what would be migrated
        
    Returns:
        bool: Success status of the migration
    """
    from datetime import datetime
    
    msg = f"[migrate_shares_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # Get bucket names from config
        shares_bucket = S3_SHARE_BUCKET_NAME
        consolidation_bucket = S3_CONSOLIDATION_BUCKET_NAME
        
        if not shares_bucket or not consolidation_bucket:
            log(f"Missing required bucket configuration: S3_SHARE_BUCKET_NAME or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all share objects for the old user (both as recipient and sharer)
        # Shares are stored with paths like: recipient_user/sharer_user/date/file.json
        # We need to migrate files where old_id appears in either position
        
        log(f"Scanning for share files involving user: {old_id}")
        
        try:
            # STEP 1: Handle existing files in consolidation bucket with old IDs (split state cleanup)
            log(f"Checking for existing share files in consolidation bucket with old ID: {old_id}")
            try:
                existing_paginator = s3_client.get_paginator('list_objects_v2')
                existing_iterator = existing_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix="shares/"
                )
                
                consolidation_files_to_update = []
                existing_shares = set()
                
                for page in existing_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            if obj['Key'].startswith('shares/'):
                                # Extract the path after 'shares/' prefix
                                share_path = obj['Key'][7:]  # Remove 'shares/' prefix
                                key_parts = share_path.split('/')
                                
                                # Check if old_id appears in the path (as recipient or sharer)
                                if len(key_parts) >= 2 and (key_parts[0] == old_id or key_parts[1] == old_id):
                                    # This file needs to be updated in consolidation bucket
                                    consolidation_files_to_update.append(obj)
                                else:
                                    # File already has correct IDs
                                    existing_shares.add(share_path)
                
                if consolidation_files_to_update:
                    log(f"Found {len(consolidation_files_to_update)} files in consolidation bucket that need ID updates")
                    
                    # Update files in consolidation bucket with new IDs
                    for obj in consolidation_files_to_update:
                        old_key = obj['Key']
                        share_path = old_key[7:]  # Remove 'shares/' prefix
                        
                        # Update path to use new IDs
                        key_parts = share_path.split('/')
                        new_key_parts = []
                        for part in key_parts:
                            new_key_parts.append(new_id if part == old_id else part)
                        new_key = f"shares/{'/'.join(new_key_parts)}"
                        
                        if dry_run:
                            log(f"Would update consolidation bucket file: {old_key} -> {new_key}")
                        else:
                            try:
                                # Copy to new location
                                copy_source = {'Bucket': consolidation_bucket, 'Key': old_key}
                                s3_client.copy_object(
                                    CopySource=copy_source,
                                    Bucket=consolidation_bucket,
                                    Key=new_key,
                                    MetadataDirective='COPY'
                                )
                                
                                # Verify copy and delete old
                                s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                                s3_client.delete_object(Bucket=consolidation_bucket, Key=old_key)
                                log(f"Updated consolidation bucket file: {old_key} -> {new_key}")
                                
                                # Add to existing_shares to avoid duplicate migration from shares bucket
                                updated_share_path = '/'.join(new_key_parts)
                                existing_shares.add(updated_share_path)
                                
                            except Exception as e:
                                log(f"Failed to update consolidation bucket file {old_key}: {str(e)}")
                                return False
                
                if existing_shares:
                    log(f"Found {len(existing_shares)} share files with correct IDs in consolidation bucket")
                
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check consolidation bucket for shares: {str(e)}")
                existing_shares = set()
            
            # STEP 2: Migrate remaining files from shares bucket to consolidation bucket
            log(f"Checking shares bucket for files to migrate")
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=shares_bucket)
            
            share_files = []
            skipped_files = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        # Check if old_id is involved in this share path
                        # Pattern: recipient/sharer/date/file.json
                        key_parts = key.split('/')
                        if len(key_parts) >= 2 and (key_parts[0] == old_id or key_parts[1] == old_id):
                            # Create new key with updated user IDs for comparison
                            new_key_parts = []
                            for part in key_parts:
                                new_key_parts.append(new_id if part == old_id else part)
                            new_key_path = '/'.join(new_key_parts)
                            
                            if new_key_path in existing_shares:
                                skipped_files.append(obj)
                                log(f"Skipping already migrated share: {key}")
                            else:
                                share_files.append(obj)
            
            if skipped_files:
                log(f"Skipped {len(skipped_files)} already migrated share files")
            
            if not share_files:
                if skipped_files:
                    log(f"All share files already migrated for user {old_id}")
                    # Clean up original files that were skipped because they're already migrated
                    if not dry_run:
                        cleanup_count = 0
                        cleanup_errors = 0
                        for obj in skipped_files:
                            old_key = obj['Key']
                            try:
                                s3_client.delete_object(Bucket=shares_bucket, Key=old_key)
                                log(f"Cleaned up already-migrated share: {old_key}")
                                cleanup_count += 1
                            except ClientError as delete_e:
                                log(f"Warning: Failed to delete already-migrated share file {old_key}: {str(delete_e)}")
                                cleanup_errors += 1
                        
                        log(f"Cleanup completed: {cleanup_count} files deleted, {cleanup_errors} errors")
                        return cleanup_errors == 0  # Return True only if no cleanup errors
                    else:
                        log(f"Would clean up {len(skipped_files)} already-migrated share files")
                        return True
                else:
                    log(f"No share files found for user {old_id}")
                return True
                
            log(f"Found {len(share_files)} share files to migrate")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in share_files)
                log(f"Would migrate {len(share_files)} files ({total_size:,} bytes)")
                log(f"Source: s3://{shares_bucket}")
                log(f"Target: s3://{consolidation_bucket}/shares/")
                
                for obj in share_files[:5]:  # Show first 5 files as examples
                    old_key = obj['Key']
                    key_parts = old_key.split('/')
                    # Update key to use new_id where old_id appears
                    new_key_parts = []
                    for part in key_parts:
                        new_key_parts.append(new_id if part == old_id else part)
                    new_key = f"shares/{'/'.join(new_key_parts)}"
                    log(f"  Would migrate: {old_key} -> {new_key} ({obj['Size']} bytes)")
                
                if len(share_files) > 5:
                    log(f"  ... and {len(share_files) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in share_files:
                old_key = obj['Key']
                
                # Update key to use new_id where old_id appears and add shares/ prefix
                key_parts = old_key.split('/')
                new_key_parts = []
                for part in key_parts:
                    new_key_parts.append(new_id if part == old_id else part)
                new_key = f"shares/{'/'.join(new_key_parts)}"
                
                try:
                    # Copy object to consolidation bucket with new key
                    copy_source = {
                        'Bucket': shares_bucket,
                        'Key': old_key
                    }
                    
                    s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=consolidation_bucket,
                        Key=new_key,
                        MetadataDirective='COPY'
                    )
                    
                    # Verify the copy was successful by checking if object exists
                    try:
                        s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                        
                        # After successful copy, delete the original file to complete migration
                        try:
                            s3_client.delete_object(Bucket=shares_bucket, Key=old_key)
                            log(f"Successfully migrated and cleaned up share: {old_key} -> {new_key}")
                        except ClientError as delete_e:
                            log(f"Warning: Failed to delete original share file {old_key} from {shares_bucket}: {str(delete_e)}")
                            # Don't fail the migration for cleanup errors, but log it
                        
                        successful_migrations += 1
                    except ClientError:
                        log(f"Failed to verify migrated share: {new_key}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"Failed to migrate share {old_key}: {str(e)}")
                    failed_migrations += 1
            
            log(f"Migration completed: {successful_migrations} successful, {failed_migrations} failed")
            
            if failed_migrations > 0:
                log(f"WARNING: {failed_migrations} shares failed to migrate")
                return False
            
            return True
            
        except ClientError as e:
            log(f"Error listing share objects: {str(e)}")
            return False
            
    except Exception as e:
        log(f"Unexpected error during shares migration: {str(e)}")
        return False

def migrate_workflow_templates_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False, workflow_table_row: dict = None, region: str = "us-east-1") -> tuple:
    """
    Migrate workflow template from S3 to USER_STORAGE_TABLE and remove s3_key from record.
    
    Args:
        old_id: Old user identifier
        new_id: New user identifier (for USER_STORAGE_TABLE)
        dry_run: If True, analyze and show what would be migrated
        workflow_table_row: Pre-fetched workflow table row to avoid duplicate queries
        
    Returns:
        Tuple of (success: bool, updated_workflow_item: dict or None)
    """
    from datetime import datetime
    
    msg = f"[migrate_workflow_templates_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        if not workflow_table_row:
            log(f"No workflow template row provided for user {old_id}.")
            return (True, None)
            
        s3_key = workflow_table_row.get("s3Key")
        template_uuid = workflow_table_row.get("templateId")
        
        if not s3_key:
            log(f"Workflow template already migrated (no s3_key found) for user {old_id}.")
            return (True, workflow_table_row)
            
        if not template_uuid:
            log(f"Missing template_uuid for workflow template, cannot migrate for user {old_id}.")
            return (False, None)
        
        # Check if template already exists in USER_STORAGE_TABLE (and fix malformed entries)
        if not dry_run:
            try:
                user_storage_table = boto3.resource('dynamodb', region_name=region).Table(USER_STORAGE_TABLE)
                
                # CONSISTENT WITH workflow_template_registry.py: use same app_id pattern
                correct_app_id = f"{new_id}#amplify-workflows"
                correct_pk = f"{correct_app_id}#workflow-templates"
                
                # Check for correct format first
                response = user_storage_table.get_item(
                    Key={
                        "PK": correct_pk,
                        "SK": template_uuid
                    }
                )
                
                if 'Item' in response:
                    log(f"Workflow template already migrated to USER_STORAGE_TABLE for user {old_id}, template: {template_uuid}")
                    updated_workflow_item = workflow_table_row.copy()
                    if "s3Key" in updated_workflow_item:
                        del updated_workflow_item["s3Key"]
                    return (True, updated_workflow_item)
                
                # REPAIR LOGIC: Check for malformed entries (old user ID in app_id portion)
                # Malformed pattern: "new_id#old-user-id-amplify-workflows#workflow-templates" 
                old_id_sanitized = re.sub(r"[^a-zA-Z0-9@._-]", "-", old_id)
                malformed_app_id = f"{new_id}#{old_id_sanitized}-amplify-workflows"
                malformed_pk = f"{malformed_app_id}#workflow-templates"
                
                malformed_response = user_storage_table.get_item(
                    Key={
                        "PK": malformed_pk,
                        "SK": template_uuid
                    }
                )
                
                if 'Item' in malformed_response:
                    log(f"DETECTED MALFORMED ENTRY: Repairing workflow template with incorrect PK: {malformed_pk}")
                    malformed_item = malformed_response['Item']
                    
                    # Copy to correct location with proper PK
                    corrected_item = malformed_item.copy()
                    corrected_item['PK'] = correct_pk
                    corrected_item['appId'] = correct_app_id
                    
                    # Save corrected entry
                    user_storage_table.put_item(Item=corrected_item)
                    log(f"Created corrected entry at PK: {correct_pk}")
                    
                    # Delete malformed entry
                    try:
                        user_storage_table.delete_item(
                            Key={
                                "PK": malformed_pk,
                                "SK": template_uuid
                            }
                        )
                        log(f"Deleted malformed entry: {malformed_pk}")
                    except Exception as delete_e:
                        log(f"Warning: Failed to delete malformed entry: {delete_e}")
                    
                    # Return as already migrated
                    updated_workflow_item = workflow_table_row.copy()
                    if "s3Key" in updated_workflow_item:
                        del updated_workflow_item["s3Key"]
                    return (True, updated_workflow_item)
                    
            except Exception as e:
                log(f"Warning: Could not check USER_STORAGE_TABLE for existing workflow template: {str(e)}")
        
        log(f"Found workflow template record for user ID {old_id}.")
        log(f"    S3 Key: {s3_key}")
        log(f"    Template UUID: {template_uuid}")
        
        if dry_run:
            log(f"Would migrate S3 workflow template to USER_STORAGE_TABLE.")
            log(f"Would download from s3_key: {s3_key}")
            log(f"Would store with template_uuid: {template_uuid}")
            log(f"Would remove s3_key from workflow record.")
            
            # Return workflow item with s3_key removed for dry run analysis
            updated_workflow_item = workflow_table_row.copy()
            if "s3_key" in updated_workflow_item:
                del updated_workflow_item["s3_key"]
            return (True, updated_workflow_item)
        
        # Initialize clients for actual migration
        s3_client = boto3.client('s3')
        user_storage_table = boto3.resource('dynamodb', region_name=region).Table(USER_STORAGE_TABLE)
        
        # Download S3 content
        log(f"Downloading workflow template from S3: {s3_key}")
        s3_response = s3_client.get_object(
            Bucket=WORKFLOW_TEMPLATES_BUCKET, 
            Key=s3_key
        )
        workflow_content = json.loads(s3_response['Body'].read().decode('utf-8'))
        
        # CRITICAL FIX: Clean up Python dict strings in the content before storing
        # This prevents future corruption by fixing the data at the migration source
        log(f"Cleaning Python dict strings from workflow content for template: {template_uuid}")
        workflow_content = _fix_python_dict_strings_in_data(workflow_content)
        
        # Store in USER_STORAGE_TABLE using template_uuid directly
        # CONSISTENT WITH workflow_template_registry.py: use same app_id pattern  
        app_id = _create_hash_key(new_id, "amplify-workflows")
        
        storage_item = {
            "PK": f"{app_id}#workflow-templates",
            "SK": template_uuid,  # Use template_uuid directly as SK
            "UUID": str(uuid.uuid4()),
            "data": _float_to_decimal(workflow_content),
            "appId": app_id,
            "entityType": "workflow-templates",
            "createdAt": int(time.time())
        }
        
        user_storage_table.put_item(Item=storage_item)
        log(f"Successfully migrated workflow template: {s3_key} → USER_STORAGE_TABLE[PK: {app_id}#workflow-templates, SK: {template_uuid}]")
        
        # After successful migration to USER_STORAGE_TABLE, delete the original S3 file
        try:
            s3_client.delete_object(Bucket=WORKFLOW_TEMPLATES_BUCKET, Key=s3_key)
            log(f"Successfully cleaned up original workflow template S3 file: {s3_key}")
        except ClientError as delete_e:
            log(f"Warning: Failed to delete original workflow template S3 file {s3_key}: {str(delete_e)}")
            # Don't fail the migration for cleanup errors, but log it
        
        # Return updated workflow item with s3_key removed
        updated_workflow_item = workflow_table_row.copy()

        return (True, updated_workflow_item)
        
    except Exception as e:
        log(f"Error migrating workflow template for user ID from {old_id} to {new_id}: {e}")
        return (False, None)


def cleanup_orphaned_workflow_templates_for_user(old_id: str, new_id: str, dry_run: bool = False, region: str = "us-east-1") -> bool:
    """
    Clean up orphaned workflow templates - S3 files that exist but have no metadata entries.
    This handles cases where workflow files exist in S3 but were never properly registered
    or where metadata was lost during testing/migration.
    
    Args:
        old_id: Old user identifier
        new_id: New user identifier 
        dry_run: If True, show what would be cleaned up without making changes
        region: AWS region
        
    Returns:
        bool: True if cleanup was successful, False otherwise
    """
    from datetime import datetime
    import boto3
    
    msg = f"[cleanup_orphaned_workflow_templates_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        if not WORKFLOW_TEMPLATES_BUCKET:
            log("WORKFLOW_TEMPLATES_BUCKET not configured, skipping orphaned workflow cleanup")
            return True
            
        # Initialize clients
        s3_client = boto3.client('s3', region_name=region)
        dynamodb_client = boto3.client('dynamodb', region_name=region)
        
        # Get all workflow files from S3 for this user
        s3_workflows = []
        prefix = f"{old_id}/"
        
        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=WORKFLOW_TEMPLATES_BUCKET, Prefix=prefix):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        if obj['Key'].endswith('.json'):
                            filename = obj['Key'].split('/')[-1]
                            template_id = filename.replace('.json', '')
                            s3_workflows.append({
                                's3_key': obj['Key'],
                                'template_id': template_id,
                                'size': obj['Size'],
                                'last_modified': obj['LastModified']
                            })
        except ClientError as s3_e:
            if s3_e.response['Error']['Code'] == 'NoSuchBucket':
                log(f"Workflow templates bucket does not exist, skipping orphaned cleanup")
                return True
            else:
                log(f"Error listing S3 workflows: {s3_e}")
                return False
        
        if not s3_workflows:
            log(f"No S3 workflow files found for user {old_id}")
            return True
            
        log(f"Found {len(s3_workflows)} workflow files in S3 for user {old_id}")
        
        # Get all template IDs that have metadata entries
        metadata_template_ids = set()
        try:
            # Query the WORKFLOW_TEMPLATES_TABLE to get known template IDs
            workflow_templates_table_name = CONFIG.get("WORKFLOW_TEMPLATES_TABLE")
            if not workflow_templates_table_name:
                log("WORKFLOW_TEMPLATES_TABLE not configured, treating all S3 workflows as orphaned")
            else:
                response = dynamodb_client.query(
                    TableName=workflow_templates_table_name,
                    KeyConditionExpression="#user = :user",
                    ExpressionAttributeNames={"#user": "user"},
                    ExpressionAttributeValues={":user": {"S": old_id}}
                )
                
                for item in response.get('Items', []):
                    template_id = item.get('templateId', {}).get('S', '')
                    if template_id:
                        metadata_template_ids.add(template_id)
                        
                log(f"Found {len(metadata_template_ids)} templates with metadata entries")
        except Exception as metadata_e:
            log(f"Warning: Could not query metadata table: {metadata_e}")
            log("Proceeding to migrate all S3 workflows as potentially orphaned")
        
        # Find truly orphaned workflows (S3 files without metadata)
        orphaned_workflows = []
        for workflow in s3_workflows:
            if workflow['template_id'] not in metadata_template_ids:
                orphaned_workflows.append(workflow)
        
        if not orphaned_workflows:
            log("No orphaned workflows found - all S3 workflows have corresponding metadata")
            return True
        
        log(f"Found {len(orphaned_workflows)} orphaned workflows to migrate:")
        for workflow in orphaned_workflows:
            log(f"  - {workflow['template_id']} ({workflow['s3_key']}, {workflow['size']} bytes)")
        
        if dry_run:
            log("DRY RUN: Would migrate these orphaned workflows to USER_DATA_STORAGE_TABLE")
            return True
        
        # Migrate orphaned workflows to USER_DATA_STORAGE_TABLE
        dynamodb = boto3.resource('dynamodb', region_name=region)
        user_storage_table = dynamodb.Table(USER_STORAGE_TABLE)
        
        success_count = 0
        for workflow in orphaned_workflows:
            try:
                s3_key = workflow['s3_key']
                template_id = workflow['template_id']
                
                log(f"Migrating orphaned workflow: {template_id}")
                
                # Download workflow content from S3
                response = s3_client.get_object(Bucket=WORKFLOW_TEMPLATES_BUCKET, Key=s3_key)
                workflow_content = json.loads(response['Body'].read().decode('utf-8'))
                
                # CRITICAL FIX: Clean up Python dict strings in orphaned workflow content  
                log(f"Cleaning Python dict strings from orphaned workflow: {template_id}")
                workflow_content = _fix_python_dict_strings_in_data(workflow_content)
                
                # Create USER_DATA_STORAGE_TABLE entry using same pattern as normal migration
                app_id = _create_hash_key(new_id, "amplify-workflows")
                pk = f"{app_id}#workflow-templates"
                
                user_storage_item = {
                    "PK": pk,
                    "SK": template_id,
                    "appId": app_id,
                    "entityType": "workflow-templates",
                    "UUID": str(uuid.uuid4()),
                    "createdAt": int(time.time()),
                    "data": _float_to_decimal(workflow_content)
                }
                
                # Check if already exists
                existing_response = user_storage_table.get_item(
                    Key={"PK": pk, "SK": template_id}
                )
                
                if 'Item' in existing_response:
                    log(f"Workflow {template_id} already exists in USER_DATA_STORAGE_TABLE, skipping")
                else:
                    # Save to USER_DATA_STORAGE_TABLE
                    user_storage_table.put_item(Item=user_storage_item)
                    log(f"SUCCESS: Migrated orphaned workflow {template_id} to USER_DATA_STORAGE_TABLE")
                
                # Delete from S3 after successful migration
                try:
                    s3_client.delete_object(Bucket=WORKFLOW_TEMPLATES_BUCKET, Key=s3_key)
                    log(f"Cleaned up S3 file: {s3_key}")
                except Exception as delete_e:
                    log(f"Warning: Failed to delete S3 file {s3_key}: {delete_e}")
                
                success_count += 1
                
            except Exception as migrate_e:
                log(f"ERROR: Failed to migrate orphaned workflow {workflow['template_id']}: {migrate_e}")
        
        log(f"Orphaned workflow cleanup complete: {success_count}/{len(orphaned_workflows)} workflows processed")
        return success_count == len(orphaned_workflows)
        
    except Exception as e:
        log(f"Unexpected error during orphaned workflow cleanup: {e}")
        return False




def migrate_scheduled_tasks_logs_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False, scheduled_tasks_table_row: dict = None, region: str = "us-east-1") -> tuple:
    """
    Comprehensive scheduled task logs migration with split state handling:
    
    1. Consolidation bucket split state: Updates existing logs with old user ID paths 
       (scheduledTaskLogs/{old_id}/... -> scheduledTaskLogs/{new_id}/...)
    2. S3 to USER_STORAGE_TABLE migration: Consolidates task logs bucket files to USER_STORAGE_TABLE
    3. detailsKey cleanup: Removes detailsKey references from SCHEDULED_TASKS_TABLE logs array
    
    Args:
        old_id: Old user identifier
        new_id: New user identifier (for USER_STORAGE_TABLE)
        dry_run: If True, analyze and show what would be migrated
        scheduled_tasks_table_row: Pre-fetched scheduled task table row to avoid duplicate queries
        region: AWS region for DynamoDB operations
        
    Returns:
        Tuple of (success: bool, updated_scheduled_task_item: dict or None)
    """
    from datetime import datetime
    
    msg = f"[migrate_scheduled_tasks_logs_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # STEP 1: Handle existing files in consolidation bucket with old IDs (split state cleanup)
        log(f"Checking consolidation bucket for existing scheduled task logs with old ID: {old_id}")
        try:
            s3_client = boto3.client('s3')
            consolidation_bucket = S3_CONSOLIDATION_BUCKET_NAME
            
            # Check for existing scheduled task logs in consolidation bucket with old user ID
            consolidation_paginator = s3_client.get_paginator('list_objects_v2')
            consolidation_iterator = consolidation_paginator.paginate(
                Bucket=consolidation_bucket,
                Prefix=f"scheduledTaskLogs/{old_id}/"
            )
            
            consolidation_files_to_update = []
            for page in consolidation_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        old_key = obj['Key']
                        # Verify this is a scheduled task log file
                        if old_key.startswith(f"scheduledTaskLogs/{old_id}/"):
                            consolidation_files_to_update.append(obj)
            
            if consolidation_files_to_update:
                log(f"Found {len(consolidation_files_to_update)} scheduled task log files in consolidation bucket that need ID updates")
                
                # Update files in consolidation bucket with new user ID
                for obj in consolidation_files_to_update:
                    old_key = obj['Key']
                    # Transform: scheduledTaskLogs/{old_id}/task/execution.json -> scheduledTaskLogs/{new_id}/task/execution.json
                    new_key = old_key.replace(f"scheduledTaskLogs/{old_id}/", f"scheduledTaskLogs/{new_id}/")
                    
                    if dry_run:
                        log(f"Would update consolidation bucket scheduled task log: {old_key} -> {new_key}")
                    else:
                        try:
                            # Copy to new location
                            copy_source = {'Bucket': consolidation_bucket, 'Key': old_key}
                            s3_client.copy_object(
                                CopySource=copy_source,
                                Bucket=consolidation_bucket,
                                Key=new_key,
                                MetadataDirective='COPY'
                            )
                            
                            # Verify copy and delete old
                            s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                            s3_client.delete_object(Bucket=consolidation_bucket, Key=old_key)
                            log(f"Updated consolidation bucket scheduled task log: {old_key} -> {new_key}")
                            
                        except Exception as e:
                            log(f"Failed to update consolidation bucket scheduled task log {old_key}: {str(e)}")
                            return (False, None)
            else:
                log(f"No existing scheduled task logs found in consolidation bucket for user {old_id}")
                
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchBucket':
                log(f"Warning: Could not check consolidation bucket for scheduled task logs: {str(e)}")
        except Exception as e:
            log(f"Error during consolidation bucket split state check: {str(e)}")
        
        # STEP 2: Continue with normal scheduled tasks table processing
        if not scheduled_tasks_table_row:
            log(f"No scheduled task row provided for user {old_id}.")
            return (True, None)
            
        logs_array = scheduled_tasks_table_row.get("logs", [])
        task_id = scheduled_tasks_table_row.get("taskId")
        
        if not logs_array:
            log(f"No logs to migrate for scheduled task {task_id} for user {old_id}.")
            return (True, scheduled_tasks_table_row)
            
        if not task_id:
            log(f"Missing taskId for scheduled task, cannot migrate for user {old_id}.")
            return (False, None)
        
        # Check if logs already migrated (no detailsKey entries)
        has_details_keys = any(log_entry.get("detailsKey") for log_entry in logs_array)
        if not has_details_keys:
            log(f"Scheduled task logs already migrated (no detailsKey found) for task {task_id} for user {old_id}.")
            return (True, scheduled_tasks_table_row)
        
        # Check if consolidated logs already exist in USER_STORAGE_TABLE
        if not dry_run:
            try:
                user_storage_table = boto3.resource('dynamodb', region_name=region).Table(USER_STORAGE_TABLE)
                hash_key = _create_hash_key(new_id, "amplify-agent-logs")
                
                response = user_storage_table.get_item(
                    Key={
                        "PK": f"{hash_key}#scheduled-task-logs",
                        "SK": task_id
                    }
                )
                
                if 'Item' in response:
                    log(f"Scheduled task logs already migrated to USER_STORAGE_TABLE for user {old_id}, task: {task_id}")
                    # Return task item with detailsKeys removed since it's already migrated
                    updated_task_item = scheduled_tasks_table_row.copy()
                    updated_logs = []
                    for log_entry in logs_array:
                        updated_log = log_entry.copy()
                        if "detailsKey" in updated_log:
                            del updated_log["detailsKey"]
                        updated_logs.append(updated_log)
                    updated_task_item["logs"] = updated_logs
                    return (True, updated_task_item)
                    
            except Exception as e:
                log(f"Warning: Could not check USER_STORAGE_TABLE for existing scheduled task logs: {str(e)}")
        
        log(f"Found scheduled task record for user ID {old_id}.")
        log(f"    Task ID: {task_id}")
        log(f"    Logs count: {len(logs_array)}")
        
        if dry_run:
            # Calculate estimated size for 400KB limit check
            estimated_size = 0
            details_keys_count = 0
            
            for log_entry in logs_array:
                if log_entry.get("detailsKey"):
                    details_keys_count += 1
                    # Rough estimate: 512 bytes per log entry (with LZW compression)
                    estimated_size += 512
            
            log(f"Would migrate S3 logs to USER_STORAGE_TABLE.")
            log(f"Would consolidate {details_keys_count} S3 log files for task {task_id}")
            log(f"Estimated consolidated size: {estimated_size / 1024:.1f}KB")
            
            if estimated_size > 350000:  # 350KB threshold (below 400KB limit)
                log(f"WARNING: Estimated size {estimated_size / 1024:.1f}KB approaches DynamoDB 400KB limit!")
                log("Consider implementing pagination if migration fails")
            
            log(f"Would remove detailsKey from logs array entries.")
            
            # Return task item with detailsKeys removed for dry run analysis
            updated_task_item = scheduled_tasks_table_row.copy()
            updated_logs = []
            for log_entry in logs_array:
                updated_log = log_entry.copy()
                if "detailsKey" in updated_log:
                    del updated_log["detailsKey"]
                updated_logs.append(updated_log)
            updated_task_item["logs"] = updated_logs
            return (True, updated_task_item)
        
        # Actually migrate the logs using existing function
        success = migrate_single_task_logs(task_id, new_id, logs_array, dry_run=False, region=region)
        
        if not success:
            log(f"Failed to migrate logs for task {task_id}")
            return (False, None)
        
        log(f"Successfully migrated logs for task {task_id}")
        
        # Return updated task item with detailsKeys removed from logs
        updated_task_item = scheduled_tasks_table_row.copy()
        updated_logs = []
        for log_entry in logs_array:
            updated_log = log_entry.copy()
            # Remove detailsKey since logs are now in USER_STORAGE_TABLE
            if "detailsKey" in updated_log:
                del updated_log["detailsKey"]
            updated_logs.append(updated_log)
        
        updated_task_item["logs"] = updated_logs
        log(f"Removed detailsKey from {len(logs_array)} log entries.")
        
        return (True, updated_task_item)
        
    except Exception as e:
        log(f"Error migrating scheduled task logs for user ID from {old_id} to {new_id}: {e}")
        return (False, None)


def cleanup_orphaned_scheduled_task_logs(old_id: str, dry_run: bool = False, region: str = "us-east-1") -> bool:
    """
    Clean up orphaned scheduled task logs that exist in S3 but have no corresponding SCHEDULED_TASKS_TABLE entries.
    
    Args:
        old_id: User ID to clean up orphaned logs for
        dry_run: If True, only show what would be deleted
        region: AWS region for DynamoDB operations
        
    Returns:
        bool: True if cleanup successful or no orphaned logs found, False if error
    """
    from datetime import datetime
    
    msg = f"[cleanup_orphaned_scheduled_task_logs][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        tasks_logs_bucket = SCHEDULED_TASKS_LOGS_BUCKET
        if not tasks_logs_bucket:
            log("SCHEDULED_TASKS_LOGS_BUCKET not configured, skipping orphaned cleanup")
            return True
            
        s3_client = boto3.client('s3')
        
        # Get all scheduled task log files for the user
        log(f"Scanning for orphaned scheduled task logs for user: {old_id}")
        try:
            paginator = s3_client.get_paginator('list_objects_v2')
            iterator = paginator.paginate(
                Bucket=tasks_logs_bucket,
                Prefix=f"{old_id}/"
            )
            
            all_log_files = []
            for page in iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        all_log_files.append(obj['Key'])
            
            if not all_log_files:
                log(f"No log files found for user {old_id} in tasks logs bucket")
                return True
                
            log(f"Found {len(all_log_files)} log files for user {old_id}")
            
            # Get all valid detailsKey references from SCHEDULED_TASKS_TABLE
            # This requires scanning all scheduled tasks for this user to build valid keys list
            dynamodb = boto3.resource('dynamodb', region_name=region)
            if not SCHEDULED_TASKS_TABLE:
                log("SCHEDULED_TASKS_TABLE not configured, cannot determine valid log references")
                return False
                
            scheduled_tasks_table = dynamodb.Table(SCHEDULED_TASKS_TABLE)
            valid_log_keys = set()
            
            # Query all scheduled tasks for this user to collect valid detailsKey references
            try:
                response = scheduled_tasks_table.query(
                    KeyConditionExpression=Key('user').eq(old_id)
                )
                
                for task in response.get('Items', []):
                    logs_array = task.get('logs', [])
                    for log_entry in logs_array:
                        details_key = log_entry.get('detailsKey')
                        if details_key:
                            valid_log_keys.add(details_key)
                
                # Handle pagination
                while 'LastEvaluatedKey' in response:
                    response = scheduled_tasks_table.query(
                        KeyConditionExpression=Key('user').eq(old_id),
                        ExclusiveStartKey=response['LastEvaluatedKey']
                    )
                    for task in response.get('Items', []):
                        logs_array = task.get('logs', [])
                        for log_entry in logs_array:
                            details_key = log_entry.get('detailsKey')
                            if details_key:
                                valid_log_keys.add(details_key)
                                
            except Exception as e:
                log(f"Warning: Could not query SCHEDULED_TASKS_TABLE for valid keys: {str(e)}")
                # If we can't determine valid keys, don't delete anything to be safe
                return True
            
            # Identify orphaned logs (files that exist but aren't referenced)
            orphaned_logs = []
            for log_file_key in all_log_files:
                if log_file_key not in valid_log_keys:
                    orphaned_logs.append(log_file_key)
            
            if not orphaned_logs:
                log(f"No orphaned logs found for user {old_id}")
                return True
                
            log(f"Found {len(orphaned_logs)} orphaned log files for user {old_id}")
            
            if dry_run:
                log(f"Would delete {len(orphaned_logs)} orphaned log files:")
                for orphaned_key in orphaned_logs[:10]:  # Show first 10 as examples
                    log(f"  Would delete: {orphaned_key}")
                if len(orphaned_logs) > 10:
                    log(f"  ... and {len(orphaned_logs) - 10} more files")
                return True
            
            # Delete orphaned logs
            deleted_count = 0
            failed_count = 0
            
            for orphaned_key in orphaned_logs:
                try:
                    s3_client.delete_object(Bucket=tasks_logs_bucket, Key=orphaned_key)
                    deleted_count += 1
                    log(f"Deleted orphaned log: {orphaned_key}")
                except Exception as e:
                    failed_count += 1
                    log(f"Failed to delete orphaned log {orphaned_key}: {str(e)}")
            
            log(f"Orphaned logs cleanup completed: {deleted_count} deleted, {failed_count} failed")
            return failed_count == 0
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchBucket':
                log(f"Tasks logs bucket {tasks_logs_bucket} does not exist")
                return True
            else:
                log(f"Error accessing tasks logs bucket: {str(e)}")
                return False
                
    except Exception as e:
        log(f"Error during orphaned logs cleanup for user {old_id}: {str(e)}")
        return False


def _get_artifacts_data(old_id: str, artifacts_table_row: dict = None, region: str = "us-east-1") -> tuple:
    """Get artifacts data from provided row or fetch from table."""
    if artifacts_table_row:
        return artifacts_table_row, artifacts_table_row.get("artifacts", [])
    
    table_name = CONFIG.get("ARTIFACTS_DYNAMODB_TABLE")
    if not table_name:
        print(f"ERROR: ARTIFACTS_DYNAMODB_TABLE not found in config")
        return None, []
    
    dynamodb = boto3.resource('dynamodb', region_name=region)
    artifacts_table = dynamodb.Table(table_name)
    response = artifacts_table.get_item(Key={"user_id": old_id})
    if "Item" not in response:
        return None, []
    return response["Item"], response["Item"].get("artifacts", [])


def _transform_artifact_key(old_key: str, old_id: str) -> str:
    """Transform artifact key from old format to new clean format."""
    return old_key.replace(f"{old_id}/", "") if old_key.startswith(f"{old_id}/") else old_key


def _create_storage_item(artifact_content: dict, new_key: str, new_id: str, old_key: str) -> dict:
    """Create USER_STORAGE_TABLE item for artifact."""
    hash_key = _create_hash_key(new_id, "amplify-artifacts")
    return {
        "PK": f"{hash_key}#artifact-content",
        "SK": new_key,
        "UUID": str(uuid.uuid4()),
        "data": _float_to_decimal(artifact_content),
        "appId": hash_key,
        "entityType": "artifact-content", 
        "createdAt": int(time.time())
    }


def _process_single_artifact(artifact_metadata: dict, old_id: str, new_id: str, s3_client, user_storage_table=None, dry_run: bool = False) -> tuple:
    """Process a single artifact for migration or dry run analysis."""
    old_key = artifact_metadata.get("key", "")
    if not old_key:
        return artifact_metadata, f"Missing key, would skip"
    
    new_key = _transform_artifact_key(old_key, old_id)
    
    try:
        # Download S3 content for analysis/migration
        s3_response = s3_client.get_object(Bucket=S3_ARTIFACTS_BUCKET, Key=old_key)
        artifact_content = json.loads(s3_response['Body'].read().decode('utf-8'))
        
        # CRITICAL FIX: Clean up Python dict strings in artifact content before storing
        artifact_content = _fix_python_dict_strings_in_data(artifact_content)
        
        if dry_run:
            # Return analysis info
            content_info = {
                "old_key": old_key,
                "new_key": new_key,
                "content_keys": list(artifact_content.keys()) if isinstance(artifact_content, dict) else "Non-dict",
                "content_size": len(json.dumps(artifact_content)),
                "storage_item": _create_storage_item(artifact_content, new_key, new_id, old_key)
            }
            updated_metadata = artifact_metadata.copy()
            updated_metadata["key"] = new_key
            return updated_metadata, content_info
        else:
            # Actually migrate
            storage_item = _create_storage_item(artifact_content, new_key, new_id, old_key)
            user_storage_table.put_item(Item=storage_item)
            
            # Delete original S3 object after successful migration
            try:
                s3_client.delete_object(Bucket=S3_ARTIFACTS_BUCKET, Key=old_key)
                result_msg = f"Successfully migrated and cleaned up: {old_key} → {new_key}"
            except Exception as delete_e:
                result_msg = f"Successfully migrated {old_key} → {new_key}, but failed to delete original: {delete_e}"
            
            updated_metadata = artifact_metadata.copy()
            updated_metadata["key"] = new_key
            return updated_metadata, result_msg
            
    except Exception as e:
        return artifact_metadata, f"Error: {e}"


def migrate_artifacts_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False, artifacts_table_row: dict = None, region: str = "us-east-1") -> tuple:
    """
    Migrate artifacts bucket data from S3 to USER_STORAGE_TABLE and update key format.
    ENHANCED: Handles partial migrations where some artifacts may already be migrated.
    
    Args:
        old_id: Old user identifier (used in current artifact keys)
        new_id: New user identifier (for USER_STORAGE_TABLE)
        dry_run: If True, analyze and show what would be migrated
        artifacts_table_row: Optional pre-fetched table row to avoid duplicate queries
        
    Returns:
        Tuple of (success: bool, updated_artifacts_array: list or None)
    """
    from datetime import datetime
    
    msg = f"[migrate_artifacts_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # Get artifacts data
        user_artifacts_item, artifacts_array = _get_artifacts_data(old_id, artifacts_table_row, region)
        
        if not user_artifacts_item:
            log(f"No artifacts found for user {old_id}.")
            return (True, None)
            
        if not artifacts_array:
            log(f"No artifacts to migrate for user {old_id}.")
            return (True, None)
        
        log(f"Found artifacts record for user ID {old_id}.")
        log(f"    Existing Data: {user_artifacts_item}")
        
        # ENHANCED: Build comprehensive artifact state for partial migrations
        migrated_artifacts = set()  # Artifacts already in USER_STORAGE_TABLE
        legacy_artifacts = set()    # Artifacts still in S3_ARTIFACTS_BUCKET
        transformed_map = {}         # Map old key -> new key for all artifacts
        
        # Build transformation map for all artifacts
        for artifact_metadata in artifacts_array:
            old_key = artifact_metadata.get("key", "")
            new_key = _transform_artifact_key(old_key, old_id)
            transformed_map[old_key] = new_key
            
            # Check if already transformed (migrated format)
            if is_migrated_artifact(old_key):
                migrated_artifacts.add(old_key)
            else:
                legacy_artifacts.add(old_key)
        
        log(f"Artifact state analysis:")
        log(f"  - Total artifacts: {len(artifacts_array)}")
        log(f"  - Already migrated format: {len(migrated_artifacts)}")
        log(f"  - Legacy format needing migration: {len(legacy_artifacts)}")
        
        # Check USER_STORAGE_TABLE for actually migrated content
        if not dry_run and legacy_artifacts:
            try:
                user_storage_table = boto3.resource('dynamodb', region_name=region).Table(USER_STORAGE_TABLE)
                hash_key = _create_hash_key(new_id, "amplify-artifacts")
                
                # Check if any artifacts already exist for this user
                response = user_storage_table.query(
                    KeyConditionExpression=Key('PK').eq(f"{hash_key}#artifact-content")
                )
                
                existing_in_table = set()
                for item in response.get('Items', []):
                    existing_in_table.add(item['SK'])  # SK contains the artifact key
                
                if existing_in_table:
                    log(f"Found {len(existing_in_table)} artifacts already in USER_STORAGE_TABLE")
                    
                    # Filter artifacts to only migrate those not in table
                    artifacts_to_migrate = []
                    for artifact_metadata in artifacts_array:
                        old_key = artifact_metadata.get("key", "")
                        new_key = transformed_map[old_key]
                        
                        if new_key in existing_in_table:
                            log(f"Skipping already migrated to table: {new_key}")
                        elif old_key in legacy_artifacts:
                            artifacts_to_migrate.append(artifact_metadata)
                    
                    if not artifacts_to_migrate and not migrated_artifacts:
                        log(f"All artifacts already migrated for user {old_id}")
                        # Return all artifacts with transformed keys
                        transformed_artifacts = []
                        for artifact_metadata in artifacts_array:
                            updated_metadata = artifact_metadata.copy()
                            old_key = artifact_metadata.get("key", "")
                            updated_metadata["key"] = transformed_map[old_key]
                            transformed_artifacts.append(updated_metadata)
                        return (True, transformed_artifacts)
                    
                    # Update artifacts_array to only include non-migrated artifacts
                    if artifacts_to_migrate:
                        artifacts_array = artifacts_to_migrate
                    
            except Exception as e:
                log(f"Warning: Could not check USER_STORAGE_TABLE for existing artifacts: {str(e)}")
        
        # Initialize clients
        s3_client = boto3.client('s3')
        user_storage_table = None if dry_run else boto3.resource('dynamodb', region_name=region).Table(USER_STORAGE_TABLE)
        
        success_count = 0
        
        # Process artifacts that need S3 migration
        s3_migrated_keys = set()
        for artifact_metadata in artifacts_array:
            old_key = artifact_metadata.get("key", "")
            if old_key in legacy_artifacts:
                # Only process artifacts that actually need S3 migration
                updated_metadata, result_info = _process_single_artifact(
                    artifact_metadata, old_id, new_id, s3_client, user_storage_table, dry_run
                )
                s3_migrated_keys.add(old_key)
                
                if not dry_run and "Successfully migrated" in str(result_info):
                    success_count += 1
        
        # CRITICAL FIX: Apply key transformation to ALL artifacts from original array
        # This ensures shared artifacts and other non-S3 artifacts get clean keys
        final_artifacts_array = []
        for artifact_metadata in artifacts_array:
            old_key = artifact_metadata.get("key", "")
            updated_metadata = artifact_metadata.copy()
            
            # Apply key transformation to ALL artifacts
            updated_metadata["key"] = transformed_map.get(old_key, old_key)
            
            # ALSO FIX: Update sharedBy field if it references the old user ID
            if "sharedBy" in updated_metadata and updated_metadata["sharedBy"] == old_id:
                updated_metadata["sharedBy"] = new_id
                log(f"Updated sharedBy field: {old_id} -> {new_id}")
            
            final_artifacts_array.append(updated_metadata)
            
            if old_key not in s3_migrated_keys:
                log(f"Applied key transformation (no S3 migration needed): {old_key} -> {updated_metadata['key']}")
        
        if dry_run:
            log(f"Would migrate {len(legacy_artifacts)} S3 artifacts to USER_STORAGE_TABLE.")
            log(f"Would update artifacts array with {len(final_artifacts_array)} items with clean keys.")
        else:
            log(f"Migrated {success_count}/{len(legacy_artifacts)} S3 artifacts to USER_STORAGE_TABLE.")
            log(f"Updated artifacts array with {len(final_artifacts_array)} items with clean keys.")
            log(f"Total artifacts processed: {len(final_artifacts_array)} (including shared/migrated artifacts)")
        
        return (True, final_artifacts_array if final_artifacts_array else None)
        
    except Exception as e:
        log(f"Error migrating artifacts for user ID from {old_id} to {new_id}: {e}")
        return (False, None)


def migrate_single_task_logs(task_id: str, task_user: str, logs_array: list, dry_run: bool = False, region: str = "us-east-1") -> bool:
    """
    Migrate all S3 log files for a single scheduled task into consolidated USER_STORAGE_TABLE entry.
    
    Args:
        task_id: The taskId from SCHEDULED_TASKS_TABLE
        task_user: User who owns the task
        logs_array: The logs array from SCHEDULED_TASKS_TABLE containing detailsKey entries
        dry_run: If True, only log what would be migrated
        
    Returns:
        True if successful, False if failed
    """
    try:
        if dry_run:
            print(f"[DRY RUN] Would consolidate {len(logs_array)} log files for task {task_id}")
            return True
            
        print(f"Consolidating {len(logs_array)} log files for task {task_id}")
        
        # Initialize consolidated log data
        consolidated_logs = []
        s3_client = boto3.client('s3')
        
        # Download and consolidate each log file
        for log_entry in logs_array:
            details_key = log_entry.get('detailsKey')
            execution_id = log_entry.get('executionId')
            executed_at = log_entry.get('executedAt')
            source = log_entry.get('source')
            status = log_entry.get('status')
            
            if not details_key:
                continue
                
            try:
                # Download log content from S3
                response = s3_client.get_object(
                    Bucket=SCHEDULED_TASKS_LOGS_BUCKET,
                    Key=details_key
                )
                log_content = json.loads(response['Body'].read().decode('utf-8'))
                
                # Compress log content to save DynamoDB space
                compressed_log_data = safe_compress(log_content)
                
                # Add to consolidated logs with metadata  
                consolidated_logs.append({
                    "executedAt": executed_at,
                    "executionId": execution_id, 
                    "source": source,
                    "status": status,
                    "logData": compressed_log_data
                })
                
            except Exception as e:
                print(f"Warning: Failed to download log {details_key}: {e}")
                # Add metadata-only entry for failed downloads
                consolidated_logs.append({
                    "executedAt": executed_at,
                    "executionId": execution_id,
                    "source": source, 
                    "status": status,
                    "logData": {"error": f"Migration failed: {str(e)}"}
                })
        
        # Store consolidated logs as dictionary for efficient lookup
        logs_dict = {}
        for log_entry in consolidated_logs:
            execution_id = log_entry["executionId"]
            logs_dict[execution_id] = log_entry["logData"]
        
        consolidated_data = {
            "taskId": task_id,
            "user": task_user,
            "logs": logs_dict
        }
        
        # Store directly in DynamoDB without going through S3
        dynamodb = boto3.resource('dynamodb', region_name=region)
        table = dynamodb.Table(USER_STORAGE_TABLE)
        
        hash_key = _create_hash_key(task_user, "amplify-agent-logs")
        sk = task_id
        
        item = {
            "PK": f"{hash_key}#scheduled-task-logs",
            "SK": sk,
            "UUID": str(uuid.uuid4()),
            "data": _float_to_decimal(consolidated_data),
            "appId": hash_key,
            "entityType": "scheduled-task-logs",
            "createdAt": int(time.time())
        }
        
        table.put_item(Item=item)
        result = {"uuid": item["UUID"]}
        
        if result:
            print(f"Successfully consolidated {len(consolidated_logs)} logs for task {task_id}")
            
            # After successful consolidation to USER_STORAGE_TABLE, delete original S3 log files
            for log_entry in logs_array:
                details_key = log_entry.get('detailsKey')
                if details_key:
                    try:
                        s3_client.delete_object(Bucket=SCHEDULED_TASKS_LOGS_BUCKET, Key=details_key)
                        print(f"Successfully cleaned up original log file: {details_key}")
                    except Exception as delete_e:
                        print(f"Warning: Failed to delete original log file {details_key}: {str(delete_e)}")
                        # Don't fail the migration for cleanup errors, but log it
            
            return True
        else:
            print(f"Failed to store consolidated logs for task {task_id}")
            return False
            
    except Exception as e:
        print(f"Error consolidating logs for task {task_id}: {e}")
        return False


def migrate_user_settings_for_user(old_id: str, new_id: str, dry_run: bool = False, shares_table_row: dict = None, region: str = "us-east-1") -> bool:
    """
    Migrate user settings from SHARES_DYNAMODB_TABLE settings column to USER_STORAGE_TABLE.
    Returns True if migration was successful or not needed, False if failed.
    """
    try:
        # Get settings data from SHARES_DYNAMODB_TABLE row
        settings_data = None
        
        if shares_table_row and "settings" in shares_table_row:
            settings_data = shares_table_row["settings"]
            print(f"Found settings data for user {old_id} in shares table row")
        
        # If no settings data, nothing to migrate
        if not settings_data:
            print(f"No settings data found for user {old_id}, skipping migration")
            return True
        
        # Check if user settings already exist in USER_STORAGE_TABLE (and fix malformed entries)
        try:
            dynamodb = boto3.resource('dynamodb', region_name=region)
            table = dynamodb.Table(USER_STORAGE_TABLE)
            
            # CONSISTENT FORMAT: Use same pattern as production code
            correct_app_id = f"{new_id}#amplify-user-settings"
            correct_pk = f"{correct_app_id}#user-settings"
            
            # Check for correct format first
            response = table.get_item(
                Key={
                    "PK": correct_pk,
                    "SK": "user-settings"
                }
            )
            
            if 'Item' in response:
                print(f"User settings already migrated to USER_STORAGE_TABLE for user {old_id} -> {new_id}")
                return True
            
            # REPAIR LOGIC: Check for malformed entries (old user ID in app_id portion)
            old_id_sanitized = re.sub(r"[^a-zA-Z0-9@._-]", "-", old_id)
            malformed_app_id = f"{new_id}#{old_id_sanitized}-amplify-user-settings"
            malformed_pk = f"{malformed_app_id}#user-settings"
            
            malformed_response = table.get_item(
                Key={
                    "PK": malformed_pk,
                    "SK": "user-settings"
                }
            )
            
            if 'Item' in malformed_response:
                print(f"DETECTED MALFORMED USER SETTINGS: Repairing entry with incorrect PK: {malformed_pk}")
                malformed_item = malformed_response['Item']
                
                # Copy to correct location with proper PK
                corrected_item = malformed_item.copy()
                corrected_item['PK'] = correct_pk
                corrected_item['appId'] = correct_app_id
                
                # Save corrected entry
                table.put_item(Item=corrected_item)
                print(f"Created corrected user settings entry at PK: {correct_pk}")
                
                # Delete malformed entry
                try:
                    table.delete_item(
                        Key={
                            "PK": malformed_pk,
                            "SK": "user-settings"
                        }
                    )
                    print(f"Deleted malformed user settings entry: {malformed_pk}")
                except Exception as delete_e:
                    print(f"Warning: Failed to delete malformed user settings entry: {delete_e}")
                
                # Return as already migrated
                return True
                
        except Exception as e:
            print(f"Warning: Could not check USER_STORAGE_TABLE for existing user settings: {str(e)}")
        
        if dry_run:
            print(f"[DRY RUN] Would migrate settings for user {old_id} -> {new_id}")
            print(f"[DRY RUN] Settings data size: {len(str(settings_data))} characters")
            return True
        
        # Store settings in USER_STORAGE_TABLE
        dynamodb = boto3.resource('dynamodb', region_name=region)
        table = dynamodb.Table(USER_STORAGE_TABLE)
        
        # CONSISTENT FORMAT: Use same pattern as production code
        app_id = f"{new_id}#amplify-user-settings"
        sk = "user-settings"
        
        item = {
            "PK": f"{app_id}#user-settings",
            "SK": sk,
            "UUID": str(uuid.uuid4()),
            "data": _float_to_decimal({"settings": settings_data}),
            "appId": app_id,
            "entityType": "user-settings",
            "createdAt": int(time.time())
        }
        
        table.put_item(Item=item)
        print(f"Successfully migrated user settings for {old_id} -> {new_id} [PK: {app_id}#user-settings]")
        return True
        
    except Exception as e:
        print(f"Error migrating user settings for {old_id}: {e}")
        return False


def migrate_code_interpreter_files_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False) -> bool:
    """
    Migrate code interpreter files from ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME to S3_CONSOLIDATION_BUCKET_NAME.
    
    Args:
        old_id: Old user identifier (used as S3 prefix)
        new_id: New user identifier (for new S3 prefix) 
        dry_run: If True, analyze and show what would be migrated
        
    Returns:
        bool: Success status of the migration
    """
    from datetime import datetime
    
    msg = f"[migrate_code_interpreter_files_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # Get bucket names from config
        code_interpreter_bucket = ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME
        consolidation_bucket = S3_CONSOLIDATION_BUCKET_NAME
        
        if not code_interpreter_bucket or not consolidation_bucket:
            log(f"Missing required bucket configuration: ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all code interpreter files for the old user
        # Files are stored with format: {user_id}/{message_id}-{file_id}-FN-{filename}
        old_prefix = f"{old_id}/"
        new_prefix = f"codeInterpreter/{new_id}/"
        old_consolidation_prefix = f"codeInterpreter/{old_id}/"  # Split state: files already in consolidation with old ID
        
        log(f"Scanning for code interpreter files with prefix: {old_prefix}")
        
        try:
            # First check if files already exist in consolidation bucket
            log(f"Checking for existing code interpreter files in consolidation bucket with prefix: {new_prefix}")
            try:
                existing_paginator = s3_client.get_paginator('list_objects_v2')
                existing_iterator = existing_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix=new_prefix
                )
                
                existing_files = set()
                for page in existing_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            # Extract file path from consolidation bucket key
                            file_path = obj['Key'][len(new_prefix):]
                            existing_files.add(file_path)
                
                if existing_files:
                    log(f"Found {len(existing_files)} code interpreter files already migrated in consolidation bucket")
                
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check consolidation bucket for code interpreter files: {str(e)}")
                existing_files = set()
            
            # CRITICAL: Check for split state - files in consolidation bucket with OLD ID
            split_state_files = []
            log(f"Checking for split state: code interpreter files in consolidation bucket with old ID prefix: {old_consolidation_prefix}")
            try:
                split_paginator = s3_client.get_paginator('list_objects_v2')
                split_iterator = split_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix=old_consolidation_prefix
                )
                
                for page in split_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            file_path = obj['Key'][len(old_consolidation_prefix):]
                            if file_path not in existing_files:  # Only migrate if not already at new location
                                split_state_files.append({
                                    'Key': obj['Key'],
                                    'Size': obj['Size'],
                                    'Source': 'consolidation_split'
                                })
                                log(f"Found split state code interpreter file to migrate: {obj['Key']} -> {new_prefix}{file_path}")
                
                if split_state_files:
                    log(f"SPLIT STATE DETECTED: Found {len(split_state_files)} code interpreter files in consolidation bucket with old ID")
                    
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check for split state code interpreter files: {str(e)}")
            
            # Get list of objects with old user prefix from source bucket
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(
                Bucket=code_interpreter_bucket,
                Prefix=old_prefix
            )
            
            code_interpreter_files = []
            skipped_files = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        file_path = obj['Key'][len(old_prefix):]
                        if file_path in existing_files:
                            skipped_files.append(obj)
                            log(f"Skipping already migrated code interpreter file: {file_path}")
                        else:
                            obj['Source'] = 'code_interpreter_bucket'
                            code_interpreter_files.append(obj)
            
            if skipped_files:
                log(f"Skipped {len(skipped_files)} already migrated code interpreter files")
            
            # Combine files from both sources (source bucket and split state)
            all_files_to_migrate = code_interpreter_files + split_state_files
            
            if not all_files_to_migrate:
                if skipped_files or existing_files:
                    log(f"All code interpreter files already migrated for user {old_id}")
                else:
                    log(f"No code interpreter files found for user {old_id}")
                return True
                
            log(f"Found {len(all_files_to_migrate)} code interpreter files to migrate")
            if split_state_files:
                log(f"  - {len(code_interpreter_files)} from source bucket")
                log(f"  - {len(split_state_files)} from consolidation bucket (split state)")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in all_files_to_migrate)
                log(f"Would migrate {len(all_files_to_migrate)} files ({total_size:,} bytes)")
                
                if code_interpreter_files:
                    log(f"From source: s3://{code_interpreter_bucket}/{old_prefix}")
                if split_state_files:
                    log(f"From split state: s3://{consolidation_bucket}/{old_consolidation_prefix}")
                log(f"Target: s3://{consolidation_bucket}/{new_prefix}")
                
                for obj in all_files_to_migrate[:5]:  # Show first 5 files as examples
                    if obj.get('Source') == 'consolidation_split':
                        file_path = obj['Key'][len(old_consolidation_prefix):]
                        log(f"  Would migrate (split state): {file_path} ({obj['Size']} bytes)")
                    else:
                        file_path = obj['Key'][len(old_prefix):]
                        log(f"  Would migrate: {file_path} ({obj['Size']} bytes)")
                
                if len(all_files_to_migrate) > 5:
                    log(f"  ... and {len(all_files_to_migrate) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in all_files_to_migrate:
                if obj.get('Source') == 'consolidation_split':
                    # Handle split state files (already in consolidation bucket with old ID)
                    old_key = obj['Key']
                    file_path = old_key[len(old_consolidation_prefix):]
                    new_key = f"{new_prefix}{file_path}"
                    source_bucket = consolidation_bucket
                    log_prefix = "[SPLIT STATE] "
                else:
                    # Handle files from source bucket
                    old_key = obj['Key']
                    file_path = old_key[len(old_prefix):]
                    new_key = f"{new_prefix}{file_path}"
                    source_bucket = code_interpreter_bucket
                    log_prefix = ""
                
                try:
                    # Copy object to new location
                    copy_source = {
                        'Bucket': source_bucket,
                        'Key': old_key
                    }
                    
                    s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=consolidation_bucket,
                        Key=new_key,
                        MetadataDirective='COPY'
                    )
                    
                    # Verify the copy was successful by checking if object exists
                    try:
                        s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                        
                        # After successful copy, delete the original file to complete migration
                        try:
                            s3_client.delete_object(Bucket=source_bucket, Key=old_key)
                            log(f"{log_prefix}Successfully migrated and cleaned up code interpreter file: {file_path}")
                        except ClientError as delete_e:
                            log(f"{log_prefix}Warning: Failed to delete original file {old_key} from {source_bucket}: {str(delete_e)}")
                            # Don't fail the migration for cleanup errors, but log it
                        
                        successful_migrations += 1
                    except ClientError:
                        log(f"{log_prefix}Failed to verify migrated code interpreter file: {file_path}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"{log_prefix}Failed to migrate code interpreter file {file_path}: {str(e)}")
                    failed_migrations += 1
            
            log(f"Migration completed: {successful_migrations} successful, {failed_migrations} failed")
            
            if failed_migrations > 0:
                log(f"WARNING: {failed_migrations} code interpreter files failed to migrate")
                return False
            
            return True
            
        except ClientError as e:
            log(f"Error listing code interpreter files: {str(e)}")
            return False
            
    except Exception as e:
        log(f"Unexpected error during code interpreter files migration: {str(e)}")
        return False


def migrate_agent_state_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False) -> bool:
    """
    Migrate agent state files from AGENT_STATE_BUCKET to S3_CONSOLIDATION_BUCKET_NAME.
    
    Args:
        old_id: Old user identifier (used as S3 prefix)
        new_id: New user identifier (for new S3 prefix) 
        dry_run: If True, analyze and show what would be migrated
        
    Returns:
        bool: Success status of the migration
    """
    from datetime import datetime
    
    msg = f"[migrate_agent_state_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # Get bucket names from config
        agent_state_bucket = AGENT_STATE_BUCKET
        consolidation_bucket = S3_CONSOLIDATION_BUCKET_NAME
        
        if not agent_state_bucket or not consolidation_bucket:
            log(f"Missing required bucket configuration: AGENT_STATE_BUCKET or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all agent state files for the old user
        # Files are stored with format: {user_id}/{session_id}/agent_state.json and {user_id}/{session_id}/index.json
        old_prefix = f"{old_id}/"
        new_prefix = f"agentState/{new_id}/"
        old_consolidation_prefix = f"agentState/{old_id}/"  # Split state: files already in consolidation with old ID
        
        log(f"Scanning for agent state files with prefix: {old_prefix}")
        
        try:
            # First check if files already exist in consolidation bucket
            log(f"Checking for existing agent state files in consolidation bucket with prefix: {new_prefix}")
            try:
                existing_paginator = s3_client.get_paginator('list_objects_v2')
                existing_iterator = existing_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix=new_prefix
                )
                
                existing_files = set()
                for page in existing_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            # Extract file path from consolidation bucket key
                            file_path = obj['Key'][len(new_prefix):]
                            existing_files.add(file_path)
                
                if existing_files:
                    log(f"Found {len(existing_files)} agent state files already migrated in consolidation bucket")
                
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check consolidation bucket for agent state files: {str(e)}")
                existing_files = set()
            
            # CRITICAL: Check for split state - files in consolidation bucket with OLD ID
            split_state_files = []
            log(f"Checking for split state: files in consolidation bucket with old ID prefix: {old_consolidation_prefix}")
            try:
                split_paginator = s3_client.get_paginator('list_objects_v2')
                split_iterator = split_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix=old_consolidation_prefix
                )
                
                for page in split_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            file_path = obj['Key'][len(old_consolidation_prefix):]
                            if file_path not in existing_files:  # Only migrate if not already at new location
                                split_state_files.append({
                                    'Key': obj['Key'],
                                    'Size': obj['Size'],
                                    'Source': 'consolidation_split'
                                })
                                log(f"Found split state file to migrate: {obj['Key']} -> {new_prefix}{file_path}")
                
                if split_state_files:
                    log(f"SPLIT STATE DETECTED: Found {len(split_state_files)} files in consolidation bucket with old ID")
                    
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check for split state files: {str(e)}")
            
            # Get list of objects with old user prefix from source bucket
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(
                Bucket=agent_state_bucket,
                Prefix=old_prefix
            )
            
            agent_state_files = []
            skipped_files = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        file_path = obj['Key'][len(old_prefix):]
                        if file_path in existing_files:
                            skipped_files.append(obj)
                            log(f"Skipping already migrated agent state file: {file_path}")
                        else:
                            obj['Source'] = 'agent_state_bucket'
                            agent_state_files.append(obj)
            
            if skipped_files:
                log(f"Skipped {len(skipped_files)} already migrated agent state files")
            
            # Combine files from both sources (source bucket and split state)
            all_files_to_migrate = agent_state_files + split_state_files
            
            if not all_files_to_migrate:
                if skipped_files or existing_files:
                    log(f"All agent state files already migrated for user {old_id}")
                else:
                    log(f"No agent state files found for user {old_id}")
                return True
                
            log(f"Found {len(all_files_to_migrate)} agent state files to migrate")
            if split_state_files:
                log(f"  - {len(agent_state_files)} from source bucket")
                log(f"  - {len(split_state_files)} from consolidation bucket (split state)")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in all_files_to_migrate)
                log(f"Would migrate {len(all_files_to_migrate)} files ({total_size:,} bytes)")
                
                if agent_state_files:
                    log(f"From source: s3://{agent_state_bucket}/{old_prefix}")
                if split_state_files:
                    log(f"From split state: s3://{consolidation_bucket}/{old_consolidation_prefix}")
                log(f"Target: s3://{consolidation_bucket}/{new_prefix}")
                
                for obj in all_files_to_migrate[:5]:  # Show first 5 files as examples
                    if obj['Source'] == 'consolidation_split':
                        file_path = obj['Key'][len(old_consolidation_prefix):]
                        log(f"  Would migrate (split state): {file_path} ({obj['Size']} bytes)")
                    else:
                        file_path = obj['Key'][len(old_prefix):]
                        log(f"  Would migrate: {file_path} ({obj['Size']} bytes)")
                
                if len(all_files_to_migrate) > 5:
                    log(f"  ... and {len(all_files_to_migrate) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in all_files_to_migrate:
                if obj['Source'] == 'consolidation_split':
                    # Handle split state files (already in consolidation bucket with old ID)
                    old_key = obj['Key']
                    file_path = old_key[len(old_consolidation_prefix):]
                    new_key = f"{new_prefix}{file_path}"
                    source_bucket = consolidation_bucket
                    log_prefix = "[SPLIT STATE] "
                else:
                    # Handle files from source bucket
                    old_key = obj['Key']
                    file_path = old_key[len(old_prefix):]
                    new_key = f"{new_prefix}{file_path}"
                    source_bucket = agent_state_bucket
                    log_prefix = ""
                
                try:
                    # Copy object to new location
                    copy_source = {
                        'Bucket': source_bucket,
                        'Key': old_key
                    }
                    
                    s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=consolidation_bucket,
                        Key=new_key,
                        MetadataDirective='COPY'
                    )
                    
                    # Verify the copy was successful by checking if object exists
                    try:
                        s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                        
                        # After successful copy, delete the original file to complete migration
                        try:
                            s3_client.delete_object(Bucket=source_bucket, Key=old_key)
                            log(f"{log_prefix}Successfully migrated and cleaned up agent state file: {file_path}")
                        except ClientError as delete_e:
                            log(f"{log_prefix}Warning: Failed to delete original file {old_key} from {source_bucket}: {str(delete_e)}")
                            # Don't fail the migration for cleanup errors, but log it
                        
                        successful_migrations += 1
                    except ClientError:
                        log(f"{log_prefix}Failed to verify migrated agent state file: {file_path}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"{log_prefix}Failed to migrate agent state file {file_path}: {str(e)}")
                    failed_migrations += 1
            
            log(f"Migration completed: {successful_migrations} successful, {failed_migrations} failed")
            
            if failed_migrations > 0:
                log(f"WARNING: {failed_migrations} agent state files failed to migrate")
                return False
            
            return True
            
        except ClientError as e:
            log(f"Error listing agent state files: {str(e)}")
            return False
            
    except Exception as e:
        log(f"Unexpected error during agent state files migration: {str(e)}")
        return False


def migrate_group_assistant_conversations_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False) -> bool:
    """
    Migrate group assistant conversation files from S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME to S3_CONSOLIDATION_BUCKET_NAME.
    
    Args:
        old_id: Old user identifier (used in DynamoDB user field)
        new_id: New user identifier (for new user field) 
        dry_run: If True, analyze and show what would be migrated
        
    Returns:
        bool: Success status of the migration
    """
    from datetime import datetime
    
    msg = f"[migrate_group_assistant_conversations_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # Get bucket names from config
        group_conversations_bucket = S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME
        consolidation_bucket = S3_CONSOLIDATION_BUCKET_NAME
        
        if not group_conversations_bucket or not consolidation_bucket:
            log(f"Missing required bucket configuration: S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all group assistant conversation files 
        # Files are stored with format: astgp/{assistant-id}/{conversation-id}.txt
        # Note: This migration is somewhat independent of user ID since files are organized by assistant
        
        log(f"Scanning for all group assistant conversation files (migration is assistant-based, not user-specific)")
        
        try:
            # First check if files already exist in consolidation bucket
            log(f"Checking for existing group assistant conversation files in consolidation bucket")
            try:
                existing_paginator = s3_client.get_paginator('list_objects_v2')
                existing_iterator = existing_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix="agentConversations/"
                )
                
                existing_files = set()
                for page in existing_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            # Extract original path from consolidation bucket key
                            if obj['Key'].startswith('agentConversations/'):
                                original_path = obj['Key'][19:]  # Remove 'agentConversations/' prefix
                                existing_files.add(original_path)
                
                if existing_files:
                    log(f"Found {len(existing_files)} group assistant conversation files already migrated in consolidation bucket")
                
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check consolidation bucket for group conversations: {str(e)}")
                existing_files = set()
            
            # Get list of all objects in the bucket (no user-specific prefix)
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=group_conversations_bucket)
            
            conversation_files = []
            skipped_files = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        # Only include files with astgp/ prefix (group assistant conversations)
                        if obj['Key'].startswith('astgp/'):
                            if obj['Key'] in existing_files:
                                skipped_files.append(obj)
                                log(f"Skipping already migrated group conversation: {obj['Key']}")
                            else:
                                conversation_files.append(obj)
            
            if skipped_files:
                log(f"Skipped {len(skipped_files)} already migrated group assistant conversation files")
            
            if not conversation_files:
                if skipped_files:
                    log(f"All group assistant conversation files already migrated")
                else:
                    log(f"No group assistant conversation files found")
                return True
                
            log(f"Found {len(conversation_files)} group assistant conversation files to migrate")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in conversation_files)
                log(f"Would migrate {len(conversation_files)} files ({total_size:,} bytes)")
                log(f"Source: s3://{group_conversations_bucket}/")
                log(f"Target: s3://{consolidation_bucket}/agentConversations/")
                
                for obj in conversation_files[:5]:  # Show first 5 files as examples
                    old_key = obj['Key']
                    new_key = f"agentConversations/{old_key}"
                    log(f"  Would migrate: {old_key} -> {new_key} ({obj['Size']} bytes)")
                
                if len(conversation_files) > 5:
                    log(f"  ... and {len(conversation_files) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in conversation_files:
                old_key = obj['Key']  # Format: astgp/{assistant-id}/{conversation-id}.txt
                new_key = f"agentConversations/{old_key}"  # Format: agentConversations/astgp/{assistant-id}/{conversation-id}.txt
                
                try:
                    # Copy object to consolidation bucket
                    copy_source = {
                        'Bucket': group_conversations_bucket,
                        'Key': old_key
                    }
                    
                    s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=consolidation_bucket,
                        Key=new_key,
                        MetadataDirective='COPY'
                    )
                    
                    # Verify the copy was successful by checking if object exists
                    try:
                        s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                        
                        # Delete original object after successful copy and verification
                        try:
                            s3_client.delete_object(Bucket=group_conversations_bucket, Key=old_key)
                            successful_migrations += 1
                            log(f"Successfully migrated and cleaned up: {old_key} -> {new_key}")
                        except ClientError as delete_e:
                            successful_migrations += 1  # Still count as successful migration
                            log(f"Successfully migrated {old_key} -> {new_key}, but failed to delete original: {str(delete_e)}")
                            
                    except ClientError:
                        log(f"Failed to verify migrated file: {new_key}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"Failed to migrate file {old_key}: {str(e)}")
                    failed_migrations += 1
            
            log(f"Migration completed: {successful_migrations} successful, {failed_migrations} failed")
            
            if failed_migrations > 0:
                log(f"WARNING: {failed_migrations} group assistant conversation files failed to migrate")
                return False
            
            return True
            
        except ClientError as e:
            log(f"Error listing group assistant conversation files: {str(e)}")
            return False
            
    except Exception as e:
        log(f"Unexpected error during group assistant conversations migration: {str(e)}")
        return False


def migrate_data_disclosure_storage_bucket(dry_run: bool = False) -> bool:
    """
    Migrate data disclosure files from DATA_DISCLOSURE_STORAGE_BUCKET to S3_CONSOLIDATION_BUCKET_NAME.
    
    Args:
        dry_run: If True, analyze and show what would be migrated
        
    Returns:
        bool: Success status of the migration
    """
    from datetime import datetime
    
    msg = f"[migrate_data_disclosure_storage_bucket][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # Get bucket names from config
        data_disclosure_bucket = DATA_DISCLOSURE_STORAGE_BUCKET
        consolidation_bucket = S3_CONSOLIDATION_BUCKET_NAME
        
        if not data_disclosure_bucket or not consolidation_bucket:
            log(f"Missing required bucket configuration: DATA_DISCLOSURE_STORAGE_BUCKET or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        log(f"Scanning for all data disclosure files")
        
        try:
            # Get list of all objects in the bucket
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=data_disclosure_bucket)
            
            disclosure_files = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        disclosure_files.append(obj)
            
            if not disclosure_files:
                log(f"No data disclosure files found")
                return True
                
            log(f"Found {len(disclosure_files)} data disclosure files to migrate")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in disclosure_files)
                log(f"Would migrate {len(disclosure_files)} files ({total_size:,} bytes)")
                log(f"Source: s3://{data_disclosure_bucket}/")
                log(f"Target: s3://{consolidation_bucket}/dataDisclosure/")
                
                for obj in disclosure_files[:5]:  # Show first 5 files as examples
                    old_key = obj['Key']
                    new_key = f"dataDisclosure/{old_key}"
                    log(f"  Would migrate: {old_key} -> {new_key} ({obj['Size']} bytes)")
                
                if len(disclosure_files) > 5:
                    log(f"  ... and {len(disclosure_files) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in disclosure_files:
                old_key = obj['Key']
                new_key = f"dataDisclosure/{old_key}"
                
                try:
                    # Copy object to consolidation bucket
                    copy_source = {
                        'Bucket': data_disclosure_bucket,
                        'Key': old_key
                    }
                    
                    s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=consolidation_bucket,
                        Key=new_key,
                        MetadataDirective='COPY'
                    )
                    
                    # Verify the copy was successful by checking if object exists
                    try:
                        s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                        
                        # Delete original object after successful copy and verification
                        try:
                            s3_client.delete_object(Bucket=data_disclosure_bucket, Key=old_key)
                            successful_migrations += 1
                            log(f"Successfully migrated and cleaned up: {old_key} -> {new_key}")
                        except ClientError as delete_e:
                            successful_migrations += 1  # Still count as successful migration
                            log(f"Successfully migrated {old_key} -> {new_key}, but failed to delete original: {str(delete_e)}")
                            
                    except ClientError:
                        log(f"Failed to verify migrated file: {new_key}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"Failed to migrate file {old_key}: {str(e)}")
                    failed_migrations += 1
            
            log(f"Migration completed: {successful_migrations} successful, {failed_migrations} failed")
            
            if failed_migrations > 0:
                log(f"WARNING: {failed_migrations} data disclosure files failed to migrate")
                return False
            
            return True
            
        except ClientError as e:
            log(f"Error listing data disclosure files: {str(e)}")
            return False
            
    except Exception as e:
        log(f"Unexpected error during data disclosure migration: {str(e)}")
        return False


def migrate_api_documentation_bucket(dry_run: bool = False) -> bool:
    """
    Migrate API documentation files from S3_API_DOCUMENTATION_BUCKET to S3_CONSOLIDATION_BUCKET_NAME.
    
    Args:
        dry_run: If True, analyze and show what would be migrated
        
    Returns:
        bool: Success status of the migration
    """
    from datetime import datetime
    
    msg = f"[migrate_api_documentation_bucket][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
        # Get bucket names from config
        api_documentation_bucket = S3_API_DOCUMENTATION_BUCKET
        consolidation_bucket = S3_CONSOLIDATION_BUCKET_NAME
        
        if not api_documentation_bucket or not consolidation_bucket:
            log(f"Missing required bucket configuration: S3_API_DOCUMENTATION_BUCKET or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        log(f"Scanning for all API documentation files")
        
        try:
            # Get list of all objects in the bucket
            paginator = s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=api_documentation_bucket)
            
            api_doc_files = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        api_doc_files.append(obj)
            
            if not api_doc_files:
                log(f"No API documentation files found")
                return True
                
            log(f"Found {len(api_doc_files)} API documentation files to migrate")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in api_doc_files)
                log(f"Would migrate {len(api_doc_files)} files ({total_size:,} bytes)")
                log(f"Source: s3://{api_documentation_bucket}/")
                log(f"Target: s3://{consolidation_bucket}/apiDocumentation/")
                
                for obj in api_doc_files[:5]:  # Show first 5 files as examples
                    old_key = obj['Key']
                    new_key = f"apiDocumentation/{old_key}"
                    log(f"  Would migrate: {old_key} -> {new_key} ({obj['Size']} bytes)")
                
                if len(api_doc_files) > 5:
                    log(f"  ... and {len(api_doc_files) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in api_doc_files:
                old_key = obj['Key']
                new_key = f"apiDocumentation/{old_key}"
                
                try:
                    # Copy object to consolidation bucket
                    copy_source = {
                        'Bucket': api_documentation_bucket,
                        'Key': old_key
                    }
                    
                    s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=consolidation_bucket,
                        Key=new_key,
                        MetadataDirective='COPY'
                    )
                    
                    # Verify the copy was successful by checking if object exists
                    try:
                        s3_client.head_object(Bucket=consolidation_bucket, Key=new_key)
                        
                        # Delete original object after successful copy and verification
                        try:
                            s3_client.delete_object(Bucket=api_documentation_bucket, Key=old_key)
                            successful_migrations += 1
                            log(f"Successfully migrated and cleaned up: {old_key} -> {new_key}")
                        except ClientError as delete_e:
                            successful_migrations += 1  # Still count as successful migration
                            log(f"Successfully migrated {old_key} -> {new_key}, but failed to delete original: {str(delete_e)}")
                            
                    except ClientError:
                        log(f"Failed to verify migrated file: {new_key}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"Failed to migrate file {old_key}: {str(e)}")
                    failed_migrations += 1
            
            log(f"Migration completed: {successful_migrations} successful, {failed_migrations} failed")
            
            if failed_migrations > 0:
                log(f"WARNING: {failed_migrations} API documentation files failed to migrate")
                return False
            
            return True
            
        except ClientError as e:
            log(f"Error listing API documentation files: {str(e)}")
            return False
            
    except Exception as e:
        log(f"Unexpected error during API documentation migration: {str(e)}")
        return False


def main():
    """
    Main function for migrating standalone S3 buckets that are not tied to user tables.
    This handles buckets that don't require user ID migration triggers.
    """
    import argparse
    from datetime import datetime
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}]", message)
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Migrate standalone S3 buckets to consolidation bucket."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true", 
        help="Do not make any changes, just show what would happen."
    )
    parser.add_argument(
        "--bucket",
        choices=["all", "data-disclosure", "api-documentation"],
        default="all",
        help="Specific bucket to migrate or 'all' for all standalone buckets"
    )
    parser.add_argument(
        "--log",
        help="Log output to the specified file (auto-generated if not provided)"
    )
    
    args = parser.parse_args()
    
    # Set up logging - auto-generate filename if not provided
    if not args.log:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = "dry_run" if args.dry_run else "migration"
        args.log = f"s3_migration_{mode}_{timestamp}.log"
    
    try:
        print(f"Logging to file: {args.log}")
        logfile = open(args.log, "w")
        
        # Use tee-like functionality to show output in both console and file
        import sys
        
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
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        # Create tee output to both console and file
        sys.stdout = TeeOutput(original_stdout, logfile)
        sys.stderr = TeeOutput(original_stderr, logfile)
        
    except Exception as e:
        print(f"Error opening log file {args.log}: {e}")
        return False
    
    log(f"Starting standalone S3 bucket migration. Dry run: {args.dry_run}")
    log(f"Target bucket(s): {args.bucket}")
    
    try:
        success = True
        
        # Migrate DATA_DISCLOSURE_STORAGE_BUCKET
        if args.bucket in ["data-disclosure", "all"]:
            log(f"\\n--- Migrating DATA_DISCLOSURE_STORAGE_BUCKET ---") 
            if not migrate_data_disclosure_storage_bucket(args.dry_run):
                success = False
        
        # Migrate S3_API_DOCUMENTATION_BUCKET  
        if args.bucket in ["api-documentation", "all"]:
            log(f"\\n--- Migrating S3_API_DOCUMENTATION_BUCKET ---") 
            if not migrate_api_documentation_bucket(args.dry_run):
                success = False
        
        # Note: S3_CONVERSION_INPUT_BUCKET_NAME uses code-update-only approach
        # No migration needed - files are temporary and go directly to consolidation bucket
        log(f"\\n--- Migration Summary ---")
        if args.bucket == "all":
            log(f"Migrated DATA_DISCLOSURE_STORAGE_BUCKET and S3_API_DOCUMENTATION_BUCKET")
        elif args.bucket == "data-disclosure":
            log(f"Migrated DATA_DISCLOSURE_STORAGE_BUCKET only")
        elif args.bucket == "api-documentation":
            log(f"Migrated S3_API_DOCUMENTATION_BUCKET only")
        log(f"S3_CONVERSION_INPUT_BUCKET_NAME uses code-update-only approach (no migration needed)")
        
        return success
        
    except Exception as e:
        log(f"Error during migration: {e}")
        return False
    
    finally:
        try:
            # Restore original stdout/stderr
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            logfile.close()
            print(f"S3 migration completed. Full log available in: {args.log}")
        except:
            pass


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)


