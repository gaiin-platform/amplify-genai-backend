import os
import re
import uuid
from datetime import datetime, timezone
from pycommon.api.api_key import deactivate_key
from pycommon.api.assistants import (
    add_assistant_path,
    share_assistant,
    list_assistants,
    delete_assistant,
    create_assistant,
)
import boto3
from boto3.dynamodb.conditions import Key
from pycommon.api.files import delete_file
from pycommon.api.amplify_users import are_valid_amplify_users
from pycommon.api.data_sources import translate_user_data_sources_to_hash_data_sources
from pycommon.api.auth_admin import verify_user_as_admin
from pycommon.api.amplify_groups import verify_user_in_amp_group
from pycommon.api.embeddings import check_embedding_completion
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation
)
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import NO_RATE_LIMIT
setup_validated(rules, get_permission_checker)

import asyncio
import aiohttp
import concurrent.futures

from pycommon.logger import getLogger
logger = getLogger("object_access_ast_groups")

# Setup AWS DynamoDB access
dynamodb = boto3.resource("dynamodb")
groups_table = dynamodb.Table(os.environ["ASSISTANT_GROUPS_DYNAMO_TABLE"])


@required_env_vars({
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="create")
def create_group(event, context, current_user, name, data):
    logger.info("Initiating group creation process")
    access_token = data["access_token"]
    data = data["data"]
    group_name = data["group_name"]
    members = filter_for_valid_members_dict(data.get("members", {}), access_token)  # includes admins
    group_types = data.get("types", {})
    amplify_groups = data.get("amplify_groups", [])
    system_users = data.get("system_users", [])

    return group_creation(current_user, group_name, members, group_types, amplify_groups, system_users)


def group_creation(
    current_user,
    group_name,
    members,
    group_types=[],
    amplify_groups=[],
    system_users=[],
):
    # create_api key -
    api_key_result = create_api_key_for_group(group_name)
    if not api_key_result["success"]:
        return {"success": False, "message": f"Failed to create group '{group_name}'"}
    group_id = api_key_result["group_id"]
    logger.debug("Group Id: %s", group_id)
    # Prepare the item dictionary for DynamoDB
    item = {
        "group_id": group_id,
        "groupName": group_name,
        "members": members,  # memebers are an object {"user": "read or write}
        "assistants": [],  # contains ast/ id because sharing/permission require it
        "createdBy": current_user,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "groupTypes": group_types,
        "amplifyGroups": amplify_groups,
        "systemUsers": system_users,
    }

    try:
        response = groups_table.put_item(Item=item)
        # Check if the response was successful
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
            logger.info(f"Group '{group_name}' created successfully")

            # log
            log_item(
                group_id,
                "Create Group",
                current_user,
                f"Group '{group_name}' created with initial members: {members}",
            )

            return {
                "success": True,
                "message": f"Group '{group_name}' created successfully",
                "data": {
                    "id": group_id,
                    "name": group_name,
                    "members": members,
                    "assistants": [],
                    "groupTypes": group_types,
                    "amplifyGroups": amplify_groups,
                    "systemUsers": system_users,
                },
            }
        else:
            logger.error(f"Failed to create group '{group_name}'")
            return {
                "success": False,
                "message": f"Failed to create group '{group_name}'",
            }
    except Exception as e:
        # Handle potential errors during the DynamoDB operation
        logger.error(f"An error occurred while creating group '{group_name}': {e}")
        return {
            "success": False,
            "message": f"An error occurred while creating group: {str(e)}",
        }

def filter_for_valid_members_dict(members, access_token):
    if not members:
        return members
    
    users = members.keys()
    _, invalid_users = are_valid_amplify_users(access_token, list(users))
    for invalid in invalid_users:
        del members[invalid]
    return members

def pascalCase(input_str):
    return "".join(x for x in input_str.title() if not x.isspace() and x.isalnum())


def create_api_key_for_group(group_name):
    id = str(uuid.uuid4())

    name = pascalCase(group_name)
    group_id = name + "_" + id

    logger.info("create api key for group")
    api_keys_table_name = os.environ["API_KEYS_DYNAMODB_TABLE"]
    api_table = dynamodb.Table(api_keys_table_name)

    api_owner_id = f"{name}/systemKey/{id}"
    timestamp = datetime.now(timezone.utc).isoformat()
    apiKey = 'amp-' + str(uuid.uuid4())
    try:
        logger.debug("Put entry in api keys table")
        # Put (or update) the item for the specified user in the DynamoDB table
        response = api_table.put_item(
            Item={
                'api_owner_id': api_owner_id,
                'owner': group_id,
                'apiKey': apiKey,
                'systemId' : group_id,
                'active': True,
                'createdAt': timestamp, 
                'accessTypes': ["api_key", 'assistants', 'chat', 'dual_embedding', 'embedding', 'file_upload'],
                'account': { 'id': 'group_account', 'name': 'No COA Required' },
                'rateLimit': NO_RATE_LIMIT,
                'purpose': "group"
            }
        )

        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
            logger.info(f"API key for created successfully")
            return {"success": True, "group_id": group_id}
        else:
            logger.error(f"Failed to create API key")
            return {"success": False, "message": "Failed to create API key"}
    except Exception as e:
        logger.error(f"An error occurred while saving API key: {e}")
        return {
            "success": False,
            "message": f"An error occurred while saving API key: {str(e)}",
        }


# not in use currently, going with group level permissions instead
def separate_members_by_access(members_dict):
    members_by_access = {"read": [], "write": []}
    for member, access_type in members_dict.items():
        if access_type in ["read", "write", "admin"]:
            access = "write" if (access_type == "admin") else access_type
            members_by_access[access].append(member)

    return tuple(members_by_access.values())


# not in use currently, going with group level permissions instead
def update_ast_perms_for_members(members_dict, assistants, access_token):
    logger.debug("Enter update permissions for members")
    read_members, write_members = separate_members_by_access(members_dict)
    # give access to all the group ast
    for ast in assistants:
        for access_type, recipients in [
            ("read", read_members),
            ("write", write_members),
        ]:
            data = {
                "assistantId": ast["id"],
                "recipientUsers": recipients,
                "accessType": access_type,
                "policy": "",
                "shareToS3": False,
            }
            logger.debug(f"updating permissions {access_type} for members {recipients}")
            if len(recipients) > 0 and not share_assistant(access_token, data):
                logger.error("Error making share assistant calls for assistant: %s", ast["id"])
                return {
                    "success": False,
                    "error": "Could not successfully make the call to share assistants with members",
                }

    return {
        "success": True,
        "message": "successfully made the call to share assistants with members",
    }


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="update")
def update_members(event, context, current_user, name, data):
    data = data["data"]
    group_id = data["group_id"]
    update_type = data["update_type"]
    members = data.get("members")  # dict for ADD , list for REMOVE

    auth_check = authorized_user(group_id, current_user, True)

    if not auth_check["success"]:
        return auth_check

    item = auth_check["item"]
    access_token = item["access"]

    if isinstance(members, list): # REMOVE
        valid_members, _ = are_valid_amplify_users(access_token, members)
        members = valid_members
    elif isinstance(members, dict): # ADD
        members = filter_for_valid_members_dict(members, access_token)

    current_members = item.get("members", {})

    # Update the members list based on the type
    if update_type == "ADD":
        updated_members = {**current_members, **members}

    elif update_type == "REMOVE":
        updated_members = {k: v for k, v in current_members.items() if k not in members}

        existingMembers = [
            member for member in members if member not in updated_members
        ]
        logger.debug("Existing members to remove: %s", existingMembers)

    else:
        return {"message": "Invalid update type", "success": False}

    # Update the item in DynamoDB
    update_res = update_members_db(group_id, updated_members)
    if not update_res["success"]:
        return update_res

    # log
    log_item(
        group_id,
        "Update Members",
        current_user,
        f"Members updated with {update_type}\nAffected members {members}\nUpdated members list {updated_members}",
    )

    return {"message": "Members updated successfully", "success": True}


