import uuid
from datetime import datetime
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
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

    download_filename = response['Item']['name']

    response_headers = {
        'ResponseContentDisposition': f'attachment; filename="{download_filename}"'
    } if download_filename else {}

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


@validated(op="set_tags")
def update_item_tags(event, context, current_user, name, data):
    table_name = os.environ['FILES_DYNAMO_TABLE']  # Get the table name from the environment variable

    data = data['data']
    item_id = data['id']
    tags = data['tags']

    dynamodb = boto3.resource('dynamodb')  # Create a DynamoDB resource using boto3
    table = dynamodb.Table(table_name)  # Select the table

    try:
        response = table.get_item(Key={'id': item_id})
        item = response.get('Item')

        if item and item.get('createdBy') == current_user:
            update_response = table.update_item(
                Key={'id': item_id},
                UpdateExpression="SET tags = :tags",
                ExpressionAttributeValues={
                    ':tags': tags
                }
            )
            return {"success": True, "message": "Tags updated"}
        else:
            return {"success": False, "message": "File not found"}

    except ClientError as e:
        return {"success": False, "message": e.response['Error']['Message']}


@validated(op="query")
def query_user_files(event, context, current_user, name, data):
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
        exclusive_start_key=exclusive_start_key,
        page_size=page_size,
        forward_scan=forward_scan
    )

    # Extract and process results from 'result' as necessary before returning
    # This may include handling pagination, converting 'Items' to a more readable format, etc.

    # Return the processed result
    return result


def query_table_index(table_name, index_name, partition_key_name, sort_key_name, partition_key_value,
                      sort_key_value_start=None, filters=None, exclusive_start_key=None, page_size=10,
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
        query_params['ExpressionAttributeNames'] = expression_attribute_names
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
