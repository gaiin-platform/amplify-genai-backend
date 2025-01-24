import os
import re
import uuid
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
from common.api_key import deactivate_key
from common.assistants import share_assistant, list_assistants, delete_assistant, create_assistant
import boto3
from common.validate import validated
from common.data_sources import translate_user_data_sources_to_hash_data_sources
from common.auth_admin import verify_user_as_admin
from common.amplify_groups import verify_user_in_amp_group
from common.register_ops import register_ops
from base_ast_group_ops import ops


# Setup AWS DynamoDB access
dynamodb = boto3.resource('dynamodb')
groups_table = dynamodb.Table(os.environ['AMPLIFY_GROUPS_DYNAMODB_TABLE'])


@validated(op='create')
def create_group(event, context, current_user, name, data):
    print("Initiating group creation process")
    data = data['data']
    group_name = data['group_name']
    members = data.get('members', {}) # includes admins
    group_types = data.get('types', {})
    return group_creation(current_user, group_name, members, group_types)

def group_creation(current_user, group_name, members, group_types):
    # create_api key - 
    api_key_result = create_api_key_for_group(group_name)
    if (not api_key_result['success']):
        return {
                'success': False,
                'message': f"Failed to create group '{group_name}'"
            }
    group_id = api_key_result['group_id']
    print("Group Id: ", group_id)
    # Prepare the item dictionary for DynamoDB
    item = {
        'group_id': group_id,
        'groupName': group_name,
        'members': members,  # memebers are an object {"user": "read or write} 
        'assistants': [], # contains ast/ id because sharing/permission require it 
        'createdBy': current_user,
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'access': api_key_result['api_key'],
        'groupTypes' : group_types,
        'amplifyGroups':[],
        'systemUsers': []
    }
    
    try:
        response = groups_table.put_item(Item=item)
        # Check if the response was successful
        if response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200:
            print(f"Group '{group_name}' created successfully")

            #log
            log_item(group_id, 'Create Group', current_user, f"Group '{group_name}' created with initial members: {members}")

            return {
                'success': True,
                'message': f"Group '{group_name}' created successfully",
                'data': {
                    'id': group_id,
                    'name': group_name,
                    'members': members,
                    'assistants': [],
                    'groupTypes': group_types
                }
            }
        else:
            print(f"Failed to create group '{group_name}'")
            return {
                'success': False,
                'message': f"Failed to create group '{group_name}'"
            }
    except Exception as e:
        # Handle potential errors during the DynamoDB operation
        print(f"An error occurred while creating group '{group_name}': {e}")
        return {
            'success': False,
            'message': f"An error occurred while creating group: {str(e)}"
        }

def pascalCase(input_str):
    return ''.join(x for x in input_str.title() if not x.isspace() and x.isalnum())

def create_api_key_for_group(group_name):
    id = str(uuid.uuid4())

    name = pascalCase(group_name)
    group_id =  name + "_" + id

    print("create api key for group")
    api_keys_table_name = os.environ['API_KEYS_DYNAMODB_TABLE']
    api_table = dynamodb.Table(api_keys_table_name)

    api_owner_id = f"{name}/systemKey/{id}"
    timestamp = datetime.now(timezone.utc).isoformat()

    apiKey = 'amp-' + str(uuid.uuid4())
    try:
        print("Put entry in api keys table")
        # Put (or update) the item for the specified user in the DynamoDB table
        response = api_table.put_item(
            Item={
                'api_owner_id': api_owner_id,
                'owner': group_id,
                'apiKey': apiKey,
                'systemId' : group_id,
                'active': True,
                'createdAt': timestamp, 
                'accessTypes': ["api_key", 'assistants' ],
                'account': { 'id': 'group_account', 'name': 'No COA Required' },
                'rateLimit': { 'rate': None, 'period': "Unlimited" },
                'purpose': "group"
            }
        )

        if response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200:
            print(f"API key for created successfully")
            return {
                'success': True,
                'api_key': apiKey,
                'group_id': group_id
            }
        else:
            print(f"Failed to create API key")
            return {
                'success': False,
                'message': 'Failed to create API key'
            }
    except Exception as e:
        print(f"An error occurred while saving API key: {e}")
        return {
            'success': False,
            'message': f"An error occurred while saving API key: {str(e)}"
        }
    
