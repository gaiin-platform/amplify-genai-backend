def get_permission_checker(user, ptype, op, data):
    print("Checking permissions for user: {} and type: {} and op: {}".format(user, ptype, op))
    return permissions_by_state_type.get(ptype, {}).get(op, lambda for_user, with_data: False)


def can_create_assistant(user, data):
    return True


def can_create_assistant_thread(user, data):
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
    "/assistant/create": {
        "create": can_create_assistant
    },
    "/assistant/list": {
        "list": can_create_assistant
    },
    "/assistant/delete": {
        "delete": can_create_assistant
    },
    "/assistant/share": {
        "share_assistant": can_create_assistant
    },
    "/openai/assistant/delete": {
        "delete": can_create_assistant
    },
    "/assistant/thread/create": {
        "create": can_create_assistant_thread
    },
    "/assistant/thread/delete": {
        "delete": can_create_assistant_thread
    },
    "/assistant/thread/list": {
        "create": can_create_assistant_thread
    },
    "/assistant/thread/message/create": {
        "add_message": can_create_assistant_thread
    },
    "/assistant/thread/message/list": {
        "get_messages": can_create_assistant_thread
    },
    "/assistant/thread/run": {
        "run": can_create_assistant_thread
    },
    "/assistant/thread/run/status": {
        "run_status": can_create_assistant_thread
    },
    "/assistant/chat": {
        "chat": can_create_assistant_thread
    },
     "/assistant/chat_with_code_interpreter": {
        "chat_with_code_interpreter": can_create_assistant_thread
    },
    "/": {
        "chat": can_create_assistant_thread
    },
}
