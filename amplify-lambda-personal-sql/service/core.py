import hashlib
import json
import os
import uuid
import time

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.encoders import CombinedEncoder
from common.validate import validated
from botocore.exceptions import ClientError

from db.local_db import describe_schemas_from_db

dynamodb = boto3.resource('dynamodb')
files_bucket = os.environ['ASSISTANTS_FILES_BUCKET_NAME']


@validated(op="describe")
def describe_personal_db_schemas(event, context, current_user, name, data):
    try:
        # Extract parameters from the event data
        event_data = data['data']
        key_table_list = event_data.get('tables')

        if not files_bucket or not key_table_list:
            return {
                'success': False,
                'message': "Required parameters 'key_table_list' missing"
            }

        print(f"Fetching schema descriptions for user: {current_user}")

        # Call the function that performs the business logic
        schema_descriptions = describe_schemas_from_db(files_bucket, key_table_list)

        print(f"Schema descriptions fetched for {current_user}")

        return {
            'success': True,
            'data': schema_descriptions
        }
    except ClientError:
        return {
            'success': False,
            'message': "Failed to describe schemas from DB"
        }
    except Exception as e:
        return {
            'success': False,
            'message': str(e)
        }