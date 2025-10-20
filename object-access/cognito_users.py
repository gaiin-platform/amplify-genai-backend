# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import json
import os
import re
import boto3
from botocore.exceptions import ClientError
from pycommon.const import APIAccessType
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation
)
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ASSISTANTS.value, APIAccessType.SHARE.value, 
                      APIAccessType.ADMIN.value, APIAccessType.API_KEY.value,])

@required_env_vars({
    "COGNITO_USERS_DYNAMODB_TABLE": [DynamoDBOperation.SCAN],
})
@validated("read")
def get_emails(event, context, current_user, name, data):
    """Get a mapping of user_ids to email addresses.
    Returns dict with user_id as key and email as value (or user_id if no email exists).
    """
    query_params = event.get("queryStringParameters", {})
    print("Query params: ", query_params)
    email_prefix = query_params.get("emailprefix", "")
    if not email_prefix or not is_valid_email_prefix(email_prefix):
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid or missing email parameter"}),
        }

    dynamodb = boto3.resource("dynamodb")
    cognito_user_table = dynamodb.Table(os.environ["COGNITO_USERS_DYNAMODB_TABLE"])

    try:
        print("Initiate query to cognito user dynamo table")
        
        # Collect all items across multiple pages
        all_items = []
        last_evaluated_key = None
        
        while True:
            # Prepare scan parameters - now projecting both user_id and email
            scan_params = {"ProjectionExpression": "user_id, email"}
            
            if email_prefix != "*":  # Add filter if not getting all entries
                # Now we need to filter by either user_id OR email prefix
                scan_params.update({
                    "FilterExpression": "begins_with(user_id, :email_prefix) OR begins_with(email, :email_prefix)",
                    "ExpressionAttributeValues": {":email_prefix": email_prefix.lower()},
                })
            
            # Add pagination token if we have one
            if last_evaluated_key:
                scan_params["ExclusiveStartKey"] = last_evaluated_key
            
            # Execute scan
            response = cognito_user_table.scan(**scan_params)
            
            # Check if we got items
            if "Items" not in response:
                break
                
            # Add items to our collection
            all_items.extend(response["Items"])
            
            # Check if there are more pages
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break  # No more pages
                
        print(f"Retrieved {len(all_items)} total items")
        
        if not all_items:
            print("No matching users found")
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "No matching users found"}),
            }

        # Build dictionary mapping user_id to email (or user_id if no email)
        user_email_map = {}
        for item in all_items:
            user_id = item.get("user_id")
            email = item.get("email", user_id)  # Default to user_id if no email
            if user_id:
                user_email_map[user_id] = email
        
        print(f"Built user-email mapping for {len(user_email_map)} users")
        
        # Keep backward compatibility: also return the old "emails" list
        email_matches = list(user_email_map.keys())  # List of user_ids for backward compat
        
        return {
            "statusCode": 200, 
            "body": json.dumps({
                "emails": email_matches,  # Backward compatibility
                "user_email_map": user_email_map  # New mapping
            })
        }

    except ClientError as e:
        print("Error: ", e.response["Error"]["Message"])
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def is_valid_email_prefix(prefix):
    if prefix == "*":
        return True
    """ Validate the email prefix against a simple character check or regex. """
    if re.match(r"^[a-zA-Z0-9._%+@-]+$", prefix):
        return True
    return False


@required_env_vars({
    "COGNITO_USERS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("read")
def get_user_groups(event, context, current_user, name, data):
    resp_data = get_cognito_amplify_groups(current_user)
    return {"statusCode": resp_data["status"], "body": json.dumps(resp_data["data"])}


def get_cognito_amplify_groups(current_user):
    dynamodb = boto3.resource("dynamodb")
    cognito_user_table = dynamodb.Table(os.environ["COGNITO_USERS_DYNAMODB_TABLE"])

    try:
        print("Initiate query to cognito user dynamo table for user: ", current_user)
        response = cognito_user_table.get_item(Key={"user_id": current_user})

        print("Response: ", response)

        if "Item" not in response:
            return {"status": 404, "data": {"error": "Failed to check cognito groups"}}

        cognito_groups = response["Item"].get("custom:vu_groups", [])
        amplify_groups = response["Item"].get("amplify_groups", [])

        print("cognito groups: ", cognito_groups)
        print("amplify groups", amplify_groups)

        return {
            "status": 200,
            "data": {"cognitoGroups": cognito_groups, "amplifyGroups": amplify_groups},
        }

    except ClientError as e:
        print("Error: ", e.response["Error"]["Message"])
        return {"status": 500, "data": {"error": str(e)}}
