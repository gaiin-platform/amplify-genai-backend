# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()


def can_update_permissions(user, data):
    return True


def can_get_permissions(user, data):
    return True


def can_create(user, data):
    return True


def can_update(user, data):
    return True


def can_delete(user, data):
    return True


def can_add_path(user, data):
    return True


def get_permission_checker(user, type, op, data):
    logger.info("Checking permissions for user: %s, type: %s, op: %s", user, type, op)
    checker = permissions_by_state_type.get(type, {}).get(op)
    if not checker:
        logger.warning("No permission checker found for type: %s and op: %s", type, op)
    return checker or (lambda user, data: False)


def get_user(event, data):
    return data["user"]


def get_data_owner(event, data):
    return data["user"]


def can_read(user, data):
    return True


permissions_by_state_type = {
    "/utilities/update_object_permissions": {
        "update_object_permissions": can_update_permissions
    },
    "/utilities/can_access_objects": {"can_access_objects": can_get_permissions},
    "/utilities/simulate_access_to_objects": {
        "simulate_access_to_objects": can_get_permissions
    },
    "/utilities/validate_users": {"validate_users": can_get_permissions},
    "/utilities/create_cognito_group": {"create_cognito_group": can_create},
    "/utilities/get_user_groups": {"read": can_read},
    "/utilities/in_cognito_amp_groups": {"in_group": can_read},
    "/utilities/emails": {"read": can_read},
    "/groups/create": {"create": can_create},
    "/groups/update/members": {"update": can_update},
    "/groups/update/members/permissions": {"update": can_update},
    "/groups/update/assistants": {"update": can_update},
    "/groups/update/types": {"update": can_update},
    "/groups/update": {"update": can_update},
    "/groups/update/amplify_groups": {"update": can_update},
    "/groups/update/system_users": {"update": can_update},
    "/groups/delete": {"delete": can_delete},
    "/groups/list": {"list": can_read},
    "/groups/list_all": {"list": can_read},
    "/groups/members/list": {"list": can_read},
    "/groups/replace_key": {"update": can_update},
    "/groups/assistants/amplify": {"create": can_create},
    "/groups/assistant/add_path": {"add_assistant_path": can_add_path},
    "/groups/verify_ast_group_member": {"verify_member": can_read},
}