def update_members_db(group_id, updated_members):
    try:
        response = groups_table.update_item(
            Key={"group_id": group_id},
            UpdateExpression="set members = :m",
            ExpressionAttributeValues={":m": updated_members},
            ReturnValues="UPDATED_NEW",
        )
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to update DynamoDB: {str(e)}")
        return {"success": False, "message": f"Failed to update DynamoDB: {str(e)}"}


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="update")
def update_members_permission(event, context, current_user, name, data):
    data = data["data"]
    group_id = data["group_id"]
    affected_user_dict = data["affected_members"]

    auth_check = authorized_user(group_id, current_user, True)

    if not auth_check["success"]:
        return auth_check

    item = auth_check["item"]

    # Update the permission of the specified member
    current_members = item.get("members", [])
    # print("before updates members: ", current_members)
    for affected_user, new_permission in affected_user_dict.items():
        memberPerms = current_members.get(affected_user, None)
        if not memberPerms:
            logger.warning(f"Member {affected_user} not found in group")
        if memberPerms == new_permission:
            logger.debug("Member already has the desired new permissions")
        else:
            current_members[affected_user] = new_permission

    update_res = update_members_db(group_id, current_members)
    if not update_res["success"]:
        return update_res

    log_item(
        group_id,
        "Update Member Permissions",
        current_user,
        f"Permission updated for members: {affected_user_dict}",
    )

    return {"message": "Member permission updated successfully", "success": True}


def remove_old_ast_versions(current_assistants, astp):
    for i in range(len(current_assistants) - 1, -1, -1):
        if current_assistants[i]["assistantId"] == astp:
            del current_assistants[i]
    return current_assistants


def is_ds_owner(ds, group_id):
    return group_id in ds.get("id", "") or group_id in ds.get("key", "")


