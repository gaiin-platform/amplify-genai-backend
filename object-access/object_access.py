# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import boto3
import json
from botocore.exceptions import ClientError
import os
from pycommon.authz import validated, setup_validated
from pycommon.api.amplify_users import are_valid_amplify_users
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

# Initialize a DynamoDB client
dynamodb = boto3.resource("dynamodb")


def is_sufficient_privilege(object_id, permission_level, policy, requested_access_type):
    if permission_level == "owner":
        return True
    elif permission_level == "write":
        return requested_access_type in ["read", "write"]
    elif permission_level == "read":
        return requested_access_type == "read"
    elif permission_level == "none":
        return False
    elif policy == "public":
        return requested_access_type == "read"
    else:
        return False


def add_access_response(access_responses, object_id, access_type, response):
    print("Add access response")
    if object_id not in access_responses:
        access_responses[object_id] = {}
    access_responses[object_id][access_type] = response
    print("Added access response: ", access_responses)


@validated("simulate_access_to_objects")
def simulate_access_to_objects(event, context, current_user, name, data):
    print("Simulating object access")
    table_name = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]
    table = dynamodb.Table(table_name)

    data = data["data"]
    data_sources = data["objects"]

    access_responses = {}

    for object_id, access_types in data_sources.items():
        print(
            "checking permissions for object: ",
            object_id,
            " with access: ",
            access_types,
        )
        # Check if any permissions already exist for the object_id
        for access_type in access_types:
            try:
                query_response = table.get_item(
                    Key={"object_id": object_id, "principal_id": current_user}
                )
                item = query_response.get("Item")

                if not item:
                    print(
                        f"User does not have access to objectId {object_id} with access type {access_type}."
                    )
                    add_access_response(access_responses, object_id, access_type, False)
                    continue

                permission_level = item.get("permission_level")
                policy = item.get("policy")
                if not is_sufficient_privilege(
                    object_id, permission_level, policy, access_type
                ):
                    print(
                        f"User does not have access to objectId {object_id} with access type {access_type}."
                    )
                    add_access_response(access_responses, object_id, access_type, False)
                    continue

                print(
                    f"User has access to objectId {object_id} with access type {access_type}."
                )
                add_access_response(access_responses, object_id, access_type, True)
            except Exception as e:
                print(f"Error in simulate_access_to_objects: {e}")
                add_access_response(access_responses, object_id, access_type, False)

    return {
        "statusCode": 200,
        "body": "User access responses simulated.",
        "data": access_responses,
    }


@validated("can_access_objects")
def can_access_objects(event, context, current_user, name, data):
    print("Can access objects")

    table_name = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]
    table = dynamodb.Table(table_name)

    data = data["data"]

    print("Data: ", data)

    try:
        data_sources = data["dataSources"]

        for object_id, access_type in data_sources.items():
            # Check if any permissions already exist for the object_id
            query_response = table.get_item(
                Key={"object_id": object_id, "principal_id": current_user}
            )
            item = query_response.get("Item")

            if not item:
                return {
                    "statusCode": 403,
                    "body": json.dumps(
                        {
                            "message": f"User does not have access to objectId.",
                            "objectId": object_id,
                            "accessType": access_type,
                        }
                    ),
                }

            permission_level = item.get("permission_level")
            policy = item.get("policy")
            if not is_sufficient_privilege(
                object_id, permission_level, policy, access_type
            ):
                print("User does not have access to objectId: ", object_id)
                return {
                    "statusCode": 403,
                    "body": json.dumps(
                        {
                            "message": f"User does not have access to objectId.",
                            "objectId": object_id,
                            "accessType": access_type,
                        }
                    ),
                }

    except ClientError as e:
        print(
            f"Error accessing DynamoDB for can_access_objects: {e.response['Error']['Message']}"
        )
        return {
            "statusCode": 500,
            "body": "Internal error determining access. Please try again later.",
        }
    print("User passed can access objects.")
    return {"statusCode": 200, "body": "User has access to the object(s)."}


