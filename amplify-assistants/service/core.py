import hashlib
import os
import time
import boto3
import json
import uuid
import random
import string
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from common.data_sources import translate_user_data_sources_to_hash_data_sources
from common.encoders import CombinedEncoder
from common.object_permissions import update_object_permissions, can_access_objects, simulate_can_access_objects
from openaiazure.assistant_api import create_new_openai_assistant

from common.validate import validated

SYSTEM_TAG = "amplify:system"
ASSISTANT_BUILDER_TAG = "amplify:assistant-builder"
ASSISTANT_TAG = "amplify:assistant"
AMPLIFY_AUTOMATION_TAG = "amplify:automation"

RESERVED_TAGS = [
    SYSTEM_TAG,
    ASSISTANT_BUILDER_TAG,
    ASSISTANT_TAG,
    AMPLIFY_AUTOMATION_TAG
]


def get_amplify_automation_assistant():
    instructions = """
You will help accomplish tasks be creating descriptions of javascript fetch operations to execute. I will execute the fetch operations for you and give you the results. You write your fetch code in javascript in special markdown blocks as shown:

```auto
fetch(<SOME URL>, {
            method: 'POST',
            headers: {
                ...
            },
            body: JSON.stringify(<Insert JSON>),
        });
```

All ```auto blocks must have a single statement wtih a fetch call to fetch(...with some params...). 

The supported URLs to fetch from are:

GET, /chats // returns a list of chat threads 
GET, /folders // returns a list of folders 
GET, /models // returns a list of models 
GET, /prompts // returns a list of prompts 
GET, /defaultModelId // returns the default model ID 
GET, /featureFlags // returns a list of feature flags 
GET, /workspaceMetadata // returns workspace metadata 
GET, /selectedConversation // returns the currently selected conversation 
GET, /selectedAssistant // returns the currently selected assistant

Help me accomplish tasks by creating ```auto blocks and then waiting for me to provide the results from the fetch calls. We keep going until the problem is solved.

Always try to output an ```auto block if possible. When the problem is solved, output <<DONE>>
    """

    description = """
Consider this assistant your very own genie, granting your data wishes within Amplify with a simple "command." You make a wish - perhaps for viewing a conversation or organizing your folders - and the assistant spells out the magic words for you to say. With minimal effort on your part, your wish is granted, and you're provided with the treasures you seek.    
    """
    id = "ast/amplify-automation"
    name = "Amplify Automator"
    datasources = []
    tags = [AMPLIFY_AUTOMATION_TAG, SYSTEM_TAG]
    created_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    tools = []
    data = {
        "provider": "amplify",
        "conversationTags": [AMPLIFY_AUTOMATION_TAG],
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


def check_user_can_share_assistant(assistant, user_id):
    if assistant:
        return assistant['user'] == user_id
    return False


def check_user_can_delete_assistant(assistant, user_id):
    if assistant:
        return assistant['user'] == user_id
    return False


def check_user_can_update_assistant(assistant, user_id):
    if assistant:
        return assistant['user'] == user_id
    return False


@validated(op="delete")
def delete_assistant(event, context, current_user, name, data):
    """
    Deletes an assistant from the DynamoDB table based on the assistant's public ID.

    Args:
        event (dict): The event data from the API Gateway.
        context (dict): The Lambda function context.
        current_user (str): The ID of the current user.
        name (str): The name of the operation
        data (dict): The data for the delete operation, including the assistant's public ID.

    Returns:
        dict: A dictionary containing the success status and message.
    """
    print(f"Deleting assistant with data: {data}")

    assistant_public_id = data['data'].get('assistantId', None)
    if not assistant_public_id:
        print("Assistant ID is required for deletion.")
        return {'success': False, 'message': 'Assistant ID is required for deletion.'}

    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])

    try:
        # Check if the user is authorized to delete the assistant
        existing_assistant = get_most_recent_assistant_version(assistants_table, assistant_public_id)
        if not check_user_can_delete_assistant(existing_assistant, current_user):
            print(f"User {current_user} is not authorized to delete assistant {assistant_public_id}")
            return {'success': False, 'message': 'You are not authorized to delete this assistant.'}

        delete_assistant_by_public_id(assistants_table, assistant_public_id)
        print(f"Assistant {assistant_public_id} deleted successfully.")
        return {'success': True, 'message': 'Assistant deleted successfully.'}
    except Exception as e:
        print(f"Error deleting assistant: {e}")
        return {'success': False, 'message': 'Failed to delete assistant.'}


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
    # Add the system assistants
    assistants.append(get_assistant_builder_assistant())
    # assistants.append(get_amplify_automation_assistant())

    assistant_ids = [assistant['id'] for assistant in assistants]

    access_rights = simulate_can_access_objects(data['access_token'], assistant_ids, ['read', 'write'])

    # Make sure each assistant has a data field and initialize it if it doesn't
    for assistant in assistants:
        if 'data' not in assistant:
            assistant['data'] = {}

    # for each assistant, add to its data the access rights
    for assistant in assistants:
        try:
            if assistant['data'] is None:
                assistant['data'] = {'access': None}
            assistant['data']['access'] = access_rights.get(assistant['id'], 'none')
        except Exception as e:
            print(f"Error adding access rights to assistant {assistant['id']}: {e}")

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