def update_group_ds_perms(ast_ds, group_type_data, group_id, access_token):
    table_name = os.environ['OBJECT_ACCESS_DYNAMODB_TABLE']
    table = dynamodb.Table(table_name)
    logger.debug("ast ds: %s", ast_ds)
    logger.debug("groupType data: %s", group_type_data)

    # compile ds into one list
    # uploaded ones have the correct permissions, data selected from the user files do not, so we need to share it with the group
    ds_selector_ds = [
        ds
        for info in group_type_data.values()
        if "dataSources" in info
        for ds in info["dataSources"]
        if not is_ds_owner(ds, group_id)
    ]
    ds_selector_ds.extend(ds for ds in ast_ds if not is_ds_owner(ds, group_id))

    # print("Updating permissions for the following ds prior to translation: ", ds_selector_ds)

    # Validate that all data sources have required 'id' field and filter out invalid ones
    valid_ds = [ds for ds in ds_selector_ds if isinstance(ds, dict) and 'id' in ds]
    invalid_count = len(ds_selector_ds) - len(valid_ds)
    
    if invalid_count > 0:
        logger.warning(f"Filtered out {invalid_count} data sources without required 'id' field")
        ds_selector_ds = valid_ds
    
    # Skip if no data sources to process
    if not ds_selector_ds:
        logger.info("No data sources to process for group permissions")
        return {'success': True}

    try:
        translated_ds = translate_user_data_sources_to_hash_data_sources(ds_selector_ds)
        logger.debug("Updating permissions for the following ds: %s", translated_ds)

        for ds in translated_ds:
            table.put_item(Item={
                    'object_id': ds['id'],
                    'principal_id': group_id,
                    'principal_type':  'group',
                    'object_type': 'datasource',
                    'permission_level': 'read',  
                    'policy': None
            })
        mapped_ids = [ds['id'] for ds in translated_ds]
        # ensure all ds have been embedded 
        check_embedding_completion(access_token, mapped_ids)   
                
        return {'success': True}
    except Exception as e:
        logger.error(f"An error occurred when updating group data source permissions: {str(e)}")
        return {"success": False, "error": str(e)}


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "OBJECT_ACCESS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="update")
def update_group_assistants(event, context, current_user, name, data):
    data = data["data"]
    group_id = data["group_id"]
    update_type = data["update_type"]
    ast_list = data["assistants"]
    return update_assistants(current_user, group_id, update_type, ast_list)


def update_assistants(current_user, group_id, update_type, ast_list):
    auth_check = authorized_user(group_id, current_user)

    if not auth_check["success"]:
        return auth_check

    item = auth_check["item"]
    access_token = item["access"]

    current_assistants = item.get("assistants", [])

    new_assistants = []
    if update_type in ["ADD", "UPDATE"]:
        for ast in ast_list:
            groupDSData = {}
            if "data" in ast and "groupTypeData" in ast["data"]:
                groupDSData = ast["data"]["groupTypeData"]
            # always reset here in case access to conv analysis has been revoked at a group level
            # Note for future: as you can see this only applies when an assistant has been updated.
            if (
                "data" in ast and "supportConvAnalysis" in ast["data"]
            ) and not item.get("supportConvAnalysis", False):
                ast["data"]["supportConvAnalysis"] = False

            update_perms_result = update_group_ds_perms(ast.get('dataSources', []), groupDSData, group_id, access_token)
            if (not update_perms_result['success']):
                logger.error("could not update ds perms for all group type data")
                return update_perms_result
            
            create_result = create_assistant(access_token, ast)
            if not create_result["success"]:
                logger.error("create ast call failed")
                return create_result

            data = create_result["data"]
            logger.debug("AST Data: %s", data)

            new_ast_data = {
                "id": data["id"],
                "assistantId": data["assistantId"],
                "version": data["version"],
                "name": ast["name"],
            }
            new_assistants.append(new_ast_data)
            if update_type == "UPDATE":
                current_assistants = remove_old_ast_versions(
                    current_assistants, data["assistantId"]
                )

        logger.info("All ast updated/added")

        current_assistants += new_assistants

        log_item(
            group_id,
            update_type + " assistants",
            current_user,
            f"Assistants {new_assistants}",
        )

    elif update_type == "REMOVE":
        # delete with public id
        for astp in ast_list:  # list of assistantIds
            # permissions are removed in the delete process
            if not delete_assistant(
                access_token,
                {"assistantId": astp, "removePermsForUsers": []}, 
            ):
                logger.error("failed to delete assistant: %s", astp)
            current_assistants = remove_old_ast_versions(current_assistants, astp)

        log_item(group_id, "remove assistants", current_user, f"Assistants {ast_list}")
    else:
        return {"message": "Invalid update type", "success": False}

    try:
        logger.debug("Update assistants in groups table")
        response = groups_table.update_item(
            Key={"group_id": group_id},
            UpdateExpression="set assistants = :o",
            ExpressionAttributeValues={":o": current_assistants},
            ReturnValues="UPDATED_NEW",
        )
        return {
            "message": "Assistants updated successfully",
            "success": True,
            "assistantData": [
                {
                    "id": assistant["id"],
                    "assistantId": assistant["assistantId"],
                    "provider": "amplify",
                }
                for assistant in new_assistants
            ],
        }
    except Exception as e:
        logger.error(f"Failed to update DynamoDB: {str(e)}")
        return {"success": False, "message": f"Failed to update DynamoDB: {str(e)}"}


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="add_assistant_path")
def add_path_to_assistant(event, context, current_user, name, data):
    data = data["data"]
    group_id = data["group_id"]
    path_data = data["path_data"]

    auth_check = authorized_user(group_id, current_user)

    if not auth_check["success"]:
        return auth_check

    item = auth_check["item"]
    access_token = item["access"]
    assistantId = path_data["assistantId"]
    current_assistants = item.get("assistants", [])

    # find the assistant in the current assistants
    assistant = next(
        (a for a in current_assistants if a["assistantId"] == assistantId), None
    )
    if not assistant:
        return {"message": "Assistant not found", "success": False}
    logger.debug("Adding path to group_id: %s with data: %s", group_id, path_data)
    log_item(
        group_id,
        "Add Path to Assistant",
        current_user,
        f"Path added to assistant {assistantId}",
    )

    # call add path to assistant
    return add_assistant_path(access_token, path_data)


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="update")
def update_group_types(event, context, current_user, name, data):
    data = data["data"]
    group_id = data["group_id"]
    new_group_types = data["types"]

    # Perform authorization check
    auth_check = authorized_user(group_id, current_user, True)
    if not auth_check["success"]:
        return auth_check

    try:
        item = auth_check["item"]

        old_group_types = item.get("groupTypes", [])

        # Update the group_types in the database
        response = groups_table.update_item(
            Key={"group_id": group_id},
            UpdateExpression="set groupTypes = :gt",
            ExpressionAttributeValues={":gt": new_group_types},
            ReturnValues="UPDATED_NEW",
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
        log_item(
            group_id,
            "Group Types Updated",
            current_user,
            f"Group types updated to {new_group_types} \n\nChange Summary\n{change_summary}",
        )

        return {"message": "Group types updated successfully", "success": True}

    except Exception as e:
        # Log the exception and return an error message
        logger.error("Update Group Types error for group %s by user %s: %s", group_id, current_user, str(e))
        return {
            "message": "Failed to update group types due to an error.",
            "success": False,
            "error": str(e),
        }


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="update")
def update_amplify_groups(event, context, current_user, name, data):
    data = data["data"]
    group_id = data["group_id"]
    amplify_groups = data["amplify_groups"]

    auth_check = authorized_user(group_id, current_user, True)
    if not auth_check["success"]:
        return auth_check

    try:
        # Update the amplify groups in the database
        response = groups_table.update_item(
            Key={"group_id": group_id},
            UpdateExpression="set amplifyGroups = :ag",
            ExpressionAttributeValues={":ag": amplify_groups},
            ReturnValues="UPDATED_NEW",
        )

        log_item(
            group_id,
            "Group Amplify Groups Updated",
            current_user,
            f"Group amplify groups updated to {amplify_groups}",
        )

        return {"message": "Amplify groups updated successfully", "success": True}
    except Exception as e:
        logger.error(f"Failed to update group amplify groups in DynamoDB: {str(e)}")
        return {"success": False, "message": f"Failed to update DynamoDB: {str(e)}"}


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="update")
def update_system_users(event, context, current_user, name, data):
    data = data["data"]
    group_id = data["group_id"]
    system_users = data["system_users"]

    auth_check = authorized_user(group_id, current_user, True)

    if not auth_check["success"]:
        return auth_check

    try:
        # Update the system users in the database
        response = groups_table.update_item(
            Key={"group_id": group_id},
            UpdateExpression="set systemUsers = :su",
            ExpressionAttributeValues={":su": system_users},
            ReturnValues="UPDATED_NEW",
        )

        log_item(
            group_id,
            "Group System Users Updated",
            current_user,
            f"Group system users updated to {system_users}",
        )

        return {"message": "System users updated successfully", "success": True}
    except Exception as e:
        logger.error(f"Failed to update group system users in DynamoDB: {str(e)}")
        return {"success": False, "message": f"Failed to update DynamoDB: {str(e)}"}