# not in use currently, going with group level permissions instead 
def separate_members_by_access(members_dict):
    members_by_access = {"read":[], "write":[]}
    for member, access_type in members_dict.items():
        if access_type in ["read", "write", "admin"]:
            access = 'write' if (access_type == 'admin') else access_type
            members_by_access[access].append(member)
   
    return tuple(members_by_access.values())


# not in use currently, going with group level permissions instead 
def update_ast_perms_for_members(members_dict, assistants, access_token):
    print("Enter update permissions for members")
    read_members, write_members = separate_members_by_access(members_dict)
    # give access to all the group ast
    for ast in assistants:
        for access_type, recipients in  [("read", read_members), ("write", write_members)]:
            data = {
                    'assistantId': ast['id'],
                    'recipientUsers': recipients,
                    'accessType': access_type,
                    'policy': '',
                    "shareToS3": False
                }
            print(f"updating permissions {access_type} for members {recipients}")
            if len(recipients) > 0 and not share_assistant(access_token, data):
                print("Error making share assistant calls for assistant: ", ast['id'])
                return {'success': False, 'error': 'Could not successfully make the call to share assistants with members'}
            
    return {'success': True, 'message': 'successfully made the call to share assistants with members'}
        


@validated(op='update')
def update_members(event, context, current_user, name, data):
    data = data['data']
    group_id = data['group_id']
    update_type = data['update_type']
    members = data.get('members') #dict for ADD , list for REMOVE

    auth_check = authorized_user(group_id, current_user, True)
    
    if (not auth_check['success']):
        return auth_check
    
    item = auth_check['item']
    token = item["access"]
    current_members = item.get('members', {})

    # Update the members list based on the type
    if update_type == 'ADD':
        updated_members = {**current_members, **members}

    elif update_type == 'REMOVE':
        updated_members = {k: v for k, v in current_members.items() if k not in members}

        existingMembers = [member for member in members if member not in updated_members]
        print("Existing members to remove: ", existingMembers)

    else:
        return {"message": "Invalid update type", "success": False}

    # Update the item in DynamoDB
    update_res = update_members_db(group_id, updated_members)
    if (not update_res['success']):
        return update_res
    
    #log 
    log_item(group_id, 'Update Members', current_user, f"Members updated with {update_type}\nAffected members {members}\nUpdated members list {updated_members}")

    return {"message": "Members updated successfully", "success": True}

def update_members_db(group_id, updated_members):
    try:
        response = groups_table.update_item(
            Key={'group_id': group_id},
            UpdateExpression="set members = :m",
            ExpressionAttributeValues={
                ':m': updated_members
            },
            ReturnValues="UPDATED_NEW"
        )
        return {'success': True}
    except Exception as e:
        print(f"Failed to update DynamoDB: {str(e)}")
        return {'success': False, 'message': f"Failed to update DynamoDB: {str(e)}"}


@validated(op='update')
def update_members_permission(event, context, current_user, name, data):
    token = data['access_token']
    data = data['data']
    group_id = data['group_id']
    affected_user_dict = data['affected_members']

    auth_check = authorized_user(group_id, current_user, True)
    
    if (not auth_check['success']):
        return auth_check
    
    item = auth_check['item']
    

    # Update the permission of the specified member
    current_members = item.get('members', [])
    # print("before updates members: ", current_members)
    for affected_user, new_permission in affected_user_dict.items():
        memberPerms = current_members.get(affected_user, None)
        if not memberPerms:
            print(f"Member {affected_user} not found in group")
            # return {"message": f"Member {affected_user} not found in group", "success": False}
        
        if memberPerms == new_permission:
            print("Member already has the desired new permissions")
        else:
            current_members[affected_user] = new_permission


    update_res = update_members_db(group_id, current_members)
    if (not update_res['success']):
        return update_res
   
    log_item(group_id, 'Update Member Permissions', current_user, f"Permission updated for members: {affected_user_dict}")

    return {"message": "Member permission updated successfully", "success": True}



def remove_old_ast_versions(current_assistants, astp):
    for i in range(len(current_assistants) - 1, -1, -1):
        if current_assistants[i]['assistantId'] == astp:
            del current_assistants[i]
    return current_assistants

def is_ds_owner(ds, group_id):
    return group_id in ds.get('id','') or group_id in ds.get('key','')

