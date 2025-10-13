#!/usr/bin/env python3
"""
Import user storage data from CSV backup to DynamoDB table.
"""

import os
import sys
import csv
import json
import boto3
import argparse
import uuid
import time
from decimal import Decimal

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _float_to_decimal(data):
    """Convert floats to Decimal in data structure"""
    return json.loads(json.dumps(data), parse_float=Decimal)

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

def import_user_storage_from_csv(csv_file: str, table_name: str) -> bool:
    """Import user storage data from CSV to new DynamoDB table."""
    
    try:
        print(f"Starting import from {csv_file} to {table_name}")
        
        dynamodb_client = boto3.client('dynamodb')
        
        items = []
        with open(csv_file, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            
            for row_num, row in enumerate(reader, 1):
                # Convert CSV row back to DynamoDB format
                item = {}
                
                for key, value in row.items():
                    if key == '_backup_timestamp' or not value.strip():
                        continue
                        
                    # Convert based on field patterns
                    if key in ['createdAt', 'updatedAt'] and value.isdigit():
                        item[key] = {'N': value}
                    elif key == 'data' and value.startswith('{'):
                        # Data field contains DynamoDB-formatted JSON - wrap in Map type
                        try:
                            parsed_data = json.loads(value)
                            item[key] = {'M': parsed_data}  # Wrap in Map type descriptor
                        except:
                            item[key] = {'S': value}
                    elif value.lower() in ['true', 'false']:
                        # Boolean field
                        item[key] = {'BOOL': value.lower() == 'true'}
                    elif value.replace('.', '', 1).isdigit() and '.' in value:
                        # Float number
                        item[key] = {'N': value}
                    elif value.isdigit():
                        # Integer number
                        item[key] = {'N': value}
                    else:
                        item[key] = {'S': value}
                
                if item:
                    items.append(item)
                
                if row_num % 100 == 0:
                    print(f"Processed {row_num} rows...")
        
        print(f"Total items to import: {len(items)}")
        
        # Batch write items
        batch_size = 25
        imported_count = 0
        
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            request_items = {
                table_name: [
                    {"PutRequest": {"Item": item}} for item in batch
                ]
            }
            
            try:
                response = dynamodb_client.batch_write_item(RequestItems=request_items)
                imported_count += len(batch)
                
                # Handle unprocessed items
                while response.get('UnprocessedItems'):
                    print(f"Retrying unprocessed items...")
                    response = dynamodb_client.batch_write_item(
                        RequestItems=response['UnprocessedItems']
                    )
                
                print(f"Imported batch {i//batch_size + 1}/{(len(items)-1)//batch_size + 1} - Total imported: {imported_count}")
                
            except Exception as e:
                print(f"Error importing batch {i//batch_size + 1}: {e}")
                return False
        
        print(f"Successfully imported {imported_count} items to {table_name}")
        return True
        
    except Exception as e:
        print(f"Error importing user storage data: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Import user storage data from CSV backup to DynamoDB table')
    parser.add_argument(
        '--csv-file', 
        required=True,
        help='Path to CSV backup file to import'
    )
    parser.add_argument(
        '--target-table', 
        default="amplify-v6-lambda-dev-user-data-storage",
        help='Target DynamoDB table name (default: amplify-v6-lambda-dev-user-data-storage)'
    )
    
    args = parser.parse_args()
    
    print(f"Importing from: {args.csv_file}")
    print(f"Target table: {args.target_table}")
    
    # Verify CSV file exists
    if not os.path.exists(args.csv_file):
        print(f"Error: CSV file not found: {args.csv_file}")
        return False
    
    # Check if table exists
    try:
        dynamodb_client = boto3.client('dynamodb')
        dynamodb_client.describe_table(TableName=args.target_table)
        print(f"Target table {args.target_table} exists")
    except dynamodb_client.exceptions.ResourceNotFoundException:
        print(f"Error: Target table {args.target_table} does not exist")
        return False
    except Exception as e:
        print(f"Error checking table: {e}")
        return False
    
    # Import data
    success = import_user_storage_from_csv(args.csv_file, args.target_table)
    
    if success:
        print("✅ User storage data import completed successfully!")
        return True
    else:
        print("❌ User storage data import failed!")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)