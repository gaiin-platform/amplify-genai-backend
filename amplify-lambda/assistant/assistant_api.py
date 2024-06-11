
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import json
import uuid
import time
from datetime import datetime
from functools import reduce
from io import BytesIO

import boto3
from botocore.exceptions import ClientError
from common.validate import HTTPException, validated
from common.secrets import get_secret_value
from boto3.dynamodb.conditions import Key
import os
from openai import OpenAI


dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

def get(dictionary, *keys):
    return reduce(lambda d, key: d.get(key, None) if isinstance(d, dict) else None, keys, dictionary)


def get_openai_client():
    openai_api_key = get_secret_value("OPENAI_API_KEY")
    client = OpenAI(
        api_key=openai_api_key
    )
    return client


def file_keys_to_file_ids(user_id, file_keys):
    bucket_name = os.environ['S3_RAG_INPUT_BUCKET_NAME']
    client = get_openai_client()

    for file_key in file_keys:
        file_key_user = file_key.split('/')[0]
        if '@' not in file_key_user or len(file_key_user) >= 6:
            return []

    file_ids = []
    for file_key in file_keys:

        print("Downloading file: {}/{} to transfer to OpenAI".format(bucket_name, file_key))
        # Use a BytesIO buffer to download the file directly into memory
        file_stream = BytesIO()
        s3.download_fileobj(bucket_name, file_key, file_stream)
        file_stream.seek(0) # Move to the beginning of the file-like object

        print("Uploading file to OpenAI: {}".format(file_key))

        # Create the file on OpenAI using the downloaded data
        response = client.files.create(
            file=file_stream,
            purpose="assistants"
        )

        print("Response: {}".format(response))
        # Here you might want to collect the file IDs into a list
        file_id = response.id
        if file_id:
            file_ids.append(file_id)

        # Important: Close the BytesIO object when done
        file_stream.close()

    return file_ids

def get_thread(thread_key, user_id):
    dynamodb = boto3.resource('dynamodb')
    threads_table = dynamodb.Table(os.environ['ASSISTANT_THREADS_DYNAMODB_TABLE'])

    # Fetch the thread item from DynamoDB
    try:
        response = threads_table.get_item(Key={'id': thread_key})
        if 'Item' not in response:
            return {'success': False, 'error': 'Thread not found'}

        item = response['Item']
        # Check user authorization
        if item['user'] != user_id:
            return {'success': False, 'error': 'Not authorized to access this thread'}

        # Extract the OpenAI thread ID from the item
        openai_thread_id = get(item, 'data','openai','threadId')

        if not openai_thread_id:
            return {'success': False, 'error': 'Thread not found'}

        # Return the thread info with thread_key and OpenAI thread ID
        return {
            'success': True,
            'thread_key': thread_key,
            'openai_thread_id': openai_thread_id
        }

    except ClientError as e:
        print(e.response['Error']['Message'])
        return {'success': False, 'error': str(e)}