def get_assistant(assistant_id):
    """
    Retrieves the assistant with the given ID.

    Args:
        assistant_id (str): The ID of the assistant to retrieve.

    Returns:
        dict: A dictionary representing the assistant, or None if the assistant is not found.
    """
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])

    try:
        # Fetch the item from the DynamoDB table using the assistant ID
        response = assistants_table.get_item(
            Key={
                'id': assistant_id
            }
        )

        # If the item is found, return it
        if 'Item' in response:
            return response['Item']
        else:
            return None
    except Exception as e:
        print(f"Error fetching assistant {assistant_id}: {e}")
        return None


@validated(op="create")
def create_assistant(event, context, current_user, name, data):
    print(f"Creating assistant with data: {data}")

    extracted_data = data['data']
    assistant_name = extracted_data['name']
    description = extracted_data['description']
    uri = extracted_data.get('uri', None)
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
    return create_or_update_assistant(
        access_token=data['access_token'],
        user_that_owns_the_assistant=current_user,
        assistant_name=assistant_name,
        description=description,
        instructions=instructions,
        tags=tags,
        data_sources=data_sources,
        tools=tools,
        provider=provider,
        uri=uri,
        assistant_public_id=assistant_public_id
    )


@validated(op="share_assistant")
def share_assistant(event, context, current_user, name, data):
    extracted_data = data['data']
    assistant_key = extracted_data['assistantId']
    recipient_users = extracted_data['recipientUsers']
    access_type = extracted_data['accessType']
    data_sources = extracted_data['dataSources']
    policy = extracted_data.get('policy', '')

    return share_assistant_with(
        access_token=data['access_token'],
        current_user=current_user,
        assistant_key=assistant_key,
        recipient_users=recipient_users,
        access_type=access_type,
        data_sources=data_sources,
        policy=policy

    )


def share_assistant_with(access_token, current_user, assistant_key, recipient_users, access_type, data_sources, policy=''):
    dynamodb = boto3.resource('dynamodb')
    assistant_entry = get_assistant(assistant_key)

    if not assistant_entry:
        return {'success': False, 'message': 'Assistant not found'}

    if not can_access_objects(
            access_token=access_token,
            data_sources=[{'id': assistant_key}],
            permission_level='owner'):
        return {'success': False, 'message': 'You are not authorized to share this assistant'}

    assistant_public_id = assistant_entry['assistantId']

    if not update_object_permissions(
            access_token=access_token,
            shared_with_users=recipient_users,
            keys=[assistant_public_id],
            object_type='assistant',
            principal_type='user',
            permission_level=access_type,
            policy=policy):
        print(f"Error updating permissions for assistant {assistant_public_id}")
        return {'success': False, 'message': 'Error updating permissions'}
    else:
        print (f"Update data sources object access permissions for users {recipient_users} for assistant {assistant_public_id}")
        update_object_permissions(
            access_token=access_token,
            shared_with_users=recipient_users,
            keys=data_sources,
            object_type='datasource',
            principal_type='user',
            permission_level='read',
            policy='')

        for user in recipient_users:     

            print(f"Creating alias for user {user} for assistant {assistant_public_id}")
            create_assistant_alias(user, assistant_public_id, assistant_entry['id'], assistant_entry['version'],
                                   'latest')
            print(f"Created alias for user {user} for assistant {assistant_public_id}")

        print(f"Successfully updated permissions for assistant {assistant_public_id}")
        return {'success': True, 'message': 'Permissions updated'}


