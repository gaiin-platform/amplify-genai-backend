
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import json
import os
import time
import uuid
import boto3
from common.validate import validated
from boto3.dynamodb.types import TypeDeserializer
from os.path import dirname
from common.encoders import CombinedEncoder
from botocore.exceptions import ClientError
from . import market_ideator

def index_category(event, context):
    deserializer = TypeDeserializer()
    for record in event['Records']:
        if record['eventName'] in ['INSERT', 'MODIFY']:
            data_dict = deserializer.deserialize({"M": record['dynamodb']['NewImage']})
            if data_dict['id'] != 'visible_categories':
                process_category(data_dict['id'], data_dict)


def index_item(event, context):
    deserializer = TypeDeserializer()

    for record in event['Records']:
        if record['eventName'] == 'INSERT':
            # Process the insertion of new items
            data_dict = deserializer.deserialize({"M": record['dynamodb']['NewImage']})
            process_item(data_dict['category'], data_dict['id'], data_dict)
            return True

        elif record['eventName'] == 'REMOVE':
            # Process the deletion of items
            data_dict = deserializer.deserialize({"M": record['dynamodb']['OldImage']})
            print(f"Deleting {data_dict.keys()}")
            print(f"Deleting {data_dict}")
            market_index_delete_item(data_dict['category'], data_dict['id'])
            return True


def write_to_s3(type, event_id, data_dict):
    dynamodb = boto3.resource('dynamodb')
    s3 = boto3.client('s3')
    bucket_name = os.environ['S3_MARKET_INDEX_BUCKET_NAME']
    try:
        file_name = f"{type}-{event_id}.json"
        print(f"Writing to S3: {bucket_name}/{file_name} :: {data_dict}")
        s3.put_object(Bucket=bucket_name, Key=file_name, Body=json.dumps(data_dict, cls=CombinedEncoder))

    except Exception as e:
        print(f"Error saving file: {e}")


def process_category(category, data_dict):

    key = f'{category}/index.json'
    parent_key = f'{dirname(category)}/index.json'

    if category == '/':
        key = 'index.json'
        parent_key = None

    if parent_key == '/index.json' or parent_key == '//index.json':
        parent_key = 'index.json'

    print(f"Processing category: {category} with data: {data_dict}")
    print(f"Parent key: {parent_key}")
    print(f"Key: {key}")

    bucket_name = os.environ['S3_MARKET_INDEX_BUCKET_NAME']
    s3 = boto3.client('s3')

    try:
        # Fetch the existing category json from S3
        s3_obj = s3.get_object(Bucket=bucket_name, Key=key)
        s3_obj_decoded = s3_obj['Body'].read().decode('utf-8')
        existing_dict = json.loads(s3_obj_decoded)
        existing_dict.update(data_dict)

        if len(existing_dict.get("categories", [])) > 0 and not data_dict.get("keep_featured", False):
            existing_dict["items"] = []

    except s3.exceptions.NoSuchKey:
        # The category json doesn't exist on S3, so let's create it
        existing_dict = {
            'id': category,
            'name': '',
            'description': '',
            'icon': '',
            'tags': [],
            'image': '',
            'items': [],
            'categories': []
        }
        existing_dict.update(data_dict)
    except Exception as e:
        print(f"An error occurred: {e}")
        return False

        # id should be populated with the category string
    existing_dict["id"] = category
    s3.put_object(Bucket=bucket_name, Key=key, Body=json.dumps(existing_dict, cls=CombinedEncoder))

    # update the parent's categories
    try:
        parent_s3_obj = s3.get_object(Bucket=bucket_name, Key=parent_key)
        parent_obj_decoded = parent_s3_obj['Body'].read().decode('utf-8')
        parent_dict = json.loads(parent_obj_decoded)

        # prepare a reduced version of existing_dict for including in parent
        reduced_dict = {k: v for k, v in existing_dict.items() if k not in ["items", "categories"]}

        # update parent's categories and remove the existing category if it exists
        parent_dict["categories"] = [category for category in parent_dict["categories"]
                                     if category["id"] != reduced_dict["id"]]
        # Then, append the new or updated category.
        parent_dict["categories"].append(reduced_dict)
        parent_dict["categories"].sort(key=lambda category: category["name"].lower())

        s3.put_object(Bucket=bucket_name, Key=parent_key, Body=json.dumps(parent_dict, cls=CombinedEncoder))
    except s3.exceptions.NoSuchKey:
        print(f"No parent category found for: {category}. Unable to update parent.")
    except Exception as e:
        print(f"An error occurred when updating parent category: {e}")
        return False

    print(f"Processed category: {category}")
    return True


