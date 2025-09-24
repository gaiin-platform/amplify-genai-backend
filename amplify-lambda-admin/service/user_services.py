
import os
import boto3
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.const import APIAccessType
add_api_access_types([APIAccessType.ASSISTANTS.value, APIAccessType.CHAT.value, APIAccessType.EMBEDDING.value, APIAccessType.ADMIN.value])

setup_validated(rules, get_permission_checker)
dynamodb = boto3.resource("dynamodb")
admin_table = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"])


@validated(op="read")
def verify_is_in_amp_group(event, context, current_user, name, data):
    amp_groups = data["data"]["groups"]
    try:
        isMember = is_in_amp_group(current_user, amp_groups)
        print(f"User {current_user} is in group: {isMember}")
        return {"success": True, "isMember": isMember}
    except Exception as e:
        print(f"Error verifying is in amp group: {str(e)}")
        return {"success": False, "message": f"Error verifying is in amp group: {str(e)}"}


def is_in_amp_group(current_user, check_amplify_groups):
    if len(check_amplify_groups) == 0:
        return False
    """
    Given a current_user and a list of group names (check_amplify_groups), determine if the user
    has access via direct or indirect (nested) membership in any of these groups.

    Steps:
    1. Retrieve all_amplify_groups from the admin table.
    2. For each group in check_amplify_groups, check if the user is a member.
    3. If found in any, return True. Otherwise, return False.
    """

    all_amplify_groups = get_all_amplify_groups()
    if all_amplify_groups is None:
        raise Exception("No Amplify Groups Found")
    
    if not all_amplify_groups or len(all_amplify_groups) == 0:
        return False

    # Check each provided group in check_amplify_groups for user membership
    visited = set()
    for group_name in check_amplify_groups:
        if user_in_group(group_name, current_user, all_amplify_groups, visited):
            return True

    # If none of the groups matched, user is not in any Amplify Group
    return False


def user_in_group(group_name, current_user, all_amplify_groups, visited):
    """
    Checks if `current_user` is in `group_name` directly or through nested groups.
    Avoids infinite loops using the `visited` set.
    """
    # If the group does not exist in the map, return False
    # If we have already visited this group, return False to avoid cycles
    if group_name not in all_amplify_groups or group_name in visited:
        return False

    visited.add(group_name)

    cur_group = all_amplify_groups[group_name]

    # Check direct membership
    members = cur_group.get("members", [])
    if current_user in members:
        return True

    # Check include groups
    for include_group_name in cur_group.get("includeFromOtherGroups", []):
        if user_in_group(include_group_name, current_user, all_amplify_groups, visited):
            return True

    # If user not found here or in any included groups
    return False


@validated(op="read")
def get_user_amplify_groups(event, context, current_user, name, data):
    # will need some rework - for now we are just going to return all the groups
    all_groups = get_all_amplify_groups()
    if not all_groups:
        return {"success": False, "message": "No Amplify Groups Found"}
    return {"success": True, "data": list(all_groups.keys())}


def get_all_amplify_groups():
    # prevent circular import
    from service.core import AdminConfigTypes
    try:
        config_item = admin_table.get_item(
            Key={"config_id": AdminConfigTypes.AMPLIFY_GROUPS.value}
        )
        if "Item" in config_item and "data" in config_item["Item"]:
            return config_item["Item"]["data"]
        else:
            print("No Amplify Groups Found")
    except Exception as e:
        print(f"Error retrieving {AdminConfigTypes.AMPLIFY_GROUPS.value}: {str(e)}")
    return None


@validated(op="read")
def get_user_affiliated_groups(event, context, current_user, name, data):
    try:
        all_groups = get_all_amplify_groups()
        if not all_groups:
            return {"success": False, "message": "No Amplify Groups Found"}
        
        affiliated_groups = find_all_user_groups(current_user, all_groups)
        return {"success": True, "data": affiliated_groups, "all_groups": all_groups}
    except Exception as e:
        print(f"Error retrieving user affiliated groups: {str(e)}")
        return {"success": False, "message": f"Error retrieving user affiliated groups: {str(e)}"}


def find_all_user_groups(current_user, all_groups):
    """
    Find all groups a user is affiliated with (direct and indirect membership).
    
    Returns a list of group names the user belongs to.
    """
    affiliated = []
    
    # Phase 1: Find all groups where user is a direct member
    direct_groups = set()
    for group_name, group_data in all_groups.items():
        members = group_data.get("members", [])
        if current_user in members:
            direct_groups.add(group_name)
            affiliated.append(group_name)
    
    # Phase 2: Find all groups that include user's groups (directly or indirectly)
    # Use BFS to find all groups that eventually include user's direct groups
    for group_name, group_data in all_groups.items():
        if group_name not in direct_groups:  # Skip already found direct groups
            visited = set()
            if group_includes_user_groups(group_name, direct_groups, all_groups, visited):
                affiliated.append(group_name)
    
    return affiliated


def group_includes_user_groups(group_name, user_direct_groups, all_groups, visited):
    """
    Check if a group includes any of the user's direct groups through its includeFromOtherGroups chain.
    """
    if group_name not in all_groups or group_name in visited:
        return False
    
    visited.add(group_name)
    group_data = all_groups[group_name]
    
    # Check if this group directly includes any of user's direct groups
    includes = group_data.get("includeFromOtherGroups", [])
    for included_group in includes:
        if included_group in user_direct_groups:
            return True
        # Recursively check if included group eventually includes user's groups
        if group_includes_user_groups(included_group, user_direct_groups, all_groups, visited):
            return True
    
    return False

