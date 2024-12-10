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
import re
from common.ops import op


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
            # Decided its okay to view deactivated keys- use case, you compromised one but unsure what name it was under, you just know what the api key is
            if (((item['owner'] == user and not delegate) or ( delegate == user))):
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
                    'body': json.dumps({'success': False , 'error': message})
                }
        else:
            return {
                'statusCode': 404,
                'body': json.dumps({'success': False , 'error': 'API key not found'})
            }
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'success': False , 'error': 'Failed to fetch API key'})
        }

@op(
    path="/apiKeys/get_keys",
    name="getApiKeys",
    method='GET',
    tags=["apiKeys"],
    description="Get a list of the user's amplify api keys.",
    params={
    }
)
@validated("read")
def get_api_keys_for_user(event, context, user, name, data):
    return get_api_keys(user)


def get_api_keys(user):
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
                # delegate will not be able to see account coa
                if user == item.get("delegate"):
                    item["account"] = None

                # check if key is expired, if so deactivate key 
                expiration = item.get("expirationDate")
                if (item.get("active") and expiration and is_expired(expiration)):
                    print(f"Key {item.get('applicationName')} is expired and will be deactivated ")
                    deactivate_key_in_dynamo(user, item.get("api_owner_id"))
                    item["active"] = False

           
            return {'success': True, 'data': response['Items']}
        else:
            print(f"User {user} has no API keys. ")
            return {'success': True, 'data': [], 'message': "User has no API keys."}
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred while retrieving API keys for user {user}: {e}")
        return {'success': False, 'data': [], 'message': str(e)}
    

@op(
    path="/apiKeys/get_keys_ast",
    name="getApiKeysForAst",
    method='GET',
    tags=["apiKeysAst"],
    description="Get user's amplify api keys filtered for assistant use.",
    params={
    }
)
@validated("read")
def get_api_keys_for_assistant(event, context, user, name, data):
    # Fetch API keys
    api_keys_res = get_api_keys(user)
    if (not api_keys_res['success']):
        return {'success': False, 'message': "API KEYS: UNAVAILABLE"}
    
    keys = api_keys_res['data']
    append_msg = "Always display all the Keys."
    if len(keys) > 0:
        append_msg += f"\n\n There are a total of {len(keys)}. When asked to list keys, always list ALL {len(keys)} of the 'API KEYS'"
        delegate_keys = []
        delegated_keys = []
        for k in keys:
            if k.get('delegate'):
                if k['delegate'] != user: # user is owner
                    delegate_keys.append(k)
                elif k['delegate'] == user and k['owner'] != user: # user is not owner
                    delegated_keys.append(k)
        
        if delegate_keys:
            append_msg += "\n\n GET OP is NOT allowed for the following Delegate keys (unauthorized): " + ', '.join([k['applicationName'] for k in delegate_keys])
        if delegated_keys:
            append_msg += "\n\n UPDATE OP is NOT allowed for the following Delegated keys (unauthorized): " + ', '.join([k['applicationName'] for k in delegated_keys])

    return {'success': True, 'All_API_Keys': f"{keys} " if keys else "No current existing keys",
            'Additional_Instructions': append_msg}



def can_create_api_key(user, account):
    if (not is_valid_account(account['id'])):
        return {"success": False, "message": "Invalid COA string"}
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

def is_valid_account(coa):
    return coa is not None

# Use Function Below if you need to validate the COA string
#def is_valid_account(coa):
#    # here we want to check valid coa string, 
#    pattern = r'^[a-zA-Z0-9._]+$'
#    return bool(pattern.match(coa))
    

@validated("create")
def create_api_keys(event, context, user, name, data):
    # api keys/get
    apiKey = data['data']


    can_create = can_create_api_key(user, apiKey["account"])
    if (not can_create['success']): 
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
    isSystem = api_key.get("systemUse", False)

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
                'rateLimit': formatRateLimit( api_key["rateLimit"] ), 
                'expirationDate': api_key.get("expirationDate", None),
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
def update_api_keys_for_user(event, context, user, name, data):
    failed = []
    for item in data['data']:
        response = update_api_key(item["apiKeyId"], item["updates"], user)
        if (not response['success']):
            failed.append(response['key_name'])

    return {"success": len(failed) == 0, "failedKeys": failed}


def update_api_key(item_id, updates, user):
    # Fetch the current state of the API key to ensure it is active and not expired
    key_name = 'unknown'
    try:
        current_key = table.get_item(Key={'api_owner_id': item_id})
        key_data = current_key.get('Item', None)
        key_name = key_data["applicationName"]
        if 'Item' not in current_key or not key_data['active'] or is_expired(key_data['expirationDate']) or user != key_data['owner']:
            print("Failed at initial screening")
            return {'success': False, 'error': 'API key is inactive or expired or does not exist or you are unauthorized to make updates', "key_name": key_name}
    except ClientError as e:
        return {'success': False, 'error': str(e),  "key_name": key_name}

    updatable_fields = {'rateLimit', 'expirationDate', 'account', 'accessTypes'}
    update_expression = []
    expression_attribute_values = {}
    expression_attribute_names = {}
    print("Updates performed on key: ", key_name)
    for field, value in updates.items():
        if field in updatable_fields:
            print("updates: ", field, "-", value)
        if (field == 'account'):
            if (not is_valid_account(value['id'])):
                warning = "Warning: Invalid COA string attached to account"
                print(warning)  # or use a logging mechanism
            # Continue with key creation despite the warning
        if (field == 'rateLimit'): 
            value = formatRateLimit(value)
        # Use attribute names to avoid conflicts with DynamoDB reserved keywords
            placeholder = f"#{field}"
            value_placeholder = f":{field}"
            update_expression.append(f"{placeholder} = {value_placeholder}")
            expression_attribute_names[placeholder] = field
            expression_attribute_values[value_placeholder] = value


    # Join the update expression and check if it's empty
    if not update_expression:
        return {'success': False, 'error': 'No valid fields provided for update', "key_name": key_name}

    # Construct the full update expression
    final_update_expression = "SET " + ", ".join(update_expression)

    try:
        response = table.update_item(
            Key={
                'api_owner_id': item_id
            },
            UpdateExpression=final_update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ExpressionAttributeNames=expression_attribute_names,
            ReturnValues='UPDATED_NEW'
        )
        return {'success': True, 'updated_attributes': response['Attributes']}
    except ClientError as e:
        return {'success': False, 'error': str(e), "key_name": key_name}