def save_item_example(bucket_name, category, id, examples_to_add):
    key = f'{category}/{id}-examples.json'

    print(f"Processing item with id = {id} in category: {category}")
    print(f"Key: {key}")
    s3 = boto3.client('s3')

    exiting_examples = {'examples': [], 'id': id, 'category': category}
    try:
        # Fetch the category JSON from S3
        s3_obj = s3.get_object(Bucket=bucket_name, Key=key)
        category_decoded = s3_obj['Body'].read().decode('utf-8')

        # Load JSON content
        exiting_examples = json.loads(category_decoded)
    except s3.exceptions.NoSuchKey:
        print(f"No existing examples found for: {category}/{id}")
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

    # Add the new examples to the list
    exiting_examples['examples'].extend(examples_to_add)

    print(f"Converting to JSON...")
    json_data = json.dumps(exiting_examples, cls=CombinedEncoder)
    print(f"JSON: {json_data}")

    try:
        print(f"Saving examples for {category}/{id} to S3 with key: {key} in bucket: {bucket_name}")
        s3.put_object(Bucket=bucket_name, Key=key, Body=json_data)
        return key
    except Exception as e:
        print(f"An error occurred saving examples for {category}/{id}: {e}")
        return False


def process_item(category, id, data_dict):
    key = f'{category}/{id}.json'

    featured = data_dict.get("featured", False)

    parent_keys = ['index.json']
    parent_categories = category.split('/')[1:]
    for i in range(len(parent_categories)):
        parent_keys.append('/' + '/'.join(parent_categories[:i+1]) + '/index.json')

    print(f"Processing item with id = {id} in category: {category} with data: {data_dict}")

    bucket_name = os.environ['S3_MARKET_INDEX_BUCKET_NAME']
    s3 = boto3.client('s3')

    try:
        # Create or update item in S3
        existing_dict = data_dict
        existing_dict["id"] = id
        s3.put_object(Bucket=bucket_name, Key=key, Body=json.dumps(existing_dict, cls=CombinedEncoder))

        # Get all the parent categories and update their items
        for i, parent_key in enumerate(parent_keys):
            try:
                if i != len(parent_keys) - 1 or featured:
                    print(f"Updating parent: {parent_key}")

                    parent_s3_obj = s3.get_object(Bucket=bucket_name, Key=parent_key)
                    parent_obj_decoded = parent_s3_obj['Body'].read().decode('utf-8')
                    parent_dict = json.loads(parent_obj_decoded)

                    existing_items = parent_dict.get("items", [])

                    # If the parent already has 9 items, remove the first one
                    # Skip this check if this is the last item in parent_keys
                    if len(existing_items) >= 9 and i != len(parent_keys) - 1:
                        existing_items.pop(0)

                    existing_dict = {k: v for k, v in existing_dict.items() if k not in ["content"]}

                    existing_items.append(existing_dict)  # add the new item
                    parent_dict["items"] = existing_items
                    s3.put_object(Bucket=bucket_name, Key=parent_key, Body=json.dumps(parent_dict, cls=CombinedEncoder))

                    print(f"Updated parent: {parent_key}")

            except s3.exceptions.NoSuchKey:
                print(f"No parent category found having key: {parent_key}. Unable to update parent.")

            except Exception as e:
                print(f"An error occurred when updating parent category: {e}")

    except Exception as e:
        print(f"An error occurred: {e}")
        return False

    print(f"Processed item with id = {id} in category: {category}")
    return True


