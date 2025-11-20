def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )


def can_execute_custom_auto(user, data):
    return True


"""
Every service must define the permissions for each operation
here. The permissions are defined as a dictionary of
dictionaries where the top level key is the path to the
service and the second level key is the operation. The value
is a function that takes a user and data and returns if the
user can do the operation.
"""
permissions_by_state_type = {
    "/assistant-api/execute-custom-auto": {
        "execute_custom_auto": can_execute_custom_auto
    },
    "/integrations/oauth/start-auth": {"start_oauth": lambda for_user, with_data: True},
    "/integrations/oauth/user/delete": {
        "delete_integration": lambda for_user, with_data: True
    },
    "/assistant-api/get-job-result": {"get_result": lambda for_user, with_data: True},
    "/assistant-api/set-job-result": {"set_result": lambda for_user, with_data: True},
    "/integrations/oauth/user/list": {
        "list_integrations": lambda for_user, with_data: True
    },
    "/integrations/list_supported": {
        "list_integrations": lambda for_user, with_data: True
    },
    "/integrations/user/files": {"list_files": lambda for_user, with_data: True},
    "/integrations/user/files/download": {
        "download_file": lambda for_user, with_data: True
    },
    "/integrations/user/files/upload": {
        "upload_files": lambda for_user, with_data: True
    },
    "/integrations/oauth/register_secret": {
        "register_secret": lambda for_user, with_data: True
    },
    "/integrations/oauth/refresh_token": {
        "refresh_token": lambda for_user, with_data: True
    },

}
