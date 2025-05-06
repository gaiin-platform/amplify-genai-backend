import os
import copy
import boto3
import json
import requests
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


def resolve_datasources(datasource_request, authorization_token=None, endpoint=None):
    """
    Resolves datasources by calling a configured resolver endpoint.
    
    Args:
        datasource_request (dict): The datasource request with the following structure:
            {
              "dataSources": [
                {
                  "id": "s3://user@domain.com/path/uuid.json",
                  "type": "application/json"
                }
              ],
              "options": {
                "useSignedUrls": true
              },
              "chat": {
                "messages": [ ... optional messages for context ... ]
              }
            }
        authorization_token (str, optional): Authorization token for the resolver endpoint
        endpoint (str, optional): The resolver endpoint URL, defaults to DATASOURCES_RESOLVER_ENDPOINT env var
    
    Returns:
        dict: The resolved datasources with signed URLs
    """
    resolver_endpoint = endpoint or os.environ.get('DATASOURCES_RESOLVER_ENDPOINT')
    if not resolver_endpoint:
        raise ValueError("No datasource resolver endpoint provided or configured in DATASOURCES_RESOLVER_ENDPOINT")

    print(f"Resolving datasources {json.dumps(datasource_request)}")

    headers = {
        'Content-Type': 'application/json'
    }
    
    if authorization_token:
        headers['Authorization'] = f'Bearer {authorization_token}'
    
    try:
        response = requests.post(
            resolver_endpoint,
            headers=headers,
            json={'datasourceRequest': datasource_request}
        )
        
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        import traceback
        traceback.print_exc()
        print(f"Error resolving datasources: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        return {
            'error': f"Failed to resolve datasources: {str(e)}",
            'dataSources': []
        }


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