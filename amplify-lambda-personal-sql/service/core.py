import os
import boto3
from common.validate import validated
from botocore.exceptions import ClientError

from db.local_db import describe_schemas_from_db, get_dbs_for_user, create_and_save_db_for_user, query_db_by_id

dynamodb = boto3.resource('dynamodb')
files_bucket = os.environ['ASSISTANTS_FILES_BUCKET_NAME']


@validated(op="create")
def create_db(event, context, current_user, name, data):
    try:
        event_data = data['data']
        s3_bucket = os.getenv('PERSONAL_SQL_S3_BUCKET')
        key_table_list = event_data.get('tables')
        db_name = event_data.get('name')
        description = event_data.get('description', '')
        tags = event_data.get('tags', [])

        if not (s3_bucket and key_table_list and db_name and description):
            return {
                'success': False,
                'message': "Missing required parameters"
            }

        print(f"Creating DB for user: {current_user}")

        s3_files_bucket = os.getenv('ASSISTANTS_FILES_BUCKET_NAME')
        db_id = create_and_save_db_for_user(current_user, s3_bucket, s3_files_bucket, key_table_list, db_name, description, tags)

        print(f"DB created for {current_user} with ID: {db_id}")

        return {
            'success': True,
            'id': db_id
        }
    except Exception as e:
        print(e)
        return {
            'success': False,
            'message': "Failed to create DB"
        }


@validated(op="list")
def get_user_dbs(event, context, current_user, name, data):
    try:
        # Extract parameters from the event data
        event_data = data['data']

        print(f"Fetching list of databases for user: {current_user}")

        # Call the function that performs the business logic
        user_dbs = get_dbs_for_user(current_user)

        print(f"List of databases fetched for {current_user}")

        return {
            'success': True,
            'data': user_dbs
        }
    except ClientError:
        return {
            'success': False,
            'message': "Failed to get list of databases for the user"
        }
    except Exception as e:
        return {
            'success': False,
            'message': str(e)
        }


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
            'message': "Failed to describe schemas from DB"
        }


@validated(op="query")
def query_personal_db(event, context, current_user, name, data):
    try:
        # Extract parameters from the event data
        event_data = data['data']
        id = event_data.get('id')
        query = event_data.get('query')

        if not id or not query:
            return {
                'success': False,
                'message': "Required parameters 'id' missing"
            }

        print(f"Querying db for: {current_user}/{id}")

        # query the db
        # Call the function that performs the business logic
        query_result = query_db_by_id(current_user, id, query)

        return {
            'success': True,
            'data': query_result
        }
    except ClientError as e:
        print("Error in query_personal_db")
        print(e)
        return {
            'success': False,
            'message': "Failed to query DB"
        }
    except Exception as e:
        print("Error in query_personal_db")
        print(e)
        return {
            'success': False,
            'message': "Failed to query DB"
        }