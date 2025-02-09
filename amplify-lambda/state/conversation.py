import json
import math
import os
import re
from common.validate import validated
from botocore.exceptions import BotoCoreError, ClientError
import boto3
import uuid
from datetime import datetime,timezone
from common.ops import op

def upload_to_s3(key, conversation, folder=None):
    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']

    try:
        s3.put_object(Bucket=conversations_bucket,
                      Key=key,
                      Body=json.dumps({"conversation":conversation, "folder":folder}))
        print(f"Successfully uploaded conversation to s3: {key}")
        return {'success' : True, 'message': "Succesfully uploaded conversation to s3"}
    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {'success' : False, 'message': "Failed to uploaded conversation to s3", 'error': str(e)}


@validated(op="conversation_upload")
def upload_conversation(event, context, current_user, name, data):
    data = data['data']
    conversation = data['conversation']
    conversation_id = data['conversationId']
    folder = data.get('folder', None)

    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']

    conversation_key = f"{current_user}/{conversation_id}" 
    return upload_to_s3(conversation_key, conversation, folder)


@op(
    path="/state/conversation/register",
    name="registerConversation",
    method="POST",
    tags=["apiDocumentation"],
    description="""Register a new conversation with messages and metadata.
    Example request:
    {
        "data": {
            "name": "My Conversation",
            "messages": [
                {
                    "role": "user",
                    "content": "Hello!",
                    "data": {}
                },
                {
                    "role": "assistant",
                    "content": "Hi there!",
                    "data": {}
                }
            ],
            "tags": ["important", "work"],
            "date": "2024-03-20",
            "data": {
                "customField": "value"
            }
        }
    }
    """,
    params={
        "name": "String. Required. Name of the conversation.",
        "messages": "Array. Required. List of message objects containing role (system/user/assistant), content, and data.",
        "tags": "Array. Optional. List of string tags for the conversation.",
        "date": "String. Optional. Date in YYYY-MM-DD format.",
        "data": "Object. Optional. Additional metadata for the conversation."
    }
)
@validated(op="conversation_upload")
def register_conversation(event, context, current_user, name, data):
    data = data['data']

    prepMessages = data['messages']

    for message in prepMessages:
        message['id'] = str(uuid.uuid4())
        message['type'] = 'chat'

    current_utc_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    conversation = {
        "id": data.get('id', str(uuid.uuid4())),
        "name": data['name'],
        "messages": prepMessages,
        "folderId": "agents",
        "tags": data.get('tags', []),
        "data": data.get('data', {}),
        "date": data.get('date', data.get('date', current_utc_time)),
        "isLocal": False
    }

    compressed_conversation = lzw_compress( json.dumps(conversation) )

    conversation_key = f"{current_user}/{conversation['id']}" 
    return upload_to_s3(conversation_key, compressed_conversation, None)



@validated("read")
def get_conversation(event, context, current_user, name, data):
    query_param =  get_conversation_query_param(event.get('queryStringParameters', {}))
    if (not query_param['success']): 
        return query_param["response"]
    
    conversation_id = query_param["query_value"]
    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']

    conversation_key = f"{current_user}/{conversation_id}"  

    try:
        response = s3.get_object(Bucket=conversations_bucket, Key=conversation_key)
        conversation_body = response['Body'].read().decode('utf-8')
        conversation_data = json.loads(conversation_body)
        return {'success': True, "conversation": conversation_data["conversation"]}
    
    except (BotoCoreError, ClientError) as e:
        error = {'success': False, 'message': "Failed to retrieve conversation from S3", 'error': str(e)}
        
        print(str(e))
        if e.response['Error']['Code'] == 'NoSuchKey':
            error["type"] = 'NoSuchKey'

        return  error


def pick_conversation_attributes(conversation):
    attributes = ['id', 'name', 'model', 'folderId', 'tags', 'isLocal', 'groupType', 'codeInterpreterAssistantId']
    return {attr: conversation.get(attr, None) for attr in attributes}


@validated("read")
def get_all_conversations(event, context, current_user, name, data):
    conversations = get_all_complete_conversations(current_user)
    if conversations == None:
        return {'success': False, 'message': "Failed to retrieve conversations from S3"}
    elif (len(conversations) == 0):
        return {'success': True, 'message': "No conversations saved to S3"}
    for item in conversations:
        if 'conversation' in item:
            item['conversation'] = pick_conversation_attributes(item['conversation'])

    presigned_urls = get_presigned_urls(current_user, conversations)
    return {'success': True, 'presignedUrls': presigned_urls}


