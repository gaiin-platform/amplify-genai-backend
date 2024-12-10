import base64
import json
import re
import uuid
from datetime import datetime
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from cryptography.fernet import Fernet
from common.ops import op
from common.validate import validated
import os
import boto3
import rag.util
import images.core
from boto3.dynamodb.conditions import Key
from common.data_sources import translate_user_data_sources_to_hash_data_sources
from common.object_permissions import can_access_objects

dynamodb = boto3.resource('dynamodb')

IMAGE_FILE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]


@op(
    path="/files/download",
    name="getDownloadUrl",
    tags=["files"],
    description="Get a url to download the file associated with a datasource key / ID.",
    params={
        "key": "The key or ID of the datasource to download.",
    }
)
@validated(op="download")
def get_presigned_download_url(event, context, current_user, name, data):
    access_token = data['access_token']
    data = data['data']
    key = data['key']
    group_id = data.get('groupId', None)

    if "://" in key:
        key = key.split("://")[1]

    dynamodb = boto3.resource('dynamodb')
    s3 = boto3.client('s3')
    files_table_name = os.environ['FILES_DYNAMO_TABLE']

    # Access the specific table
    files_table = dynamodb.Table(files_table_name)

    print(f"Getting presigned download URL for {key} for user {current_user}")

    # Since groups can have documents that belongs to other and the group itself only has permission we need to check this instead of if they are owner
    # In case of wordpress plugin we will always be a system user so we will never be the owner
    # 

    # Retrieve the item from DynamoDB to check ownership
    try:
        response = files_table.get_item(Key={'id': key})
    except ClientError as e:
        print(f"Error getting file metadata from DynamoDB: {e}")
        error_message = e.response['Error']['Message']
        return {'success': False, 'message': error_message}

    if 'Item' not in response:
        # User doesn't match or item doesn't exist
        print(f"File not found for user {current_user}: {response}")
        return {'success': False, 'message': 'File not found'}

    if response['Item']['createdBy'] != current_user and response['Item']['createdBy'] != group_id:
        translated_ds = None
        if (group_id):# check group_id has perms 
            try:# need global 
                translated_ds = translate_user_data_sources_to_hash_data_sources({"key" : key, 'type': response['Item']['type']})
            except:
                print("Translation for unauthorized user using group id failed")
        if (not (translated_ds and can_access_objects(access_token, translated_ds))):
            # User doesn't match or item doesn't exist
            print(f"User doesn't match for file for {current_user}: {response['Item']}")
            print(f"groupId attached? {group_id}")
            return {'success': False, 'message': 'User does not have access'}

    download_filename = response['Item']['name']
    is_file_type = response['Item']['type'] in IMAGE_FILE_TYPES
    response_headers = {
        'ResponseContentDisposition': f'attachment; filename="{download_filename}"'
    } if download_filename and not is_file_type else {}

    bucket_name = os.environ['S3_IMAGE_INPUT_BUCKET_NAME'] if is_file_type  else os.environ['S3_RAG_INPUT_BUCKET_NAME']
    # If the user matches, generate a presigned URL for downloading the file from S3
    try:
        presigned_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={
                'Bucket': bucket_name,
                'Key': key,
                **response_headers
            },
            ExpiresIn=3600  # Expiration time for the presigned URL, in seconds
        )
    except ClientError as e:
        print(f"Error generating presigned download URL: {e}")
        return {'success': False, 'message': "File not found"}

    if presigned_url:
        return {'success': True, 'downloadUrl': presigned_url}
    else:
        return {'success': False, 'message': 'File not found'}


def create_file_metadata_entry(current_user, name, file_type, tags, data_props, knowledge_base):
    dynamodb = boto3.resource('dynamodb')
    bucket_name = os.environ[ 'S3_IMAGE_INPUT_BUCKET_NAME' if (file_type in IMAGE_FILE_TYPES) else 'S3_RAG_INPUT_BUCKET_NAME' ] 
    dt_string = datetime.now().strftime('%Y-%m-%d')
    key = f'{current_user}/{dt_string}/{uuid.uuid4()}.json'

    files_table = dynamodb.Table(os.environ['FILES_DYNAMO_TABLE']) 
    files_table.put_item(
        Item={
            'id': key,
            'name': name,
            'type': file_type,
            'tags': tags,
            'data': data_props,
            'knowledgeBase': knowledge_base,
            'createdAt': datetime.now().isoformat(),
            'updatedAt': datetime.now().isoformat(),
            'createdBy': current_user,
            'updatedBy': current_user
        }
    )

    if tags is not None and len(tags) > 0:
        update_file_tags(current_user, key, tags)

    return bucket_name, key


