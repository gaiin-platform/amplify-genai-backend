import os
import copy
import boto3
from boto3.dynamodb.types import TypeDeserializer


def extract_key(source):
    # Look for a :// protocol separator and extract everyting after it
    return source.split("://")[1] if "://" in source else source


def translate_user_data_sources_to_hash_data_sources(data_sources):
    dynamodb_client = boto3.client('dynamodb')
    hash_files_table_name = os.environ['HASH_FILES_DYNAMO_TABLE']
    type_deserializer = TypeDeserializer()

    translated_data_sources = []

    for ds in data_sources:
        key = ds['id']

        try:
            if ("image/" in ds['type']):
                ds['id'] = extract_key(ds['id']) 
                translated_data_sources.append(ds)
                continue

            if key.startswith("s3://"):
                key = extract_key(key)

            response = dynamodb_client.get_item(
                TableName=hash_files_table_name,
                Key={
                    'id': {'S': key}
                }
            )

            item = response.get('Item')
            if item:
                deserialized_item = {k: type_deserializer.deserialize(v) for k, v in item.items()}
                ds['id'] =  deserialized_item['textLocationKey']
        except Exception as e:
            print(e)
            pass

        translated_data_sources.append(ds)

    return [ds for ds in translated_data_sources if ds is not None]




def get_data_source_keys(data_sources):
    print("Get keys from data sources")
    data_sources_keys = []
    for i in range(len(data_sources)):
        ds = data_sources[i]
        if ('metadata' in ds and "image/" in ds['type']):
            data_sources_keys.append(ds['id'])
            continue
        # print("current datasource: ", ds)
        key = ''
        if (ds['id'].startswith("global/")):
            key = ds['id']
        else:
            if (ds["id"].startswith("s3://global/")):
                key = extract_key(ds['id'])
            else:
                ds_copy = copy.deepcopy(ds)
                # Assistant attached data sources tends to have id vals of uuids vs they key we need
                if ('key' in ds):
                    ds_copy['id'] = ds["key"]

                key = translate_user_data_sources_to_hash_data_sources([ds_copy])[0]['id']  # cant

            print("Updated Key: ", key)

        if (not key): return {'success': False, 'error': 'Could not extract key'}
        data_sources_keys.append(key)

    print("Datasource Keys: ", data_sources_keys)
    return data_sources_keys