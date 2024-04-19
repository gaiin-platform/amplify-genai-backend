from datetime import datetime
import json
import time
import logging
import os
import uuid

from boto3.dynamodb.conditions import Key
from common.object_permissions import update_object_permissions
from common.data_sources import translate_user_data_sources_to_hash_data_sources, extract_key
from common.share_assistants import share_assistant

import boto3

from common.validate import HTTPException, validated

dynamodb = boto3.resource('dynamodb')


def get_s3_data(bucket_name, s3_key):
  print("Fetching data from S3: {}/{}".format(bucket_name, s3_key))
  s3 = boto3.resource('s3')
  obj = s3.Object(bucket_name, s3_key)
  data = obj.get()['Body'].read().decode('utf-8')
  return data

def get_data_from_dynamodb(user, name):
  dynamodb = boto3.resource('dynamodb')
  table = dynamodb.Table(os.environ['DYNAMODB_TABLE'])

  print("Querying DynamoDB for user: {} and name: {}".format(user, name))

  response = table.query(
    IndexName="UserNameIndex",
    KeyConditionExpression=Key('user').eq(user) & Key('name').eq(name)
  )

  items = response.get('Items', [])
  return items

@validated("load")
def load_data_from_s3(event, context, current_user, name, data):

  s3_key = data['data']['key']
  print("Loading data from S3: {}".format(s3_key))

  user_data = get_data_from_dynamodb(current_user, "/state/share")

  # Check if the given s3_key exists in the user's data
  if any(s3_key == data_dict.get('key') for item in user_data for data_dict in item.get('data', [])):
    # If s3_key found, fetch data from S3 and return
    print("Loading data from S3: {}".format(s3_key))
    return get_s3_data(os.environ['S3_BUCKET_NAME'], s3_key)
  else:
    raise HTTPException(404, "Data not found")


def put_s3_data(bucket_name, filename, data):
  s3_client = boto3.client('s3')

  # Check if bucket exists
  try:
    s3_client.head_bucket(Bucket=bucket_name)
  except boto3.exceptions.botocore.exceptions.ClientError:
    # If bucket does not exist, create it
    s3_client.create_bucket(Bucket=bucket_name)

  # Now put the object (file)
  s3_client.put_object(Body=json.dumps(data).encode(), Bucket=bucket_name, Key=filename)

  return filename


@validated("get")
def get_base_prompts(event, context, current_user, name, data):
  data = data['data']

  s3_key = 'base.json'
  base_data = get_s3_data(os.environ['S3_BASE_PROMPTS_BUCKET_NAME'], s3_key)

  return {
    'success': True,
    'message': 'Successfully fetched base prompts',
    'data': json.loads(base_data)
  }




def handle_conversation_datasource_permissions(access_token, recipient_users, conversations):
  print('Enter handle shared datasources in conversations')
  datasources_keys = []
  for conversation in conversations:
      for message in conversation['messages']:  
          # Check if 'data' and 'dataSources' keys exist and 'dataSources' has items
          if 'data' in message and 'dataSources' in message['data'] and len(message['data']['dataSources']) > 0:
              for doc in message['data']['dataSources']:
                  datasources_keys.append(doc)

  data_sources = translate_user_data_sources_to_hash_data_sources(datasources_keys)
  for i in range(len(data_sources)):
    data_sources[i] = extract_key(data_sources[i]['id'])
     
  print("Datasources: ", data_sources)

  if not update_object_permissions(
        access_token=access_token,
        shared_with_users= recipient_users,
        keys= data_sources,
        object_type='datasource',
        principal_type='user',
        permission_level='read',
        policy=''):
    print(f"Error adding permissions for shared files in conversations")
    return {'success': False, 'error': 'Error updating datasource permissions'}
  
  print("object permissions for datasources success")
  return {'success': True, 'message': 'Updated object access permissions'}




def extract_access_token(event):
  try: # Extract the Authorization header
        auth_header = event['headers']['Authorization']
        access_token = auth_header.split(" ")[1] if len(auth_header.split(" ")) > 1 else None
        return {'success': True, 'message': 'Retrieved access token', 'access_token': access_token}
  except KeyError:
      return {'success': False, 'error': 'Authorization token not provided'}


