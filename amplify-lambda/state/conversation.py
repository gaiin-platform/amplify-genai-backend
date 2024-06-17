import json
import os
import re
from common.validate import validated
from botocore.exceptions import BotoCoreError, ClientError
import boto3

@validated(op="conversation_upload")
def upload_conversation(event, context, current_user, name, data):
    data = data['data']
    conversation = data['conversation']
    conversation_id = data['conversationId']
    folder = data.get('folder', None)

    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']

    conversation_key = f"{current_user}/{conversation_id}" 
    try:
        s3.put_object(Bucket=conversations_bucket,
                      Key=conversation_key,
                      Body=json.dumps({"conversation":conversation, "folder":folder}))
        return {
                'statusCode': 200,
                'body': json.dumps({'success' : True, 'message': "Succesfully uploaded conversation to s3"})
                }
    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
                'statusCode': 404,
                'body': json.dumps({'success' : False, 'message': "Failed to uploaded conversation to s3", 'error': str(e)})
                }


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
        return {
            'statusCode': 200,
            'body': json.dumps({'success': True, "conversation": conversation_data["conversation"]})
        }
    except (BotoCoreError, ClientError) as e:
        error = {'success': False, 'message': "Failed to retrieve conversation from S3", 'error': str(e)}
        
        print(str(e))
        if e.response['Error']['Code'] == 'NoSuchKey':
            error["type"] = 'NoSuchKey'
        return  {
            'statusCode': 404,
            'body': json.dumps(error)
        }
    

@validated("read")
def get_all_conversations(event, context, current_user, name, data):
    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']
    user_prefix = current_user + '/'

    try:
        # List all objects in the bucket with the given prefix
        response = s3.list_objects_v2(Bucket=conversations_bucket, Prefix=user_prefix)
        if 'Contents' not in response:
            return {
                'statusCode': 200,
                'body': json.dumps({'success': True, 'conversationsData': []})
            }
        
        conversations = []
        for obj in response['Contents']:
            conversation_key = obj['Key']
            # Get each conversation object
            try:
                conversation_response = s3.get_object(Bucket=conversations_bucket, Key=conversation_key)
                conversation_body = conversation_response['Body'].read().decode('utf-8')
                conversations.append(json.loads(conversation_body))
            except (BotoCoreError, ClientError) as e:
                print(f"Failed to retrieve : {obj} with error: {str(e)}")
        return {
            'statusCode': 200,
            'body': json.dumps({'success': True, 'conversationsData': conversations})
        }

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
            'statusCode': 404,
            'body': json.dumps({'success': False, 'message': "Failed to retrieve conversations from S3", 'error': str(e)})
        }
    

@validated("get_multiple_conversations")
def get_multiple_conversations(event, context, current_user, name, data):
    data = data['data']
    conversation_ids = data['conversationIds']

    s3 = boto3.client('s3')
    conversations_bucket = os.environ['S3_CONVERSATIONS_BUCKET_NAME']
    user_prefix = current_user + '/'

    try:
        conversations = []
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
            
        return {
            'statusCode': 200,
            'body': json.dumps({'success': True, 'conversations': conversations})
        }

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
            'statusCode': 404,
            'body': json.dumps({'success': False, 'message': "Failed to retrieve conversations from S3", 'error': str(e)})
        }


@validated("read")
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
        return {
            'statusCode': 200,
            'body': json.dumps({'success': True, 'message': "Successfully deleted conversation from S3"})
        }
    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
            'statusCode': 404,
            'body': json.dumps({'success': False, 'message': "Failed to delete conversation from S3", 'error': str(e)})
        }   



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
            
        return {
            'statusCode': 200,
            'body': json.dumps({'success': True, 'message': "Successfully deleted all conversations from S3"})
        }

    except (BotoCoreError, ClientError) as e:
        print(str(e))
        return {
            'statusCode': 404,
            'body': json.dumps({'success': False, 'message': "Failed to delete all conversations from S3", 'error': str(e)})
        } 



def get_conversation_query_param(query_params):
    print("Query params: ", query_params)
    conversation_id = query_params.get('conversationId', '')
    if ((not conversation_id) or (not is_valid_uuidv4(conversation_id))):
        return {'success' : False,
                 "response":  {'statusCode': 400,
                    'body': json.dumps({'success': False, 'error': 'Invalid or missing conversation id parameter'})
                    }
                }
    return {
            'success':  True,
            'query_value': conversation_id
            }            
    
def is_valid_uuidv4(uuid):
    # Regular expression for validating a UUID version 4
    regex = r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    match = re.fullmatch(regex, uuid, re.IGNORECASE)
    return bool(match)