@validated("read")
def get_empty_conversations(event, context, current_user, name, data):
    conversations = get_all_complete_conversations(current_user)
    if (not conversations):
        return {'success': False, 'message': "Failed to retrieve conversations from S3"}
    elif (len(conversations) == 0):
        return {'success': True, 'message': "No conversations saved to S3"}
    
    empty_conversations = []
    nonempty_conversations_ids = []
    for item in conversations:
        if ('conversation' in item and len(item['conversation']['messages']) == 0):
            empty_conversations.append( pick_conversation_attributes(item['conversation']) )
        else: 
            nonempty_conversations_ids.append(item['conversation']['id'] )

    presigned_urls = get_presigned_urls(current_user, empty_conversations)
    return {'success': True, 'presignedUrls': presigned_urls, 'nonEmptyIds': nonempty_conversations_ids}


def get_all_complete_conversations(current_user):
    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']
    user_prefix = current_user + '/'

    try:
        # List all objects in the bucket with the given prefix
        response = s3.list_objects_v2(Bucket=conversations_bucket, Prefix=user_prefix)
        if 'Contents' not in response:
            return []
        
        conversations = []
        print("Number of conversation in list obj: ", len(response['Contents']))
        for obj in response['Contents']:
            conversation_key = obj['Key']
            # Get each conversation object
            try:
                conversation_response = s3.get_object(Bucket=conversations_bucket, Key=conversation_key)
                conversation_body = conversation_response['Body'].read().decode('utf-8')
                conversation = json.loads(conversation_body)
                uncompressed_conversation = lzw_uncompress(conversation["conversation"])
                if (uncompressed_conversation):
                    conversations.append({
                        'conversation': uncompressed_conversation,
                        'folder': conversation["folder"]
                    })
                else:
                    print("Conversation failed to uncompress")
            except (BotoCoreError, ClientError) as e:
                print(f"Failed to retrieve : {obj} with error: {str(e)}")
        print("Number of conversations retrieved: ", len(conversations))

        return conversations

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return None


@validated("get_multiple_conversations")
def get_multiple_conversations(event, context, current_user, name, data):
    data = data['data']
    conversation_ids = data['conversationIds']

    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']
    user_prefix = current_user + '/'

    try:
        conversations = []
        failedToFetchConversations = []
        noSuchKeyConversations = []

        for id in conversation_ids:
            conversation_key = user_prefix + id
            # Get each conversation object
            try:
                conversation_response = s3.get_object(Bucket=conversations_bucket, Key=conversation_key)
                conversation_body = conversation_response['Body'].read().decode('utf-8')
                conversation_data = json.loads(conversation_body)
                conversations.append(conversation_data["conversation"])

            except (BotoCoreError, ClientError) as e:
                print(f"Failed to retrieve conversation id: {id} with error: {str(e)}")
                if e.response['Error']['Code'] == 'NoSuchKey':
                    print("added to no such key list: ", id)
                    noSuchKeyConversations.append(id)
                else:
                    failedToFetchConversations.append(id)
            
        # Generate a pre-signed URL for the uploaded file
        presigned_urls = get_presigned_urls(current_user, conversations, 100)

        return {'success': True, 'presignedUrls': presigned_urls, 
                "noSuchKeyConversations": noSuchKeyConversations,
                "failed" : failedToFetchConversations}


    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {'success': False, 'message': "Failed to retrieve conversations from S3", 'error': str(e)}


def get_presigned_urls(current_user, conversations, chunk_size=400):
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']
    s3 = boto3.client('s3')
    
    total_chunks = math.ceil(len(conversations) / chunk_size)
    presigned_urls = []

    for i in range(total_chunks):
        start_index = i * chunk_size
        end_index = min(start_index + chunk_size, len(conversations))

        # Extract the chunk of conversation data
        chunk_data = conversations[start_index:end_index]
        chunk_json = json.dumps(chunk_data)

        chunk_key = f"temp/{current_user}/conversations_chunk_{i}.json"

        s3.put_object(
            Bucket=conversations_bucket,
            Key=chunk_key,
            Body=chunk_json,
            ContentType='application/json'
        )

        # Generate a GET presigned URL for this chunk
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': conversations_bucket, 'Key': chunk_key},
            ExpiresIn=3600  # 1 hour
        )

        presigned_urls.append(presigned_url)
    print("Number of presigned urls needed: ", len(presigned_urls))
    return presigned_urls