@validated(op="set")
def set_datasource_metadata_entry(event, context, current_user, name, data):

    data = data['data']
    key = data['id']
    name = data['name']
    dtype = data['type']
    kb = data.get('knowledge_base','default')
    data_props = data.get('data',{})
    tags = data.get('tags',[])

    dynamodb = boto3.resource('dynamodb')

    files_table = dynamodb.Table(os.environ['FILES_DYNAMO_TABLE'])

    # Check if the item already exists
    response = files_table.get_item(
        Key={
            'id': key
        }
    )

    if 'Item' in response and response['Item'].get('createdBy') != current_user:
        # Item already exists, return some error or existing key
        return {
            'success': False,
            'message': 'Item already exists'
        }

    # Item does not exist, proceed with insertion
    files_table.put_item(
        Item={
            'id': key,
            'name': name,
            'type': dtype,
            'tags': tags,
            'data': data_props,
            'knowledgeBase': kb,
            'createdAt': datetime.now().isoformat(),
            'updatedAt': datetime.now().isoformat(),
            'createdBy': current_user,
            'updatedBy': current_user
        }
    )

    if tags is not None and len(tags) > 0:
        update_file_tags(current_user, key, tags)

    return key


@op(
    path="/files/upload",
    name="getUploadUrl",
    tags=["files"],
    description="Get a url to upload a file to.",
    params={
        "name": "The name of the file to upload.",
        "type": "The mime type of the file to upload as a string.",
        "tags": "The tags associated with the file as a list of strings.",
        "data": "The data associated with the file or an empty dictionary.",
        "knowledgeBase": "The knowledge base associated with the file. You can put 'default' if you don't have a "
                         "specific knowledge base. "
    }
)

def create_and_store_fernet_key():
    # Read the parameter name from environment variable
    parameter_name = os.getenv('FILE_UPLOAD_ENCRYPTION_PARAMETER')
    
    if not parameter_name:
        raise ValueError("The environment variable FILE_UPLOAD_ENCRYPTION_PARAMETER is not set")
    
    # Generate a new Fernet key
    key = Fernet.generate_key()
    key_str = key.decode()  # Converting byte key to string

    # Initialize the SSM client
    ssm_client = boto3.client('ssm')
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    ssm_parameter_name=f"{parameter_name}_{timestamp}"
    # Store the key in the SSM Parameter Store
    try:
        response = ssm_client.put_parameter(
            
            Name=ssm_parameter_name,
            Value=key_str,
            Type='SecureString',
            Overwrite=False  # Allow overwriting the main parameter
        )
        print(f"Fernet key successfully stored in {ssm_parameter_name}")
        
        # Create a backup parameter name with a timestamp
        
        backup_parameter_name = f"{parameter_name}_backup_{timestamp}"
        print(f"Creating backup parameter {backup_parameter_name}")
        
        backup_response = ssm_client.put_parameter(
            Name=backup_parameter_name,
            Value=key_str,
            Type='SecureString',
            Overwrite=False  # Do not allow overwriting the backup parameter
        )
        print(f"Backup Fernet key successfully stored in {backup_parameter_name}")
        
        return response, backup_response

    except Exception as e:
        print(f"Error storing the Fernet key: {str(e)}")
        raise

def parameter_exists(ssm_client, parameter_name):
    try:
        ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
        return True
    except ssm_client.exceptions.ParameterNotFound:
        return False
    except Exception as e:
        raise RuntimeError(f"Error checking parameter existence: {str(e)}")


