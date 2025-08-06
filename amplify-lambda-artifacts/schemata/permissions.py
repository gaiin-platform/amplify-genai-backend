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


def can_share(user, data):
    return True


def can_delete(user, data):
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
    "/artifacts/get_all": {"read": can_read},
    "/artifacts/get": {"read": can_read},
    "/artifacts/delete": {"delete": can_delete},
    "/artifacts/save": {"save": can_save},
    "/artifacts/share": {"share": can_share},
}
