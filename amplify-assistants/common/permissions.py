def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )


def can_create_assistant(user, data):
    return True


def can_list_assistant(user, data):
    return True


def can_read(user, data):
    return True


def can_delete_assistant(user, data):
    return True


def can_chat_with_code_interpreter(user, data):
    return True


def can_download(user, data):
    return True


def can_get_group_assistant_conversations(user, data):
    return True


def can_get_group_assistant_dashboards(user, data):
    return True


def can_save_user_rating(user, data):
    return True

def can_get_group_conversations_data(user, data):
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
    "/assistant/create": {"create": can_create_assistant},
    "/assistant/list": {"list": can_list_assistant},
    "/assistant/delete": {"delete": can_delete_assistant},
    "/assistant/share": {"share_assistant": can_create_assistant},
    "/assistant/openai/delete": {"delete": can_delete_assistant},
    "/assistant/openai/thread/delete": {"delete": can_delete_assistant},
    "/assistant/chat/codeinterpreter": {"chat": can_chat_with_code_interpreter},
    "/assistant/create/codeinterpreter": {"create": can_create_assistant},
    "/assistant/files/download/codeinterpreter": {"download": can_download},
    "/assistant/remove_astp_permissions": {
        "remove_astp_permissions": can_delete_assistant
    },
    "/assistant/get_group_assistant_conversations": {
        "get_group_assistant_conversations": can_get_group_assistant_conversations
    },
    "/assistant/get/system_user": {"get": can_read},
    "/assistant/get_group_assistant_dashboards": {
        "get_group_assistant_dashboards": can_get_group_assistant_dashboards
    },
    "/assistant/save_user_rating": {
        "save_user_rating": can_save_user_rating
    },
    "/assistant/get_group_conversations_data": {
        "get_group_conversations_data": can_get_group_conversations_data
    }
}