def encrypt_account_data(data):
    ssm_client = boto3.client('ssm')
    parameter_name = os.getenv('FILE_UPLOAD_ENCRYPTION_PARAMETER')
    print("Parameter name being accessed:", parameter_name)
    print("Enter encrypt account data")

    if not parameter_name:
        raise ValueError("The environment variable FILE_UPLOAD_ENCRYPTION_PARAMETER is not set")

    try:
        # Check if the parameter exists
        if not parameter_exists(ssm_client, parameter_name):
            print(f"Parameter {parameter_name} does not exist. Creating it now.")
            create_and_store_fernet_key()
        
        # Fetch the parameter securely, which holds the encryption key
        response = ssm_client.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )
        # The key needs to be a URL-safe base64-encoded 32-byte key
        key = response['Parameter']['Value'].encode()
        # Ensure the key is in the correct format for Fernet
        fernet = Fernet(key)

        data_str = json.dumps(data)
        # Encrypt the data
        encrypted_data = fernet.encrypt(data_str.encode())
        encrypted_data_b64 = base64.b64encode(encrypted_data).decode('utf-8')

        # print("Encrypted value:", encrypted_data_b64)
        return {
            'success': True,
            'encrypted_data': encrypted_data_b64
        }

    except Exception as e:
        print(f"Error during parameter retrieval or encryption for {parameter_name}: {str(e)}")
        return {
            'success': False,
            'error': f"Error {str(e)}"
        }
@validated(op="upload")
def get_presigned_url(event, context, current_user, name, data):
    access = data['allowed_access']
    if ('file_upload' not in access and 'full_access' not in access):
        print("User does not have access to the file_upload functionality")
        return {'success': False, 'error': 'User does not have access to the file_upload functionality'}
    
    # we need the perms to be under the groupId if applicable
    groupId = data['data'].get('groupId', None) 
    if (groupId): 
        print("GroupId ds upload: ", groupId)
        current_user = groupId
    account_data = { "user": current_user,
                     "account" : data['account'],
                     "api_key" : data['access_token'] if data['api_accessed'] else None
                    } 
    # encrypted data using parameter 
    e_result = encrypt_account_data(account_data)
    encrypted_metadata = e_result['encrypted_data'] if e_result['success'] else ""
    
    # print(f"Data is {data}")
    data = data['data']

    s3 = boto3.client('s3')

    name = data['name']
    name = re.sub(r'[_\s]+', '_', name)
    file_type = data['type']
    tags = data['tags']
    props = data['data']
    knowledge_base = data['knowledgeBase']
    
    print(
        f"\nGetting presigned URL for {name} of type {type} with tags {tags} and data {data} and knowledge base {knowledge_base}")

    # Set the S3 bucket and key
    bucket_name, key = create_file_metadata_entry(current_user, name, file_type, tags, props, knowledge_base)
    print(f"Created metadata entry for file {key} in bucket {bucket_name}")

    # Generate a presigned URL for uploading the file to S3
    presigned_url = s3.generate_presigned_url(
        ClientMethod='put_object',
        Params={
            'Bucket': bucket_name,
            'Key': key,
            'ContentType': file_type,
            'Metadata': {'encrypted_metadata': encrypted_metadata}
            # Add any additional parameters like ACL, ContentType, etc. if needed
        },
        ExpiresIn=3600  # Set the expiration time for the presigned URL, in seconds
    )

    if file_type in IMAGE_FILE_TYPES:
        print("Generating presigned urls for Image file")
        metadata_key = key + ".metadata.json"
        presigned_metadata_url = s3.generate_presigned_url(
                                ClientMethod='get_object',
                                Params={
                                    'Bucket': bucket_name,
                                    'Key': metadata_key
                                },
                                ExpiresIn=3600 
        )
        return {'success': True,
                'uploadUrl': presigned_url,
                'metadataUrl': presigned_metadata_url,
                'key': key}

    [file_text_content_bucket_name, text_content_key] = rag.util.get_text_content_location(bucket_name, key)

    print(f"Getting presigned URL for text content {text_content_key} in bucket {file_text_content_bucket_name}")

    presigned_text_status_content_url = s3.generate_presigned_url(
        ClientMethod='head_object',
        Params={
            'Bucket': file_text_content_bucket_name,
            'Key': text_content_key
        },
        ExpiresIn=3600  # Set the expiration time for the presigned URL, in seconds
    )

    presigned_text_content_url = s3.generate_presigned_url(
        ClientMethod='get_object',
        Params={
            'Bucket': file_text_content_bucket_name,
            'Key': text_content_key
        },
        ExpiresIn=3600  # Set the expiration time for the presigned URL, in seconds
    )

    [file_text_metadata_bucket_name, text_metadata_key] = rag.util.get_text_metadata_location(bucket_name, key)

    presigned_text_metadata_url = s3.generate_presigned_url(
        ClientMethod='get_object',
        Params={
            'Bucket': file_text_metadata_bucket_name,
            'Key': text_metadata_key
        },
        ExpiresIn=3600  # Set the expiration time for the presigned URL, in seconds
    )

    if presigned_url:
        return {'success': True,
                'uploadUrl': presigned_url,
                'statusUrl': presigned_text_status_content_url,
                'contentUrl': presigned_text_content_url,
                'metadataUrl': presigned_text_metadata_url,
                'key': key}
    else:
        return {'success': False}
    

