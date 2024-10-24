import os

import boto3
from boto3.dynamodb.types import TypeDeserializer

dynamodb_client = boto3.client('dynamodb')

hash_files_table = os.getenv('HASH_FILES_DYNAMO_TABLE')


def replace_content_json(input_string):
    return input_string.replace('.content.json', '.json')


def strip_s3_protocol(input_string):
    return input_string.replace('s3://', '')


def sanitize_s3_data_source_key(data_source_key):
    # check if the data_source_key is a dict
    if isinstance(data_source_key, dict):
        return {**data_source_key, "id": sanitize_s3_data_source_key(data_source_key['id'])}

    if data_source_key is None:
        return None

    #data_source_key = replace_content_json(data_source_key)
    data_source_key = strip_s3_protocol(data_source_key)
    return data_source_key


def translate_user_data_sources_to_hash_data_sources(data_sources):
    def translate_data_source(ds_id):
        key = ds_id

        try:
            if key.startswith("s3://") or extract_protocol(ds_id) is None:
                key = extract_key(key)

                key = replace_content_json(key)

                print(f"Translating data source {ds_id} with key {key}")

                response = dynamodb_client.get_item(
                    TableName=hash_files_table,
                    Key={'id': {'S': key}}
                )

                if 'Item' in response:
                    item_dict = {k: TypeDeserializer().deserialize(v) for k, v in response['Item'].items()}
                    print(f"Item: {item_dict}")
                    return f"s3://{item_dict['textLocationKey']}"
                else:
                    print(f"Failed to find item with key {key}")
                    return ds_id  # No item found with the given ID
            else:
                return ds_id
        except Exception as e:
            print(f"Failed to translate data source {ds_id}: {str(e)}")
            return ds_id

    translated = [translate_data_source(ds) for ds in data_sources]
    return [ds for ds in translated if ds is not None]


def extract_protocol(url):
    try:
        # Find the index where '://' appears
        protocol_end_index = url.find('://')

        # If '://' is not found, it might not be a valid URL, but we still handle cases like 'mailto:'
        if protocol_end_index == -1:
            colon_index = url.find(':')
            if colon_index == -1:
                return None
            return url[:colon_index + 1]  # Include the colon

        # Extract and return the protocol (including the '://')
        return url[:protocol_end_index + 3]  # Include the '://'
    except:
        return None


def extract_key(url):
    proto = extract_protocol(url) or ''
    return url[len(proto):]