@validated("update_object_permissions")
def update_object_permissions(event, context, current_user, name, data):
    table_name = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]
    data = data["data"]
    print("Entered update object permissions")
    try:
        data_sources = data["dataSources"]
        email_list = data["emailList"]
        print("Email list: ", email_list)
        provided_permission_level = data[
            "permissionLevel"
        ]  # Permission level provided for other users
        policy = data["policy"]  # No need to use get() since policy is always present
        principal_type = data.get("principalType")
        object_type = data.get("objectType")

        table = dynamodb.Table(table_name)

        for object_id in data_sources:
            print("Current object Id: ", object_id)

            # Check if any permissions already exist for the object_id
            query_response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("object_id").eq(
                    object_id
                )
            )
            items = query_response.get("Items")

            if not items:
                print(
                    " no permissions, create the initial item with the current_user as the owner"
                )
                table.put_item(
                    Item={
                        "object_id": object_id,
                        "principal_id": current_user,
                        "principal_type": principal_type,
                        "object_type": object_type,
                        "permission_level": "owner",
                        "policy": policy,
                    }
                )

            owner_key = {"object_id": object_id, "principal_id": current_user}
            owner_response = table.get_item(Key=owner_key)
            owner_item = owner_response.get("Item")
            print(
                "check if the current_user has 'owner' or 'write' permissions for the object_id"
            )
            if owner_item and owner_item.get("permission_level") in ["owner", "write"]:
                # If current_user is the owner or has write permission, proceed with updates
                print("current_user does have permissions to proceed with updates")
                for principal_id in email_list:
                    if current_user != principal_id:  # edge case
                        print("Object ID: ", object_id, " for user: ", principal_id)
                        # Create or update the permission level for each principal_id
                        principal_key = {
                            "object_id": object_id,
                            "principal_id": principal_id,
                        }
                        # Use the provided permission level for other users
                        update_expression = "SET principal_type = :principal_type, object_type = :object_type, permission_level = :permission_level, policy = :policy"
                        expression_attribute_values = {
                            ":principal_type": principal_type,
                            ":object_type": object_type,
                            ":permission_level": provided_permission_level,  # Use the provided permission level
                            ":policy": policy,
                        }
                        table.update_item(
                            Key=principal_key,
                            UpdateExpression=update_expression,
                            ExpressionAttributeValues=expression_attribute_values,
                        )
            else:
                # The current_user does not have 'owner' or 'write' permissions
                print("The current_user does not have 'owner' or 'write' permissions")
                return {
                    "statusCode": 403,
                    "body": json.dumps(
                        f"User {current_user} does not have sufficient permissions to update permissions for objectId {object_id}."
                    ),
                }

    except ClientError as e:
        return {
            "statusCode": e.response["ResponseMetadata"]["HTTPStatusCode"],
            "body": json.dumps(
                f"Error accessing/updating DynamoDB: {e.response['Error']['Message']}"
            ),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps(f"Error processing request: {str(e)}"),
        }
    print("Permissions updated successfully")
    return {"statusCode": 200, "body": json.dumps("Permissions updated successfully.")}


@validated("validate_users")
def validate_users(event, context, current_user, name, data):
    """
    Validates a list of user names (emails) against Amplify user directory.
    Returns which user names are valid Amplify users and which are not.
    """
    print("Validating users")
    
    try:
        data = data["data"]
        user_names = data["user_names"]
        
        # Extract access token from the event
        # The pycommon validation system should have already validated the token
        access_token = event.get("headers", {}).get("Authorization", "")
        if access_token.startswith("Bearer "):
            access_token = access_token[7:]  # Remove "Bearer " prefix
        
        # Call the pycommon function to validate users
        valid_users, invalid_users = are_valid_amplify_users(access_token, user_names)
        
        return {
            "statusCode": 200,
            "body": json.dumps("User validation completed successfully."),
            "data": {
                "valid_users": valid_users,
                "invalid_users": invalid_users
            }
        }
        
    except Exception as e:
        print(f"Error in validate_users: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps(f"Error processing user validation request: {str(e)}"),
        }