@op(
    path="/files/tags/list",
    name="listTagsForUser",
    tags=["files"],
    description="Get a list of all tags that can be added to files or used to search for groups of files.",
    params={
    }
)
@validated(op="list")
def list_tags_for_user(event, context, current_user, name, data):
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['USER_TAGS_DYNAMO_TABLE'])

    try:
        # Retrieve the item corresponding to the user
        response = table.get_item(
            Key={'user': current_user}
        )
        # Check if 'Item' key is in the response which indicates a result was returned
        if 'Item' in response:
            user_tags = response['Item'].get('tags', [])
            print(f"Tags for user ID '{current_user}': {user_tags}")
            return {
                'success': True,
                'data': {'tags': user_tags}
            }
        else:
            print(f"No tags found for user ID '{current_user}'.")
            return {
                'success': True,
                'data': {'tags': []}
            }
    except ClientError as e:
        print(f"Error getting tags for user ID '{current_user}': {e.response['Error']['Message']}")
        return {
            'success': False,
            'data': {'tags': []}
        }


@op(
    path="/files/tags/delete",
    name="deleteTagForUser",
    tags=["files"],
    description="Delete a tag from the list of the user's tags.",
    params={
        "tag": "The tag to delete."
    }
)
@validated(op="delete")
def delete_tag_from_user(event, context, current_user, name, data):
    data = data['data']
    tag_to_delete = data['tag']

    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['USER_TAGS_DYNAMO_TABLE'])

    try:
        # Update the item to delete the tag from the set of tags
        response = table.update_item(
            Key={'user': current_user},  # Assumes that `current_user` holds the user ID
            UpdateExpression="DELETE #tags :tag",
            ExpressionAttributeNames={
                '#tags': 'tags',  # Assumes 'Tags' is the name of the attribute
            },
            ExpressionAttributeValues={
                ':tag': set([tag_to_delete])  # The tag to delete, must be a set
            },
            ReturnValues="UPDATED_NEW"
        )
        print(f"Tag '{tag_to_delete}' deleted successfully from user ID: {current_user}")
        return {
            'success': True,
            'message': "Tag deleted successfully"
        }

    except boto3.client('dynamodb').exceptions.ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == "ValidationException" and "provided key element does not match" in e.response['Error'][
            'Message']:
            print(f"User ID: {current_user} does not exist or tag does not exist.")
            return {
                'success': False,
                'message': "User ID does not exist or tag does not exist"
            }
        else:
            return {
                'success': False,
                'message': e.response['Error']['Message']
            }


@op(
    path="/files/tags/create",
    name="createTagsForUser",
    tags=["files"],
    description="Create one or more tags for the user that can be added to files.",
    params={
        "tags": "A list of string tags to create for the user."
    }
)
@validated(op="create")
def create_tags(event, context, current_user, name, data):
    data = data['data']
    tags_to_add = data['tags']

    # Call the helper function to add tags to the user
    return add_tags_to_user(current_user, tags_to_add)


def add_tags_to_user(current_user, tags_to_add):
    """Add a tag to user's list of tags if it doesn't already exist."""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['USER_TAGS_DYNAMO_TABLE'])

    try:
        response = table.update_item(
            Key={'user': current_user},
            UpdateExpression="ADD #tags :tags",
            ExpressionAttributeNames={
                '#tags': 'tags',  # Assuming 'Tags' is the name of the attribute
            },
            ExpressionAttributeValues={
                ':tags': set(tags_to_add)  # The tags to add as a set
            },
            ReturnValues="UPDATED_NEW"
        )
        print(f"Tags added successfully to user ID: {current_user}")
        return {
            'success': True,
            'message': "Tags added successfully"
        }

    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == "ValidationException":
            # If the item doesn't exist, create it with the specified tags
            response = table.put_item(
                Item={
                    'UserID': current_user,
                    'tags': set(tags_to_add)
                }
            )
            print(f"New user created with tags for user ID: {current_user}")
            return {
                'success': True,
                'message': "Tags added successfully"
            }
        else:
            print(f"Error adding tags to user ID: {current_user}: {e.response['Error']['Message']}")
            return {
                'success': False,
                'message': e.response['Error']['Message']
            }


