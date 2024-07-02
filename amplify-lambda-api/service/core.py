from datetime import datetime, timezone
import json
import re
from common.validate import validated
from botocore.exceptions import ClientError
from decimal import Decimal
import boto3
import os
import uuid
import random
import boto3
import json
from botocore.exceptions import ClientError
import re


dynamodb = boto3.resource('dynamodb')
api_keys_table_name = os.environ['API_KEYS_DYNAMODB_TABLE']
table = dynamodb.Table(api_keys_table_name)

@validated("read")
def get_api_key(event, context, user, name, data):
    query_params = event.get('queryStringParameters', {})
    print("Query params: ", query_params)
    api_key_id = query_params.get('apiKeyId', '')
    
    # Check if the API key ID is valid
    if not api_key_id or not is_valid_id_format(api_key_id):
        return {
            'statusCode': 400,
            'body': json.dumps({'success': False , 'error': 'Invalid or missing API key ID parameter'})
        }
    
    try:
        print('Retrieve the item from DynamoDB')
        response = table.get_item(
            Key={
                'api_owner_id': api_key_id
            }
        )
        # Check if item exists and validate the user's role
        if 'Item' in response:
            item = response['Item']
            delegate = item.get('delegate')
            # checks
            # owner with no delegete - owners cant see delegate keys
            # is the delegate - delegates can see the key
            # key must be active
            if (((item['owner'] == user and not delegate) or ( delegate == user)) and item.get('active', False)):
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'success': True,
                        'message': 'Successfully fetched API key',
                        'data': item['apiKey']  # Returning the actual apiKey
                    })
                }
            else:
                message = 'Unauthorized to access this API key' if item.get('active', False) else "The key has been deactivated"
                return {
                    'statusCode': 403,
                    'body': json.dumps({'error': message})
                }
        else:
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'API key not found'})
            }
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Failed to fetch API key'})
        }


@validated("read")
def get_api_keys_for_user(event, context, user, name, data):
    print("Getting keys from dyanmo")
    try:
        # Use a Scan operation with a FilterExpression to find items where the user is the owner or the delegate
        # Use a ProjectionExpression to specify the attributes to retrieve (excluding 'apiKey')
        response = table.scan(
        FilterExpression="#owner = :user or delegate = :user",
        ExpressionAttributeNames={
            "#owner": "owner"  # Use '#owner' to substitute 'owner'
        },
        ExpressionAttributeValues={
            ':user': user
        },
        ProjectionExpression="api_owner_id, #owner, delegate, applicationName, applicationDescription, createdAt, lastAccessed, rateLimit, expirationDate, accessTypes, active, account, systemId"
        )
        # Check if any items were found
        if 'Items' in response and response['Items']:
            print(f"API keys found for user {user}")
            for item in response['Items']:
                if user == item.get("delegate"):
                    item["account"] = None
           
            return {'success': True, 'data': response['Items']}
        else:
            print(f"No API keys found for user {user}")
            return {'success': False, 'data': [], 'message': "No API keys found."}
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred while retrieving API keys for user {user}: {e}")
        return {'success': False, 'data': [], 'message': str(e)}
    


def can_create_api_key(user, account):
    # here we want to check valid coa string, 
    pattern = re.compile(r'^(\w{3}\.\w{2}\.\w{5}\.\w{4}\.\w{3}\.\w{3}\.\w{3}\.\w{3}\.\w{1})$')
    if (not bool(pattern.match(account))) :
        return {"success":False, "message": "Invalid COA string"}
    # if delegate,  check user table for valid delegate email cognito user 
    cognito_user_table = dynamodb.Table(os.environ['COGNITO_USERS_TABLE'])
    try:
        response = cognito_user_table.get_item(Key={'user_id': user})
        if 'Item' in response:
            return {'success': True}
        else:
            return {"success": False, "message": "Invalid user, unable to verify user"}
    except Exception as e:
        print(f"Error checking user existence: {e}")
        return {"success": False, "message": "Unable to verify user at this time"}

    

@validated("create")
def create_api_keys(event, context, user, name, data):
    # api keys/get
    apiKey = data['data']


    can_create = can_create_api_key(user, apiKey["account"])
    if (not can_create): 
        return {
            'success': False,
            'message': can_create['message']
        }
    return create_api_key_for_user(user, apiKey)