def is_valid_group_id(group_id):
    pattern = r"^.+\_[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}$"
    return bool(re.match(pattern, group_id))


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.DELETE_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "FILES_DYNAMO_TABLE": [DynamoDBOperation.QUERY],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="delete")
def delete_group(event, context, current_user, name, data):
    query_params = event.get("queryStringParameters", {})
    logger.debug("Query params: %s", query_params)
    group_id = query_params.get("group_id", "")
    if not group_id or not is_valid_group_id(group_id):
        logger.warning("Invalid or missing group id parameter")
        return {"message": "Invalid or missing group id parameter", "success": False}

    auth_check = authorized_user(group_id, current_user, True)

    if not auth_check["success"]:
        return auth_check

    item = auth_check["item"]
    access_token = item["access"]

    assistants = item["assistants"]
    for ast_data in assistants:  # list of assistantIds
        # permissions are removed in the delete process
        if not delete_assistant(
            item["access"],
            {
                "assistantId": ast_data["assistantId"],
                "removePermsForUsers": [],  # list(item['members'].keys())
            },
        ):
            logger.error("failed to delete assistant: %s", ast_data)
    
    # Delete all files in the group
    file_deletion_result = delete_group_files(group_id, access_token)

    try:
        response = groups_table.delete_item(Key={"group_id": group_id})
        # Check the response for successful deletion
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
            log_item(group_id, "Delete Group", current_user, f"Group {group_id} deleted")

            # deactivate key
            owner_api_id = group_id.replace("_", "/systemKey/")
            deactivate_key(item["access"], owner_api_id)

            return {
                "message": "Group deleted successfully", 
                "success": True,
                "file_deletion_summary": file_deletion_result
            }
        else:
            return {"message": "Failed to delete group", "success": False}

    except Exception as e:
        return {"message": f"An error occurred while deleting group: {str(e)}", "success": False}


def get_latest_assistants(assistants):
    latest_assistants = {}
    for assistant in assistants:
        # Set version to 1 if it doesn't exist
        assistant.setdefault("version", 1)
        assistant_id = assistant.get("assistantId", None)
        # will exclude system ast since they dont have assistantId
        if assistant_id and (
            assistant_id not in latest_assistants
            or latest_assistants[assistant_id]["version"] < assistant["version"]
        ):
            latest_assistants[assistant_id] = assistant

    return list(latest_assistants.values())


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.SCAN],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated(op="list")
def list_groups(event, context, current_user, name, data):
    groups_result = get_my_groups(current_user, data["access_token"])
    if not groups_result["success"]:
        return groups_result
    groups = groups_result["data"]

    # Run the async processing
    return asyncio.run(process_groups_async(groups, current_user))