@op(
    path="/files/tags/set_tags",
    name="setTagsForFile",
    tags=["files"],
    description="Set a file's list of tags.",
    params={
        "id": "The ID of the file to set tags for.",
        "tags": "A list of string tags to set for the file."
    }
)
@validated(op="set_tags")
def update_item_tags(event, context, current_user, name, data):
    data = data['data']
    item_id = data['id']
    tags = data['tags']

    # Call the helper function to update tags and add them to the user.
    success, message = update_file_tags(current_user, item_id, tags)

    return {"success": success, "message": message}


def update_file_tags(current_user, item_id, tags):
    # Helper function that updates tags in DynamoDB and adds tags to the user
    table_name = os.environ['FILES_DYNAMO_TABLE']  # Get the table name from the environment variable

    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)

    try:
        response = table.get_item(Key={'id': item_id})
        item = response.get('Item')

        if item and item.get('createdBy') == current_user:
            # Update the item's tags in DynamoDB
            table.update_item(
                Key={'id': item_id},
                UpdateExpression="SET tags = :tags",
                ExpressionAttributeValues={
                    ':tags': tags
                }
            )

            # Add tags to the user
            tags_added = add_tags_to_user(current_user, tags)
            if tags_added['success']:
                return True, "Tags updated and added to user"
            else:
                return False, f"Error adding tags to user: {tags_added['message']}"

        else:
            return False, "File not found or not authorized to update tags"

    except ClientError as e:
        print(f"Unable to update tags: {e.response['Error']['Message']}")
        return False, "Unable to update tags"


@op(
    path="/files/query",
    name="queryFilesByNameAndType",
    tags=["files"],
    description="Search a user's list of files with a query.",
    params={
        "namePrefix": "The prefix to search for in the file names.",
        "types": "A list of file mime types (e.g., 'application/pdf', 'text/plain', etc.) and must not be empty.",
    }
)
@op(
    path="/files/query",
    name="queryFilesByName",
    tags=["files"],
    description="Search a user's list of files with a query.",
    params={
        "namePrefix": "The prefix to search for in the file names."
    }
)
@op(
    path="/files/query",
    name="queryFilesByTags",
    tags=["files"],
    description="Search a user's list of files with a query.",
    params={
        "tags": "A list of tags to search for or an empty list.",
    }
)
@validated(op="query")
def query_user_files(event, context, current_user, name, data):
    print(f"Querying user files for {current_user}")
    # Extract the query parameters from the event
    query_params = data['data']

    # Map the provided sort key to the corresponding index name
    sort_index = query_params.get('sortIndex', 'createdAt')
    sort_index_lookup = {
        'createdAt': 'createdByAndAt',
        'name': 'createdByAndName',
        'type': 'createdByAndType'
    }
    index_name = sort_index_lookup.get(sort_index, 'createdByAndAt')

    # Extract the pagination and filtering parameters
    start_date = query_params.get('startDate', '2021-01-01T00:00:00Z')
    page_size = query_params.get('pageSize', 10)
    exclusive_start_key = query_params.get('pageKey')
    name_prefix = query_params.get('namePrefix')
    created_at_prefix = query_params.get('createdAtPrefix')
    type_prefix = query_params.get('typePrefix')
    type_filters = query_params.get('types')
    tag_search = query_params.get('tags', None)
    page_index = query_params.get('pageIndex', 0)
    forward_scan = query_params.get('forwardScan', False)

    # Determine the sort key and begins_with attribute based on sort_index
    sort_key_name = 'createdAt' if sort_index == 'createdAt' else sort_index

    sort_key_value_start = None
    # Initialize a list to hold any begins_with filters
    begins_with_filters = []

    # Determine the begins_with filters based on provided prefixes and the sort index
    if name_prefix:
        if sort_index == "name":
            sort_key_value_start = name_prefix
        else:
            begins_with_filters.append({'attribute': 'name', 'value': name_prefix, 'expression': 'contains'})

    if created_at_prefix:
        if sort_index == "createdAt":
            sort_key_value_start = created_at_prefix
        else:
            begins_with_filters.append(
                {'attribute': 'createdAt', 'value': created_at_prefix, 'expression': 'begins_with'})

    if type_prefix:
        if sort_index == "type":
            sort_key_value_start = type_prefix
        else:
            begins_with_filters.append({'attribute': 'type', 'value': type_prefix, 'expression': 'begins_with'})

    if tag_search:
        begins_with_filters.append({'attribute': 'tags', 'value': tag_search, 'expression': 'contains'})

    # Print all of the params (for debugging purposes)
    print(f"Querying user files with the following parameters: "
          f"start_date={start_date}, "
          f"page_size={page_size}, "
          f"exclusive_start_key={exclusive_start_key}, "
          f"name_prefix={name_prefix}, "
          f"created_at_prefix={created_at_prefix}, "
          f"type_prefix={type_prefix}, "
          f"type_filters={type_filters}, "
          f"tag_search={tag_search}, "
          f"page_index={page_index}"
          f"forward_scan={forward_scan}"
          f"sort_index={index_name}")

    # Use 'query_table_index' as the refactored function with new parameters
    result = query_table_index(
        table_name=os.environ['FILES_DYNAMO_TABLE'],
        index_name=index_name,
        partition_key_name='createdBy',
        sort_key_name=sort_key_name,
        partition_key_value=current_user,
        sort_key_value_start=sort_key_value_start,
        filters=begins_with_filters,
        type_filters=type_filters,
        exclusive_start_key=exclusive_start_key,
        page_size=page_size,
        forward_scan=forward_scan
    )

    # Extract and process results from 'result' as necessary before returning
    # This may include handling pagination, converting 'Items' to a more readable format, etc.

    # Return the processed result
    return result