def chat_with_assistant(current_user, assistant_id, messages, file_keys):

    assistant_info = get_assistant(assistant_id, current_user)
    if not assistant_info['success']:
        return assistant_info  # Return error if any

    client = get_openai_client()

    print(f"Assistant info: {assistant_info}")

    openai_assistant_id = assistant_info['openai_assistant_id']

    if not openai_assistant_id:
        return {'success': False, 'message': 'Assistant not found'}

    print("Creating OpenAI thread...")
    openai_thread_id = client.beta.threads.create().id

    # Making calls to add each individual message is expensive and a
    # bit silly when they all get listed as user messages. It is easier
    # to just combine their content.
    contentstr = ""
    for msg in messages:
        contentstr += ("\n\n" + msg['role'] + ": " + msg['content'])

    print(f"Adding message {msg['id']}")
    result = add_message_to_openai_thread(
        client,
        current_user,
        "",
        openai_thread_id,
        messages[0]['id'],
        contentstr,
        'user',
        file_keys
    )
    if not result['success']:
        return {'success': False, 'error': 'Failed to sync messages to thread.'}

    print(f"Running assistant {openai_assistant_id} on thread {openai_thread_id}")
    run = client.beta.threads.runs.create(
        thread_id=openai_thread_id,
        assistant_id=openai_assistant_id
    )

    tries = 28
    while tries > 0:
        print(f"Checking for the result of the run {run.id}")
        status = client.beta.threads.runs.retrieve(
            thread_id=openai_thread_id,
            run_id=run.id
        )
        print(f"Status {status.status}")
        if status.status == 'completed':
            break

        time.sleep(1)

    print(f"Fetching the messages from {openai_thread_id}")
    messages = client.beta.threads.messages.list(thread_id=openai_thread_id)


    print(f"Formatting messages")
    ourMessages = []
    for message in messages.data:

        existing_id = message.metadata.get('id', None)
        if existing_id:
            # Our message, skip it
            continue

        ourMessage = {
            'data': {
                'openai':{
                    'messageId': message.id,
                    'threadId': message.thread_id,
                    'runId': message.run_id,
                    'assistantId': message.assistant_id,
                    'createdAt': message.created_at,
                }, **message.metadata},
            'type': 'chat',
            'role': message.role
        }

        content = []
        contentStr = ""
        for item in message.content:
            if item.type == 'text':
                content.append({
                    'type': 'text',
                    'value': {
                        'annotations': item.text.annotations
                    }
                })
                contentStr += item.text.value

            elif item.type == 'image_file':
                content.append({
                    'type': 'text',
                    'value': {
                        'file': f"openai-file://{item.file_id.file_id}",
                    }
                })

        ourMessage['content'] = contentStr
        ourMessage['data']['content'] = content

        ourMessages.append(ourMessage)

    return {
        'success': True,
        'message': 'Chat completed successfully',
        'data': ourMessages  # Ensure this is the correct attribute containing message data
    }





def fetch_messages_for_thread(thread_key, user_id):
    # Get the thread using the extracted function
    thread_info = get_thread(thread_key, user_id)
    if not thread_info['success']:
        return thread_info  # Return error if any

    # Use the OpenAI thread ID to fetch messages
    openai_thread_id = thread_info['openai_thread_id']
    client = get_openai_client()
    messages = client.beta.threads.messages.list(thread_id=openai_thread_id)

    ourMessages = []
    for message in messages.data:
        ourMessage = {
            'data': {
                'openai':{
                    'messageId': message.id,
                    'threadId': message.thread_id,
                    'runId': message.run_id,
                    'assistantId': message.assistant_id,
                    'createdAt': message.created_at,
                }, **message.metadata},
            'type': 'chat',
            'role': message.role,
            'id': str(uuid.uuid4())
        }
        existing_id = message.metadata.get('id', None)
        if existing_id:
            ourMessage['id'] = existing_id

        content = []
        contentStr = ""
        for item in message.content:
            if item.type == 'text':
                content.append({
                    'type': 'text',
                    'value': {
                        'annotations': item.text.annotations
                    }
                })
                contentStr += item.text.value

            elif item.type == 'image_file':
                content.append({
                    'type': 'text',
                    'value': {
                        'file': f"openai-file://{item.file_id.file_id}",
                    }
                })

        ourMessage['content'] = contentStr
        ourMessage['data']['content'] = content

        ourMessages.append(ourMessage)


    # export interface Message {
    #     role: Role;
    #     content: string;
    #     id: string;
    #     type: string | undefined;
    #     data: any | undefined;
    # }

    # Need to handle the following:
    #
    # 1. Fetching file ids and converting to fileKeys pointing to s3 objects
    # 2. Conversion of openai threadId to our threadId
    # 3. Inserting the correct links and stuff in the annotations area
    # 4. Conversion of openai assistantId to our assitantId
    # 5. Conversion of openai runId to our runId

    # Return the messages
    return {
        'success': True,
        'message': 'Messages retrieved successfully',
        'data': ourMessages  # Ensure this is the correct attribute containing message data
    }


