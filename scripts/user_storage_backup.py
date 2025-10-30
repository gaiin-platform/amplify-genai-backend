#!/usr/bin/env python3
"""
User Storage Table Backup Script
Backs up OLD_USER_STORAGE_TABLE from basic-ops to CSV for migration to amplify-lambda
"""
import boto3
import csv
import json
import argparse
import sys
from datetime import datetime
from typing import List, Dict, Any
from config import get_config

def flatten_dynamodb_item(item: Dict[str, Any]) -> Dict[str, str]:
    """Convert DynamoDB item format to flat dictionary with string values."""
    flattened = {}
    
    for key, value in item.items():
        if isinstance(value, dict):
            # Handle DynamoDB typed attributes
            if 'S' in value:  # String
                flattened[key] = value['S']
            elif 'N' in value:  # Number
                flattened[key] = value['N']
            elif 'B' in value:  # Binary
                flattened[key] = str(value['B'])
            elif 'SS' in value:  # String Set
                flattened[key] = json.dumps(value['SS'])
            elif 'NS' in value:  # Number Set
                flattened[key] = json.dumps(value['NS'])
            elif 'BS' in value:  # Binary Set
                flattened[key] = json.dumps([str(b) for b in value['BS']])
            elif 'M' in value:  # Map
                flattened[key] = json.dumps(value['M'])
            elif 'L' in value:  # List
                flattened[key] = json.dumps(value['L'])
            elif 'NULL' in value:  # Null
                flattened[key] = ''
            elif 'BOOL' in value:  # Boolean
                flattened[key] = str(value['BOOL'])
            else:
                # Fallback for any other format
                flattened[key] = json.dumps(value)
        else:
            flattened[key] = str(value)
    
    return flattened

def backup_user_storage_table(table_name: str, output_file: str = None) -> tuple[str, int]:
    """Backup OLD_USER_STORAGE_TABLE to CSV, returns (filename, item_count)."""
    
    dynamodb = boto3.client('dynamodb')
    
    # CSV output file
    backup_file = output_file or f"user_storage_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    print("=" * 60)
    print("USER STORAGE TABLE BACKUP")
    print("=" * 60)
    print(f"Source table: {table_name}")
    print(f"Backup file: {backup_file}")
    print()
    
    try:
        # Verify table exists
        try:
            dynamodb.describe_table(TableName=table_name)
            print(f"✓ Source table {table_name} exists")
        except dynamodb.exceptions.ResourceNotFoundException:
            print(f"✗ Source table {table_name} does not exist")
            return None, 0
        
        print("Scanning table to determine CSV headers...")
        
        # Get all possible field names first by scanning table
        all_fields = set(['_backup_timestamp'])
        
        # Scan first few items to get field names
        response = dynamodb.scan(TableName=table_name, Limit=10)
        for item in response.get('Items', []):
            all_fields.update(item.keys())
        
        fieldnames = sorted(list(all_fields))
        print(f"Found {len(fieldnames)} unique fields")
        
        # Open CSV file and create writer
        with open(backup_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            
            # Write header
            writer.writeheader()
            
            # Scan the table with pagination
            paginator = dynamodb.get_paginator('scan')
            page_iterator = paginator.paginate(TableName=table_name)
            
            item_count = 0
            
            for page in page_iterator:
                items = page.get('Items', [])
                
                for item in items:
                    # Flatten the DynamoDB item
                    flat_item = flatten_dynamodb_item(item)
                    
                    # Add backup timestamp
                    flat_item['_backup_timestamp'] = datetime.now().isoformat()
                    
                    # Write to CSV
                    writer.writerow(flat_item)
                    item_count += 1
                    
                    if item_count % 100 == 0:
                        print(f"  Backed up {item_count} items...")
            
            print(f"✓ Completed backup: {item_count} items")
            print()
            print("=" * 60)
            print("BACKUP SUMMARY")
            print("=" * 60)
            print(f"Items backed up: {item_count}")
            print(f"Backup file: {backup_file}")
            
            if item_count > 0:
                print()
                print("✓ USER STORAGE BACKUP COMPLETED SUCCESSFULLY!")
                return backup_file, item_count
            else:
                print("✗ BACKUP FAILED - No data was backed up")
                return None, 0
                
    except Exception as e:
        print(f"✗ BACKUP FAILED: {e}")
        return None, 0

def main():
    """Main backup function."""
    parser = argparse.ArgumentParser(description='Backup DynamoDB User Storage Table to CSV')
    parser.add_argument(
        '--output-file',
        help='Output CSV filename (default: auto-generated with timestamp)'
    )
    
    args = parser.parse_args()
    
    # Get table name from config instead of hardcoded argument
    config = get_config()
    table_name = config.get("OLD_USER_STORAGE_TABLE")
    
    if not table_name:
        print("❌ ERROR: OLD_USER_STORAGE_TABLE not configured in config.py")
        print("Please check your DEP_NAME and STAGE settings in config.py")
        sys.exit(1)
    
    print(f"Backing up table: {table_name} (from config.py)")
    
    backup_file, item_count = backup_user_storage_table(table_name, args.output_file)
    
    if backup_file and item_count > 0:
        print(f"\nBackup saved to: {backup_file}")
        print("\nNext steps:")
        print("1. Deploy amplify-lambda with new user-data-storage table")
        print("2. Run migration script to import this data to new table")
        return backup_file, item_count
    else:
        print("\nBackup failed!")
        return None, 0

if __name__ == "__main__":
    backup_file, item_count = main()
    sys.exit(0 if backup_file else 1)