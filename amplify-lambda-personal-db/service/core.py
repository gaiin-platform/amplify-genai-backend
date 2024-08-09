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

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['PERSONAL_DB_DYNAMO_TABLE'])


def validate_data_against_schema(data, db_schema):
    return True


@validated(op="list_items")
def list_personal_db_items(event, context, current_user, name, data):
    try:
        data = data['data']
        db_name = data['name']

        print(f"Finding personal DB definition")

        db_definition_response = table.get_item(
            Key={
                'hashKey': current_user,
                'rangeKey': db_name
            }
        )

        if 'Item' not in db_definition_response:
            print(f"DB not found for {current_user}/{db_name}")
            return {
                'success': False,
                'message': f"DB definition for '{db_name}' not found"
            }

        db_definition_response = db_definition_response['Item']

        print(f"Found DB for {current_user}/{db_name}")
        print(f"Def: {db_definition_response}")

        response = table.query(
            KeyConditionExpression=Key('hashKey').eq(db_definition_response['id'])
        )

        print(f"Listing items in {current_user}/{db_name}")

        dbs = [dict(item) for item in response['Items']]

        print(f"Found {len(dbs)} rows in {current_user}/{db_name}")

        return {
            'success': True,
            'data': dbs
        }
    except ClientError as e:
        return {
            'success': False,
            'message': "Failed to list DBs"
        }

@validated(op="insert_db_row")
def insert_db_row(event, context, current_user, name, data):
    try:
        data = data['data']
        db_name = data['name']
        data = data['row']

        print(f"Fetching schema for {current_user}/{db_name}")
        # Get the DB definition
        db_definition_response = table.get_item(
            Key={
                'hashKey': current_user,
                'rangeKey': db_name
            }
        )

        if 'Item' not in db_definition_response:
            print(f"DB not found for {current_user}/{db_name}")
            return {
                'success': False,
                'message': f"DB definition for '{db_name}' not found"
            }

        print(f"Retrieving scheam for {current_user}/{db_name}")
        db_schema = db_definition_response['Item']['schema']
        db_id = db_definition_response['Item']['id']
        db_date = db_definition_response['Item']['updatedAt']
        db_version = db_definition_response['Item']['version']

        if not db_schema:
            print(f"Warning, schema not found for {current_user}/{db_name}")

        # Validate the data against the schema
        # (You'll need to implement the validation logic)
        if not validate_data_against_schema(data, db_schema):
            print(f"Schema validation failed for {current_user}/{db_name}")
            return {
                'success': False,
                'message': f"Data does not match the schema for '{db_name}'"
            }

        print(f"Data validated successfully against schame for {current_user}/{db_name}")

        # Generate the hash key and range key
        itemName = f"{db_id}"
        itemRangeKey = str(uuid.uuid4())

        if 'hashKey' in data:
            del data['hashKey']
        if 'rangeKey' in data:
            del data['rangeKey']
        if 'id' in data:
            del data['id']

        dbid = f"{db_id}/{itemRangeKey}"

        row_data = {
            **data,
            'hashKey': itemName,
            'rangeKey': itemRangeKey,
            'id': dbid,
            '__dbDefId': db_id,
            '__dbVersion': db_version,
            '__dbUpdatedAt': db_date
        }

        print(f"Inserting into {current_user}/{db_name}")

        table.put_item(
            Item=row_data
        )

        print(f"Insert success for {current_user}/{db_name}")

        return {
            'success': True,
            'message': f"Row inserted into DB '{db_name}'"
        }

    except ClientError as e:
        return {
            'success': False,
            'message': f"Failed to insert row: {str(e)}"
        }


@validated(op="list_dbs")
def list_personal_dbs(event, context, current_user, name, data):
    try:

        response = table.query(
            KeyConditionExpression=Key('hashKey').eq(current_user)
        )

        dbs = [dict(item) for item in response['Items']]

        return {
            'success': True,
            'data': dbs
        }
    except ClientError as e:
        return {
            'success': False,
            'message': "Failed to list DBs"
        }


@validated(op="create_db")
def create_or_update_personal_db(event, context, current_user, name, data):
    data = data['data']
    name = data['name']
    description = data['description']
    schema = data.get('schema')
    tags = data.get('tags')
    related_to = data.get('related_to')
    data = data.get('data', {})

    existing = table.get_item(
        Key={
            'hashKey': current_user,
            'rangeKey': name
        }
    )

    dbid = existing.get('Item', {}).get('id', f"pdb/{str(uuid.uuid4())}")
    schemaHash = hashlib.sha256(json.dumps(schema, cls=CombinedEncoder).encode()).hexdigest()
    created_at = existing.get('Item',{}).get('createdAt',time.strftime('%Y-%m-%dT%H:%M:%S'))

    print(f"Using DB ID of {dbid}")

    updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')

    db_metadata = {
        'hashKey': current_user,
        'rangeKey': name,
        'user': current_user,
        'createdAt': created_at,
        'updatedAt': updated_at,
        'id': dbid,
        'version': schemaHash,
        'name': name,
        'description': description,
        'schema': schema,
        'tags': tags,
        'relatedTo': related_to,
        'data': data
    }

    try:

        response = table.put_item(
            Item=db_metadata
        )

        print(f"New db created for user ID: {current_user}")
        return {
            'success': True,
            'message': "DB created successfully"
        }
    except:
        print(f"DB creation failed for: {current_user}")
        return {
            'success': False,
            'message': "DB could not be created"
        }