async def process_groups_async(groups, current_user):
    """
    Process groups concurrently by making all list_assistants calls in parallel
    """
    if not groups:
        return {"success": True, "data": [], "incompleteGroupData": []}

    # First, collect all API keys for all groups
    group_api_data = []
    for group in groups:
        api_key_result = retrieve_api_key(group["group_id"])
        if not api_key_result["success"]:
            return api_key_result
        group_api_data.append({
            "group": group,
            "api_key": api_key_result["apiKey"]
        })

    # Since list_assistants is synchronous, we'll use concurrent.futures for parallel execution
    
    # Execute all list_assistants calls in parallel using ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Create future tasks for each group
        future_to_group = {
            executor.submit(list_assistants, group_data["api_key"]): group_data["group"]
            for group_data in group_api_data
        }
        
        # Collect results as they complete
        group_results = []
        for future in concurrent.futures.as_completed(future_to_group):
            group = future_to_group[future]
            try:
                ast_result = future.result()
                group_results.append({
                    "group": group,
                    "ast_result": ast_result,
                    "success": True
                })
            except Exception as e:
                logger.error(f"Error fetching assistants for group {group['groupName']}: {str(e)}")
                group_results.append({
                    "group": group,
                    "ast_result": None,
                    "success": False,
                    "error": str(e)
                })

    # Process results and build response
    group_info = []
    failed_to_list = []
    
    for result in group_results:
        group = result["group"]
        group_name = group["groupName"]
        
        if not result["success"] or not result["ast_result"]["success"]:
            failed_to_list.append(group_name)
            continue

        group_members = group.get("members", {})
        ast_access = group_members.get(current_user)
        hasAdminInterfaceAccess = ast_access in ["write", "admin"]

        # filter old versions
        assistants = get_latest_assistants(result["ast_result"]["data"])
        logger.debug(f"{group_name} - {len(assistants)} Assistant Count")
        published_assistants = []
        # append groupId and correct permissions if published
        for ast in assistants:
            if (
                "isPublished" in ast["data"] and ast["data"]["isPublished"]
            ) or hasAdminInterfaceAccess:
                ast["groupId"] = group["group_id"]
                ast["data"]["access"]["write"] = hasAdminInterfaceAccess
                published_assistants.append(ast)

        if len(published_assistants) > 0 or hasAdminInterfaceAccess:
            group_info.append(
                {
                    "name": group_name,
                    "id": group["group_id"],
                    "members": group_members,
                    "assistants": published_assistants,
                    "groupTypes": group["groupTypes"],
                    "supportConvAnalysis": group.get("supportConvAnalysis", False),
                    "amplifyGroups": group.get("amplifyGroups", []),
                    "systemUsers": group.get("systemUsers", []),
                }
            )

    if len(failed_to_list) == len(group_info):
        return {
            "success": False,
            "message": "Failed to retrive assistants for users groups",
        }
    return {"success": True, "data": group_info, "incompleteGroupData": failed_to_list}


def get_my_groups(current_user, token):
    group_rows = []
    try:
        response = groups_table.scan()

        for group in response.get("Items", []):
            if is_group_member(current_user, group, token):
                logger.debug(f"Adding {group['groupName']} to group_rows")
                group_rows.append(group)

        # Handle pagination if there are more records
        while "LastEvaluatedKey" in response:
            response = groups_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            for group in response.get("Items", []):
                if is_group_member(current_user, group, token):
                    group_rows.append(group)

        return {"success": True, "data": group_rows}
    except Exception as e:
        # Handle potential errors during the DynamoDB operation
        logger.error(f"An error occurred while retrieving groups and members: {e}")
        return {
            "success": False,
            "message": f"An error occurred while retrieving groups and members: {str(e)}",
        }


def is_group_member(current_user, group, token):
    logger.debug(f"Checking if {current_user} is a member of {group['groupName']}")
    # check if member
    if group.get("isPublic", False):
        logger.debug(f"Group is public")
        return True
    elif current_user in group.get(
        "members", {}
    ):  # Check if current_user is a key in the members dictionary
        logger.debug(f"User is a direct member of {group['group_id']}")
        return True
    elif current_user in group.get("systemUsers", []):
        logger.debug(f"User is a system user of {group['group_id']}")
        return True
    else:
        logger.debug(
            f"User is not a member of {group['group_id']}... checking if the user and group share a common amplify group"
        )
        # check if amplify group
        groups_amplify_groups = group.get("amplifyGroups", [])
        logger.debug("Groups amplify groups: %s", groups_amplify_groups)
        # Check for intersection if there is at least one common group then they are a member
        is_in_amplify_group = verify_user_in_amp_group(token, groups_amplify_groups)
        logger.debug("Is user in any of the amplify groups: %s", is_in_amplify_group)
        return is_in_amplify_group


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated(op="verify_member")
def verify_is_member_ast_group(event, context, current_user, name, data):
    token = data["access_token"]
    data = data["data"]
    group_id = data["groupId"]

    try:
        response = groups_table.get_item(Key={"group_id": group_id})
        group_item = response.get("Item")

        if not group_item:
            return {
                "success": False,
                "message": f"No group entry found for groupId: {group_id}",
            }

        is_member = is_group_member(current_user, group_item, token)
        return {"isMember": is_member, "success": True}
    except Exception as e:
        logger.error(
            f"An error occurred while verifying if user is a member of the group: {str(e)}"
        )

    return {
        "message": f"Unable to verify if user is a member of the group",
        "success": False,
    }