def update_group_ds_perms(ast_ds, group_type_data, group_id):
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    table = dynamodb.Table(table_name)
    print("ast ds: ", ast_ds)
    print("groupType ds: ", group_type_data)

    #compile ds into one list 
    # uploaded ones have the correct permissions, data selected from the user files do not, so we need to share it with the group 
    ds_selector_ds = [
        ds for info in group_type_data.values()
        if 'dataSources' in info
        for ds in info['dataSources']
        if  not is_ds_owner(ds, group_id)
    ]
    ds_selector_ds.extend(
        ds for ds in ast_ds if not is_ds_owner(ds, group_id)
    )

    # print("Updating permissions for the following ds prior to translation: ", ds_selector_ds)


    try:
        translated_ds = translate_user_data_sources_to_hash_data_sources(ds_selector_ds)
        print("Updating permissions for the following ds: ", translated_ds)
        for ds in translated_ds:
            table.put_item(Item={
                    'object_id': ds['id'],
                    'principal_id': group_id,
                    'principal_type':  'group',
                    'object_type': 'datasource',
                    'permission_level': 'read',  
                    'policy': None
            })
            
                
        return {'success': True}
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return {'success': False, 'error': str(e)}


@validated(op='update')
def update_group_assistants(event, context, current_user, name, data):
    data = data['data']
    group_id = data['group_id']
    update_type = data['update_type']
    ast_list = data['assistants']
    return update_assistants(current_user, group_id, update_type, ast_list)


def update_assistants(current_user, group_id, update_type, ast_list):
    auth_check = authorized_user(group_id, current_user)
    
    if (not auth_check['success']):
        return auth_check
    
    item = auth_check['item']
    access_token = item['access']

    current_assistants = item.get('assistants', [])

    new_assistants = []
    if update_type in ["ADD", "UPDATE"]:
        for ast in ast_list:
            groupDSData = {} 
            if ('data' in ast and 'groupTypeData' in ast["data"]): groupDSData = ast["data"]["groupTypeData"]
            # always reset here in case access to conv analysis has been revoked at a group level
            # Note for future: as you can see this only applies when an assistant has been updated. 
            if (('data' in ast and 'supportConvAnalysis' in ast["data"]) and 
                 not item.get('supportConvAnalysis', False)): ast["data"]["supportConvAnalysis"] = False

            update_perms_result = update_group_ds_perms(ast['dataSources'], groupDSData, group_id)
            if (not update_perms_result['success']):
                print("could not update ds perms for all group type data")
                return update_perms_result
            
            create_result = create_assistant(access_token, ast)
            if (not create_result['success']):
                print("create ast call failed")
                return create_result
            
            data = create_result['data']
            print("AST Data: ", data)
            
            new_ast_data ={'id':  data["id"], 'assistantId': data["assistantId"], "version": data['version'], "name" : ast["name"]}
            new_assistants.append(new_ast_data)
            if (update_type == "UPDATE"):
                current_assistants = remove_old_ast_versions(current_assistants, data['assistantId'])
        
        print("All ast updated/added")
        
        current_assistants += new_assistants

        log_item(group_id, update_type + ' assistants', current_user, f"Assistants {new_assistants}")

    elif update_type == 'REMOVE':
        #delete with public id 
        for astp in ast_list:# list of assistantIds
            #permissions are removed in the delete process
            if not delete_assistant(access_token, {'assistantId' : astp,
                                                   "removePermsForUsers": []#list(members.keys() 
                                                   }):
                print ("failed to delete assistant: ", astp)
            current_assistants = remove_old_ast_versions(current_assistants, astp)
                    
        log_item(group_id, 'remove assistants', current_user, f"Assistants {ast_list}")
    else:
        return {"message": "Invalid update type", "success": False}

    try:
        print("Update assistants in groups table")
        response = groups_table.update_item(
            Key={'group_id': group_id},
            UpdateExpression="set assistants = :o",
            ExpressionAttributeValues={
                ':o': current_assistants
            },
            ReturnValues="UPDATED_NEW"
        )
        return { "message": "Assistants updated successfully", "success": True, 
                 "assistantData": [ {'id': assistant['id'], 'assistantId': assistant['assistantId'], 'provider': 'amplify'}
                                    for assistant in new_assistants]
               }
    except Exception as e:
        print(f"Failed to update DynamoDB: {str(e)}")
        return {'success': False, 'message': f"Failed to update DynamoDB: {str(e)}"}


