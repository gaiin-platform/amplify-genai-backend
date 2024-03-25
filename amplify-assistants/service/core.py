import hashlib
import os
import time
import boto3
import json
import uuid
import random
import string
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.data_sources import translate_user_data_sources_to_hash_data_sources
from common.object_permissions import update_object_permissions, can_access_objects, simulate_can_access_objects
from openaiazure.assistant_api import create_new_openai_assistant

from common.validate import validated


SYSTEM_TAG = "amplify:system"
ASSISTANT_BUILDER_TAG = "amplify:assistant-builder"
ASSISTANT_TAG = "amplify:assistant"

RESERVED_TAGS = [
    SYSTEM_TAG,
    ASSISTANT_BUILDER_TAG,
    ASSISTANT_TAG
]


def get_assistant_builder_assistant():
    instructions = """
You are going to help me build a customized ChatGPT assistant. To do this, you will need to help me create the instructions that guide the assistant in its job. 

What we want to define is:
1. A name and description of the assistant. 
2. What the assistant does.
3. What are the rules about how it does its work (e.g., what questions it will or won't answer, things its way of working, etc.)
4. It's tone of voice. Is it informal or formal in style. Does it have a persona or personality?

You will ask me questions to help determine these things. As we go, try to incrementally output values for all these things. You will write the instructions in a detailed manner that incorporates all of my feedback. Every time I give you new information that changes things, update the assistant.

At the end of every message you output, you will update the assistant in a special code block WITH THIS EXACT FORMAT:

```assistant
{
"name": "<FILL IN NAME>"
"description": "<FILL IN DESCRIPTION>"
"instructions": "<FILL IN INSTRUCTIONS>"
}
```
    """

    description = "This assistant will guide you through the process of building a customized large language model assistant."
    id = "ast/assistant-builder"
    name = "Assistant Creator"
    datasources = []
    tags = [ASSISTANT_BUILDER_TAG, SYSTEM_TAG]
    created_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    tools = []
    data = {
        "provider": "amplify",
        "conversationTags": [ASSISTANT_BUILDER_TAG],
    }

    return {
        'id': id,
        'coreHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'hash': hashlib.sha256(instructions.encode()).hexdigest(),
        'instructionsHash': hashlib.sha256(instructions.encode()).hexdigest(),
        'dataSourcesHash': hashlib.sha256(json.dumps(datasources).encode()).hexdigest(),
        'version': 1,
        'name': name,
        'description': description,
        'instructions': instructions,
        'tags': tags,
        'createdAt': created_at,
        'updatedAt': updated_at,
        'dataSources': datasources,
        'data': data,
        'tools': tools,
        'user': 'amplify'
    }


@validated(op="list")
def list_assistants(event, context, current_user, name, data):
    """
    Retrieves all assistants associated with the current user.

    Args:
        event (dict): The event object containing the request data.
        context (dict): The context object containing information about the current environment.
        current_user (str): The ID of the current user.
        name (str): The name of the assistant (not used in this function).
        data (dict): The data object containing additional parameters (not used in this function).

    Returns:
        dict: A dictionary containing the list of assistants.
    """
    assistants = list_user_assistants(current_user)
    assistants.append(get_assistant_builder_assistant())


    assistant_ids = [assistant['id'] for assistant in assistants]
    access_rights = simulate_can_access_objects(data['access_token'], assistant_ids, ['read','write'])

    # Make sure each assistant has a data field and initialize it if it doesn't
    for assistant in assistants:
        if 'data' not in assistant:
            assistant['data'] = {}

    # for each assistant, add to its data the access rights
    for assistant in assistants:
        assistant['data']['access'] = access_rights.get(assistant['id'], 'none')

    return {
        'success': True,
        'message': 'Assistants retrieved successfully',
        'data': assistants
    }


def list_user_assistants(user_id):
    """
    Retrieves all assistants associated with the given user ID and returns them as a list of dictionaries.

    Args:
        user_id (str): The ID of the user.

    Returns:
        list: A list of dictionaries, where each dictionary represents an assistant.
    """
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])

    # Query the DynamoDB table to get all assistants for the user
    response = assistants_table.query(
        IndexName='UserNameIndex',
        KeyConditionExpression=Key('user').eq(user_id),
    )

    # Create a list of dictionaries representing the assistants
    assistants = [item for item in response['Items']]

    return assistants