def get_most_recent_assistant_version(assistants_table,
                                      assistant_public_id):
    """
    Retrieves the most recent version of an assistant from the DynamoDB table.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        user_that_owns_the_assistant (str): The ID of the user that owns the assistant.
        assistant_name (str): The name of the assistant.
        assistant_public_id (str): The public ID of the assistant (optional).

    Returns:
        dict: The most recent assistant item, or None if not found.
    """
    if assistant_public_id:
        response = assistants_table.query(
            IndexName='AssistantIdIndex',
            KeyConditionExpression=Key('assistantId').eq(assistant_public_id),
            Limit=1,
            ScanIndexForward=False
        )
        if response['Count'] > 0:
            return max(response['Items'], key=lambda x: x.get('version', 1))

    return None


def save_assistant(assistants_table, assistant_name, description, instructions, data_sources, provider, tools,
                   user_that_owns_the_assistant, version, tags, uri=None, assistant_public_id=None):
    """
    Saves the assistant data to the DynamoDB table.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        assistant_name (str): The name of the assistant.
        description (str): The description of the assistant.
        instructions (str): The instructions for the assistant.
        data_sources (list): A list of data sources used by the assistant.
        provider (str): The provider of the assistant (e.g., 'amplify', 'openai').
        tools (list): A list of tools used by the assistant.
        user_that_owns_the_assistant (str): The ID of the user that owns the assistant.
        assistant_public_id (str): The public ID of the assistant (optional).

    Returns:
        dict: The saved assistant data.
        :param assistant_public_id:
        :param version:
        :param tags:
        :param uri:
    """
    # Get the current timestamp in the format 2024-01-16T12:40:23.308162
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S')

    # Create a dictionary of the core details of the assistant
    # This will be used to create a hash to check if the assistant already exists
    core_sha256, datasources_sha256, full_sha256, instructions_sha256 = \
        get_assistant_hashes(assistant_name,
                             description,
                             instructions,
                             data_sources,
                             provider,
                             tools)

    assistant_database_id = f'ast/{str(uuid.uuid4())}'

    # Create an assistantId
    if not assistant_public_id:
        assistant_public_id = f'astp/{str(uuid.uuid4())}'

    # Create the new item for the DynamoDB table
    new_item = {
        'id': assistant_database_id,
        'assistantId': assistant_public_id,
        'user': user_that_owns_the_assistant,
        'dataSourcesHash': datasources_sha256,
        'instructionsHash': instructions_sha256,
        'tags': tags,
        'uri': uri,
        'coreHash': core_sha256,
        'hash': full_sha256,
        'name': assistant_name,
        'description': description,
        'instructions': instructions,
        'createdAt': timestamp,
        'updatedAt': timestamp,
        'dataSources': data_sources,
        'version': version
    }

    assistants_table.put_item(Item=new_item)
    return new_item


def delete_assistant_by_public_id(assistants_table, assistant_public_id):
    """
    Deletes all versions of an assistant from the DynamoDB table based on the assistant's public ID.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        assistant_public_id (str): The public ID of the assistant.

    Returns:
        None
    """
    response = assistants_table.query(
        IndexName='AssistantIdIndex',
        KeyConditionExpression=Key('assistantId').eq(assistant_public_id)
    )

    for item in response['Items']:
        assistants_table.delete_item(
            Key={
                'id': item['id']
            }
        )


def delete_assistant_by_id(assistants_table, assistant_id):
    """
    Deletes a specific version of an assistant from the DynamoDB table based on the assistant's ID.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        assistant_id (str): The ID of the assistant.

    Returns:
        None
    """
    assistants_table.delete_item(
        Key={
            'id': assistant_id
        }
    )