def query_table_index(table_name, index_name, partition_key_name, sort_key_name, partition_key_value,
                      sort_key_value_start=None, filters=None, type_filters=None, exclusive_start_key=None,
                      page_size=10,
                      forward_scan=False):
    """
    Do not allow the client to directly provide the table_name, index_name, partition_key_name,
    or any of the attribute value names in the filters. This is not a safe function to directly
    expose, just like you wouldn't expose the raw query interface of Dynamo.

    :param table_name:
    :param index_name:
    :param partition_key_name:
    :param sort_key_name:
    :param partition_key_value:
    :param sort_key_value_start:
    :param filters:
    :param exclusive_start_key:
    :param page_size:
    :param forward_scan:
    :return:
    """
    dynamodb = boto3.client('dynamodb')

    # Initialize the key condition expression for the partition key
    key_condition_expression = f"{partition_key_name} = :partition_key_value"
    expression_attribute_values = {':partition_key_value': {'S': partition_key_value}}
    expression_attribute_names = {}

    if sort_key_value_start is not None:
        # Placeholder for sort key to handle reserved words
        sort_key_placeholder = f"#{sort_key_name}"
        expression_attribute_names[sort_key_placeholder] = sort_key_name
        key_condition_expression += f" AND {sort_key_placeholder} >= :sort_key_value_start"
        expression_attribute_values[':sort_key_value_start'] = {'S': sort_key_value_start}

    # Prepare the query parameters
    query_params = {
        'TableName': table_name,
        'IndexName': index_name,
        'KeyConditionExpression': key_condition_expression,
        'ExpressionAttributeValues': expression_attribute_values,
        'ScanIndexForward': forward_scan
    }

    # Add filter expression if begins_with_filters are provided
    filter_expressions = []

    if type_filters is not None:
        type_filter_expressions = []
        for index, type_filter in enumerate(type_filters):
            type_filter_expressions.append(f"#type_f = :type_value_{index}")
            # Assuming the type values are strings
            expression_attribute_values[f":type_value_{index}"] = {'S': type_filter}

        expression_attribute_names["#type_f"] = "type"
        type_filter_expression = " OR ".join(type_filter_expressions)
        filter_expressions.append(type_filter_expression)

    if filters:
        for filter_def in filters:
            attr_name = filter_def['attribute']
            attr_values = filter_def['value']
            attr_op = filter_def['expression']

            # If attr_values is a single value, make it a list to standardize processing
            if not isinstance(attr_values, list):
                attr_values = [attr_values]

            # Create placeholders for attribute names
            attr_name_placeholder = f"#{attr_name}"
            expression_attribute_names[attr_name_placeholder] = attr_name

            # Create a separate contains condition for each value in the list
            for i, val in enumerate(attr_values):
                # Create placeholders for attribute values
                attr_value_placeholder = f":{attr_op}_value_{attr_name}_{i}"

                # Set the expression attribute values, conservatively assuming the values are strings
                expression_attribute_values[attr_value_placeholder] = {'S': str(val)}

                # Depending on the operation, add the correct filter expression
                if attr_op == 'begins_with' and len(attr_values) == 1:  # 'begins_with' can't be used with lists
                    filter_expressions.append(f"begins_with({attr_name_placeholder}, {attr_value_placeholder})")
                elif attr_op == 'contains':  # Check each value in the provided list
                    filter_expressions.append(f"contains({attr_name_placeholder}, {attr_value_placeholder})")

    # Join all filter expressions with AND (if any)
    if filter_expressions:
        query_params['FilterExpression'] = " AND ".join(filter_expressions)
        if len(expression_attribute_names) > 0:
            query_params['ExpressionAttributeNames'] = expression_attribute_names
        if len(expression_attribute_values) > 0:
            query_params['ExpressionAttributeValues'] = expression_attribute_values

    # Limit the query if there's no begins_with filter provided
    if not filter_expressions:
        query_params['Limit'] = page_size

    # Use exclusive_start_key if provided
    if exclusive_start_key:
        serializer = TypeSerializer()
        exclusive_start_key = {k: serializer.serialize(v) for k, v in exclusive_start_key.items()}
        query_params['ExclusiveStartKey'] = exclusive_start_key

    print(f"Query: {query_params}")

    # Query the DynamoDB table or index
    response = dynamodb.query(**query_params)

    items = [unmarshal_dynamodb_item(item) for item in response.get('Items', [])]
    last_evaluated_key = response.get('LastEvaluatedKey')
    if last_evaluated_key:
        last_evaluated_key = unmarshal_dynamodb_item(last_evaluated_key)

    return {
        'success': True,
        'data': {
            'items': items,
            'pageKey': last_evaluated_key
        }
    }