def handle_share_assistant(access_token, prompts, recipient_users):
  for prompt in prompts:
      assistant_data = prompt['data']['assistant']['definition']

      data_sources = assistant_data['dataSources']   
      
      for i in range(len(data_sources)):
        data_sources[i] = extract_key(data_sources[i]['id'])
      
      
      
      data =  {
        'assistantId': prompt['id'],
        'recipientUsers': recipient_users,
        'accessType': 'read',
        'dataSources': data_sources,
        'policy': '',  
      }
      
      if not share_assistant(access_token, data):
        print("Error making share assistant calls for assistant: ", prompt['id'])
        return {'success': False, 'error': 'Could not successfully make the call to share assistants'}
      
  print("Share assistant call was a success")    
  return {'success': True, 'message': 'Successfully made the calls to share assistants'}
  
   


@validated("append")
def share_with_users(event, context, current_user, name, data):

  access_token_info = extract_access_token(event)
  if (not access_token_info['success']): 
    return access_token_info
  access_token = access_token_info['access_token']
  print("Access token extracted success")

  users = data['data']['sharedWith']
  note = data['data']['note']
  new_data = data['data']['sharedData']
  new_data['sharedBy'] = current_user

  conversations = new_data['history']

  if (len(conversations) > 0):
    object_permissions = handle_conversation_datasource_permissions(access_token, users, conversations)
    if (not object_permissions['success']): 
      return object_permissions
  

  #new_data['history'] = remove_code_interpreter_details(conversations) # if it has any
  prompts = new_data['prompts']
  
  if (len(prompts) > 0):
    shared_assistants = handle_share_assistant(access_token, prompts, users)
    if (not shared_assistants['success']):
      return shared_assistants

  succesful_shares = []

  for user in users:
    try:
      # Generate a unique file key for each user
      dt_string = datetime.now().strftime('%Y-%m-%d')
      s3_key = '{}/{}/{}/{}.json'.format(user, current_user, dt_string, str(uuid.uuid4()))

      put_s3_data(os.environ['S3_BUCKET_NAME'], s3_key, new_data)

      dynamodb = boto3.resource('dynamodb')
      table = dynamodb.Table(os.environ['DYNAMODB_TABLE'])

      # Step 1: Query using the secondary index to get the primary key
      response = table.query(
        IndexName="UserNameIndex",
        KeyConditionExpression=Key('user').eq(user) & Key('name').eq(name)
      )

      items = response.get('Items')
      timestamp = int(time.time() * 1000)

      if not items:
        # No item found with user and name, create a new item
        id_key = '{}/{}'.format(user, str(uuid.uuid4()))  # add the user's name to the key in DynamoDB
        new_item = {
          'id': id_key,
          'user': user,
          'name': name,
          'data': [{'sharedBy': current_user, 'note': note, 'sharedAt': timestamp, 'key': s3_key}],
          'createdAt': timestamp,
          'updatedAt': timestamp
        }
        table.put_item(Item=new_item)
        succesful_shares.append(user)

      else:
        # Otherwise, update the existing item
        item = items[0]

        result = table.update_item(
          Key={ 'id': item['id'] },
          ExpressionAttributeNames={ '#data': 'data' },
          ExpressionAttributeValues={ ':data': [{'sharedBy':current_user, 'note':note, 'sharedAt':timestamp, 'key':s3_key}], ':updatedAt': timestamp },
          UpdateExpression='SET #data = list_append(#data, :data), updatedAt = :updatedAt',
          ReturnValues='ALL_NEW',
        )

        succesful_shares.append(user)

    except Exception as e:
      logging.error(e)
      continue

  return succesful_shares

def remove_code_interpreter_details(conversations):
  for conversation in conversations:
      print(conversation)
      if 'codeInterpreterAssistantId' in conversation:
          for message in conversation['messages']:
              if 'codeInterpreterMessageData' in message:
                  message['codeInterpreterMessageData'] = None  
          conversation['codeInterpreterAssistantId'] = None
  return conversations


@validated("read")
def get_share_data_for_user(event, context, current_user, name, data):

  tableName = os.environ['DYNAMODB_TABLE']
  dynamodb = boto3.resource('dynamodb')
  table = dynamodb.Table(tableName)

  try:
    # Step 1: Query using the secondary index to get the primary key
    response = table.query(
      IndexName="UserNameIndex",
      KeyConditionExpression=Key('user').eq(current_user) & Key('name').eq(name)
    )

    items = response.get('Items')

    if not items:
      # No item found with user and name, return message
      logging.info("No shared data found for current user: {} and name: {}".format(current_user, name))
      return []

    else:
      # Otherwise, retrieve the shared data
      item = items[0]
      if 'data' in item:
        share_data = item['data']
        return share_data

  except Exception as e:
    logging.error(e)
    return None