def delete_assistant_version(assistants_table, assistant_public_id, version):
    """
    Deletes a specific version of an assistant from the DynamoDB table based on the assistant's public ID and version.

    Args:
        assistants_table (boto3.Table): The DynamoDB table for assistants.
        assistant_public_id (str): The public ID of the assistant.
        version (int): The version of the assistant to delete.

    Returns:
        None
    """
    response = assistants_table.query(
        IndexName='AssistantIdIndex',
        KeyConditionExpression=Key('assistantId').eq(assistant_public_id),
        FilterExpression=Attr('version').eq(version)
    )

    for item in response['Items']:
        assistants_table.delete_item(
            Key={
                'id': item['id']
            }
        )


def create_or_update_assistant(
        access_token,
        user_that_owns_the_assistant,
        assistant_name,
        description,
        instructions,
        tags,
        data_sources,
        tools,
        provider,
        uri,
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
        uri (str): The URI of the assistant (optional).
        assistant_public_id (str): The public ID of the assistant (optional).

    Returns:
        dict: A dictionary containing the success status, message, and data (assistant ID and version).
    """
    dynamodb = boto3.resource('dynamodb')
    assistants_table = dynamodb.Table(os.environ['ASSISTANTS_DYNAMODB_TABLE'])

    existing_assistant = get_most_recent_assistant_version(assistants_table, assistant_public_id)

    if existing_assistant:

        if not check_user_can_update_assistant(existing_assistant, user_that_owns_the_assistant):
            return {'success': False, 'message': 'You are not authorized to update this assistant'}

        # The assistant already exists, so we need to create a new version
        assistant_public_id = existing_assistant['assistantId']
        assistant_name = assistant_name
        assistant_version = existing_assistant['version']  # Default to version 1 if not present

        # Increment the version number
        new_version = assistant_version + 1

        new_item = save_assistant(
            assistants_table,
            assistant_name,
            description,
            instructions,
            data_sources,
            provider,
            tools,
            user_that_owns_the_assistant,
            new_version,
            tags,
            uri,
            assistant_public_id
        )
        new_item['version'] = new_version

        # Update the permissions for the new assistant
        if not update_object_permissions(
                access_token,
                [user_that_owns_the_assistant],
                [new_item['id']],
                'assistant',
                'user',
                'owner'):
            print(f"Error updating permissions for assistant {new_item['id']}")
        else:
            print(f"Successfully updated permissions for assistant {new_item['id']}")

        update_assistant_latest_alias(assistant_public_id, new_item['id'], new_version)

        print(f"Indexing assistant {new_item['id']} for RAG")
        save_assistant_for_rag(new_item)
        print(f"Added RAG entry for {new_item['id']}")

        # Return success response
        return {
            'success': True,
            'message': 'Assistant created successfully',
            'data': {'assistantId': assistant_public_id,
                     'id': new_item['id'],
                     'version': new_version}
        }
    else:
        new_item = save_assistant(
            assistants_table,
            assistant_name,
            description,
            instructions,
            data_sources,
            provider,
            tools,
            user_that_owns_the_assistant,
            1,
            tags,
            uri,
            None, 
        )

        # Update the permissions for the new assistant
        if not update_object_permissions(
                access_token,
                [user_that_owns_the_assistant],
                [new_item['assistantId'], new_item['id']],
                'assistant',
                'user',
                'owner'):
            print(f"Error updating permissions for assistant {new_item['id']}")
        else:
            print(f"Successfully updated permissions for assistant {new_item['id']}")

        create_assistant_alias(user_that_owns_the_assistant, new_item['assistantId'], new_item['id'], 1, 'latest')

        print(f"Indexing assistant {new_item['id']} for RAG")
        save_assistant_for_rag(new_item)
        print(f"Added RAG entry for {new_item['id']}")

        # Return success response
        return {
            'success': True,
            'message': 'Assistant created successfully',
            'data': {'assistantId': new_item['assistantId'],
                     'id': new_item['id'],
                     'version': new_item['version']}
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


def alias_key_of_type(assistant_public_id, alias_type):
    return f"{assistant_public_id}?type={alias_type}"


def create_assistant_alias(user, assistant_public_id, database_id, version, alias_type):
    dynamodb = boto3.resource('dynamodb')
    alias_table = dynamodb.Table(os.environ['ASSISTANTS_ALIASES_DYNAMODB_TABLE'])
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S')
    new_item = {
        'assistantId': alias_key_of_type(assistant_public_id, alias_type),
        'user': user,
        'createdAt': timestamp,
        'updatedAt': timestamp,
        'aliasTo': alias_type,
        'currentVersion': version,
        'data': {'id': database_id}
    }
    alias_table.put_item(Item=new_item)


def update_assistant_latest_alias(assistant_public_id, new_id, version):
    update_assistant_alias_by_type(assistant_public_id, new_id, version, 'latest')


def update_assistant_published_alias(assistant_public_id, new_id, version):
    update_assistant_alias_by_type(assistant_public_id, new_id, version, 'latest_published')


def update_assistant_alias_by_type(assistant_public_id, new_id, version, alias_type):
    try:
        dynamodb = boto3.resource('dynamodb')
        alias_table = dynamodb.Table(os.environ['ASSISTANTS_ALIASES_DYNAMODB_TABLE'])

        alias_key = alias_key_of_type(assistant_public_id, alias_type)

        # Find all current entries for assistantId (hash) across all users (range) where version = "latest"
        response = alias_table.query(
            IndexName='AssistantIdIndex',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('assistantId').eq(alias_key)
        )

        for item in response['Items']:
            try:
                print(f"Updating assistant alias: {item}")
                timestamp = time.strftime('%Y-%m-%dT%H:%M:%S')
                updated_item = {
                    'assistantId': alias_key,
                    'user': item['user'],
                    'updatedAt': timestamp,
                    'createdAt': item['createdAt'],
                    'currentVersion': version,
                    'aliasTo': item['aliasTo'],
                    'data': {
                        'id': new_id
                    }
                }
                alias_table.put_item(Item=updated_item)
                print(f"Updated assistant alias: {updated_item}")
            except ClientError as e:
                print(f"Error updating assistant alias: {e}")
    except ClientError as e:
        print(f"Error updating assistant alias: {e}")


def generate_assistant_chunks_metadata(assistant):
    output = {
        "chunks": [
            {
                "content": f"{assistant['description']}",
                "locations": [
                    {
                        "assistantId": assistant['assistantId'],
                        "version": assistant['version'],
                        "updatedAt": assistant['updatedAt'],
                        "createdAt": assistant['createdAt'],
                        "tags": assistant['tags']
                    }
                ],
                "indexes": [0],
                "char_index": 0
            },
            {
                "content": f"{assistant['name']}: {assistant['description']}. {', '.join(assistant['tags'])}",
                "locations": [
                    {
                        "assistantId": assistant['assistantId'],
                        "version": assistant['version'],
                        "updatedAt": assistant['updatedAt'],
                        "createdAt": assistant['createdAt'],
                        "tags": assistant['tags']
                    }
                ],
                "indexes": [0],
                "char_index": 0
            },
            {
                "content": assistant['instructions'],
                "locations": [
                    {
                        "assistantId": assistant['assistantId'],
                        "version": assistant['version'],
                        "updatedAt": assistant['updatedAt'],
                        "createdAt": assistant['createdAt'],
                        "tags": assistant['tags']
                    }
                ],
                "indexes": [0],
                "char_index": 0
            },
            {
                "content": f"{assistant['name']}: {assistant['instructions']}. {', '.join(assistant['tags'])}",
                "locations": [
                    {
                        "assistantId": assistant['assistantId'],
                        "version": assistant['version'],
                        "updatedAt": assistant['updatedAt'],
                        "createdAt": assistant['createdAt'],
                        "tags": assistant['tags']
                    }
                ],
                "indexes": [0],
                "char_index": 0
            }
        ],
        "src": assistant['id']
    }
    return output


def save_assistant_for_rag(assistant):
    try:
        key = assistant['id']
        assistant_chunks = generate_assistant_chunks_metadata(assistant)
        chunks_bucket = os.environ['S3_RAG_CHUNKS_BUCKET_NAME']

        s3 = boto3.client('s3')
        print(f"Saving assistant description to {key}-assistant.chunks.json")
        chunks_key = f"assistants/{key}-assistant.chunks.json"
        s3.put_object(Bucket=chunks_bucket,
                      Key=chunks_key,
                      Body=json.dumps(assistant_chunks, cls=CombinedEncoder))
        print(f"Uploaded chunks to {chunks_bucket}/{chunks_key}")
    except Exception as e:
        print(f"Error saving assistant for RAG: {e}")
