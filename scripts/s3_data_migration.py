import os
import json
import boto3
import re
import time
import uuid
from typing import Optional, Dict, Any
from botocore.exceptions import ClientError
from decimal import Decimal
from pycommon.lzw import safe_compress

# DynamoDB table for user storage
USER_STORAGE_TABLE = "amplify-v6-lambda-dev-user-data-storage"


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


def _float_to_decimal(data):
    """Convert floats to Decimal in data structure"""
    return json.loads(json.dumps(data), parse_float=Decimal)


def _create_hash_key(current_user, app_id):
    """Create a secure hash key combining user and app_id"""
    if not current_user or not app_id:
        raise ValueError("Both current_user and app_id are required")

    if not isinstance(current_user, str) or not isinstance(app_id, str):
        raise ValueError("Both current_user and app_id must be strings")

    # Allow underscore in email part, replace other unsafe chars with dash
    sanitized_user = re.sub(r"[^a-zA-Z0-9@._-]", "-", current_user)
    sanitized_app = re.sub(r"[^a-zA-Z0-9-]", "-", app_id)

    # Use # as delimiter to match DynamoDB convention
    return f"{sanitized_user}#{sanitized_app}"








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
        # Environment variables for bucket names
        conversations_bucket = os.environ.get("S3_CONVERSATIONS_BUCKET_NAME") 
        consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
        
        if not conversations_bucket or not consolidation_bucket:
            log(f"Missing required environment variables: S3_CONVERSATIONS_BUCKET_NAME or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all conversation objects for the old user
        old_prefix = f"{old_id}/"
        new_prefix = f"conversations/{new_id}/"
        
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
                            conversation_files.append(obj)
            
            if skipped_files:
                log(f"Skipped {len(skipped_files)} already migrated conversation files")
            
            if not conversation_files:
                if skipped_files:
                    log(f"All conversation files already migrated for user {old_id}")
                else:
                    log(f"No conversation files found for user {old_id}")
                return True
                
            log(f"Found {len(conversation_files)} conversation files to migrate")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in conversation_files)
                log(f"Would migrate {len(conversation_files)} files ({total_size:,} bytes)")
                log(f"Source: s3://{conversations_bucket}/{old_prefix}")
                log(f"Target: s3://{consolidation_bucket}/{new_prefix}")
                
                for obj in conversation_files[:5]:  # Show first 5 files as examples
                    conversation_id = obj['Key'][len(old_prefix):]
                    log(f"  Would migrate: {conversation_id} ({obj['Size']} bytes)")
                
                if len(conversation_files) > 5:
                    log(f"  ... and {len(conversation_files) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in conversation_files:
                old_key = obj['Key']
                conversation_id = old_key[len(old_prefix):]  # Extract conversation ID
                new_key = f"{new_prefix}{conversation_id}"
                
                try:
                    # Copy object to consolidation bucket
                    copy_source = {
                        'Bucket': conversations_bucket,
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
                        successful_migrations += 1
                        log(f"Successfully migrated conversation: {conversation_id}")
                    except ClientError:
                        log(f"Failed to verify migrated conversation: {conversation_id}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"Failed to migrate conversation {conversation_id}: {str(e)}")
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
        # Environment variables for bucket names
        shares_bucket = os.environ.get("S3_SHARE_BUCKET_NAME") 
        consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
        
        if not shares_bucket or not consolidation_bucket:
            log(f"Missing required environment variables: S3_SHARE_BUCKET_NAME or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all share objects for the old user (both as recipient and sharer)
        # Shares are stored with paths like: recipient_user/sharer_user/date/file.json
        # We need to migrate files where old_id appears in either position
        
        log(f"Scanning for share files involving user: {old_id}")
        
        try:
            # First check if share files already exist in consolidation bucket  
            log(f"Checking for existing share files in consolidation bucket")
            try:
                existing_paginator = s3_client.get_paginator('list_objects_v2')
                existing_iterator = existing_paginator.paginate(
                    Bucket=consolidation_bucket,
                    Prefix="shares/"
                )
                
                existing_shares = set()
                for page in existing_iterator:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            # Extract original share path from consolidation bucket key
                            if obj['Key'].startswith('shares/'):
                                original_path = obj['Key'][7:]  # Remove 'shares/' prefix
                                existing_shares.add(original_path)
                
                if existing_shares:
                    log(f"Found {len(existing_shares)} share files already migrated in consolidation bucket")
                
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchBucket':
                    log(f"Warning: Could not check consolidation bucket for shares: {str(e)}")
                existing_shares = set()
            
            # Get list of objects in shares bucket - we'll filter by old_id patterns
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
                        successful_migrations += 1
                        log(f"Successfully migrated share: {old_key} -> {new_key}")
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

def migrate_workflow_templates_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False, workflow_table_row: dict = None) -> tuple:
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
            
        s3_key = workflow_table_row.get("s3_key")
        template_uuid = workflow_table_row.get("template_uuid")
        
        if not s3_key:
            log(f"Workflow template already migrated (no s3_key found) for user {old_id}.")
            return (True, workflow_table_row)
            
        if not template_uuid:
            log(f"Missing template_uuid for workflow template, cannot migrate for user {old_id}.")
            return (False, None)
        
        # Check if template already exists in USER_STORAGE_TABLE
        if not dry_run:
            try:
                user_storage_table = boto3.resource('dynamodb').Table(USER_STORAGE_TABLE)
                hash_key = _create_hash_key(new_id, "amplify-workflows")
                
                response = user_storage_table.get_item(
                    Key={
                        "PK": f"{hash_key}#workflow-templates",
                        "SK": template_uuid
                    }
                )
                
                if 'Item' in response:
                    log(f"Workflow template already migrated to USER_STORAGE_TABLE for user {old_id}, template: {template_uuid}")
                    # Return workflow item with s3_key removed since it's already migrated
                    updated_workflow_item = workflow_table_row.copy()
                    if "s3_key" in updated_workflow_item:
                        del updated_workflow_item["s3_key"]
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
        user_storage_table = boto3.resource('dynamodb').Table(USER_STORAGE_TABLE)
        
        # Download S3 content
        log(f"Downloading workflow template from S3: {s3_key}")
        s3_response = s3_client.get_object(
            Bucket="amplify-v6-agent-loop-dev-workflow-templates", 
            Key=s3_key
        )
        workflow_content = json.loads(s3_response['Body'].read().decode('utf-8'))
        
        # Store in USER_STORAGE_TABLE using template_uuid directly
        hash_key = _create_hash_key(new_id, "amplify-workflows")
        
        storage_item = {
            "PK": f"{hash_key}#workflow-templates",
            "SK": template_uuid,  # Use template_uuid directly as SK
            "UUID": str(uuid.uuid4()),
            "data": _float_to_decimal(workflow_content),
            "appId": hash_key,
            "entityType": "workflow-templates",
            "createdAt": int(time.time()),
            "migrated_from_s3": True,
            "original_bucket": "amplify-v6-agent-loop-dev-workflow-templates",
            "original_path": s3_key,
            "migration_timestamp": str(int(time.time()))
        }
        
        user_storage_table.put_item(Item=storage_item)
        log(f"Successfully migrated workflow template: {s3_key} → USER_STORAGE_TABLE[{template_uuid}]")
        
        # Return updated workflow item with s3_key removed
        updated_workflow_item = workflow_table_row.copy()
        if "s3_key" in updated_workflow_item:
            del updated_workflow_item["s3_key"]
            
        log(f"Removed s3_key from workflow record.")
        return (True, updated_workflow_item)
        
    except Exception as e:
        log(f"Error migrating workflow template for user ID from {old_id} to {new_id}: {e}")
        return (False, None)




def migrate_scheduled_tasks_logs_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False, scheduled_tasks_table_row: dict = None) -> tuple:
    """
    Migrate scheduled task logs from S3 to USER_STORAGE_TABLE and remove detailsKey from logs array.
    
    Args:
        old_id: Old user identifier
        new_id: New user identifier (for USER_STORAGE_TABLE)
        dry_run: If True, analyze and show what would be migrated
        scheduled_tasks_table_row: Pre-fetched scheduled task table row to avoid duplicate queries
        
    Returns:
        Tuple of (success: bool, updated_scheduled_task_item: dict or None)
    """
    from datetime import datetime
    
    msg = f"[migrate_scheduled_tasks_logs_bucket_for_user][dry-run: {dry_run}] %s"
    
    def log(*messages):
        for message in messages:
            print(f"[{datetime.now()}] {msg % message}")
    
    try:
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
                user_storage_table = boto3.resource('dynamodb').Table(USER_STORAGE_TABLE)
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
        success = migrate_single_task_logs(task_id, new_id, logs_array, dry_run=False)
        
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


def _get_artifacts_data(old_id: str, artifacts_table_row: dict = None) -> tuple:
    """Get artifacts data from provided row or fetch from table."""
    if artifacts_table_row:
        return artifacts_table_row, artifacts_table_row.get("artifacts", [])
    
    dynamodb = boto3.resource('dynamodb')
    artifacts_table = dynamodb.Table("amplify-v6-artifacts-dev-user-artifacts")
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
        "createdAt": int(time.time()),
        "migrated_from_s3": True,
        "original_bucket": "amplify-v6-artifacts-dev-bucket",
        "original_path": old_key,
        "migration_timestamp": str(int(time.time()))
    }


def _process_single_artifact(artifact_metadata: dict, old_id: str, new_id: str, s3_client, user_storage_table=None, dry_run: bool = False) -> tuple:
    """Process a single artifact for migration or dry run analysis."""
    old_key = artifact_metadata.get("key", "")
    if not old_key:
        return artifact_metadata, f"Missing key, would skip"
    
    new_key = _transform_artifact_key(old_key, old_id)
    
    try:
        # Download S3 content for analysis/migration
        s3_response = s3_client.get_object(Bucket="amplify-v6-artifacts-dev-bucket", Key=old_key)
        artifact_content = json.loads(s3_response['Body'].read().decode('utf-8'))
        
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
            
            updated_metadata = artifact_metadata.copy()
            updated_metadata["key"] = new_key
            return updated_metadata, f"Successfully migrated: {old_key} → {new_key}"
            
    except Exception as e:
        return artifact_metadata, f"Error: {e}"


def migrate_artifacts_bucket_for_user(old_id: str, new_id: str, dry_run: bool = False, artifacts_table_row: dict = None) -> tuple:
    """
    Migrate artifacts bucket data from S3 to USER_STORAGE_TABLE and update key format.
    
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
        user_artifacts_item, artifacts_array = _get_artifacts_data(old_id, artifacts_table_row)
        
        if not user_artifacts_item:
            log(f"No artifacts found for user {old_id}.")
            return (True, None)
            
        if not artifacts_array:
            log(f"No artifacts to migrate for user {old_id}.")
            return (True, None)
        
        log(f"Found artifacts record for user ID {old_id}.")
        log(f"    Existing Data: {user_artifacts_item}")
        
        # Check if artifacts already migrated to USER_STORAGE_TABLE
        if not dry_run:
            try:
                user_storage_table = boto3.resource('dynamodb').Table(USER_STORAGE_TABLE)
                hash_key = _create_hash_key(new_id, "amplify-artifacts")
                
                # Check if any artifacts already exist for this user
                response = user_storage_table.query(
                    KeyConditionExpression=boto3.dynamodb.conditions.Key('PK').eq(f"{hash_key}#artifact-content")
                )
                
                existing_artifacts = set()
                for item in response.get('Items', []):
                    existing_artifacts.add(item['SK'])  # SK contains the artifact key
                
                if existing_artifacts:
                    log(f"Found {len(existing_artifacts)} artifacts already migrated to USER_STORAGE_TABLE")
                    # Filter out already migrated artifacts from the array
                    filtered_artifacts = []
                    skipped_count = 0
                    
                    for artifact_metadata in artifacts_array:
                        old_key = artifact_metadata.get("key", "")
                        new_key = _transform_artifact_key(old_key, old_id)
                        
                        if new_key in existing_artifacts:
                            skipped_count += 1
                            log(f"Skipping already migrated artifact: {new_key}")
                        else:
                            filtered_artifacts.append(artifact_metadata)
                    
                    if skipped_count > 0:
                        log(f"Skipped {skipped_count} already migrated artifacts")
                    
                    if not filtered_artifacts:
                        log(f"All artifacts already migrated for user {old_id}")
                        # Return the transformed artifact keys for consistency
                        transformed_artifacts = []
                        for artifact_metadata in artifacts_array:
                            updated_metadata = artifact_metadata.copy()
                            old_key = artifact_metadata.get("key", "")
                            updated_metadata["key"] = _transform_artifact_key(old_key, old_id)
                            transformed_artifacts.append(updated_metadata)
                        return (True, transformed_artifacts)
                    
                    # Update artifacts_array to only include non-migrated artifacts
                    artifacts_array = filtered_artifacts
                    
            except Exception as e:
                log(f"Warning: Could not check USER_STORAGE_TABLE for existing artifacts: {str(e)}")
        
        # Initialize clients
        s3_client = boto3.client('s3')
        user_storage_table = None if dry_run else boto3.resource('dynamodb').Table(USER_STORAGE_TABLE)
        
        updated_artifacts_array = []
        success_count = 0
        
        # Process each artifact
        for artifact_metadata in artifacts_array:
            updated_metadata, result_info = _process_single_artifact(
                artifact_metadata, old_id, new_id, s3_client, user_storage_table, dry_run
            )
            updated_artifacts_array.append(updated_metadata)
            
            if not dry_run and "Successfully migrated" in str(result_info):
                success_count += 1
        
        if dry_run:
            log(f"Would migrate {len(artifacts_array)} S3 artifacts to USER_STORAGE_TABLE.")
            log(f"Would update artifacts array with {len(updated_artifacts_array)} items with clean keys.")
        else:
            log(f"Migrated {success_count}/{len(artifacts_array)} S3 artifacts to USER_STORAGE_TABLE.")
            log(f"Updated artifacts array with {len(updated_artifacts_array)} items with clean keys.")
        
        return (True, updated_artifacts_array if updated_artifacts_array else None)
        
    except Exception as e:
        log(f"Error migrating artifacts for user ID from {old_id} to {new_id}: {e}")
        return (False, None)


def migrate_single_task_logs(task_id: str, task_user: str, logs_array: list, dry_run: bool = False) -> bool:
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
                    Bucket="amplify-v6-agent-loop-dev-scheduled-tasks-logs",
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
        dynamodb = boto3.resource('dynamodb')
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
            "createdAt": int(time.time()),
            "migrated_from_s3": True,
            "original_bucket": "amplify-v6-agent-loop-dev-scheduled-tasks-logs",
            "original_path": f"consolidated-logs-for-task-{task_id}",
            "migration_timestamp": str(int(time.time()))
        }
        
        table.put_item(Item=item)
        result = {"uuid": item["UUID"]}
        
        if result:
            print(f"Successfully consolidated {len(consolidated_logs)} logs for task {task_id}")
            return True
        else:
            print(f"Failed to store consolidated logs for task {task_id}")
            return False
            
    except Exception as e:
        print(f"Error consolidating logs for task {task_id}: {e}")
        return False


def migrate_user_settings_for_user(old_id: str, new_id: str, dry_run: bool = False, shares_table_row: dict = None) -> bool:
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
        
        # Check if user settings already exist in USER_STORAGE_TABLE
        try:
            dynamodb = boto3.resource('dynamodb')
            table = dynamodb.Table(USER_STORAGE_TABLE)
            hash_key = _create_hash_key(new_id, "amplify-user-settings")
            
            response = table.get_item(
                Key={
                    "PK": f"{hash_key}#user-settings",
                    "SK": "user-settings"
                }
            )
            
            if 'Item' in response:
                print(f"User settings already migrated to USER_STORAGE_TABLE for user {old_id} -> {new_id}")
                return True
                
        except Exception as e:
            print(f"Warning: Could not check USER_STORAGE_TABLE for existing user settings: {str(e)}")
        
        if dry_run:
            print(f"[DRY RUN] Would migrate settings for user {old_id} -> {new_id}")
            print(f"[DRY RUN] Settings data size: {len(str(settings_data))} characters")
            return True
        
        # Store settings in USER_STORAGE_TABLE
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(USER_STORAGE_TABLE)
        
        hash_key = _create_hash_key(new_id, "amplify-user-settings")
        sk = "user-settings"
        
        item = {
            "PK": f"{hash_key}#user-settings",
            "SK": sk,
            "UUID": str(uuid.uuid4()),
            "data": _float_to_decimal({"settings": settings_data}),
            "appId": hash_key,
            "entityType": "user-settings",
            "createdAt": int(time.time()),
            "migrated_from_shares_table": True,
            "original_user_id": old_id,
            "migration_timestamp": str(int(time.time()))
        }
        
        table.put_item(Item=item)
        print(f"Successfully migrated user settings for {old_id} -> {new_id}")
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
        # Environment variables for bucket names
        code_interpreter_bucket = os.environ.get("ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME") 
        consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
        
        if not code_interpreter_bucket or not consolidation_bucket:
            log(f"Missing required environment variables: ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all code interpreter files for the old user
        # Files are stored with format: {user_id}/{message_id}-{file_id}-FN-{filename}
        old_prefix = f"{old_id}/"
        new_prefix = f"codeInterpreter/{new_id}/"
        
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
            
            # Get list of objects with old user prefix
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
                            code_interpreter_files.append(obj)
            
            if skipped_files:
                log(f"Skipped {len(skipped_files)} already migrated code interpreter files")
            
            if not code_interpreter_files:
                if skipped_files:
                    log(f"All code interpreter files already migrated for user {old_id}")
                else:
                    log(f"No code interpreter files found for user {old_id}")
                return True
                
            log(f"Found {len(code_interpreter_files)} code interpreter files to migrate")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in code_interpreter_files)
                log(f"Would migrate {len(code_interpreter_files)} files ({total_size:,} bytes)")
                log(f"Source: s3://{code_interpreter_bucket}/{old_prefix}")
                log(f"Target: s3://{consolidation_bucket}/{new_prefix}")
                
                for obj in code_interpreter_files[:5]:  # Show first 5 files as examples
                    file_path = obj['Key'][len(old_prefix):]
                    log(f"  Would migrate: {file_path} ({obj['Size']} bytes)")
                
                if len(code_interpreter_files) > 5:
                    log(f"  ... and {len(code_interpreter_files) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in code_interpreter_files:
                old_key = obj['Key']
                file_path = old_key[len(old_prefix):]  # Extract file path after user prefix
                new_key = f"{new_prefix}{file_path}"
                
                try:
                    # Copy object to consolidation bucket
                    copy_source = {
                        'Bucket': code_interpreter_bucket,
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
                        successful_migrations += 1
                        log(f"Successfully migrated code interpreter file: {file_path}")
                    except ClientError:
                        log(f"Failed to verify migrated code interpreter file: {file_path}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"Failed to migrate code interpreter file {file_path}: {str(e)}")
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
        # Environment variables for bucket names
        agent_state_bucket = os.environ.get("AGENT_STATE_BUCKET") 
        consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
        
        if not agent_state_bucket or not consolidation_bucket:
            log(f"Missing required environment variables: AGENT_STATE_BUCKET or S3_CONSOLIDATION_BUCKET_NAME")
            return False
            
        s3_client = boto3.client("s3")
        
        # List all agent state files for the old user
        # Files are stored with format: {user_id}/{session_id}/agent_state.json and {user_id}/{session_id}/index.json
        old_prefix = f"{old_id}/"
        new_prefix = f"agentState/{new_id}/"
        
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
            
            # Get list of objects with old user prefix
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
                            agent_state_files.append(obj)
            
            if skipped_files:
                log(f"Skipped {len(skipped_files)} already migrated agent state files")
            
            if not agent_state_files:
                if skipped_files:
                    log(f"All agent state files already migrated for user {old_id}")
                else:
                    log(f"No agent state files found for user {old_id}")
                return True
                
            log(f"Found {len(agent_state_files)} agent state files to migrate")
            
            if dry_run:
                total_size = sum(obj['Size'] for obj in agent_state_files)
                log(f"Would migrate {len(agent_state_files)} files ({total_size:,} bytes)")
                log(f"Source: s3://{agent_state_bucket}/{old_prefix}")
                log(f"Target: s3://{consolidation_bucket}/{new_prefix}")
                
                for obj in agent_state_files[:5]:  # Show first 5 files as examples
                    file_path = obj['Key'][len(old_prefix):]
                    log(f"  Would migrate: {file_path} ({obj['Size']} bytes)")
                
                if len(agent_state_files) > 5:
                    log(f"  ... and {len(agent_state_files) - 5} more files")
                
                return True
            
            # Perform actual migration
            successful_migrations = 0
            failed_migrations = 0
            
            for obj in agent_state_files:
                old_key = obj['Key']
                file_path = old_key[len(old_prefix):]  # Extract file path after user prefix
                new_key = f"{new_prefix}{file_path}"
                
                try:
                    # Copy object to consolidation bucket
                    copy_source = {
                        'Bucket': agent_state_bucket,
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
                        successful_migrations += 1
                        log(f"Successfully migrated agent state file: {file_path}")
                    except ClientError:
                        log(f"Failed to verify migrated agent state file: {file_path}")
                        failed_migrations += 1
                        
                except ClientError as e:
                    log(f"Failed to migrate agent state file {file_path}: {str(e)}")
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
        # Environment variables for bucket names
        group_conversations_bucket = os.environ.get("S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME") 
        consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
        
        if not group_conversations_bucket or not consolidation_bucket:
            log(f"Missing required environment variables: S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME or S3_CONSOLIDATION_BUCKET_NAME")
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
                        successful_migrations += 1
                        log(f"Successfully migrated: {old_key} -> {new_key}")
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
        # Environment variables for bucket names
        data_disclosure_bucket = os.environ.get("DATA_DISCLOSURE_STORAGE_BUCKET") 
        consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
        
        if not data_disclosure_bucket or not consolidation_bucket:
            log(f"Missing required environment variables: DATA_DISCLOSURE_STORAGE_BUCKET or S3_CONSOLIDATION_BUCKET_NAME")
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
                        successful_migrations += 1
                        log(f"Successfully migrated: {old_key} -> {new_key}")
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
        # Environment variables for bucket names
        api_documentation_bucket = os.environ.get("S3_API_DOCUMENTATION_BUCKET") 
        consolidation_bucket = os.environ.get("S3_CONSOLIDATION_BUCKET_NAME")
        
        if not api_documentation_bucket or not consolidation_bucket:
            log(f"Missing required environment variables: S3_API_DOCUMENTATION_BUCKET or S3_CONSOLIDATION_BUCKET_NAME")
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
                        successful_migrations += 1
                        log(f"Successfully migrated: {old_key} -> {new_key}")
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
        help="Log output to the specified file (optional)"
    )
    
    args = parser.parse_args()
    
    # Set up logging
    if args.log:
        try:
            logfile = open(args.log, "w")
            import sys
            sys.stdout = logfile
            sys.stderr = logfile
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
        if args.log:
            try:
                logfile.close()
            except:
                pass


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)


