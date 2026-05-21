#!/usr/bin/env python3
"""
Generate migration_users.csv from Cognito user pool.

This script connects to AWS Cognito user pool and extracts all users,
creating a CSV file mapping email addresses (old_id) to user IDs/sub (new_id).

Usage:
    python generate_migration_csv.py

Environment Variables Required:
    - COGNITO_USER_POOL_ID: The Cognito User Pool ID
    - AWS credentials (via AWS CLI, environment variables, or IAM roles)
"""

import boto3
import csv
import os
import sys
import yaml
from datetime import datetime
from typing import List, Tuple, Optional


def get_cognito_users(user_pool_id: str) -> List[Tuple[str, str]]:
    """
    Fetch all users from Cognito and extract email and sub pairs.
    
    Args:
        user_pool_id: The Cognito User Pool ID
        
    Returns:
        List of tuples containing (email, sub) pairs
        
    Raises:
        Exception: If Cognito API calls fail
    """
    cognito = boto3.client("cognito-idp")
    users_data = []
    pagination_token = None
    
    print(f"Connecting to Cognito User Pool: {user_pool_id}")
    
    while True:
        try:
            args = {"UserPoolId": user_pool_id}
            if pagination_token:
                args["PaginationToken"] = pagination_token
                
            response = cognito.list_users(**args)
            
            for user in response["Users"]:
                # Extract user attributes into a dictionary
                user_attributes = {
                    attr["Name"]: attr["Value"] for attr in user["Attributes"]
                }
                
                email = user_attributes.get("email")
                sub = user_attributes.get("sub")  # This is the User ID
                
                if email and sub:
                    users_data.append((email, sub))
                    print(f"  Found user: {email} -> {sub}")
                else:
                    # Log missing data for debugging
                    missing_fields = []
                    if not email:
                        missing_fields.append("email")
                    if not sub:
                        missing_fields.append("sub")
                    print(f"  Warning: Skipping user missing {', '.join(missing_fields)}")
                    
            pagination_token = response.get("PaginationToken")
            if not pagination_token:
                break
                
        except Exception as e:
            print(f"Error fetching users from Cognito: {str(e)}", file=sys.stderr)
            raise
    
    print(f"Total users extracted: {len(users_data)}")
    return users_data


def write_migration_csv(users_data: List[Tuple[str, str]], output_file: str) -> None:
    """
    Write user data to CSV file in migration format.
    
    Args:
        users_data: List of (email, sub) tuples
        output_file: Path to output CSV file
    """
    print(f"Writing migration CSV to: {output_file}")
    
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            
            # Write header
            writer.writerow(['old_id', 'new_id'])
            
            # Write user data (sorted by email for consistency)
            for email, sub in sorted(users_data):
                writer.writerow([email, sub])
                
        print(f"Successfully wrote {len(users_data)} users to {output_file}")
        
    except Exception as e:
        print(f"Error writing CSV file: {str(e)}", file=sys.stderr)
        raise


def load_cognito_user_pool_id(stage: str = None) -> Optional[str]:
    """
    Load COGNITO_USER_POOL_ID from environment variable or stage var files.
    
    Args:
        stage: The deployment stage (dev, staging, prod)
        
    Returns:
        The Cognito User Pool ID or None if not found
    """
    # First try environment variable
    user_pool_id = os.environ.get("COGNITO_USER_POOL_ID")
    if user_pool_id:
        return user_pool_id
    
    # If no stage provided, try to detect common stages
    if not stage:
        stages_to_try = ['dev', 'staging', 'prod']
    else:
        stages_to_try = [stage]
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    var_dir = os.path.join(os.path.dirname(script_dir), 'var')
    
    for stage_name in stages_to_try:
        var_file_path = os.path.join(var_dir, f'{stage_name}-var.yml')
        
        if os.path.exists(var_file_path):
            try:
                with open(var_file_path, 'r') as f:
                    var_data = yaml.safe_load(f)
                    if var_data and 'COGNITO_USER_POOL_ID' in var_data:
                        print(f"Found COGNITO_USER_POOL_ID in {var_file_path}")
                        return var_data['COGNITO_USER_POOL_ID']
            except Exception as e:
                print(f"Warning: Could not read {var_file_path}: {e}")
                continue
    
    return None


def main():
    """Main function to generate migration CSV from Cognito users."""
    
    # Try to load COGNITO_USER_POOL_ID from environment or stage files
    user_pool_id = load_cognito_user_pool_id()
    if not user_pool_id:
        print("Error: COGNITO_USER_POOL_ID not found", file=sys.stderr)
        print("Options to provide it:", file=sys.stderr)
        print("1. Set environment variable: export COGNITO_USER_POOL_ID=your_user_pool_id", file=sys.stderr)
        print("2. Add COGNITO_USER_POOL_ID to /var/{stage}-var.yml files", file=sys.stderr)
        sys.exit(1)
    
    # Determine output file path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, "migration_users.csv")
    
    print(f"=== Cognito Migration CSV Generator ===")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"User Pool ID: {user_pool_id}")
    print(f"Output File: {output_file}")
    print()
    
    try:
        # Fetch users from Cognito
        users_data = get_cognito_users(user_pool_id)
        
        if not users_data:
            print("Warning: No users found in Cognito User Pool", file=sys.stderr)
            return
        
        # Write to CSV file
        write_migration_csv(users_data, output_file)
        
        print()
        print("=== Migration CSV Generation Complete ===")
        print(f"✅ Successfully processed {len(users_data)} users")
        print(f"✅ CSV file ready at: {output_file}")
        print()
        print("Next steps:")
        print("1. Review the generated CSV file")
        print("2. Run your migration script using this CSV")
        
    except KeyboardInterrupt:
        print("\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()