@validated(op='update')
def update_group_types(event, context, current_user, name, data):
    data = data['data']
    group_id = data['group_id']
    new_group_types = data['types']
    
    # Perform authorization check
    auth_check = authorized_user(group_id, current_user, True)
    if not auth_check['success']:
        return auth_check

    try:
        item = auth_check['item']

        old_group_types = item.get('groupTypes', [])

        # Update the group_types in the database
        response = groups_table.update_item(
            Key={'group_id': group_id},
            UpdateExpression="set groupTypes = :gt",
            ExpressionAttributeValues={
                ':gt': new_group_types
            },
            ReturnValues="UPDATED_NEW"
        )
        old_keys = set(old_group_types)
        new_keys = set(new_group_types)

        # Determine the types that were added, removed, and updated
        types_added = new_keys - old_keys
        types_removed = old_keys - new_keys

        change_summary = f"Group types updated to {new_group_types}\n\n"
        if types_removed:
            change_summary += f"Types Removed: {types_removed}\n"
        if types_added:
            change_summary += f"Types Added: {types_added}\n"

        # Log the update
        log_item(group_id, 'Group Types Updated', current_user, f"Group types updated to {new_group_types} \n\nChange Summary\n{change_summary}")

        return {"message": "Group types updated successfully", "success": True}

    except Exception as e:
        # Log the exception and return an error message
        print(group_id, 'Update Group Types', current_user, str(e))
        return {"message": "Failed to update group types due to an error.", "success": False, "error": str(e)}

def is_valid_group_id(group_id):
    pattern = r'^.+\_[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}$'
    return bool(re.match(pattern, group_id))


@validated(op='delete')
def delete_group(event, context, current_user, name, data):
    query_params = event.get('queryStringParameters', {})
    print("Query params: ", query_params)
    group_id = query_params.get('group_id', '')
    if (not group_id or not is_valid_group_id(group_id)):
        return {"message": "Invalid or missing group id parameter", "success": False}

    auth_check = authorized_user(group_id, current_user, True)
    
    if (not auth_check['success']):
        return auth_check
    
    item = auth_check['item']
    
    assistants = item['assistants']
    for ast_data in assistants:# list of assistantIds
        #permissions are removed in the delete process
        if not delete_assistant(item['access'], {'assistantId' : ast_data['assistantId'],
                                                 "removePermsForUsers": [] #list(item['members'].keys())
                                                 }):
            print ("failed to delete assistant: ", ast_data)

    try:
        response = groups_table.delete_item(
            Key={'group_id': group_id}
        )
        # Check the response for successful deletion
        if response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200:
            log_item(group_id, 'Delete Group', current_user, f"Group {group_id} deleted")
            
            #deactivate key 
            owner_api_id = group_id.replace('_', '/systemKey/')
            deactivate_key(item['access'], owner_api_id)

            return {"message": "Group deleted successfully", "success": True}
        else:
            return {"message": "Failed to delete group", "success": False}

    except Exception as e:
        return {"message": f"An error occurred: {str(e)}", "success": False}

def get_latest_assistants(assistants):
    latest_assistants = {}
    for assistant in assistants:
        # Set version to 1 if it doesn't exist
        assistant.setdefault('version', 1)
        assistant_id = assistant.get('assistantId', None)
        # will exclude system ast since they dont have assistantId
        if (assistant_id and (assistant_id not in latest_assistants or latest_assistants[assistant_id]['version'] < assistant['version'])):
            latest_assistants[assistant_id] = assistant
    
    return list(latest_assistants.values())