@validated(op="create")
def create_assistant(event, context, current_user, name, data):
    print(f"Creating assistant with data: {data}")

    extracted_data = data['data']
    assistant_name = extracted_data['name']
    description = extracted_data['description']
    assistant_public_id = extracted_data.get('assistantId', None)
    tags = extracted_data.get('tags', [])

    # delete any tag that starts with amplify: or is in the reserved tags
    tags = [tag for tag in tags if not tag.startswith("amplify:") and tag not in RESERVED_TAGS]

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
        user_that_owns_the_assistant=current_user,
        assistant_name=assistant_name,
        description=description,
        instructions=instructions,
        tags=tags,
        data_sources=data_sources,
        tools=tools,
        provider=provider,
        assistant_public_id=assistant_public_id
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
        user_that_owns_the_assistant,
        assistant_name,
        description,
        instructions,
        tags,
        data_sources,
        tools,
        provider,
        assistant_public_id=None
):
    """
    Creates a new assistant in the DynamoDB table and sets the appropriate permissions.

    Args:
        access_token (str): The access token of the user (required for updating permissions to give the user access).
        user_that_owns_the_assistant (str): The ID of the user creating the assistant.
        assistant_name (str): The name of the assistant.
        description (str): The description of the assistant.
        instructions (str): The instructions for the assistant.
        tags (list): A list of tags associated with the assistant.
        data_sources (list): A list of data sources used by the assistant.
        tools (list): A list of tools used by the assistant.
        provider (str): The provider of the assistant (e.g., 'amplify', 'openai').
        assistant_public_id (str): The public ID of the assistant (optional).

    Returns:
        dict: A dictionary containing the success status, message, and data (assistant ID and version).
    """
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])

    # Get the current timestamp in the format 2024-01-16T12:40:23.308162
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S')

    data = {'provider': 'amplify'}
    if provider == 'openai':
        result = create_new_openai_assistant(
            assistant_name,
            instructions,
            data_sources,
            tools
        )
        data = result['data']

    # Create a dictionary of the core details of the assistant
    # This will be used to create a hash to check if the assistant already exists
    core_sha256, datasources_sha256, full_sha256, instructions_sha256 = \
        get_assistant_hashes(assistant_name,
                             description,
                             instructions,
                             data_sources,
                             provider,
                             tools)

    # Check if the assistant already exists in the DynamoDB table
    if assistant_public_id:
        response = assistants_table.query(
            IndexName='AssistantIdIndex',
            KeyConditionExpression=Key('assistantId').eq(assistant_public_id),
            Limit=1,
            ScanIndexForward=False
        )
        if response['Count'] == 0:
            return {'success': False, 'message': 'Assistant not found'}
    else:
        response = assistants_table.query(
            IndexName='UserNameIndex',
            KeyConditionExpression=Key('user').eq(user_that_owns_the_assistant) & Key('name').eq(assistant_name),
        )

    if response['Count'] > 0:
        # The assistant already exists, so we need to create a new version
        existing_assistant = max(response['Items'], key=lambda x: x.get('version', 1))
        assistant_db_id = existing_assistant['id']
        assistant_public_id = existing_assistant['assistantId']
        assistant_name = assistant_name
        assistant_version = existing_assistant['version']  # Default to version 1 if not present
        print(f"Assistant already exists with ID: {assistant_db_id} and version: {assistant_version}")

        # Increment the version number
        new_version = assistant_version + 1

        # Create a new user-specific ID with the same UUID component and incremented version
        uuid_part = assistant_db_id.split('/')[2]
        assistant_database_id = f'ast/{full_sha256}/{uuid_part}/{new_version}'
        hash_id_key = f'ast/{full_sha256}/{new_version}'

        # Create the new item for the DynamoDB table
        new_item = {
            'id': assistant_database_id,
            'assistantId': assistant_public_id,
            'user': user_that_owns_the_assistant,
            'dataSourcesHash': datasources_sha256,
            'instructionsHash': instructions_sha256,
            'coreHash': core_sha256,
            'hash': full_sha256,
            'name': assistant_name,
            'description': description,
            'instructions': instructions,
            'tags': tags,
            'createdAt': timestamp,
            'updatedAt': timestamp,
            'dataSources': data_sources,
            'data': data,
            'version': new_version
        }
        # print(json.dumps(new_item, indent=4))
        assistants_table.put_item(Item=new_item)

        # Update the permissions for the new assistant
        if not update_object_permissions(
                access_token,
                [user_that_owns_the_assistant],
                [assistant_public_id, assistant_database_id],
                'assistant',
                'user',
                'owner'):
            print(f"Error updating permissions for assistant {assistant_database_id}")
        else:
            print(f"Successfully updated permissions for assistant {assistant_database_id}")

        # Return success response
        return {
            'success': True,
            'message': 'Assistant created successfully',
            'data': {'assistantId': assistant_database_id, 'version': new_version}
        }
    else:
        # The assistant does not exist, so we can create a new one
        assistant_database_id = f'ast/{full_sha256}/{str(uuid.uuid4())}/1'

        # Create an assistantId
        assistant_public_id = f'astp/{str(uuid.uuid4())}'

        # Create the new item for the DynamoDB table
        new_item = {
            'id': assistant_database_id,
            'assistantId': assistant_public_id,
            'user': user_that_owns_the_assistant,
            'dataSourcesHash': datasources_sha256,
            'instructionsHash': instructions_sha256,
            'coreHash': core_sha256,
            'hash': full_sha256,
            'name': assistant_name,
            'description': description,
            'instructions': instructions,
            'tags': tags,
            'createdAt': timestamp,
            'updatedAt': timestamp,
            'dataSources': data_sources,
            'data': data,
            'version': 1
        }
        # print(json.dumps(new_item, indent=4))
        assistants_table.put_item(Item=new_item)

        # Update the permissions for the new assistant
        if not update_object_permissions(
                access_token,
                [user_that_owns_the_assistant],
                [assistant_public_id, assistant_database_id],
                'assistant',
                'user',
                'owner'):
            print(f"Error updating permissions for assistant {assistant_database_id}")
        else:
            print(f"Successfully updated permissions for assistant {assistant_database_id}")

        # Return success response
        return {
            'success': True,
            'message': 'Assistant created successfully',
            'data': {'assistantId': assistant_public_id}
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
