def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )


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
    "/apiKeys/keys/get": {"read": can_read},
    "/apiKeys/key/rotate": {"rotate": can_save},
    "/apiKeys/get_keys_ast": {"read": can_read},
    "/apiKeys/keys/create": {"create": can_save},
    "/apiKeys/key/deactivate": {"deactivate": can_deactivate},
    "/apiKeys/keys/update": {"update": can_save},
    "/apiKeys/get_system_ids": {"read": can_read},
    "/apiKeys/api_documentation/get": {"read": can_read},
    "/apiKeys/api_documentation/upload": {"upload": can_save},
    "/apiKeys/api_documentation/get_templates": {"read": can_read},
    "/apiKeys/register_ops": {"register_ops": can_save}, 
}