delete_enabled = True
def market_index_delete_item(category, id):

    if not delete_enabled:
        print(f"Delete is not enabled. Skipping deletion of item with id = {id} in category: {category}")
        return False


    key = f'{category}/{id}.json'

    # Derive parent categories from the category path
    parent_keys = ['index.json']
    parent_categories = category.split('/')[1:]
    for i in range(len(parent_categories)):
        parent_keys.append('/' + '/'.join(parent_categories[:i+1]) + '/index.json')

    print(f"Deleting item with id = {id} from category: {category}")

    # Setup S3 bucket and client
    bucket_name = os.environ['S3_MARKET_INDEX_BUCKET_NAME']
    s3 = boto3.client('s3')

    archive_key = "archive/" + key
    try:
        print(f"Archiving object to: {archive_key}")
        # Copy the object to the new location
        s3.copy({'Bucket': bucket_name, 'Key': key}, bucket_name, archive_key)

        print(f"Object copied to: {archive_key}")

    except Exception as e:
        print(f"Error archiving object: {str(e)}")

    # Delete the item from S3
    try:
        s3.delete_object(Bucket=bucket_name, Key=key)
        print(f"Deleted item with id = {id} from category: {category}")

    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'NoSuchKey':
            print(f"No item found with key: {key}. Perhaps it was already deleted.")
        else:
            print(f"An error occurred: {e}")
            return False

    # Update all the parent categories to remove this item
    for parent_key in parent_keys:
        try:
            print(f"Updating parent: {parent_key}")

            # Retrieve the current parent item list
            parent_s3_obj = s3.get_object(Bucket=bucket_name, Key=parent_key)
            parent_obj_decoded = parent_s3_obj['Body'].read().decode('utf-8')
            parent_dict = json.loads(parent_obj_decoded)

            existing_items = parent_dict.get("items", [])
            # Remove the item with the specified id
            parent_dict["items"] = [item for item in existing_items if item["id"] != id]

            # Write back the updated parent item list
            s3.put_object(Bucket=bucket_name, Key=parent_key, Body=json.dumps(parent_dict))

            print(f"Updated parent: {parent_key}")

        except s3.exceptions.NoSuchKey:
            print(f"No parent category found having key: {parent_key}. Nothing to update.")

        except Exception as e:
            print(f"An error occurred when updating parent category: {e}")
            return False

    return True


