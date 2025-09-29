def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )



"""
Every service must define the permissions for each operation
here. The permissions are defined as a dictionary of
dictionaries where the top level key is the path to the
service and the second level key is the operation. The value
is a function that takes a user and data and returns if the
user can do the operation.
"""
permissions_by_state_type = {
    "/user-data/put": {"route": lambda for_user, with_data: True},
    "/user-data/get": {"route": lambda for_user, with_data: True},
    "/user-data/get-by-uuid": {"route": lambda for_user, with_data: True},
    "/user-data/query-range": {"route": lambda for_user, with_data: True},
    "/user-data/query-prefix": {"route": lambda for_user, with_data: True},
    "/user-data/query-type": {"route": lambda for_user, with_data: True},
    "/user-data/delete": {"route": lambda for_user, with_data: True},
    "/user-data/batch-put": {"route": lambda for_user, with_data: True},
    "/user-data/batch-get": {"route": lambda for_user, with_data: True},
    "/user-data/batch-delete": {"route": lambda for_user, with_data: True},
    "/user-data/delete-by-uuid": {"route": lambda for_user, with_data: True},
    "/user-data/list-apps": {"route": lambda for_user, with_data: True},
    "/user-data/list-entity-types": {"route": lambda for_user, with_data: True},
}
