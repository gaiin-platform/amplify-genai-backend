#!/usr/bin/env python3
"""
Backup prerequisite script for ID migration.
Creates backups of all DynamoDB tables and S3 buckets before migration.

Usage:
    python backup_prereq.py --dry-run
    python backup_prereq.py --backup-name "pre-migration-backup"
"""

import boto3
import argparse
from datetime import datetime, timedelta
from typing import Dict, Tuple
from config import get_config

dynamodb = boto3.client('dynamodb')
s3 = boto3.client('s3')
backup_client = boto3.client('backup')

def log(*messages):
    for message in messages:
        print(f"[{datetime.now()}]", message)

def parse_args():
    parser = argparse.ArgumentParser(
        description="Create backups before ID migration."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be backed up without creating backups"
    )
    parser.add_argument(
        "--backup-name",
        help="Custom backup name prefix (auto-generated if not provided)"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true", 
        help="Only verify existing backups, don't create new ones"
    )
    parser.add_argument(
        "--log",
        help="Log output to file (auto-generated if not provided)"
    )
    return parser.parse_args()

def setup_logging(args):
    """Setup logging with auto-generated filename if needed."""
    if not args.log:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = "verify" if args.verify_only else ("dry_run" if args.dry_run else "backup")
        args.log = f"backup_prereq_{mode}_{timestamp}.log"
    
    print(f"Logging to file: {args.log}")
    logfile = open(args.log, "w")
    
    # Use tee-like functionality
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
    
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    sys.stdout = TeeOutput(original_stdout, logfile)
    sys.stderr = TeeOutput(original_stderr, logfile)
    
    return logfile, original_stdout, original_stderr

def get_all_migration_tables() -> Dict[str, str]:
    """Get all DynamoDB tables that will be involved in migration."""
    config = get_config()
    
    # Remove non-table entries
    tables = {}
    skip_keys = {'needs_edit', 'DEP_NAME', 'STAGE'}
    
    for key, value in config.items():
        if key not in skip_keys and isinstance(value, str) and value:
            tables[key] = value
            
    return tables

def get_all_migration_buckets() -> Dict[str, str]:
    """Get all S3 buckets that will be involved in migration."""
    config = get_config()
    
    # Known S3 bucket keys from migration scripts
    bucket_keys = [
        'S3_ARTIFACTS_BUCKET',
        'S3_CONVERSATIONS_BUCKET_NAME', 
        'S3_SHARE_BUCKET_NAME',
        'S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME',
        'DATA_DISCLOSURE_STORAGE_BUCKET',
        'S3_API_DOCUMENTATION_BUCKET',
        'S3_CONSOLIDATION_BUCKET_NAME',
        'SCHEDULED_TASKS_LOGS_BUCKET',
        'ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME'
    ]
    
    buckets = {}
    for key in bucket_keys:
        if key in config and config[key]:
            buckets[key] = config[key]
    
    return buckets

def check_pitr_status(table_name: str) -> bool:
    """Check if Point-in-Time Recovery is enabled for a table."""
    try:
        response = dynamodb.describe_continuous_backups(TableName=table_name)
        pitr = response.get('ContinuousBackupsDescription', {}).get('PointInTimeRecoveryDescription', {})
        return pitr.get('PointInTimeRecoveryStatus') == 'ENABLED'
    except Exception as e:
        log(f"Warning: Could not check PITR status for {table_name}: {e}")
        return False

def enable_pitr(table_name: str, dry_run: bool = False) -> bool:
    """Enable Point-in-Time Recovery for a table."""
    try:
        if dry_run:
            log(f"Would enable PITR for table: {table_name}")
            return True
            
        dynamodb.update_continuous_backups(
            TableName=table_name,
            PointInTimeRecoverySpecification={'PointInTimeRecoveryEnabled': True}
        )
        log(f"Successfully enabled PITR for table: {table_name}")
        return True
    except Exception as e:
        log(f"Error enabling PITR for {table_name}: {e}")
        return False

def create_table_backup(table_name: str, backup_name: str, dry_run: bool = False) -> Tuple[bool, str]:
    """Create an on-demand backup for a DynamoDB table."""
    try:
        full_backup_name = f"{backup_name}-{table_name.split('-')[-1]}"
        
        if dry_run:
            log(f"Would create backup '{full_backup_name}' for table: {table_name}")
            return True, "dry-run-backup-arn"
            
        response = dynamodb.create_backup(
            TableName=table_name,
            BackupName=full_backup_name
        )
        
        backup_arn = response['BackupDetails']['BackupArn']
        log(f"Successfully created backup '{full_backup_name}' for table: {table_name}")
        log(f"  Backup ARN: {backup_arn}")
        return True, backup_arn
        
    except Exception as e:
        log(f"Error creating backup for {table_name}: {e}")
        return False, ""

def check_bucket_exists(bucket_name: str) -> bool:
    """Check if S3 bucket exists and is accessible."""
    try:
        s3.head_bucket(Bucket=bucket_name)
        return True
    except Exception:
        return False

def enable_s3_versioning(bucket_name: str, dry_run: bool = False) -> bool:
    """Enable S3 versioning for backup protection."""
    try:
        if dry_run:
            log(f"Would enable versioning for bucket: {bucket_name}")
            return True
            
        s3.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={'Status': 'Enabled'}
        )
        log(f"Successfully enabled versioning for bucket: {bucket_name}")
        return True
        
    except Exception as e:
        log(f"Error enabling versioning for {bucket_name}: {e}")
        return False

