from .update_admin_config_schema import update_admin_config_schema
from .auth_as_admin_schema import auth_as_admin_schema
from .upload_pptx_schema import upload_pptx_schema
from .verify_in_amp_group_schema import verify_in_amp_group_schema
from .add_user_access_ast_admin import add_user_access_ast_admin

rules = {
    "validators": {
        "/amplifymin/configs": {"read": {}},  # get
        "/amplifymin/configs/update": {"update": update_admin_config_schema},
        "/amplifymin/feature_flags": {"read": {}},
        "/amplifymin/auth": {"read": auth_as_admin_schema},
        "/amplifymin/pptx_templates": {"read": {}},
        "/amplifymin/pptx_templates/delete": {"delete": {}},
        "/amplifymin/pptx_templates/upload": {"upload": upload_pptx_schema},
        "/amplifymin/verify_amp_member": {"read": verify_in_amp_group_schema},
        "/amplifymin/amplify_groups/list": {"read": {}},
        "/amplifymin/amplify_groups/affiliated": {"read": {}},
        "/amplifymin/user_app_configs": {"read": {}},
    },
    "api_validators": {
        "/amplifymin/auth": {"read": auth_as_admin_schema},
        "/amplifymin/verify_amp_member": {"read": verify_in_amp_group_schema},
        "/amplifymin/amplify_groups/affiliated": {"read": {}},
    },
}