def fetch_run_status(run_key, user_id):
    dynamodb = boto3.resource('dynamodb')
    runs_table = dynamodb.Table(os.environ['THREAD_RUNS_DYNAMODB_TABLE'])

    # Fetch the run item from the DynamoDB table
    response = runs_table.get_item(Key={'id': run_key})
    if 'Item' not in response:
        return {'success': False, 'message': 'Run not found'}

    item = response['Item']
    # Auth check: verify ownership
    if item['user'] != user_id:
        return {'success': False, 'message': 'Not authorized to see this run'}

    # Extract the OpenAI-related IDs from the retrieved item
    run_id = item['data']['openai']['runId']
    thread_id = item['data']['openai']['threadId']

    # Retrieve the run status using the OpenAI Client
    client = get_openai_client()
    run = client.beta.threads.runs.retrieve(
        thread_id=thread_id,
        run_id=run_id
    )

    # Update the run status in the DynamoDB table if it has changed
    if run.status != item['status']:
        try:
            timestamp = int(time.time() * 1000)
            runs_table.update_item(
                Key={'id': run_key},
                UpdateExpression='SET #status = :status, updatedAt = :updatedAt',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': run.status, ':updatedAt': timestamp},
                ReturnValues='UPDATED_NEW'
            )
        except Exception as e:
            print(e)
            # Optionally return a message indicating that a failure occurred during the status update

    return {'success': True, 'message': 'Run status retrieved successfully', 'data': {'status': run.status}}


def get_assistant(assistant_id, current_user):
    dynamodb = boto3.resource('dynamodb')
    assistantstable = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])

    try:
        # Fetch the assistant from DynamoDB
        response = assistantstable.get_item(Key={'id': assistant_id})
        if 'Item' not in response:
            return {'success': False, 'error': 'Assistant not found'}

        assistant_item = response['Item']

        # Authorization check: the user making the request should own the assistant
        if assistant_item['user'] != current_user:
            return {'success': False, 'error': 'Not authorized to access this assistant'}

        # Extract the OpenAI assistant ID from the item
        openai_assistant_id = get(assistant_item, 'data', 'openai', 'assistantId')

        # If we have a valid OpenAI assistant ID, return the successful result
        if openai_assistant_id:
            return {
                'success': True,
                'assistant_key': assistant_id,
                'openai_assistant_id': openai_assistant_id
            }
        else:
            return {'success': False, 'error': 'Assistant not found'}

    except ClientError as e:
        # DynamoDB client error handling
        print(e.response['Error']['Message'])
        return {'success': False, 'error': str(e)}


def run_thread(thread_id, user_id, assistant_id):
    dynamodb = boto3.resource('dynamodb')
    threads_table = dynamodb.Table(os.environ['ASSISTANT_THREADS_DYNAMODB_TABLE'])
    assistantstable = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])
    runstable = dynamodb.Table(os.environ['THREAD_RUNS_DYNAMODB_TABLE'])

    # Get the thread info
    response = threads_table.get_item(Key={'id': thread_id})
    if 'Item' not in response:
        return {'success': False, 'message': 'Thread not found'}
    thread_item = response['Item']

    # Authorization check
    if thread_item['user'] != user_id:
        return {'success': False, 'message': 'You are not authorized to run this thread'}

    # Get the assistant info
    assistantResponse = assistantstable.get_item(Key={'id': assistant_id})
    if 'Item' not in assistantResponse:
        return {'success': False, 'message': 'Assistant not found'}
    assistant_item = assistantResponse['Item']

    # Another authorization check
    if assistant_item['user'] != user_id:
        return {'success': False, 'message': 'You are not authorized to run this assistant'}

    openai_thread_id = get(thread_item, 'data', 'openai', 'threadId') #item['data']['openai']['thread_id']
    openai_assistant_id = get(assistant_item, 'data', 'openai', 'assistantId') #item['data']['openai']['thread_id']

    # Running the assistant's thread
    print(f"Running thread: {thread_id}/{assistant_id}/=>openai=>{openai_thread_id}/{openai_assistant_id}")

    client = get_openai_client()
    run = client.beta.threads.runs.create(
        thread_id=openai_thread_id,
        assistant_id=openai_assistant_id
    )
    timestamp = int(time.time() * 1000)
    run_key = f'{user_id}/run/{str(uuid.uuid4())}'

    # DynamoDB new item to represent the run
    new_item = {
        'id': run_key,
        'data': {'openai': {'threadId': openai_thread_id, 'assistantId': openai_assistant_id, 'runId': run.id}},
        'thread': thread_id,
        'assistant': assistant_id,
        'user': user_id,
        'createdAt': timestamp,
        'updatedAt': timestamp,
        'status': run.status
    }
    runstable.put_item(Item=new_item)

    return {'success': True, 'message': 'Run started successfully', 'data': {'runId': run_key}}


