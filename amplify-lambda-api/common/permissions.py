def get_permission_checker(user, ptype, op, data):
    print("Checking permissions for user: {} and type: {} and op: {}".format(user, ptype, op))
    return permissions_by_state_type.get(ptype, {}).get(op, lambda for_user, with_data: False)


def can_read(user, data):
    return True

def can_save(user, data):
    return True

def can_deactivate(user, data):
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
    "/apiKeys/get_keys": {
        "read": can_read
    },
    "/apiKeys/get_key": {
        "read": can_read
    },
     "/apiKeys/get_keys_ast": {
        "read": can_read
    },
    "/apiKeys/create_keys": {
        "create": can_save
    },
    "/apiKeys/deactivate_key": {
        "deactivate": can_deactivate
    },
     "/apiKeys/update_keys" : {
        "update": can_save
    },
    "/apiKeys/get_system_ids": {
        "read": can_read
    },
    "/apiKeys/api_documentation": {
        "read": can_read
    }
}