@validated("delete")
def delete_conversation(event, context, current_user, name, data):
    query_param =  get_conversation_query_param(event.get('queryStringParameters', {}))
    if (not query_param['success']): 
        return query_param["response"]
    
    conversation_id = query_param["query_value"]
    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']

    conversation_key = current_user + '/' + conversation_id 
    
    try:
        s3.delete_object(Bucket=conversations_bucket, Key=conversation_key)
        return {'success': True, 'message': "Successfully deleted conversation from S3"}
    
    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {'success': False, 'message': "Failed to delete conversation from S3", 'error': str(e)}


@validated("delete_multiple_conversations")
def delete_multiple_conversations(event, context, current_user, name, data):
    data = data['data']
    conversation_ids = data['conversationIds']

    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']
    user_prefix = current_user + '/'

    try:
        for id in conversation_ids:
            conversation_key = user_prefix + id
            # Get each conversation object
            try:
                 s3.delete_object(Bucket=conversations_bucket, Key=conversation_key)
            except (BotoCoreError, ClientError) as e:
                print(f"Failed to delete conversation id: {id} with error: {str(e)}")
            
        return {'success': True, 'message': "Successfully deleted all conversations from S3"}

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {'success': False, 'message': "Failed to delete all conversations from S3", 'error': str(e)}


def get_conversation_query_param(query_params):
    print("Query params: ", query_params)
    conversation_id = query_params.get('conversationId', '')
    if ((not conversation_id) or (not is_valid_uuidv4(conversation_id))):
        return {'success': False, 'error': 'Invalid or missing conversation id parameter'}
    return {
            'success':  True,
            'query_value': conversation_id
            }            

def is_valid_uuidv4(uuid):
    # Regular expression for validating a UUID version 4
    regex = r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    match = re.fullmatch(regex, uuid, re.IGNORECASE)
    return bool(match)


def lzw_uncompress(compressed_data):
    dictionary = {i: chr(i) for i in range(256)}  # Build initial dictionary

    decompressed_string = ""
    previous_entry = dictionary.get(compressed_data[0])
    if not previous_entry:
        print(f"Invalid compressed data: First entry not found in dictionary")
        return ''

    decompressed_string += previous_entry
    next_code = 256

    for code in compressed_data[1:]:
        if code in dictionary:
            current_entry = dictionary[code]
        elif code == next_code:
            current_entry = previous_entry + previous_entry[0]
        else:
            raise ValueError("Invalid compressed data: Entry for code not found")

        decompressed_string += current_entry
        dictionary[next_code] = previous_entry + current_entry[0]
        next_code += 1
        previous_entry = current_entry

    # Postprocessing to convert the tagged Unicode characters back to their original form
    unicode_pattern = re.compile(r"U\+([0-9a-f]{4})", re.IGNORECASE)
    output = unicode_pattern.sub(lambda m: chr(int(m.group(1), 16)), decompressed_string)
    try:
        # Ensure the decompressed string is parsed into a dictionary
        return json.loads(output)
    except json.JSONDecodeError:
        raise ValueError("Failed to parse JSON from decompressed string")


def lzw_compress(str_input):
    if not str_input:
        return []

    # Initialize the dictionary with single-character mappings
    dictionary = {chr(i): i for i in range(256)}
    next_code = 256
    compressed_output = []

    # Preprocessing to convert Unicode characters to a unique format
    processed_input = ''.join(
        [f'U+{ord(char):04x}' if ord(char) > 255 else char for char in str_input]
    )

    current_pattern = ''
    for character in processed_input:
        new_pattern = current_pattern + character
        if new_pattern in dictionary:
            current_pattern = new_pattern
        else:
            compressed_output.append(dictionary[current_pattern])
            dictionary[new_pattern] = next_code
            next_code += 1
            current_pattern = character

    if current_pattern != '':
        compressed_output.append(dictionary[current_pattern])

    return compressed_output