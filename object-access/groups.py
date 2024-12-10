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
from cognito_users import get_cognito_amplify_groups

# Setup AWS DynamoDB access
dynamodb = boto3.resource('dynamodb')
groups_table = dynamodb.Table(os.environ['AMPLIFY_GROUPS_DYNAMODB_TABLE'])

def addAdminInterfaceAccess(members):
    # needs to be updated for the new admin config way
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['COGNITO_USERS_TABLE'])

    ast_admin_amp_group = 'Ast_Admin_Interface'

    members_with_access = [member for member, access_type in members.items() if access_type in ['admin', 'write']]

    try:
        for user_id in members_with_access:
            response = table.get_item(Key={'user_id': user_id})
            if 'Item' not in response:
                print(f"No item found for user_id {user_id}")
                pass

            user_data = response['Item']
            current_groups = user_data.get('amplify_groups', '[]')
            if  ast_admin_amp_group not in current_groups:
                new_groups = current_groups[:-1]  # Remove the closing ']'
                if len(current_groups) > 2:
                    new_groups += ', '  # Add a comma only if there are already items
                new_groups += f"{ast_admin_amp_group}]"

                # Update the item in the database
                update_response = table.update_item(
                    Key={'user_id': user_id},
                    UpdateExpression="SET amplify_groups = :g",
                    ExpressionAttributeValues={
                        ':g': new_groups
                    },
                    ReturnValues="UPDATED_NEW"
                )
                # print(f"Update response: {update_response}")
    except ClientError as e:
        print(f"Error accessing DynamoDB: {e.response['Error']['Message']}")
        return False


@validated(op='create')
def create_group(event, context, current_user, name, data):
    print("Initiating group creation process")
    data = data['data']
    group_name = data['group_name']
    members = data.get('members', {}) # includes admins
    addAdminInterfaceAccess(members)
    group_types = data.get('types', {})
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
        # 'systemUsers': []

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
                'rateLimit': { 'rate': None, 'period': "UNLIMITED" },
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
    access_token = item["access"]
    assistants = item["assistants"]
    current_members = item.get('members', {})

    # Update the members list based on the type
    if update_type == 'ADD':
        addAdminInterfaceAccess(members)
        updated_members = {**current_members, **members}
        # not in use currently, going with group level permissions instead 
        # share_result = update_ast_perms_for_members(members, assistants, access_token)
        # if (not share_result['success']):
        #     return share_result
        
    elif update_type == 'REMOVE':
        updated_members = {k: v for k, v in current_members.items() if k not in members}

        existingMembers = [member for member in members if member not in updated_members]
        print("Existing members to remove: ", existingMembers)
        # not in use currently, going with group level permissions instead 
        # for ast in assistants:    
        #     print("removing perms for ast ", ast)                                       # list expected for REMOVE
        #     remove_astp_perms(access_token, {'assistant_public_id': ast['assistantId'], "users":  existingMembers})
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

    # not in use currently, going with group level permissions instead 
    # share_result = update_ast_perms_for_members(affected_user_dict, item['assistants'], item["access"])
    # if (not share_result['success']):
    #     return share_result

    # print("after updated members: ", current_members)

    addAdminInterfaceAccess(affected_user_dict)


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


def update_group_ds_perms(ast_ds, group_type_data, group_id):
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    table = dynamodb.Table(table_name)
    #compie ds into one list 
    # uploaded ones have the correct permissions, data selected from the user files do not, so we need to share it with the group 
    ds_selector_ds = [
        ds for info in group_type_data.values()
        if 'dataSources' in info
        for ds in info['dataSources']
        if 'groupId' not in ds
    ]
    ds_selector_ds.extend(
        ds for ds in ast_ds if 'groupId' not in ds
    )

    try:
        translated_ds = translate_user_data_sources_to_hash_data_sources(ds_selector_ds)
        print("Updating permissions for the following ds: ", translated_ds)
        # for group_type, info in group_type_data.items():
        #     if 'dataSources' in info:
        #         print(f"Updated Object Access for DS in Group Type: {group_type}")
        #         translated_ds = translate_user_data_sources_to_hash_data_sources(info['dataSources'])
        for ds in translated_ds:
        #             if (not 'groupId' in ds):
                    # this is to handle for when a member uses the dataselector
                        # is_group_ds_owner = 'groupId' in ds
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
def update_assistants(event, context, current_user, name, data):
    data = data['data']
    group_id = data['group_id']
    update_type = data['update_type']
    ast_list = data['assistants']

    auth_check = authorized_user(group_id, current_user)
    
    if (not auth_check['success']):
        return auth_check
    
    item = auth_check['item']
    access_token = item['access']
    members = item['members']

    current_assistants = item.get('assistants', [])

    new_assistants = []
    if update_type in ["ADD", "UPDATE"]:
        for ast in ast_list:
            groupDSData = {} 
            if ('data' in ast and 'groupTypeData' in ast["data"]): groupDSData = ast["data"]["groupTypeData"]

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
            
            new_ast_data ={'id':  data["id"], 'assistantId': data["assistantId"], "version": data['version']}
            new_assistants.append(new_ast_data)
            if (update_type == "UPDATE"):
                current_assistants = remove_old_ast_versions(current_assistants, data['assistantId'])
        
        print("All ast updated/added")

        # not in use currently, going with group level permissions instead 
        # #update permisions for added the new assistants, updated ones dont need to go through this again         
        # if (update_type == "ADD"):
        #     share_result = update_ast_perms_for_members(members, new_assistants, access_token)
        #     if (not share_result['success']):
        #         print("update perms for members failed")
        #         return share_result
        
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


@validated(op='list')
def list_groups(event, context, current_user, name, data):
    groups_result = get_my_groups(current_user)
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
        assistants = ast_result['data']
        published_assistants = []
        #append groupId and correct permissions if published
        for ast in assistants:
            ast_access = group["members"][current_user]
            hasAdminInterfaceAccess = ast_access in ['write', 'admin']
            if (("isPublished" in ast["data"] and ast["data"]["isPublished"]) or hasAdminInterfaceAccess):
                ast['groupId'] = group["group_id"]
                ast["data"]["access"]['write'] = hasAdminInterfaceAccess
                published_assistants.append(ast)

        group_info.append({
            'name' : group_name, 
            'id' : group['group_id'], 
            'members': group['members'], 
            'assistants': published_assistants,
            'groupTypes': group['groupTypes']
        })

    if (len(failed_to_list) == len(group_info)):
        return {
            'success': False,
            'message': "Failed to retrive assistants for users groups"
        }
    return {"success": True, "data": group_info, "incompleteGroupData": failed_to_list}    

def get_my_groups(current_user):
    group_rows = []
    try:
        response = groups_table.scan()

        for group in response.get('Items', []):
            if is_group_member(current_user, group): group_rows.append(group)

        # Handle pagination if there are more records
        while 'LastEvaluatedKey' in response:
            response = groups_table.scan(
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            for group in response.get('Items', []):
                if is_group_member(current_user, group): group_rows.append(group)


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

def is_group_member(current_user, group):
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
            user_groups = get_cognito_amplify_groups(current_user)
            users_amplify_group =  user_groups['data']['amplifyGroups'] if (user_groups['status'] == 200) else []
            groups_amplify_groups = group.get("amplifyGroups", [])
            print("groups amplify groups: ", groups_amplify_groups)
            # Check for intersection if there is at least one common group then they are a member
            is_in_amplify_group =  bool(set(users_amplify_group) & set(groups_amplify_groups)) 
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
    