def add_message_to_openai_thread(client, user_id, thread_id, openai_thread_id, message_id, content, role, file_keys, data={}):
    dynamodb = boto3.resource('dynamodb')

    for file_key in file_keys:
        file_key_user = file_key.split('/')[0]
        if '@' not in file_key_user or len(file_key_user) < 6 or user_id != file_key_user:
            return {'success': False, 'message': 'You are not authorized to access the referenced files'}

    message = client.beta.threads.messages.create(
        thread_id=openai_thread_id,
        role=role,
        content=content,
        metadata={**{'id': message_id}, **data},
        file_ids=file_keys_to_file_ids(user_id, file_keys)
    )

    # Return the result
    return {
        'success': True,
        'message': 'Message added successfully',
        'data': {
            'message':{
                'id': message_id,
                'data': {
                    'threadId': thread_id,
                    'openai': {'messageId': message.id}
                }
            }
        }
    }

def add_message_to_thread(user_id, thread_id, message_id, content, role, file_keys, data={}):
    dynamodb = boto3.resource('dynamodb')
    threads_table = dynamodb.Table(os.environ['ASSISTANT_THREADS_DYNAMODB_TABLE'])

    # Fetch the thread from DynamoDB
    response = threads_table.get_item(Key={'id': thread_id})
    if 'Item' not in response:
        return {'success': False, 'message': 'Thread not found'}

    # Authorization check
    item = response['Item']
    if item['user'] != user_id:
        return {'success': False, 'message': 'You are not authorized to modify this thread'}

    openai_thread_id = get(item, 'data', 'openai', 'threadId')

    # Ensure thread_id is valid
    print(f"Adding message to thread: {thread_id}")
    if not openai_thread_id:
        return {'success': False, 'message': 'Thread not found'}

    for file_key in file_keys:
        file_key_user = file_key.split('/')[0]
        if '@' not in file_key_user or len(file_key_user) < 6 or user_id != file_key_user:
            return {'success': False, 'message': 'You are not authorized to access the referenced files'}

    # Create a message for the thread
    client = get_openai_client()
    message = client.beta.threads.messages.create(
        thread_id=openai_thread_id,
        role=role,
        content=content,
        metadata={**{'id': message_id}, **data},
        file_ids=file_keys_to_file_ids(user_id, file_keys)
    )

    # Return the result
    return {
        'success': True,
        'message': 'Message added successfully',
        'data': {
            'message':{
                'id': message_id,
                'data': {
                    'threadId': thread_id,
                    'openai': {'messageId': message.id}
                }
            }
        }
    }


def delete_thread_by_id(thread_id, user_id):
    dynamodb = boto3.resource('dynamodb')
    threads_table = dynamodb.Table(os.environ['ASSISTANT_THREADS_DYNAMODB_TABLE'])

    # Fetch the thread from DynamoDB
    response = threads_table.get_item(Key={'id': thread_id})
    if 'Item' not in response:
        return {'success': False, 'message': 'Thread not found'}

    # Authorization check
    item = response['Item']
    if item['user'] != user_id:
        return {'success': False, 'message': 'You are not authorized to delete this thread'}


    openai_thread_id = get(item, 'data', 'openai', 'threadId')

    # Ensure thread_id is valid
    print(f"Deleting thread: {thread_id}=>openai=>{openai_thread_id}")
    if not openai_thread_id:
        return {'success': False, 'message': 'Thread not found'}

    # Delete the thread using the OpenAI Client
    client = get_openai_client()
    result = client.beta.threads.delete(openai_thread_id)

    if result.deleted:
        # If the delete operation was successful, delete the entry from DynamoDB as well
        threads_table.delete_item(Key={'id': thread_id})
        return {'success': True, 'message': 'Thread deleted successfully'}
    else:
        return {'success': False, 'message': 'Thread could not be deleted'}


