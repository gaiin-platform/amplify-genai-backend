import hashlib
import os
import time
import boto3
import json
import uuid

from botocore.exceptions import ClientError

from common.data_sources import translate_user_data_sources_to_hash_data_sources
from common.object_permissions import update_object_permissions, can_access_objects
from openaiazure.assistant_api import create_new_openai_assistant

from common.validate import validated


@validated(op="create")
def create_assistant(event, context, current_user, name, data):
    extracted_data = data['data']
    assistant_name = extracted_data['name']
    description = extracted_data['description']
    tags = extracted_data.get('tags', [])
    instructions = extracted_data['instructions']
    data_sources = extracted_data.get('dataSources', [])
    tools = extracted_data.get('tools', [])
    provider = extracted_data.get('provider', 'amplify')

    print(f"Data sources before translation: {data_sources}")
    data_sources = translate_user_data_sources_to_hash_data_sources(data_sources)
    print(f"Data sources after translation: {data_sources}")

    # Auth check: need to update to new permissions endpoint
    if not can_access_objects(data['access_token'], data_sources):
        return {'success': False, 'message': 'You are not authorized to access the referenced files'}

    # Assuming get_openai_client and file_keys_to_file_ids functions are defined elsewhere
    return create_new_assistant(
        access_token=data['access_token'],
        user_id=current_user,
        assistant_name=assistant_name,
        description=description,
        instructions=instructions,
        tags=tags,
        data_sources=data_sources,
        tools=tools,
        provider=provider
    )


@validated(op="share_assistant")
def share_assistant(event, context, current_user, name, data):
    extracted_data = data['data']
    assistant_key = extracted_data['assistantKey']
    recipient_users = extracted_data['recipientUsers']
    access_type = extracted_data['accessType']
    policy = extracted_data.get('policy', '')

    return share_assistant_with(
        access_token=data['access_token'],
        current_user=current_user,
        assistant_key=assistant_key,
        recipient_users=recipient_users,
        access_type=access_type,
        policy=policy
    )


def share_assistant_with(access_token, current_user, assistant_key, recipient_users, access_type, policy=''):
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])
    assistant_entry = assistants_table.get_item(Key={'id': assistant_key})

    if not assistant_entry or 'Item' not in assistant_entry:
        return {'success': False, 'message': 'Assistant not found'}

    if not can_access_objects(
            access_token=access_token,
            data_sources=[{'id': assistant_key}],
            permission_level='owner'):
        return {'success': False, 'message': 'You are not authorized to share this assistant'}

    if not update_object_permissions(
            access_token=access_token,
            shared_with_users=recipient_users,
            keys=[assistant_key],
            object_type='assistant',
            principal_type='user',
            permission_level=access_type,
            policy=policy):
        print(f"Error updating permissions for assistant {assistant_key}")
        return {'success': False, 'message': 'Error updating permissions'}
    else:
        print(f"Successfully updated permissions for assistant {assistant_key}")
        return {'success': True, 'message': 'Permissions updated'}


def create_new_assistant(
        access_token,
        user_id,
        assistant_name,
        description,
        instructions,
        tags,
        data_sources,
        tools,
        provider
):
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])
    timestamp = int(time.time() * 1000)

    # Create a dictionary of the core details of the assistant
    # This will be used to create a hash to check if the assistant already exists
    core_sha256, datasources_sha256, full_sha256, instructions_sha256 = \
        get_assistant_hashes(assistant_name,
                             description,
                             instructions,
                             data_sources,
                             provider,
                             tools)

    data = {'provider': 'amplify'}
    if provider == 'openai':
        result = create_new_openai_assistant(
            assistant_name,
            instructions,
            data_sources,
            tools
        )
        data = result['data']

    user_id_key = f'ast/{full_sha256}/{str(uuid.uuid4())}'
    hash_id_key = f'ast/{full_sha256}'

    # DynamoDB new item structure for the assistant
    new_item = {
        'id': user_id_key,
        'user': user_id,
        'dataSourcesHash': datasources_sha256,
        'instructionsHash': instructions_sha256,
        'coreHash': core_sha256,
        'hash': full_sha256,
        'assistant': assistant_name,
        'description': description,
        'instructions': instructions,
        'tags': tags,
        'createdAt': timestamp,
        'updatedAt': timestamp,
        'dataSources': data_sources,
        'data': data
    }
    print(json.dumps(new_item, indent=4))
    # Put the new item into the DynamoDB table
    assistants_table.put_item(Item=new_item)

    # Check if the assistant already exists by the hash_id_key
    existing = assistants_table.get_item(Key={'id': hash_id_key})
    if not existing or 'Item' not in existing:
        # Return success response
        # DynamoDB new item structure for the assistant
        hash_item = {
            'id': hash_id_key,
            'dataSourcesHash': datasources_sha256,
            'instructionsHash': instructions_sha256,
            'coreHash': core_sha256,
            'hash': full_sha256,
            'assistant': assistant_name,
            'description': description,
            'instructions': instructions,
            'tags': tags,
            'createdAt': timestamp,
            'updatedAt': timestamp,
            'dataSources': data_sources,
            'data': data
        }
        print(json.dumps(new_item, indent=4))
        # Put the new item into the DynamoDB table
        assistants_table.put_item(Item=hash_item)

    if not update_object_permissions(
            access_token,
            [user_id],
            [hash_id_key, user_id_key],
            'assistant',
            'user',
            'owner'):
        print(f"Error updating permissions for assistant {user_id_key}")
    else:
        print(f"Successfully updated permissions for assistant {user_id_key}")

    # Return success response
    return {
        'success': True,
        'message': 'Assistant created successfully',
        'data': {'assistantId': user_id_key}
    }


def get_assistant_hashes(assistant_name, description, instructions, data_sources, provider, tools):
    core_details = {
        'instructions': instructions,
        'dataSources': data_sources,
        'tools': tools,
        'provider': provider
    }
    # Create a sha256 of the core details to use as a hash
    # This will be used to check if the assistant already exists
    # and to check if the assistant has been updated
    core_sha256 = hashlib.sha256(json.dumps(core_details, sort_keys=True).encode()).hexdigest()
    datasources_sha256 = hashlib.sha256(json.dumps(data_sources.sort(key=lambda x: x['id'])).encode()).hexdigest()
    instructions_sha256 = hashlib.sha256(json.dumps(instructions, sort_keys=True).encode()).hexdigest()
    core_details['assistant'] = assistant_name
    core_details['description'] = description
    full_sha256 = hashlib.sha256(json.dumps(core_details, sort_keys=True).encode()).hexdigest()
    return core_sha256, datasources_sha256, full_sha256, instructions_sha256
