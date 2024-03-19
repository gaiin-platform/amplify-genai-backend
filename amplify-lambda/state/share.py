from datetime import datetime
import json
import time
import logging
import os
import uuid

from boto3.dynamodb.conditions import Key

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

@validated("append")
def share_with_users(event, context, current_user, name, data):

  users = data['data']['sharedWith']
  new_data = data['data']['sharedData']
  note = data['data']['note']

  new_data['sharedBy'] = current_user

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
