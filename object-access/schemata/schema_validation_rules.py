from .update_object_permissions import update_object_permissions
from .check_object_permissions import check_object_permissions
from .simulate_access_to_objects import simulate_access_to_objects
from .in_amp_cogn_group_schema import in_amp_cogn_group_schema
from .create_cognito_group_schema import create_cognito_group_schema
from .members_schema import members_schema
from .update_group_type_schema import update_group_type_schema
from .update_amplify_group_schema import update_amplify_group_schema
from .update_system_user_schema import update_system_user_schema
from .create_admin_group_schema import create_admin_group_schema
from .create_assistant_schema import create_assistant_schema
from .create_amplify_assistants_group_schema import (
    create_amplify_assistants_group_schema,
)
from .update_ast_schema import update_ast_schema
from .update_members_schema import update_members_schema
from .update_members_perms_schema import update_members_perms_schema
from .update_groups_schema import update_groups_schema
from .groupId_schema import groupId_schema
from .assistant_path_schema import assistant_path_schema
from .add_assistant_path_schema import add_assistant_path_schema
from .validate_users_schema import validate_users_schema

rules = {
    "validators": {
        "/utilities/update_object_permissions": {
            "update_object_permissions": update_object_permissions
        },
        "/utilities/can_access_objects": {"can_access_objects": check_object_permissions},
        "/utilities/simulate_access_to_objects": {
            "simulate_access_to_objects": simulate_access_to_objects
        },
        "/utilities/validate_users": {"validate_users": validate_users_schema},
        "/utilities/create_cognito_group": {
            "create_cognito_group": create_cognito_group_schema
        },
        "/utilities/get_user_groups": {"read": {}},
        "/utilities/in_cognito_amp_groups": {"in_group": in_amp_cogn_group_schema},
        "/utilities/emails": {"read": {}},
        "/groups/create": {"create": create_admin_group_schema},
        "/groups/update/members": {"update": update_members_schema},
        "/groups/update/members/permissions": {"update": update_members_perms_schema},
        "/groups/update/assistants": {"update": update_ast_schema},
        "/groups/update/types": {"update": update_group_type_schema},
        "/groups/update/amplify_groups": {"update": update_amplify_group_schema},
        "/groups/update/system_users": {"update": update_system_user_schema},
        "/groups/delete": {"delete": {}},
        "/groups/list": {"list": {}},
        "/groups/list_all": {"list": {}},
        "/groups/members/list": {"list": {}},
        "/groups/update": {"update": update_groups_schema},
        "/groups/replace_key": {"update": groupId_schema},
        "/groups/assistants/amplify": {"create": create_amplify_assistants_group_schema},
        "/groups/assistant/add_path": {"add_assistant_path": add_assistant_path_schema},
        "/groups/verify_ast_group_member": {"verify_member": groupId_schema},
    },
    "api_validators": {
        "/utilities/update_object_permissions": {
            "update_object_permissions": update_object_permissions
        },
        "/utilities/can_access_objects": {"can_access_objects": check_object_permissions},
        "/utilities/simulate_access_to_objects": {
            "simulate_access_to_objects": simulate_access_to_objects
        },
        "/utilities/validate_users": {"validate_users": validate_users_schema},
        "/groups/verify_ast_group_member": {"verify_member": groupId_schema},
        "/utilities/emails": {"read": {}},
    }
}

