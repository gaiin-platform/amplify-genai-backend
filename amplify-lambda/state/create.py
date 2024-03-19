import os
import time
import uuid

import boto3

from common.validate import validated



dynamodb = boto3.resource('dynamodb')


@validated("create")
def create(event, context, user, name, data):
    timestamp = str(time.time())

    table = dynamodb.Table(os.environ['DYNAMODB_TABLE'])

    item = {
      'id': str(uuid.uuid1()),
      'data': data["data"],
      'user': user,
      'name': name,
      'createdAt': timestamp,
      'updatedAt': timestamp,
    }

    # write the todo to the database
    table.put_item(Item=item)

    return item