@validated(op='list')
def list_groups(event, context, current_user, name, data):
    groups_result = get_my_groups(current_user, data['access_token'])
    if (not groups_result['success']):
        return groups_result
    groups = groups_result['data']
    
    group_info = []
    failed_to_list = []
    for group in groups:
        group_name = group['groupName']
        # use api key to call list ast 
        ast_result = list_assistants(group['access'])

        if (not ast_result['success']):
            failed_to_list.append(group_name)
            continue

        #filter old versions
        assistants = get_latest_assistants(ast_result['data'])
        print(f"{group_name} - {len(assistants)}")
        published_assistants = []
        #append groupId and correct permissions if published
        for ast in assistants:
            ast_access = group["members"][current_user]
            hasAdminInterfaceAccess = ast_access in ['write', 'admin']
            if (("isPublished" in ast["data"] and ast["data"]["isPublished"]) or hasAdminInterfaceAccess):
                ast['groupId'] = group["group_id"]
                ast["data"]["access"]['write'] = hasAdminInterfaceAccess
                published_assistants.append(ast)
                
        if (len(published_assistants) > 0):
            group_info.append({
                'name' : group_name, 
                'id' : group['group_id'], 
                'members': group['members'], 
                'assistants': published_assistants,
                'groupTypes': group['groupTypes'],
                'supportConvAnalysis': group.get('supportConvAnalysis', False), 
            })

    if (len(failed_to_list) == len(group_info)):
        return {
            'success': False,
            'message': "Failed to retrive assistants for users groups"
        }
    return {"success": True, "data": group_info, "incompleteGroupData": failed_to_list}    