def get_category_from_s3(category):
    key = f'{category}/index.json'

    if category == '/':
        key = 'index.json'

    bucket_name = os.environ['S3_MARKET_INDEX_BUCKET_NAME']
    s3 = boto3.client('s3')

    try:
        # Fetch the category JSON from S3
        s3_obj = s3.get_object(Bucket=bucket_name, Key=key)
        category_decoded = s3_obj['Body'].read().decode('utf-8')

        # Load JSON content
        category_dict = json.loads(category_decoded)
    except s3.exceptions.NoSuchKey:
        print(f"No category found for: {category}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

    return category_dict


@validated("get_category")
def get_category(event, context, user, name, data):
    data = data['data']
    item_category = data['category']

    print(f"User {user} fetching category {item_category}")

    category_dict = get_category_from_s3(item_category)

    if category_dict is None:
        return {
            'success': False,
            'message': 'Category does not exist'
        }

    return {'success': True, 'message': 'Category fetched successfully', 'data': category_dict}


@validated("list_categories")
def list_categories(event, context, user, name, data):
    print(f"User {user} fetching categories")
    dynamodb = boto3.resource('dynamodb')
    category_table = dynamodb.Table(os.environ['MARKET_CATEGORIES_DYNAMO_TABLE'])

    response = category_table.get_item(Key={'id': 'visible_categories'})
    if 'Item' not in response:
        print(f"No categories configured.")
        return {
            'success': False,
            'message': 'Category does not exist'
        }

    return {'success': True, 'message': 'Category fetched successfully', 'data': response['Item']['categories']}



@validated("get_examples")
def get_item_examples(event, context, user, name, data):

    data = data['data']
    item_id = data['id']
    item_category = data['category']

    key = f'{item_category}/{item_id}-examples.json'

    if item_category == '/':
        key = f"/{item_id}-examples.json"

    bucket_name = os.environ['S3_MARKET_INDEX_BUCKET_NAME']
    s3 = boto3.client('s3')

    try:
        # Fetch the category JSON from S3
        s3_obj = s3.get_object(Bucket=bucket_name, Key=key)
        category_decoded = s3_obj['Body'].read().decode('utf-8')

        # Load JSON content
        category_dict = json.loads(category_decoded)
    except s3.exceptions.NoSuchKey:
        print(f"No examples found for: {item_category}/{item_id}")
        return {'success': True,
                'message': 'No examples found',
                'data': {'examples': [], 'id': item_id, 'category': item_category}};
    except Exception as e:
        print(f"An error occurred: {e}")
        return {'success': False,
                'message': 'Error fetching examples',
                'data': {}};

    return {'success': True,
            'message': 'No examples found',
            'data': category_dict};



@validated("get_item")
def get_item(event, context, user, name, data):
    dynamodb = boto3.resource('dynamodb')

    data = data['data']
    item_id = data['id']

    print(f"User {user} loading item {item_id}")

    item_table = dynamodb.Table(os.environ['MARKET_ITEMS_DYNAMO_TABLE'])

    # Check if category exists in the category table by seeing if an item with that
    # ID exists
    response = item_table.get_item(Key={'id': item_id})
    if 'Item' not in response:
        print(f"Item {item_id} does not exist")
        return {
            'success': False,
            'message': 'Item does not exist'
        }

    return {'success': True, 'message': 'Category fetched successfully', 'data': response['Item']}


@validated("ideate")
def market_ideate(event, context, user, name, data):
    data = data['data']
    item_category = data['category']
    task = data['task']

    ideas = market_ideator.generate_prompts_for_task(task)
    return {'success': True, 'message': 'Ideas generated successfully', data: ideas}

@validated("publish_item")
def publish_item(event, context, user, name, data):
    timestamp = str(time.time())
    dynamodb = boto3.resource('dynamodb')

    data = data['data']
    item_name = data['name']
    item_tags = data['tags']
    item_description = data['description']
    item_category = data['category']

    print(f"User {user} publishing item to category {item_category}")

    item_table = dynamodb.Table(os.environ['MARKET_ITEMS_DYNAMO_TABLE'])
    category_table = dynamodb.Table(os.environ['MARKET_CATEGORIES_DYNAMO_TABLE'])

    # Check if category exists in the category table by seeing if an item with that
    # ID exists
    response = category_table.get_item(Key={'id': item_category})
    if 'Item' not in response:
        print(f"Category {item_category} does not exist")
        return {
            'success': False,
            'message': 'Category does not exist'
        }

    key = f"mkt-{str(uuid.uuid1())}"

    item = {
        'id': key,
        'content': data["content"],
        'user': user,
        'name': item_name,
        'tags': item_tags,
        'description': item_description,
        'category': item_category,
        'createdAt': timestamp,
        'updatedAt': timestamp,
    }

    # write the todo to the database
    item_table.put_item(Item=item)

    return {'success': True, 'message': 'Item published successfully', 'data': {'key': key}}


@validated("delete_item")
def delete_item(event, context, user, name, data):
    dynamodb = boto3.resource('dynamodb')

    data = data['data']
    item_key = data['id']

    print(f"User {user} deleting item with key {item_key}")

    item_table = dynamodb.Table(os.environ['MARKET_ITEMS_DYNAMO_TABLE'])

    try:
        # Delete the item from the database
        item_table.delete_item(Key={'id': item_key})

        return {'success': True, 'message': 'Item deleted successfully', 'data': {'key': item_key}}
    except Exception as e:
        print(f"Error deleting item: {str(e)}")

        return {
            'success': False,
            'message': 'Error deleting item'
        }