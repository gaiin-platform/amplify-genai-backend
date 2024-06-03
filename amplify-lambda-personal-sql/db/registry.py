import os

import boto3
from boto3.dynamodb.conditions import Key
from functools import wraps
from boto3.dynamodb.types import TypeSerializer

from common.secrets import update_dict_with_secrets, store_secrets_in_dict

dynamodb = boto3.client('dynamodb')
serializer = TypeSerializer()
handler_registry = {}


def db_handler(type):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Call the actual function
            return func(*args, **kwargs)

        # Register the function in the handler_registry
        print(f"Registering handler for database type {type}")
        handler_registry[type] = func
        return wrapper

    return decorator


def get_db_handler_for_type(type):
    if type in handler_registry:
        return handler_registry[type]
    else:
        raise ValueError(f"No handler found for database type {type}")


def load_db_by_id(current_user, db_id):

    # Get the metadata for the database
    metadata = get_db_metadata(current_user, db_id)
    db_type = metadata['type']

    # Extract the data
    data = metadata['data']

    handler = get_db_handler_for_type(db_type)

    # Load the database
    conn = handler(current_user, db_id, metadata, data)
    return conn


def register_db(current_user, db_type, db_id, db_name, description, tags, timestamp, data):
    # Save metadata such as description and tags
    metadata_item = {
        'id': db_id,
        'type': db_type,
        'creator': current_user,
        'name': db_name,
        'description': description,
        'tags': tags,
        'createdAt': timestamp,
        'lastModified': timestamp,
        'data': store_secrets_in_dict(data)
    }

    # Convert metadata item to the DynamoDB format using TypeSerializer
    metadata_item_dynamodb = {k: serializer.serialize(v) for k, v in metadata_item.items()}

    # Save metadata to DynamoDB
    metadata_table = os.getenv('PERSONAL_SQL_METADATA_TABLE')
    dynamodb.put_item(TableName=metadata_table, Item=metadata_item_dynamodb)


def get_dbs_for_user(user_id):
    """
    Fetches metadata for all databases owned by the specified user, excluding S3 keys.

    Args:
        user_id (str): The ID of the user.

    Returns:
        list: A list of metadata objects for the user's databases, excluding the S3 keys.
    """
    # Initialize the DynamoDB resource
    dynamodb = boto3.resource('dynamodb')

    # Get the metadata table name from environment variable
    metadata_table_name = os.getenv('PERSONAL_SQL_METADATA_TABLE')
    if not metadata_table_name:
        raise ValueError("Environment variable 'PERSONAL_SQL_METADATA_TABLE' is not set.")

    # Reference the metadata table
    table = dynamodb.Table(metadata_table_name)

    # Query the table using the CreatorIndex
    response = table.query(
        IndexName='CreatorIndex',
        KeyConditionExpression=Key('creator').eq(user_id)
    )

    # Extract items and exclude the s3Key
    items = response['Items']
    for item in items:
        if 'data' in item:
            del item['data']

    return items


def get_db_metadata(current_user, db_id):
    # Get the DynamoDB table name from environment variable
    metadata_table_name = os.getenv('PERSONAL_SQL_METADATA_TABLE')
    if not metadata_table_name:
        raise ValueError("Environment variable 'PERSONAL_SQL_METADATA_TABLE' is not set.")
    # Reference the metadata table
    dyn = boto3.resource('dynamodb')
    table = dyn.Table(metadata_table_name)
    # Fetch the metadata for the given db_id
    response = table.get_item(Key={'id': db_id})
    if 'Item' not in response:
        print(f"No metadata found for database with id {db_id}")
        raise ValueError(f"No metadata found for database with id {db_id}")
    metadata = response['Item']

    # This will need to change in order to implement sharing
    if metadata.get('creator') != current_user:
        print(f"User {current_user} is not the creator of the database with id {db_id}")
        raise ValueError(f"User {current_user} is not the creator of the database with id {db_id}")

    data = metadata.get('data')
    metadata['data'] = update_dict_with_secrets(data)

    return metadata