def verify_backups(tables: Dict[str, str], backup_name: str) -> Dict[str, bool]:
    """Verify that backups exist and are recent."""
    results = {}
    
    log(f"\n=== VERIFYING BACKUPS ===")
    
    for table_key, table_name in tables.items():
        if not table_name:
            continue
            
        try:
            # Check for recent backups (within last 24 hours)
            response = dynamodb.list_backups(
                TableName=table_name,
                TimeRangeLowerBound=datetime.now() - timedelta(days=1)
            )
            
            backups = response.get('BackupSummaries', [])
            recent_backups = [b for b in backups if backup_name in b['BackupName']]
            
            if recent_backups:
                latest_backup = max(recent_backups, key=lambda x: x['BackupCreationDateTime'])
                log(f"✅ {table_name}: Recent backup found - {latest_backup['BackupName']}")
                results[table_name] = True
            else:
                log(f"❌ {table_name}: No recent backup with name pattern '{backup_name}'")
                results[table_name] = False
                
        except Exception as e:
            log(f"❌ {table_name}: Error checking backups - {e}")
            results[table_name] = False
    
    return results

def main():
    args = parse_args()
    
    try:
        logfile, original_stdout, original_stderr = setup_logging(args)
        
        if not args.backup_name:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            args.backup_name = f"id-migration-backup-{timestamp}"
        
        log(f"Starting backup prerequisite process")
        log(f"Backup name: {args.backup_name}")
        log(f"Dry run: {args.dry_run}")
        log(f"Verify only: {args.verify_only}")
        
        # Get all tables and buckets
        tables = get_all_migration_tables()
        buckets = get_all_migration_buckets()
        
        log(f"\nFound {len(tables)} tables and {len(buckets)} buckets for backup")
        
        if args.verify_only:
            # Only verify existing backups
            verification_results = verify_backups(tables, args.backup_name)
            
            failed_verifications = [name for name, success in verification_results.items() if not success]
            
            if failed_verifications:
                log(f"\n❌ VERIFICATION FAILED for {len(failed_verifications)} tables:")
                for table_name in failed_verifications:
                    log(f"  - {table_name}")
                return False
            else:
                log(f"\n✅ All backup verifications passed!")
                return True
        
        # Create backups
        log(f"\n=== DYNAMODB TABLE BACKUPS ===")
        
        backup_results = {}
        pitr_results = {}
        
        for table_key, table_name in tables.items():
            if not table_name:
                log(f"Skipping {table_key}: No table name configured")
                continue
                
            log(f"\nProcessing table: {table_name}")
            
            # Check if table exists
            try:
                dynamodb.describe_table(TableName=table_name)
            except Exception as e:
                log(f"Warning: Table {table_name} not accessible: {e}")
                backup_results[table_name] = False
                continue
            
            # Check and enable PITR
            pitr_enabled = check_pitr_status(table_name)
            if not pitr_enabled:
                log(f"PITR not enabled for {table_name}, enabling...")
                pitr_results[table_name] = enable_pitr(table_name, args.dry_run)
            else:
                log(f"PITR already enabled for {table_name}")
                pitr_results[table_name] = True
            
            # Create on-demand backup
            success, backup_arn = create_table_backup(table_name, args.backup_name, args.dry_run)
            backup_results[table_name] = success
        
        # S3 Bucket Protection
        log(f"\n=== S3 BUCKET PROTECTION ===")
        
        versioning_results = {}
        
        for bucket_key, bucket_name in buckets.items():
            if not bucket_name:
                log(f"Skipping {bucket_key}: No bucket name configured")
                continue
                
            log(f"\nProcessing bucket: {bucket_name}")
            
            # Check if bucket exists
            if not check_bucket_exists(bucket_name):
                log(f"Warning: Bucket {bucket_name} not accessible")
                versioning_results[bucket_name] = False
                continue
            
            # Enable versioning for protection
            versioning_results[bucket_name] = enable_s3_versioning(bucket_name, args.dry_run)
        
        # Summary
        log(f"\n=== BACKUP SUMMARY ===")
        
        failed_backups = [name for name, success in backup_results.items() if not success]
        failed_pitr = [name for name, success in pitr_results.items() if not success]
        failed_versioning = [name for name, success in versioning_results.items() if not success]
        
        if failed_backups:
            log(f"❌ FAILED TABLE BACKUPS ({len(failed_backups)}):")
            for table_name in failed_backups:
                log(f"  - {table_name}")
        
        if failed_pitr:
            log(f"❌ FAILED PITR SETUP ({len(failed_pitr)}):")
            for table_name in failed_pitr:
                log(f"  - {table_name}")
                
        if failed_versioning:
            log(f"❌ FAILED S3 VERSIONING ({len(failed_versioning)}):")
            for bucket_name in failed_versioning:
                log(f"  - {bucket_name}")
        
        total_failures = len(failed_backups) + len(failed_pitr) + len(failed_versioning)
        
        if total_failures == 0:
            log(f"\n✅ ALL BACKUPS COMPLETED SUCCESSFULLY!")
            log(f"✅ Tables backed up: {len([r for r in backup_results.values() if r])}")
            log(f"✅ PITR enabled: {len([r for r in pitr_results.values() if r])}")
            log(f"✅ S3 versioning enabled: {len([r for r in versioning_results.values() if r])}")
            
            if not args.dry_run:
                log(f"\n🚀 READY FOR MIGRATION!")
                log(f"You can now run the migration script safely.")
            
            return True
        else:
            log(f"\n❌ BACKUP FAILURES DETECTED!")
            log(f"Fix the above issues before proceeding with migration.")
            return False
            
    except Exception as e:
        log(f"Error during backup process: {e}")
        return False
        
    finally:
        try:
            import sys
            sys.stdout = original_stdout  
            sys.stderr = original_stderr
            logfile.close()
            print(f"Backup process completed. Full log available in: {args.log}")
        except:
            pass

if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)