def unmarshal_dynamodb_item(item):
    deserializer = TypeDeserializer()
    # Unmarshal a DynamoDB item into a normal Python dictionary
    python_data = {k: deserializer.deserialize(v) for k, v in item.items()}
    return python_data


def query_user_files_by_created_at2(user, created_at_start, page_size, exclusive_start_key=None):
    # Initialize a boto3 DynamoDB client
    dynamodb = boto3.client('dynamodb')

    # Define the query parameters
    query_params = {
        'TableName': os.environ['FILES_DYNAMO_TABLE'],
        'IndexName': 'createdByAndAt',  # This is the name of the GSI
        'KeyConditionExpression': 'createdBy = :created_by AND createdAt >= :created_at_start',
        'ExpressionAttributeValues': {
            ':created_by': {'S': user},
            ':created_at_start': {'S': created_at_start}  # assuming 'createdAt' is a string timestamp
        },
        'Limit': page_size,
        'ScanIndexForward': True  # Set to False if you want to sort by createdAt in descending order
    }

    # If an `exclusive_start_key` is provided, add it to the parameters
    if exclusive_start_key:
        query_params['ExclusiveStartKey'] = exclusive_start_key

    # Query the DynamoDB GSI
    response = dynamodb.query(**query_params)

    # Extract the items and the last evaluated key for pagination
    items = response.get('Items', [])
    plain_items = [unmarshal_dynamodb_item(item) for item in items]
    last_evaluated_key = response.get('LastEvaluatedKey')

    if last_evaluated_key:
        last_evaluated_key = unmarshal_dynamodb_item(last_evaluated_key)

    # Return the result as items and the pagination key to continue the query
    return {
        'success': True,
        'data': {
            'items': plain_items,
            'pageKey': last_evaluated_key
        }
    }