@validated("deactivate")
def deactivate_key(event, context, user, name, data):
    item_id = data['data']["apiKeyId"]
    if not is_valid_id_format(item_id):
        return {
            'statusCode': 400,
            'body': json.dumps({'success': False, 'error': 'Invalid or missing API key ID parameter'})
        }
    return deactivate_key_in_dynamo(user, item_id)


def deactivate_key_in_dynamo(user, key_id):
    try:
        response = table.get_item(
            Key={
                'api_owner_id': key_id
            }
        )
        if 'Item' in response:
            item = response['Item']

            if (item['owner'] == user or item['delegate'] == user):
                print("updating key")
                update_response = table.update_item(
                    Key={
                        'api_owner_id': key_id
                    },
                    UpdateExpression='SET active = :val',
                    ExpressionAttributeValues={
                        ':val': False
                    },
                    ReturnValues='UPDATED_NEW'
                )

                # Check if the item was successfully updated
                if not update_response['Attributes']['active']:
                    print("successfully deactivated")
                    return {'success': True, 'message': 'API key successfully deactivated.'}
                else:
                    return {'success': False, 'error': 'Unable to update value to False.'}
            else:
                return {'success': False, 'error': 'Unauthorized to deactivate this API key'}
        else:
            return {'success': False, 'error': 'API key not found'}

    except Exception as e:
        # Handle potential errors
        print(f"An error occurred: {e}")
        return {'success': False, 'error': 'Failed to deactivate API key'}


def is_valid_id_format(id):
    print("Validating key id: ", id)
    regex = r'^[^/]+/(ownerKey|delegateKey|systemKey)/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    match = re.fullmatch(regex, id, re.IGNORECASE)
    return bool(match)

def is_expired(date_str):
    if (not date_str): return False
    date = datetime.strptime(date_str, '%Y-%m-%d')
    return date <= datetime.now()


@validated("read")
def get_system_ids(event, context, current_user, name, data): 
    print("Getting system-specific API keys from DynamoDB")
    try:
        # Use a Scan operation with a FilterExpression to find items where the user is the owner and systemId is not null
        response = table.scan(
            FilterExpression="#owner = :user and attribute_type(systemId, :type) and active = :active",
            ExpressionAttributeNames={
                "#owner": "owner"  # Use '#owner' to avoid reserved keyword conflicts
            },
            ExpressionAttributeValues={
                ':user': current_user,
                ':type': 'S',  # Assuming systemId is a string attribute
                ':active': True
            },
            ProjectionExpression="#owner, applicationName, lastAccessed, rateLimit, expirationDate, accessTypes, systemId"
        )
        # Check if any items were found
        if 'Items' in response and response['Items']:
            print(f"System API keys found for owner {current_user}")
            print(f"items {response['Items']}")
            return {'success': True, 'data': response['Items']}
        else:
            print(f"No active system API keys found for owner {current_user}")
            return {'success': True, 'data': [], 'message': "No active system API keys found."}
    except Exception as e:
        # Handle potential errors
        print(f"An error occurred while retrieving system API keys for owner {current_user}: {e}")
        return {'success': False, 'message': str(e)}



@validated("read")
def get_documentation(event, context, current_user, name, data): 
    print(f"Getting presigned download URL for user {current_user}")
    
    doc_presigned_url = generate_presigned_url('Amplify_API_Documentation.pdf')
    csv_presigned_url =  generate_presigned_url('Amplify_API_Documentation.csv')
    postman_presigned_url = generate_presigned_url('Postman_Amplify_API_Collection.json')

    res = {'success': True}
    if doc_presigned_url : res['doc_url'] =  doc_presigned_url
    if csv_presigned_url : res['csv_url'] =  csv_presigned_url
    if postman_presigned_url : res['postman_url'] =  postman_presigned_url

    if len(res) > 1:
        return res
    else:
        print("Failed to retrieve a new presigned url")
        return {'success': False, 'message': 'Files not found' }
    
def generate_presigned_url(file):
    s3 = boto3.client('s3')
    bucket_name = os.environ['S3_API_DOCUMENTATION_BUCKET']
    try:
        return s3.generate_presigned_url(
                ClientMethod='get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': file,
                    'ResponseContentDisposition': f"attachment; filename={file}"
                },
                ExpiresIn=7200  # Expires in 3 hrs 
            )
    except ClientError as e:
        print(f"Error generating presigned download URL for file {file}: {e}")
        return None
    

def formatRateLimit(rateLimit):
    if rateLimit.get("rate", None):
        rateLimit["rate"] = Decimal(str(rateLimit["rate"]))
    return rateLimit