def authorized_user(group_id, user, requires_admin_access=False):
    # Fetch the current item from DynamoDB
    response = groups_table.get_item(Key={"group_id": group_id})
    item = response.get("Item")

    if not item:
        return {"message": "Group not found", "success": False}

    members = item.get("members", {})

    if not user in members.keys():
        return {"message": "User is not a member of the group", "success": False}

    # ensure the current user has write access
    if members[user] == "read":
        return {"message": "User does not have write access", "success": False}

    if requires_admin_access and members[user] != "admin":
        return {"message": "User does not have admin access", "success": False}

    if item.get("access"):
        logger.debug("Found 'access' attribute in group item. Initiating cleanup...")
        cleanup_result = clean_old_keys()
        if not cleanup_result["success"]:
            # Log or handle the error appropriately if cleanup fails
            logger.warning(f"Warning: clean_old_keys failed: {cleanup_result['message']}")

    api_key_result = retrieve_api_key(group_id)

    if not api_key_result["success"]:
        return api_key_result

    item["access"] = api_key_result["apiKey"]

    return {"item": item, "success": True}


def retrieve_api_key(group_id):
    api_owner_id = group_id.replace("_", "/systemKey/")

    api_keys_table_name = os.getenv("API_KEYS_DYNAMODB_TABLE")
    if not api_keys_table_name:
        raise ValueError("API_KEYS_DYNAMODB_TABLE is not provided.")

    # retrieve api key
    try:
        api_keys_table = dynamodb.Table(api_keys_table_name)
        api_response = api_keys_table.get_item(Key={"api_owner_id": api_owner_id})
        api_item = api_response.get("Item")

        if not api_item:
            return {
                "success": False,
                "message": f"No API key found for group: {group_id}",
            }

        return {"success": True, "apiKey": api_item["apiKey"], "item": api_item}
    except Exception as e:
        return {"success": False, "message": f"Error retrieving API key: {str(e)}"}


def log_item(group_id, action, username, details):
    audit_table = dynamodb.Table(os.environ["AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE"])
    audit_table.put_item(
        Item={
            "log_id": f"{username}/{ str(uuid.uuid4())}",
            "group_id": group_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "user": username,
            "details": details,
        }
    )
    logger.debug("item logged for group: %s", group_id)


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.SCAN],
})
@validated(op="list")
def list_all_groups_for_admins(event, context, current_user, name, data):
    # verify is admin
    if not verify_user_as_admin(data["access_token"], "Get All Assistant Admin Groups"):
        return {"success": False, "error": "Unable to authenticate user as admin"}

    groups = []
    try:
        response = groups_table.scan()
        groups.extend(response.get("Items", []))

        # Handle pagination if there are more records
        while "LastEvaluatedKey" in response:
            response = groups_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            groups.extend(response.get("Items", []))

        filtered_groups = [
            {
                "group_id": group.get("group_id"),
                "groupName": group.get("groupName"),
                "amplifyGroups": group.get("amplifyGroups", []),
                "createdBy": group.get("createdBy"),
                "isPublic": group.get("isPublic", False),
                "numOfAssistants": len(group.get("assistants")),
                "supportConvAnalysis": group.get("supportConvAnalysis", False),
            }
            for group in groups
        ]
        return {"success": True, "data": filtered_groups}
    except Exception as e:
        logger.error(f"An error occurred while retrieving groupss: {e}")
        return {
            "success": False,
            "message": f"An error occurred while retrieving groups: {str(e)}",
        }


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="update")
def replace_group_key(event, context, current_user, name, data):
    api_keys_table_name = dynamodb.Table(os.getenv("API_KEYS_DYNAMODB_TABLE"))
    if not api_keys_table_name:
        raise ValueError("API_KEYS_DYNAMODB_TABLE is not provided.")

    group_id = data["data"]["groupId"]
    # verify is admin
    if not verify_user_as_admin(
        data["access_token"], "Replace Assistant Admin Group API Key"
    ):
        return {"success": False, "error": "Unable to authenticate user as admin"}

    try:  # verify group exists
        group_response = groups_table.get_item(Key={"group_id": group_id})
        if "Item" not in group_response:
            logger.error(f"Group '{group_id}' not found.")
            return {"success": False, "message": f"Group '{group_id}' not found"}

        group_item = group_response["Item"]
    except Exception as e:
        logger.error(f"Error retrieving group '{group_id}': {e}")
        return {
            "success": False,
            "message": f"Error retrieving group '{group_id}': {str(e)}",
        }

    # Extract current API key (old key)
    api_key_result = retrieve_api_key(group_id)
    if not api_key_result["success"]:
        return api_key_result
    old_access = api_key_result["apiKey"]

    if not old_access:
        logger.error(f"No existing API key found for group '{group_id}'.")
        return {
            "success": False,
            "message": f"No existing API key found for group '{group_id}'",
        }

    # Find the old API key entry in the api_keys_table (assuming no direct index, using scan)
    api_record = api_key_result["item"]

    if not api_record:
        logger.error(f"Old API key record not found for '{old_access}'.")
        return {
            "success": False,
            "message": f"Old API key record not found for '{old_access}'",
        }

    new_api_key = "amp-" + str(uuid.uuid4())

    # Update the group item with the new access key and move old key into inactive_old_keys
    inactive_keys = group_item.get("inactive_old_keys", [])
    if old_access not in inactive_keys:
        inactive_keys.append(old_access)

    try:
        groups_table.update_item(
            Key={"group_id": group_id},
            UpdateExpression="SET #oldKeys = :oldAccList",
            ExpressionAttributeNames={"#oldKeys": "inactive_old_keys"},
            ExpressionAttributeValues={":oldAccList": inactive_keys},
        )
    except Exception as e:
        logger.error(f"Error updating group '{group_id}' with new API key: {e}")
        return {
            "success": False,
            "message": f"Error updating group with new API key: {str(e)}",
        }

    try:
        api_keys_table_name.update_item(
            Key={"api_owner_id": api_record["api_owner_id"]},
            UpdateExpression="SET #act = :trueVal, #ak = :newKeyVal",
            ExpressionAttributeNames={"#act": "active", "#ak": "apiKey"},
            ExpressionAttributeValues={":trueVal": True, ":newKeyVal": new_api_key},
        )
    except Exception as e:
        logger.error(f"Error replacing old API key '{old_access}' with new key: {e}")
        return {
            "success": False,
            "message": f"Error updating old API key with new key: {str(e)}",
        }

    # Log the action
    log_item(
        group_id,
        "Replace Group API Key",
        current_user,
        f"Admin Replaced Group {group_id} API Key",
    )

    return {
        "success": True,
        "message": f"Group '{group_id}' API key replaced successfully",
    }


