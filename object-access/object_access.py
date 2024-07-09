from common.validate import validated
import logging
import boto3
import json
from botocore.exceptions import ClientError
import os

# Initialize a DynamoDB client
dynamodb = boto3.resource('dynamodb')


def is_sufficient_privilege(object_id, permission_level, policy, requested_access_type):
    if permission_level == 'owner':
        return True
    elif permission_level == 'write':
        return requested_access_type in ['read', 'write']
    elif permission_level == 'read':
        return requested_access_type == 'read'
    elif permission_level == 'none':
        return False
    elif policy == 'public':
        return requested_access_type == 'read'
    else:
        return False


def add_access_response(access_responses, object_id, access_type, response):
    print("Add access response")
    if object_id not in access_responses:
        access_responses[object_id] = {}
    access_responses[object_id][access_type] = response
    print("Added access response: ", access_responses)



@validated("simulate_access_to_objects")
def simulate_access_to_objects(event, context, current_user, name, data, username):
    print("Simulating object access")
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    table = dynamodb.Table(table_name)

    data = data['data']
    data_sources = data['objects']

    access_responses = {}

    for object_id, access_types in data_sources.items():
        print("checking permissions for object: ", object_id, " with access: ", access_types)
        # Check if any permissions already exist for the object_id
        for access_type in access_types:
            try:
                query_response = table.get_item(
                    Key={
                        'object_id': object_id,
                        'principal_id': current_user
                    }
                )
                item = query_response.get('Item')

                if not item:
                    print(f"User does not have access to objectId {object_id} with access type {access_type}.")
                    add_access_response(access_responses, object_id, access_type, False)
                    continue

                permission_level = item.get('permission_level')
                policy = item.get('policy')
                if not is_sufficient_privilege(object_id, permission_level, policy, access_type):
                    print(f"User does not have access to objectId {object_id} with access type {access_type}.")
                    add_access_response(access_responses, object_id, access_type, False)
                    continue

                print(f"User has access to objectId {object_id} with access type {access_type}.")
                add_access_response(access_responses, object_id, access_type, True)
            except:
                print(f"Error in simulate_access_to_objects.")
                add_access_response(access_responses, object_id, access_type, False)

    return {
        'statusCode': 200,
        'body': 'User access responses simulated.',
        'data': access_responses
    }


@validated("can_access_objects")
def can_access_objects(event, context, current_user, name, data, username):
    print("Can access objects")

    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    table = dynamodb.Table(table_name)

    data = data['data']

    print("Data: ", data)

    try:
        data_sources = data['dataSources']

        for object_id, access_type in data_sources.items():
            # Check if any permissions already exist for the object_id
            query_response = table.get_item(
                Key={
                    'object_id': object_id,
                    'principal_id': current_user
                }
            )
            item = query_response.get('Item')

            if not item:
                return {
                    'statusCode': 403,
                    'body': json.dumps({
                        'message': f"User does not have access to objectId.",
                        'objectId': object_id,
                        'accessType': access_type
                    })
                }

            permission_level = item.get('permission_level')
            policy = item.get('policy')
            if not is_sufficient_privilege(object_id, permission_level, policy, access_type):
                print("User does not have access to objectId: ", object_id)
                return {
                    'statusCode': 403,
                    'body': json.dumps({
                        'message': f"User does not have access to objectId.",
                        'objectId': object_id,
                        'accessType': access_type
                    })
                }

    except ClientError as e:
        print(f"Error accessing DynamoDB for can_access_objects: {e.response['Error']['Message']}")
        return {
            'statusCode': 500,
            'body': "Internal error determining access. Please try again later."
        }
    print("User passed can access objects.")
    return {
        'statusCode': 200,
        'body': 'User has access to the object(s).'
    }


