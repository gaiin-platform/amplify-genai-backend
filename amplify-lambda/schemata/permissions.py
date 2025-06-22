"""permissions.py

This module defines permission checks for various operations and state types.
Each permission function determines whether a user is authorized to perform a
specific action.  The `permissions_by_state_type` dictionary maps state types
and operations to their corresponding permission functions.

Copyright (c) Vanderbilt University
Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas, Sam Hays
"""


def can_share(user, data):
    return True


def can_save(user, data):
    return True


def can_delete_item(user, data):
    return True


def can_upload(user, data):
    return True


def can_create_assistant(user, data):
    return True


def can_create_assistant_thread(user, data):
    return True


def can_read(user, data):
    return True


def can_chat(user, data):
    return True


def get_permission_checker(user, type, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, type, op
        )
    )
    return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)


def get_user(event, data):
    return data["user"]


def get_data_owner(event, data):
    return data["user"]


def can_delete_file(user, data):
    return True


permissions_by_state_type = {
    "/state/share": {"append": can_share, "read": can_share},
    "/state/share/load": {"load": can_share},
    "/datasource/metadata/set": {"set": can_upload},
    "/files/upload": {"upload": can_upload},
    "/files/set_tags": {"set_tags": can_upload},
    "/files/tags/create": {"create": can_save},
    "/files/tags/delete": {"delete": can_delete_item},
    "/files/tags/list": {"list": can_read},
    "/files/query": {"query": can_read},
    "/files/download": {"download": can_read},
    "/files/delete": {"delete": can_delete_file},
    "/chat/convert": {"convert": can_save},
    "/state/accounts/charge": {"create_charge": can_save},
    "/state/accounts/get": {"get": can_read},
    "/state/accounts/save": {"save": can_save},
    "/state/conversation/upload": {"conversation_upload": can_upload},
    "/state/conversation/register": {"conversation_upload": can_upload},
    "/state/conversation/get/multiple": {"get_multiple_conversations": can_read},
    "/state/conversation/get": {"read": can_read},
    "/state/conversation/get/all": {"read": can_read},
    "/state/conversation/get/empty": {"read": can_read},
    "/state/conversation/get/metadata": {"read": can_read},
    "/state/conversation/get/since/{timestamp}": {"read": can_read},
    "/state/conversation/delete": {"delete": can_delete_item},
    "/state/conversation/delete_multiple": {
        "delete_multiple_conversations": can_delete_item
    },
    "/chat": {"chat": can_chat},
    "/state/settings/save": {"save": can_save},
    "/state/settings/get": {"get": can_read},
    "/files/reprocess/rag": {"upload": can_upload},
}