def get_my_groups(current_user, token):
    group_rows = []
    try:
        response = groups_table.scan()

        for group in response.get('Items', []):
            if is_group_member(current_user, group, token): group_rows.append(group)

        # Handle pagination if there are more records
        while 'LastEvaluatedKey' in response:
            response = groups_table.scan(
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            for group in response.get('Items', []):
                if is_group_member(current_user, group, token): group_rows.append(group)


        return {
            'success': True,
            'data': group_rows
        }
    except Exception as e:
        # Handle potential errors during the DynamoDB operation
        print(f"An error occurred while retrieving groups and members: {e}")
        return {
            'success': False,
            'message': f"An error occurred while retrieving groups and members: {str(e)}"
        }

def is_group_member(current_user, group, token):
    members = group.get('members', {})
    in_members_list = current_user in members
    
    # check if member
    if in_members_list:  # Check if current_user is a key in the members dictionary
        return True
    else: 
        print(f"User is not a member of {group['group_id']}... checking if group is public or the user and group share a common amplify group")
         # check if public
        is_public = group.get("isPublic", False)

        is_in_amplify_group = False
        if (not is_public):
            # check if amplify group
            groups_amplify_groups = group.get("amplifyGroups", [])
            print("groups amplify groups: ", groups_amplify_groups)
            # Check for intersection if there is at least one common group then they are a member
            is_in_amplify_group = verify_user_in_amp_group(token, groups_amplify_groups)
            print("is_in_amplify_group: ", is_in_amplify_group)

        
            # if so add them to the members list 
        if (is_public or is_in_amplify_group):
            print("Is Public Group: ", is_public)
            print("Is user in amplify group: ", is_in_amplify_group)
            #update members list
            group_id = group["group_id"]
            members[current_user] = 'read'
            update_res = update_members_db(group_id, members)
            if (not update_res['success']):
                print(f"Warning: amplify group member failed to add to group: {group_id} members dict")
            return True
    return False
        
    
def authorized_user(group_id, user, requires_admin_access = False):
     # Fetch the current item from DynamoDB
    response = groups_table.get_item(Key={'group_id': group_id})
    item = response.get('Item')
    
    if not item:
        return {"message": "Group not found", "success": False}
    
    members = item["members"]
    
    if (not user in members.keys()):
        return {"message": "User is not a member of the group", "success": False}
    
    # ensure the current user has write access
    if (members[user] == 'read'):
        return {"message": "User does not have write access", "success": False}
    
    if (requires_admin_access and members[user] != "admin"):
        return {"message": "User does not have admin access", "success": False}
    
    return {"item": item, "success": True}


def log_item(group_id, action, username, details):
    audit_table = dynamodb.Table(os.environ['AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE'])
    audit_table.put_item(
        Item={
            'log_id': f"{username}/{ str(uuid.uuid4())}",
            'group_id': group_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'action': action,
            'user': username,
            'details': details
        }
    )
    print("item logged for group: ", group_id)
    


@validated(op='list')
def list_all_groups_for_admins(event, context, current_user, name, data):
    # verify is admin 
    if (not verify_user_as_admin(data['access_token'], 'Get All Assistant Admin Groups')):
        return {'success': False , 'error': 'Unable to authenticate user as admin'}

    groups = []
    try:
        response = groups_table.scan()
        groups.extend(response.get('Items', []))

        # Handle pagination if there are more records
        while 'LastEvaluatedKey' in response:
            response = groups_table.scan(
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            groups.extend(response.get('Items', []))

        filtered_groups = [
                {'group_id': group.get('group_id'), 'groupName': group.get('groupName'), 
                 'amplifyGroups' : group.get('amplifyGroups', []), 'createdBy' : group.get('createdBy'),
                 'isPublic' : group.get('isPublic', False), 'numOfAssistants' : len(group.get('assistants')), 
                 "supportConvAnalysis": group.get('supportConvAnalysis', False)}
                for group in groups
            ]
        return {
            'success': True,
            'data': filtered_groups
        }
    except Exception as e:
        print(f"An error occurred while retrieving groupss: {e}")
        return {
            'success': False,
            'message': f"An error occurred while retrieving groups: {str(e)}"
        }
    


@validated(op='update')
def replace_group_key(event, context, current_user, name, data):
    api_keys_table_name = dynamodb.Table(os.getenv('API_KEYS_DYNAMODB_TABLE'))
    if not api_keys_table_name:
        raise ValueError("API_KEYS_DYNAMODB_TABLE is not provided.")

    group_id = data['data']['groupId']
    # verify is admin 
    if (not verify_user_as_admin(data['access_token'], 'Replace Assistant Admin Group API Key')):
        return {'success': False , 'error': 'Unable to authenticate user as admin'}
    try:
        group_response = groups_table.get_item(Key={'group_id': group_id})
        if 'Item' not in group_response:
            print(f"Group '{group_id}' not found.")
            return {'success': False, 'message': f"Group '{group_id}' not found"}

        group_item = group_response['Item']
    except Exception as e:
        print(f"Error retrieving group '{group_id}': {e}")
        return {'success': False, 'message': f"Error retrieving group '{group_id}': {str(e)}"}

    # Extract current API key (old key)
    old_access = group_item.get('access')
    if not old_access:
        print(f"No existing API key found for group '{group_id}'.")
        return {'success': False, 'message': f"No existing API key found for group '{group_id}'"}

    # Find the old API key entry in the api_keys_table (assuming no direct index, using scan)
    api_record = None
    try:
        # Retrieve item from DynamoDB
        response = api_keys_table_name.query(
            IndexName='ApiKeyIndex',
            KeyConditionExpression='apiKey = :apiKeyVal',
            ExpressionAttributeValues={
                ':apiKeyVal': old_access
            }
        )
        items = response['Items']


        if not items:
            print("API key does not exist.")
            raise LookupError("API key not found.")
        
        api_record = items[0]
    except Exception as e:
        print(f"Error getting old API key '{old_access}': {e}")
        return {'success': False, 'message': f"Error scanning for old API key: {str(e)}"}

    if not api_record:
        print(f"Old API key record not found for '{old_access}'.")
        return {'success': False, 'message': f"Old API key record not found for '{old_access}'"}

    new_api_key = 'amp-' + str(uuid.uuid4())

    # Update the group item with the new access key and move old key into inactive_old_keys
    inactive_keys = group_item.get('inactive_old_keys', [])
    if old_access not in inactive_keys:
        inactive_keys.append(old_access)

    try:
        groups_table.update_item(
            Key={'group_id': group_id},
            UpdateExpression="SET #acc = :newAcc, #oldKeys = :oldAccList",
            ExpressionAttributeNames={
                '#acc': 'access',
                '#oldKeys': 'inactive_old_keys'
            },
            ExpressionAttributeValues={
                ':newAcc': new_api_key,
                ':oldAccList': inactive_keys
            }
        )
    except Exception as e:
        print(f"Error updating group '{group_id}' with new API key: {e}")
        return {'success': False, 'message': f"Error updating group with new API key: {str(e)}"}

    try:
       api_keys_table_name.update_item(
        Key={'api_owner_id': api_record['api_owner_id']},
        UpdateExpression="SET #act = :trueVal, #ak = :newKeyVal",
        ExpressionAttributeNames={
            '#act': 'active',
            '#ak': 'apiKey'
        },
        ExpressionAttributeValues={
            ':trueVal': True,
            ':newKeyVal': new_api_key
        }
    )
    except Exception as e:
        print(f"Error replacing old API key '{old_access}' with new key: {e}")
        return {'success': False, 'message': f"Error updating old API key with new key: {str(e)}"}

    # Log the action
    log_item(group_id, 'Replace Group API Key', current_user, f"Admin Replaced Group {group_id} API Key")

    return {
        'success': True,
        'message': f"Group '{group_id}' API key replaced successfully",
    }



@validated(op='update')
def update_ast_admin_groups(event, context, current_user, name, data):
    groups_to_update = data['data']['groups'] 

    updated_groups = []
    failed_updates = []

    for group_data in groups_to_update:
        group_id = group_data.get('group_id')

        existing_group_data = None
        try:
            current_response = groups_table.get_item(Key={'group_id': group_id})
            existing_group_data = current_response.get('Item')
            if not existing_group_data:
                failed_updates.append({
                    'group_id': group_id,
                    'message': f"Group '{group_id}' does not exist"
                })
                continue
        except Exception as e:
            print(f"An error occurred while fetching group '{group_id}': {e}")
            failed_updates.append({
                'group_id': group_id,
                'message': f"An error occurred while fetching group data: {str(e)}"
            })
            continue

        update_expressions = []
        expression_values = {}

        # Only update fields if they exist in the input and are allowed to be updated
        if  group_data['isPublic'] != existing_group_data.get("isPublic", None):
            update_expressions.append('isPublic = :isPublic')
            expression_values[':isPublic'] = group_data['isPublic']
        if  group_data['amplifyGroups'] != existing_group_data.get("amplifyGroups", None):
            update_expressions.append('amplifyGroups = :amplifyGroups')
            expression_values[':amplifyGroups'] = group_data['amplifyGroups']
        if  group_data['supportConvAnalysis'] != existing_group_data.get("supportConvAnalysis", None):
            update_expressions.append('supportConvAnalysis = :supportConvAnalysis')
            expression_values[':supportConvAnalysis'] = group_data['supportConvAnalysis']

        # If no changes, skip this group
        if not update_expressions:
            continue

        update_expression = 'SET ' + ', '.join(update_expressions)

        try:
            response = groups_table.update_item(
                Key={
                    'group_id': group_id
                },
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ReturnValues='ALL_NEW'
            )

            if response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200:
                updated_item = response.get('Attributes', {})
                print(f"Group '{group_id}' updated successfully")

                # Log the update
                log_item(group_id, 'Update Group', current_user, f"Group '{group_id}' fields updated.")

                updated_groups.append(group_id)
            else:
                print(f"Failed to update group '{group_id}'")
                failed_updates.append({
                    'group_id': group_id,
                    'message': f"Failed to update group '{group_id}'"
                })

        except Exception as e:
            print(f"An error occurred while updating group '{group_id}': {e}")
            failed_updates.append({
                'group_id': group_id,
                'message': f"An error occurred: {str(e)}"
            })

    # Construct the final response
    if len(failed_updates) == 0:
        return {
            'success': True,
            'message': 'All groups updated successfully',
            'data': updated_groups
        }
    else:
        print("Failed groups: \nfailed:",  failed_updates, '\nupdated:', updated_groups) 
        return {
            'success': False,
            'message': 'Some group updates failed',
            'data': {
                'updated': updated_groups,
                'failed': failed_updates
            }
        }


@validated(op='create')
def create_amplify_assistants(event, context, current_user, name, data):
    print("Creating group")
    token = data['access_token']
     # verify is admin 
    if (not verify_user_as_admin(token, 'Create Amplify Assistants Admin Group')):
        return {'success': False , 'error': 'Unable to authenticate user as admin'}

    assistants = data['data'].get('assistants', [])
    create_result = group_creation(current_user, "Amplify Assistants", {current_user:"admin"}, [])
    if (not create_result['success']): 
        print("Failed to create group")
        return create_result
    group_id = create_result['data']['id']

    assistants = [
        {**ast_def, 
        'groupId': group_id, 
        'data': {**ast_def.get('data', {}), 'groupId': group_id}}
        for ast_def in assistants
    ]

    result = register_ops(token, ops)

    if (not result):
        return {"success": False, "message": "Failed to register ops"}

    print("Adding assistants")
    if (len(assistants) > 0):
        ast_result = update_assistants(current_user, group_id, "ADD", assistants)
        if (not ast_result['success']): 
            print("Failed to add assistants")
            return ast_result
    
    return {"success": True, "data": {"id": group_id}}


