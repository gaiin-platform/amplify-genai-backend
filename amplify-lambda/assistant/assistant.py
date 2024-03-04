import uuid
from datetime import datetime
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeDeserializer
from common.validate import validated
import os
from . import assistant_api as assistants
import boto3
import rag.util


@validated(op="get_messages")
def get_messages_assistant_thread(event, context, current_user, name, data):
    thread_key = data['data']['id']
    # Assuming get_openai_client function is defined elsewhere
    return assistants.fetch_messages_for_thread(thread_key, current_user)


@validated(op="run_status")
def get_run_status_assistant_thread(event, context, current_user, name, data):
    run_key = data['data']['id']
    # Assuming get_openai_client function is defined elsewhere
    return assistants.fetch_run_status(run_key, current_user)


@validated(op="run")
def run_assistant_thread(event, context, current_user, name, data):
    thread_id = data['data']['id']
    assistant_id = data['data']['assistantId']

    # Assuming get_openai_client is defined elsewhere and provides a client instance
    return assistants.run_thread(thread_id, current_user, assistant_id)


@validated(op="chat")
def chat_with_assistant(event, context, current_user, name, data):
    assistant_id = data['data'].get('id')
    messages = data['data'].get('messages')
    file_keys = data['data'].get('fileKeys')

    return assistants.chat_with_assistant(
        current_user,
        assistant_id,
        messages,
        file_keys
    )


@validated(op="add_message")
def add_message_assistant_thread(event, context, current_user, name, data):
    thread_id = data['data'].get('id')
    content = data['data'].get('content')
    message_id = data['data'].get('messageId')
    role = data['data'].get('role')
    file_keys = data['data'].get('fileKeys', [])
    metadata = data['data'].get('data', {})

    # Assuming get_openai_client and file_keys_to_file_ids are defined elsewhere
    # and both provide their respective functionality
    return assistants.add_message_to_thread(
        current_user,
        thread_id,
        message_id,
        content,
        role,
        file_keys,
        metadata
    )


@validated(op="delete")
def delete_assistant_thread(event, context, current_user, name, data):
    thread_id = data['data'].get('id')

    # Assuming get_openai_client is defined elsewhere and provides an instance of the OpenAI client
    return assistants.delete_thread_by_id(thread_id, current_user)


@validated(op="create")
def create_assistant_thread(event, context, current_user, name, data):
    # Assuming get_openai_client function is defined elsewhere
    return assistants.create_new_thread(current_user)


@validated(op="create")
def create_assistant(event, context, current_user, name, data):
    extracted_data = data['data']
    assistant_name = extracted_data['name']
    description = extracted_data['description']
    tags = extracted_data.get('tags', [])
    instructions = extracted_data['instructions']
    file_keys = extracted_data.get('fileKeys', [])
    tools = extracted_data.get('tools', [])

    # Assuming get_openai_client and file_keys_to_file_ids functions are defined elsewhere
    return assistants.create_new_assistant(
        user_id=current_user,
        assistant_name=assistant_name,
        description=description,
        instructions=instructions,
        tags=tags,
        file_keys=file_keys,
        tools=tools
    )


@validated(op="delete")
def delete_assistant(event, context, current_user, name, data):
    assistant_id = data['data']['id']
    print(f"Deleting assistant: {assistant_id}")

    # Assuming get_openai_client function is defined elsewhere
    return assistants.delete_assistant_by_id(assistant_id, current_user)