@required_env_vars({
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="update")
def update_ast_admin_groups(event, context, current_user, name, data):
    groups_to_update = data["data"]["groups"]

    updated_groups = []
    failed_updates = []

    for group_data in groups_to_update:
        group_id = group_data.get("group_id")

        existing_group_data = None
        try:
            current_response = groups_table.get_item(Key={"group_id": group_id})
            existing_group_data = current_response.get("Item")
            if not existing_group_data:
                failed_updates.append(
                    {
                        "group_id": group_id,
                        "message": f"Group '{group_id}' does not exist",
                    }
                )
                continue
        except Exception as e:
            logger.error(f"An error occurred while fetching group '{group_id}': {e}")
            failed_updates.append(
                {
                    "group_id": group_id,
                    "message": f"An error occurred while fetching group data: {str(e)}",
                }
            )
            continue

        update_expressions = []
        expression_values = {}

        # Only update fields if they exist in the input and are allowed to be updated
        if group_data["isPublic"] != existing_group_data.get("isPublic", None):
            update_expressions.append("isPublic = :isPublic")
            expression_values[":isPublic"] = group_data["isPublic"]
        if group_data["amplifyGroups"] != existing_group_data.get(
            "amplifyGroups", None
        ):
            update_expressions.append("amplifyGroups = :amplifyGroups")
            expression_values[":amplifyGroups"] = group_data["amplifyGroups"]
        if group_data["supportConvAnalysis"] != existing_group_data.get(
            "supportConvAnalysis", None
        ):
            update_expressions.append("supportConvAnalysis = :supportConvAnalysis")
            expression_values[":supportConvAnalysis"] = group_data[
                "supportConvAnalysis"
            ]

        # If no changes, skip this group
        if not update_expressions:
            continue

        update_expression = "SET " + ", ".join(update_expressions)

        try:
            response = groups_table.update_item(
                Key={"group_id": group_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ReturnValues="ALL_NEW",
            )

            if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200:
                updated_item = response.get("Attributes", {})
                logger.info(f"Group '{group_id}' updated successfully")

                # Log the update
                log_item(
                    group_id,
                    "Update Group",
                    current_user,
                    f"Group '{group_id}' fields updated.",
                )

                updated_groups.append(group_id)
            else:
                logger.error(f"Failed to update group '{group_id}'")
                failed_updates.append(
                    {
                        "group_id": group_id,
                        "message": f"Failed to update group '{group_id}'",
                    }
                )

        except Exception as e:
            logger.error(f"An error occurred while updating group '{group_id}': {e}")
            failed_updates.append(
                {"group_id": group_id, "message": f"An error occurred: {str(e)}"}
            )

    # Construct the final response
    if len(failed_updates) == 0:
        return {
            "success": True,
            "message": "All groups updated successfully",
            "data": updated_groups,
        }
    else:
        logger.error("Failed groups - failed: %s updated: %s", failed_updates, updated_groups)
        return {
            "success": False,
            "message": "Some group updates failed",
            "data": {"updated": updated_groups, "failed": failed_updates},
        }


@required_env_vars({
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.GET_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
    "OBJECT_ACCESS_DYNAMODB_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@validated(op="create")
def create_amplify_assistants(event, context, current_user, name, data):
    logger.info("Creating group")
    token = data["access_token"]
    # verify is admin
    if not verify_user_as_admin(token, "Create Amplify Assistants Admin Group"):
        return {"success": False, "error": "Unable to authenticate user as admin"}
    data = data["data"]
    member_list = data.get("members", [])
    members_dict = {member: "admin" for member in member_list} if member_list else {}
    members_access_map = filter_for_valid_members_dict(members_dict, token)

    assistants = data.get("assistants", [])
    create_result = group_creation(current_user, "Amplify Assistants", members_access_map)
    if not create_result["success"]:
        logger.error("Failed to create group")
        return create_result
    group_id = create_result["data"]["id"]

    assistants = [
        {
            **ast_def,
            "groupId": group_id,
            "data": {**ast_def.get("data", {}), "groupId": group_id},
        }
        for ast_def in assistants
    ]

    result = asyncio.run(register_ops(token))

    if not result.get("success", False):
        return {"success": False, "message": "Failed to register ops"}

    logger.info("Adding assistants")
    if len(assistants) > 0:
        ast_result = update_assistants(current_user, group_id, "ADD", assistants)
        if not ast_result["success"]:
            logger.error("Failed to add assistants")
            return ast_result

    return {"success": True, "data": {"id": group_id}}

def clean_old_keys():
    """
    Scans the groups_table and removes the 'access' attribute from each item
    if it exists. This is intended as a one-time cleanup operation.
    """
    logger.debug("Starting clean_old_keys process...")
    try:
        scan_kwargs = {}
        updated_count = 0
        while True:
            response = groups_table.scan(**scan_kwargs)
            items = response.get("Items", [])
            for item in items:
                if "access" in item:
                    try:
                        groups_table.update_item(
                            Key={"group_id": item["group_id"]},
                            UpdateExpression="REMOVE #acc",
                            ExpressionAttributeNames={"#acc": "access"},
                        )
                        updated_count += 1
                        logger.debug(
                            f"Removed 'access' attribute from group_id: {item['group_id']}"
                        )
                    except Exception as e:
                        logger.error(f"Error updating item {item['group_id']}: {e}")

            # Handle pagination
            if "LastEvaluatedKey" in response:
                scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            else:
                break  # Exit loop if no more items to scan

        logger.info(f"Finished clean_old_keys process. Updated {updated_count} items.")
        return {
            "success": True,
            "message": f"Successfully cleaned old keys. {updated_count} items updated.",
        }

    except Exception as e:
        logger.error(f"An error occurred during clean_old_keys: {e}")
        return {
            "success": False,
            "message": f"An error occurred during clean_old_keys: {str(e)}",
        }


def delete_group_files(group_id, access_token):
    """
    Delete all files belonging to a group by querying files where createdBy = group_id
    """
    logger.info(f"Deleting all files for group: {group_id}")
    
    try:
        # Query all files created by this group
        files_to_delete = []
        
        # Use DynamoDB to query files by createdBy (which is the group_id for group files)
        dynamodb = boto3.resource("dynamodb")
        files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
        
        # Query using the createdBy index
        response = files_table.query(
            IndexName="createdBy",
            KeyConditionExpression=Key("createdBy").eq(group_id)
        )
        
        files_to_delete.extend(response.get("Items", []))
        
        # Handle pagination
        while "LastEvaluatedKey" in response:
            response = files_table.query(
                IndexName="createdBy",
                KeyConditionExpression=Key("createdBy").eq(group_id),
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            files_to_delete.extend(response.get("Items", []))
        
        logger.info(f"Found {len(files_to_delete)} files to delete for group {group_id}")
        
        # Delete each file with try-catch
        deleted_count = 0
        failed_count = 0
        
        for file_item in files_to_delete:
            file_id = file_item.get("id")
            if file_id:
                try:
                    logger.debug(f"Attempting to delete file: {file_id}")
                    result = delete_file(access_token, file_id)
                    if result.get("success", False):
                        deleted_count += 1
                        logger.debug(f"Successfully deleted file: {file_id}")
                    else:
                        failed_count += 1
                        logger.error(f"Failed to delete file {file_id}: {result.get('message', 'Unknown error')}")
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Exception while deleting file {file_id}: {str(e)}")
        
        logger.info(f"File deletion summary - Deleted: {deleted_count}, Failed: {failed_count}")
        return {
            "success": True, 
            "deleted_count": deleted_count, 
            "failed_count": failed_count,
            "total_files": len(files_to_delete)
        }
        
    except Exception as e:
        logger.error(f"Error querying/deleting group files: {str(e)}")
        return {
            "success": False, 
            "error": str(e),
            "deleted_count": 0,
            "failed_count": 0
        }


async def register_ops(token):
    # temp implementation
    api_doc_ops = ["assistant", "state", "apiKeys", "models", "embedding"]
    data = {"data": {"command": "register"}}
    
    base_url = os.environ.get("API_BASE_URL")
    if not base_url:
        logger.error("API_BASE_URL environment variable not set")
        return {"success": False, "error": "API_BASE_URL not configured"}
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    async def make_register_call(session, path):
        url = f"{base_url}/{path}/register_ops"
        try:
            async with session.post(url, json=data, headers=headers) as response:
                result = await response.json()
                logger.debug(f"Register ops call to {path}: {response.status}")
                # Check for success in the response body, not just HTTP status
                is_successful = result.get("success", False) if result else False
                logger.debug(f"Register ops call to {path}: {result}")
                return {
                    "path": path,
                    "status": response.status,
                    "success": is_successful,
                    "result": result
                }
        except Exception as e:
            logger.error(f"Failed to register ops for {path}: {str(e)}")
            return {
                "path": path,
                "status": None,
                "success": False,
                "error": str(e)
            }
    
    # Make all calls concurrently
    async with aiohttp.ClientSession() as session:
        tasks = [make_register_call(session, path) for path in api_doc_ops]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    successful_ops = []
    failed_ops = []
    
    for result in results:
        if isinstance(result, Exception):
            failed_ops.append({"error": str(result)})
        elif result.get("success"):
            successful_ops.append(result["path"])
        else:
            failed_ops.append(result)
    
    logger.info(f"Successfully registered ops for: {successful_ops}")
    if failed_ops:
        logger.error(f"Failed to register ops for: {failed_ops}")
    
    return {
        "success": len(failed_ops) == 0,
        "successful_ops": successful_ops,
        "failed_ops": failed_ops,
        "total_attempted": len(api_doc_ops)
    }