@validated("update_object_permissions")
def update_object_permissions(event, context, current_user, name, data, username):
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    data = data['data']
    print("Entered update object permissions")
    try:
        data_sources = data['dataSources']
        email_list = data['emailList']
        print("Email list: ", email_list)
        provided_permission_level = data['permissionLevel']  # Permission level provided for other users
        policy = data['policy']  # No need to use get() since policy is always present
        principal_type = data.get('principalType')
        object_type = data.get('objectType')

        # Get the DynamoDB table
        table = dynamodb.Table(table_name)
        
        for object_id in data_sources:
            print("Current object Id: ", object_id)
        
            # Check if any permissions already exist for the object_id
            query_response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('object_id').eq(object_id)
            )
            items = query_response.get('Items')

            # If there are no permissions, create the initial item with the current_user as the owner
            if not items:
                print(" no permissions, create the initial item with the current_user as the owner")
                table.put_item(Item={
                    'object_id': object_id,
                    'principal_id': current_user,
                    'principal_type': principal_type,
                    'object_type': object_type,
                    'permission_level': 'owner',  # The current_user becomes the owner
                    'policy': policy
                })

            # Check if the current_user has 'owner' or 'write' permissions for the object_id
            owner_key = {
                'object_id': object_id,
                'principal_id': current_user
            }
            owner_response = table.get_item(Key=owner_key)
            owner_item = owner_response.get('Item')
            print("check if the current_user has 'owner' or 'write' permissions for the object_id")
            if owner_item and owner_item.get('permission_level') in ['owner', 'write']:
                # If current_user is the owner or has write permission, proceed with updates
                print("current_user foes have permissions to proceed with updates")
                for principal_id in email_list:
                    if (current_user != principal_id):  # edge case
                        print("Object ID: ", object_id, " for user: ", principal_id)
                        # Create or update the permission level for each principal_id
                        principal_key = {
                            'object_id': object_id,
                            'principal_id': principal_id
                        }
                        # Use the provided permission level for other users
                        update_expression = "SET principal_type = :principal_type, object_type = :object_type, permission_level = :permission_level, policy = :policy"
                        expression_attribute_values = {
                            ':principal_type': principal_type,
                            ':object_type': object_type,
                            ':permission_level': provided_permission_level,  # Use the provided permission level
                            ':policy': policy
                        }
                        table.update_item(
                            Key=principal_key,
                            UpdateExpression=update_expression,
                            ExpressionAttributeValues=expression_attribute_values
                        )
            else:
                # The current_user does not have 'owner' or 'write' permissions
                print("The current_user does not have 'owner' or 'write' permissions")
                return {
                    'statusCode': 403,
                    'body': json.dumps(
                        f"User {current_user} does not have sufficient permissions to update permissions for objectId {object_id}.")
                }

    except ClientError as e:
        return {
            'statusCode': e.response['ResponseMetadata']['HTTPStatusCode'],
            'body': json.dumps(f"Error accessing/updating DynamoDB: {e.response['Error']['Message']}")
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps(f"Error processing request: {str(e)}")
        }
    print("Permissions updated successfully")
    return {
        'statusCode': 200,
        'body': json.dumps('Permissions updated successfully.')
    }

@validated("create_cognito_group")
def create_cognito_group(event, context, current_user, name, data, username):
    if ( not username ):
        print('Access from API, no need to continue fuction')
        return None
    """
    Create a Cognito user group in the specified user pool and add the current user to it.

    :param event: AWS Lambda event object.
    :param context: AWS Lambda context object.
    :param current_user: The username or sub of the current user.
    :param name: The name of the user pool (not used in this function).
    :param data: The data containing the groupName and description.
    :return: The response from the create_group call or None if an error occurred.
    """
    data = data['data']
    user_pool_id = os.environ['COGNITO_USER_POOL_ID']
    group_name = data['groupName']
    description = data['groupDescription']

    # Initialize a Cognito Identity Provider client
    cognito_idp = boto3.client('cognito-idp')

    try:
        # Create the group
        response = cognito_idp.create_group(
            GroupName=group_name,
            UserPoolId=user_pool_id,
            Description=description
        )
        print(f"Group '{group_name}' created successfully.")

        # Add the current user to the group
        cognito_idp.admin_add_user_to_group(
            UserPoolId=user_pool_id,
            Username=username,
            GroupName=group_name
        )
        print(f"User '{current_user}' added to group '{group_name}' successfully.")

        return response
    except ClientError as e:
        print(f"An error occurred: {e}")
        return None