@validated(op="download")
def get_presigned_download_url(event, context, current_user, name, data):
    data = data['data']
    key = data['key']

    if "://" in key:
        key = key.split("://")[1]

    dynamodb = boto3.resource('dynamodb')
    s3 = boto3.client('s3')
    bucket_name = os.environ['S3_RAG_INPUT_BUCKET_NAME']
    files_table_name = os.environ['FILES_DYNAMO_TABLE']

    # Access the specific table
    files_table = dynamodb.Table(files_table_name)

    print(f"Getting presigned download URL for {key} for user {current_user}")

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

    if response['Item']['createdBy'] != current_user:
        # User doesn't match or item doesn't exist
        print(f"User doesn't match for file for {current_user}: {response['Item']}")
        return {'success': False, 'message': 'File not found'}

    # If the user matches, generate a presigned URL for downloading the file from S3
    try:
        presigned_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={
                'Bucket': bucket_name,
                'Key': key
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


@validated(op="upload")
def get_presigned_url(event, context, current_user, name, data):
    print(f"Data is {data}")
    data = data['data']

    dynamodb = boto3.resource('dynamodb')
    s3 = boto3.client('s3')
    # Retrieve the uploaded file from the Lambda event
    table = dynamodb.Table(os.environ['DYNAMODB_TABLE'])

    name = data['name']
    type = data['type']
    tags = data['tags']
    props = data['data']
    knowledge_base = data['knowledgeBase']

    print(
        f"Getting presigned URL for {name} of type {type} with tags {tags} and data {data} and knowledge base {knowledge_base}")

    # Set the S3 bucket and key
    bucket_name = os.environ['S3_RAG_INPUT_BUCKET_NAME']
    dt_string = datetime.now().strftime('%Y-%m-%d')
    key = '{}/{}/{}.json'.format(current_user, dt_string, str(uuid.uuid4()))

    files_table = dynamodb.Table(os.environ['FILES_DYNAMO_TABLE'])
    files_table.put_item(
        Item={
            'id': key,
            'name': name,
            'type': type,
            'tags': tags,
            'data': props,
            'knowledgeBase': knowledge_base,
            'createdAt': datetime.now().isoformat(),
            'updatedAt': datetime.now().isoformat(),
            'createdBy': current_user,
            'updatedBy': current_user
        }
    )

    # Generate a presigned URL for uploading the file to S3
    presigned_url = s3.generate_presigned_url(
        ClientMethod='put_object',
        Params={
            'Bucket': bucket_name,
            'Key': key,
            'ContentType': type
            # Add any additional parameters like ACL, ContentType, etc. if needed
        },
        ExpiresIn=3600  # Set the expiration time for the presigned URL, in seconds
    )

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


# @validated(op="query")
# def query_user_files(event, context, current_user, name, data):
#     # Extract the query parameters from the event
#     query_params = data['data']
#
#     # Extract the pagination parameters
#     start_date = query_params.get('startDate', '2021-01-01T00:00:00Z')
#     page_size = query_params.get('pageSize', 10)
#     exclusive_start_key = query_params.get('pageKey')
#
#     # Query the user's files from the DynamoDB table
#     result = query_user_files_by_created_at(current_user, start_date, page_size, exclusive_start_key)
#
#     # Return the result
#     return result
@validated(op="query")
def query_user_files(event, context, current_user, name, data):
    # Extract the query parameters from the event
    query_params = data['data']

    # Extract the pagination and filtering parameters
    start_date = query_params.get('startDate', '2021-01-01T00:00:00Z')
    page_size = query_params.get('pageSize', 10)
    exclusive_start_key = query_params.get('pageKey')
    name_prefix = query_params.get('namePrefix')
    created_at_prefix = query_params.get('createdAtPrefix')
    type_prefix = query_params.get('typePrefix')
    tag_filter_list = query_params.get('tags', [])
    page_index = query_params.get('pageIndex', 0)
    forward_scan = query_params.get('forwardScan', False)

    # print all of the params
    print(f"Querying user files with the following parameters: "
          f"start_date={start_date}, "
          f"page_size={page_size}, "
          f"exclusive_start_key={exclusive_start_key}, "
          f"name_prefix={name_prefix}, "
          f"created_at_prefix={created_at_prefix}, "
          f"type_prefix={type_prefix}, "
          f"tag_filter_list={tag_filter_list}, "
          f"page_index={page_index}"
          f"forward_scan={forward_scan}")

    # Use 'query_user_files_by_created_at' as the updated function with new parameters
    result = query_user_files_by_created_at(
        user=current_user,
        created_at_start=start_date,
        name_prefix=name_prefix,
        created_at_prefix=created_at_prefix,
        type_prefix=type_prefix,
        tag_filter_list=tag_filter_list,
        page_index=page_index,
        page_size=page_size,
        exclusive_start_key=exclusive_start_key,
        forward_scan=forward_scan
    )

    # Return the result
    return result


def unmarshal_dynamodb_item(item):
    deserializer = TypeDeserializer()
    # Unmarshal a DynamoDB item into a normal Python dictionary
    python_data = {k: deserializer.deserialize(v) for k, v in item.items()}
    return python_data


def query_user_files_by_created_at(
        user,
        created_at_start,
        name_prefix=None,
        created_at_prefix=None,
        type_prefix=None,
        tag_filter_list=None,
        page_index=0,
        page_size=10,
        exclusive_start_key=None,
        forward_scan=False
):
    # Initialize a boto3 DynamoDB client
    dynamodb = boto3.client('dynamodb')

    # Compute the offset for pagination
    offset = page_size * page_index

    # Prepare the base query parameters
    query_params = {
        'TableName': os.environ['FILES_DYNAMO_TABLE'],
        'IndexName': 'createdByAndAt',
        'KeyConditionExpression': 'createdBy = :created_by AND createdAt >= :created_at_start',
        'ExpressionAttributeValues': {
            ':created_by': {'S': user},
            ':created_at_start': {'S': created_at_start}
        },
        'ScanIndexForward': forward_scan
    }

    if not name_prefix and not created_at_prefix and not type_prefix and not tag_filter_list:
        # If no filters are provided, use the basic query parameters
        query_params['Limit'] = page_size

    # Add exclusive_start_key to the query parameters if provided
    if exclusive_start_key:
        exclusive_start_key = {
            'createdBy': {'S': user},  # Assuming 'createdBy' is the partition key
            'id': {'S': exclusive_start_key}  # Assuming 'createdAt' is the sort key
        }
        query_params['ExclusiveStartKey'] = exclusive_start_key
        print(f"Using exclusive_start_key: {exclusive_start_key}")

    # Initialize placeholder dictionary and filter expression components
    expression_attribute_names = {}
    filter_expressions = []

    # Apply name prefix filter if provided
    if name_prefix:
        filter_expressions.append("begins_with(#name, :name_prefix)")
        query_params['ExpressionAttributeValues'][':name_prefix'] = {'S': name_prefix}
        expression_attribute_names['#name'] = 'name'

    # Apply created_at prefix filter if provided
    if created_at_prefix:
        filter_expressions.append("begins_with(#createdAt, :created_at_prefix)")
        query_params['ExpressionAttributeValues'][':created_at_prefix'] = {'S': created_at_prefix}
        expression_attribute_names['#createdAt'] = 'createdAt'

    # Apply type prefix filter if provided
    if type_prefix:
        filter_expressions.append("begins_with(#type, :type_prefix)")
        query_params['ExpressionAttributeValues'][':type_prefix'] = {'S': type_prefix}
        expression_attribute_names['#type'] = 'type'

    # Apply tag filter list if provided
    if tag_filter_list:
        tags_filters = " OR ".join(f"contains(#tags, :tag{index})" for index, tag in enumerate(tag_filter_list))
        filter_expressions.append(f"({tags_filters})")
        for index, tag in enumerate(tag_filter_list):
            query_params['ExpressionAttributeValues'][f":tag{index}"] = {'S': tag}
        expression_attribute_names['#tags'] = 'tags'

    # Only add expression attribute names to query_params if it's not empty
    if expression_attribute_names:
        query_params['ExpressionAttributeNames'] = expression_attribute_names

    # Join all filter expressions with 'AND' and add to query_params if any filters exist
    if filter_expressions:
        query_params['FilterExpression'] = " AND ".join(filter_expressions)

    # Query the DynamoDB GSI
    response = dynamodb.query(**query_params)

    # Extract the items and trim down to the page size
    items = response.get('Items', [])
    plain_items = [unmarshal_dynamodb_item(item) for item in items]
    # Only return the items for the requested page, discard the rest (used for estimate)
    page_items = plain_items[offset:offset + page_size]

    # Estimate the total number of items
    total_estimate = len(page_items) + offset

    # Pagination information
    last_evaluated_key = response.get('LastEvaluatedKey')
    if last_evaluated_key:
        last_evaluated_key = unmarshal_dynamodb_item(last_evaluated_key)

    # Return the results
    return {
        'success': True,
        'data': {
            'items': page_items,
            'pageKey': last_evaluated_key,
            'totalEstimate': total_estimate
        }
    }


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
