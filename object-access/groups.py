import os
import re
import uuid
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
from common.api_key import deactivate_key
from common.assistants import share_assistant, list_assistants, remove_astp_perms, delete_assistant, create_assistant
import boto3
from common.validate import validated

# Setup AWS DynamoDB access
dynamodb = boto3.resource('dynamodb')
groups_table = dynamodb.Table(os.environ['AMPLIFY_GROUPS_DYNAMODB_TABLE'])

@validated(op='create')
def create_group(event, context, current_user, name, data):
    print("Initiating group creation process")
    data = data['data']
    group_name = data['groupName']
    members = data.get('members', {}) # includes admins
    
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
        'access': api_key_result['api_key']
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
                'message': f"Group '{group_name}' created successfully"
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
                'accessTypes': ["api_key", 'assistants', 'share'],
                'account': { 'id': 'group_account', 'name': 'No COA Required' }
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
    

def separate_members_by_access(members_dict):
    members_by_access = {"read":[], "write":[]}
    for member, access_type in members_dict.items():
        if access_type in ["read", "write", "admin"]:
            access = 'write' if (access_type == 'admin') else access_type
            members_by_access[access].append(member)
   
    return tuple(members_by_access.values())

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
        updated_members = {**current_members, **members}
        share_result = update_ast_perms_for_members(members, assistants, access_token)
        if (not share_result['success']):
            return share_result
        
    elif update_type == 'REMOVE':
        updated_members = {k: v for k, v in current_members.items() if k not in members}

        existingMembers = [member for member in members if member not in updated_members]
        print("Existing members to remove: ", existingMembers)
        for ast in assistants:    
            print("removing perms for ast ", ast)                                       # list expected for REMOVE
            remove_astp_perms(access_token, {'assistant_public_id': ast['assistantId'], "users":  existingMembers})
    else:
        return {"message": "Invalid update type", "success": False}

    # Update the item in DynamoDB
    response = groups_table.update_item(
        Key={'group_id': group_id},
        UpdateExpression="set members = :m",
        ExpressionAttributeValues={
            ':m': updated_members
        },
        ReturnValues="UPDATED_NEW"
    )
     #log 
    log_item(group_id, 'Update Members', current_user, f"Members updated with {update_type}\nAffected members {members}\nUpdated members list {updated_members}")

    return {"message": "Members updated successfully", "success": True}

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
    print("before updates members: ", current_members)
    for affected_user, new_permission in affected_user_dict.items():
        memberPerms = current_members.get(affected_user, None)
        if not memberPerms:
            print(f"Member {affected_user} not found in group")
            # return {"message": f"Member {affected_user} not found in group", "success": False}
        
        if memberPerms == new_permission:
            print("Member already has the desired new permissions")
        else:
            current_members[affected_user] = new_permission


    share_result = update_ast_perms_for_members(affected_user_dict, item['assistants'], item["access"])
    if (not share_result['success']):
        return share_result

    print("after updated members: ", current_members)

    try:
        response = groups_table.update_item(
            Key={'group_id': group_id},
            UpdateExpression="set members = :o",
            ExpressionAttributeValues={
                ':o': current_members
            },
            ReturnValues="UPDATED_NEW"
        )
    except Exception as e:
        print(f"Failed to update DynamoDB: {str(e)}")
        return {'success': False, 'message': f"Failed to update DynamoDB: {str(e)}"}
   
    log_item(group_id, 'Update Member Permissions', current_user, f"Permission updated for members: {affected_user_dict}")

    return {"message": "Member permission updated successfully", "success": True}

def remove_old_ast(current_assistants, astp):
    for i in range(len(current_assistants) - 1, -1, -1):
        if current_assistants[i]['assistantId'] == astp:
            del current_assistants[i]
    return current_assistants

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
            create_result = create_assistant(access_token, ast)
            if (not create_result['success']):
                print("create ast call failed")
                return create_result
            
            data = create_result['data']
            print("AST Data: ", data)

            new_ast_data ={'id':  data["id"], 'assistantId': data["assistantId"], "version": data['version']}
            new_assistants.append(new_ast_data)
            if (update_type == "UPDATE"):
                current_assistants = remove_old_ast(current_assistants, data['assistantId'])
        
        print("All ast updated/added")
        #update permisions for added the new assistants, updated ones dont need to go through this again         
        if (update_type == "ADD"):
            share_result = update_ast_perms_for_members(members, new_assistants, access_token)
            if (not share_result['success']):
                print("update perms for members failed")
                return share_result
        
        current_assistants += new_assistants

        log_item(group_id, update_type + ' assistants', current_user, f"Assistants {new_assistants}")

    elif update_type == 'REMOVE':
        #delete with public id 
        for astp in ast_list:# list of assistantIds
            #permissions are removed in the delete process
            if not delete_assistant(access_token, {'assistantId' : astp,
                                                   "removePermsForUsers": list(members.keys()) 
                                                   }):
                print ("failed to delete assistant: ", astp)
            current_assistants = remove_old_ast(current_assistants, astp)
                    
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
        return {"message": "Assistants updated successfully", "success": True}
    except Exception as e:
        print(f"Failed to update DynamoDB: {str(e)}")
        return {'success': False, 'message': f"Failed to update DynamoDB: {str(e)}"}



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
                                                 "removePermsForUsers": list(item['members'].keys())
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
        assistants = get_latest_assistants(ast_result['data'])
        #append groupId and correct permissions
        for ast in assistants:
            ast['groupId'] = group["group_id"]
            ast_access = group["members"][current_user]
            ast["data"]["access"]['write'] = ast_access in ['write', 'admin']

        group_info.append({
            'name' : group_name, 
            'id' : group['group_id'], 
            'members': group['members'], 
            'assistants': assistants
        })

    if (len(failed_to_list) == len(group_info)):
        return {
            'success': False,
            'message': "Failed to retrive assistants for users groups"
        }
    return {"success": True, "data": group_info, "incompleteGroupData": failed_to_list}

@validated(op='list')
def list_group_members(event, context, current_user, name, data):
    groups_result = get_my_groups(current_user)
    if (not groups_result['success']):
        return groups_result
    groups = groups_result['data']

    groups_and_members = {}
    for group in groups:
        groups_and_members[group['groupName']] = list(group['members'].keys())

    return {"suceess": True, "groupMemberData" : groups_and_members}
    

def get_my_groups(current_user):
    group_rows = []
    try:
        response = groups_table.scan()

        for group in response.get('Items', []):
            if current_user in group.get('members', {}):  # Check if current_user is a key in the members dictionary
                group_rows.append(group)

        # Handle pagination if there are more records
        while 'LastEvaluatedKey' in response:
            response = groups_table.scan(
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            for group in response.get('Items', []):
                if current_user in group.get('members', {}): 
                    group_rows.append(group)

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
    


