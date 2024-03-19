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


@validated("can_access_objects")
def can_access_objects(event, context, current_user, name, data):
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    table = dynamodb.Table(table_name)

    data = data['data']

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

            # If there are no permissions, create the initial item with the current_user as the owner
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

    return {
        'statusCode': 200,
        'body': 'User has access to the object(s).'
    }


@validated("update_object_permissions")
def update_object_permissions(event, context, current_user, name, data):
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    data = data['data']

    try:
        data_sources = data['dataSources']
        email_list = data['emailList']
        provided_permission_level = data['permissionLevel']  # Permission level provided for other users
        policy = data['policy']  # No need to use get() since policy is always present
        principal_type = data.get('principalType')
        object_type = data.get('objectType')

        # Get the DynamoDB table
        table = dynamodb.Table(table_name)

        for object_id in data_sources:
            # Check if any permissions already exist for the object_id
            query_response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('object_id').eq(object_id)
            )
            items = query_response.get('Items')

            # If there are no permissions, create the initial item with the current_user as the owner
            if not items:
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

            if owner_item and owner_item.get('permission_level') in ['owner', 'write']:
                # If current_user is the owner or has write permission, proceed with updates
                for principal_id in email_list:
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

    return {
        'statusCode': 200,
        'body': json.dumps('Permissions updated successfully.')
    }
