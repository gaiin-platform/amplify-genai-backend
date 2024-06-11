
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os

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