def create_api_key_for_user(user, api_key) :
    print("Validated and now creating api key")
    api_keys_table_name = os.environ['API_KEYS_DYNAMODB_TABLE']
    table = dynamodb.Table(api_keys_table_name)
    delegate = api_key.get("delegate", None)
    isSystem = api_key["systemUse"]

    key_type = 'delegate' if delegate else ('system' if isSystem else 'owner') 
    id = f"{user}/{key_type}Key/{str(uuid.uuid4())}"
    timestamp = datetime.now(timezone.utc).isoformat()

    apiKey = 'amp-' + str(uuid.uuid4())
    

    app_name = api_key["appName"]

    sys_id = None
    if (isSystem):
        sys_id = f"{app_name.replace(' ', '-')}-{''.join(random.choices('0123456789', k=6))}" # system Id 
    
    try:
        print("Put entry in api keys table")

        # Put (or update) the item for the specified user in the DynamoDB table
        response = table.put_item(
            Item={
                'api_owner_id': id,
                'owner': user,
                'apiKey': apiKey,
                'account': api_key["account"],
                'delegate': delegate,
                'systemId' : sys_id,
                'active': True,
                'applicationName': app_name,
                'applicationDescription': api_key["appDescription"],
                'createdAt': timestamp, 
                'lastAccessed': timestamp,
                'rateLimit': api_key["rateLimit"], # format {rate: , time: }
                'expirationDate': api_key["expiration"],
                'accessTypes' :  api_key["accessTypes"]
            }
        )

        # Check if the response was successful
        if response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200:
            print(f"API key for user {user} created successfully")
            return {
                'success': True,
                'message': 'API key created successfully'
            }
        else:
            print(f"Failed to create API key for user {user}")
            return {
                'success': False,
                'message': 'Failed to create API key'
            }
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred while saving API key for user {user}: {e}")
        return {
            'success': False,
            'message': f"An error occurred while saving API key: {str(e)}"
        }

@validated("update")
def update_api_key_for_user(event, context, user, name, data):
    data = data['data']
    #update field needs to be one of the following 
    item_id = data["id"]
    updatable_fields = {
       'rateLimit', 'expirationDate'
    }
    update_expression = []
    expression_attribute_values = {}
    for field, value in data.items():
        if field in updatable_fields:
            update_expression.append(f"{field} = :{field}")
            expression_attribute_values[f":{field}"] = value

    # Join the update expression and check if it's empty
    if not update_expression:
        return {'success': False, 'error': 'No valid fields provided for update'}

    # Construct the full update expression
    final_update_expression = "SET " + ", ".join(update_expression)

    try:
        response = table.update_item(
            Key={
                'id': item_id
            },
            UpdateExpression=final_update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues='UPDATED_NEW'
        )
        return {'success': True, 'updated_attributes': response['Attributes']}
    except ClientError as e:
        return {'success': False, 'error': str(e)}



@validated("deactivate")
def deactivate_key(event, context, user, name, data):
    item_id = data['data']["id"]
    if not is_valid_id_format(item_id):
        return {
            'statusCode': 400,
            'body': json.dumps({'success': False, 'error': 'Invalid or missing API key ID parameter'})
        }

    try:
        response = table.get_item(
            Key={
                'api_owner_id': item_id
            }
        )
        if 'Item' in response:
            item = response['Item']

            if (item['owner'] == user or item['delegate'] == user):
                update_response = table.update_item(
                    Key={
                        'api_owner_id': item_id
                    },
                    UpdateExpression='SET active = :val',
                    ExpressionAttributeValues={
                        ':val': False
                    },
                    ReturnValues='UPDATED_NEW'
                )

                # Check if the item was successfully updated
                if update_response['Attributes']['active'] == False:
                    return {
                        'statusCode': 200,
                        'body': json.dumps({'success': True, 'message': 'API key successfully deactivated.'})
                    }
                else:
                    return {
                        'statusCode': 400,
                        'body': json.dumps({'success': False, 'error': 'Unable to update value to False.'})
                    }
            else:
                return {
                    'statusCode': 403,
                    'body': json.dumps({'success': False, 'error': 'Unauthorized to deactivate this API key'})
                }
        else:
            return {
                'statusCode': 404,
                'body': json.dumps({'success': False, 'error': 'API key not found'})
            }
    except ClientError as e:
        return {
            'statusCode': 400,
            'body': json.dumps({'success': False, 'error': str(e)})
        }
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'success': False, 'error': 'Failed to deactivate API key'})
        }


def is_valid_id_format(id):
    print("Validating key id: ", id)
    regex = r'^[^/]+/(ownerKey|delegateKey|systemKey)/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    match = re.fullmatch(regex, id, re.IGNORECASE)
    return bool(match)