def create_new_thread(user_id):
    dynamodb = boto3.resource('dynamodb')
    threads_table = dynamodb.Table(os.environ['ASSISTANT_THREADS_DYNAMODB_TABLE'])
    timestamp = int(time.time() * 1000)

    # Create a new thread using the OpenAI Client
    client = get_openai_client()
    thread = client.beta.threads.create()
    thread_key = f'{user_id}/thr/{str(uuid.uuid4())}'

    # DynamoDB new item structure for the thread
    new_item = {
        'id': thread_key,
        'data': {'openai': {'threadId': thread.id}},
        'user': user_id,
        'createdAt': timestamp,
        'updatedAt': timestamp,
    }

    # Put the new item into the DynamoDB table
    threads_table.put_item(Item=new_item)

    # Return success response
    return {
        'success': True,
        'message': 'Assistant thread created successfully',
        'data': {'threadId': thread_key}
    }


def create_new_assistant(
        user_id,
        assistant_name,
        description,
        instructions,
        tags,
        file_keys,
        tools
):
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])
    timestamp = int(time.time() * 1000)

    for file_key in file_keys:
        file_key_user = file_key.split('/')[0]
        if '@' not in file_key_user or len(file_key_user) < 6 or user_id != file_key_user:
            return {'success': False, 'message': 'You are not authorized to access the referenced files'}

    # Create a new assistant using the OpenAI Client
    client = get_openai_client()
    assistant = client.beta.assistants.create(
        name=assistant_name,
        instructions=instructions,
        tools=tools,
        model="gpt-4-1106-preview",
        file_ids=file_keys_to_file_ids(user_id, file_keys)
    )

    id_key = f'{user_id}/ast/{str(uuid.uuid4())}'

    # DynamoDB new item structure for the assistant
    new_item = {
        'id': id_key,
        'user': user_id,
        'assistant': assistant_name,
        'description': description,
        'instructions': instructions,
        'tags': tags,
        'createdAt': timestamp,
        'updatedAt': timestamp,
        'fileKeys': file_keys,
        'data': {'openai': {'assistantId': assistant.id}}
    }

    print(json.dumps(new_item, indent=4))

    # Put the new item into the DynamoDB table
    assistants_table.put_item(Item=new_item)

    # Return success response
    return {
        'success': True,
        'message': 'Assistant created successfully',
        'data': {'assistantId': id_key}
    }


def delete_assistant_by_id(assistant_id, user_id):
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])

    # Check if the assistant belongs to the user
    try:
        response = assistants_table.get_item(Key={'id': assistant_id})
    except ClientError as e:
        print(e.response['Error']['Message'])
        return {'success': False, 'message': 'Assistant not found'}

    if 'Item' not in response:
        return {'success': False, 'message': 'Assistant not found'}

    item = response['Item']

    # Auth check: verify ownership
    if item['user'] != user_id:
        return {'success': False, 'message': 'Not authorized to delete this assistant'}

    # Retrieve the OpenAI assistant ID
    openai_assistant_id = item['data']['openai']['assistantId']  # Or use your `get` utility function

    # Delete the assistant from OpenAI
    client = get_openai_client()
    try:
        assistant_deletion_result = client.beta.assistants.delete(assistant_id=openai_assistant_id)
    except Exception as e:
        return {'success': False, 'message': f'Failed to delete OpenAI assistant: {e}'}

    if not assistant_deletion_result.deleted:
        return {'success': False, 'message': 'Failed to delete OpenAI assistant'}

    # Delete the assistant record in DynamoDB
    try:
        assistants_table.delete_item(Key={'id': assistant_id})
    except ClientError as e:
        print(e.response['Error']['Message'])
        return {'success': False, 'message': 'Failed to delete assistant record from database'}

    return {'success': True, 'message': 'Assistant deleted successfully'}