import os
import boto3
import requests
import json

from common.datasources import sanitize_s3_data_source_key


def update_object_permissions(current_user, data):

    print(f"Updating object permissions for {current_user}")

    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']

    if not table_name:
        raise ValueError("Environment variable 'OBJECT_ACCESS_DYNAMODB_TABLE' is not set.")

    try:
        data_sources = data['dataSources']
        email_list = data['emailList']
        provided_permission_level = data['permissionLevel']  # Permission level provided for other users
        policy = data['policy']  # No need to use get() since policy is always present
        principal_type = data.get('principalType')
        object_type = data.get('objectType')

        print(f"Updating permission on {data_sources} for {email_list} with {provided_permission_level} and {policy}")

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
                    'permission_level': 'write',  # The current_user becomes the owner
                    'policy': policy
                })
                print(f"Created initial item for {object_id} with {current_user} as owner")

            else:
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
                    print(f"Updated item for {object_id} with {principal_id} to {provided_permission_level}")

    except Exception as e:
        print(f"Failed to update permissions: {str(e)}")
        return False

    print(f"Updated permissions for {data_sources} for {email_list} with {provided_permission_level} and {policy}")
    return True


def can_access_objects(current_user, access_token, data_sources, permission_level="read"):
    print(f"Checking access on data sources: {data_sources}")

    # Check if the data source has s3:// or .content.json in the key and strip it out as
    # needed by calling sanitize_s3_data_source_key
    data_sources = [sanitize_s3_data_source_key(ds) for ds in data_sources]

    # Check if the id of all the data_sources starts with the current_user
    if current_user and all([ds['id'].startswith(current_user+"/") for ds in data_sources]):
        return True

    access_levels = {ds['id']: permission_level for ds in data_sources}

    print(f"With access levels: {access_levels}")

    request_data = {
        'data': {
            'dataSources': access_levels
        }
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    # Replace 'permissions_endpoint' with the actual permissions endpoint URL
    permissions_endpoint = os.environ['OBJECT_ACCESS_API_ENDPOINT']

    try:
        response = requests.post(
            permissions_endpoint,
            headers=headers,
            data=json.dumps(request_data)
        )

        response_content = response.json() # to adhere to object access return response dict

        print(f"Response: {response_content}")

        if response.status_code != 200 or response_content.get('statusCode', None) != 200:
            print(f"User does not have access to data sources: {response.status_code}")
            return False
        elif response.status_code == 200 and response_content.get('statusCode', None) == 200:
            return True

    except Exception as e:
        print(f"Error checking access on data sources: {e}")
        return False

    return False


def simulate_can_access_objects(access_token, object_ids, permission_levels=["read"]):
    print(f"Simulating access on data sources: {object_ids}")

    access_levels = {id: permission_levels for id in object_ids}

    # Set the access levels result for each object to false for every object id and permission level
    all_denied = {id: {pl: False for pl in permission_levels} for id in object_ids}

    print(f"With access levels: {access_levels}")

    request_data = {
        'data': {
            'objects': access_levels
        }
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    # Replace 'permissions_endpoint' with the actual permissions endpoint URL
    permissions_endpoint = os.environ['OBJECT_SIMULATE_ACCESS_API_ENDPOINT']

    try:
        response = requests.post(
            permissions_endpoint,
            headers=headers,
            data=json.dumps(request_data)
        )

        response_content = response.json() # to adhere to object access return response dict
        
        if response.status_code != 200 or response_content.get('statusCode', None) != 200:
            print(f"Error simulating user access")
            return all_denied
        elif response.status_code == 200 and response_content.get('statusCode', None) == 200:
            result = response.json()
            if 'data' in result:
                return result['data']
            else:
                return all_denied

    except Exception as e:
        print(f"Error simulating access on data sources: {e}")
        return all_denied

    return all_denied

