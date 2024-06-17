def get_permission_checker(user, ptype, op, data):
    print("Checking permissions for user: {} and type: {} and op: {}".format(user, ptype, op))
    return permissions_by_state_type.get(ptype, {}).get(op, lambda for_user, with_data: False)


def can_create_db(user, data):
    """
    Sample permission checker
    :param user: the user to check
    :param data: the request data
    :return: if the user can do the operation
    """
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
    "/pdb/sql/create": {
        "create": can_create_db
    },
    "/pdb/sql/register": {
        "register": can_create_db
    },
    "/pdb/sql/list": {
        "list": can_create_db
    },
    "/pdb/sql/row/insert": {
        "insert": can_create_db
    },
    "/pdb/sql/row/list": {
        "list_items": can_create_db
    },
    "/pdb/sql/schema": {
        "describe": can_create_db
    },
    "/pdb/sql/files/schema": {
        "describe": can_create_db
    },
    "/pdb/sql/query": {
        "query": can_create_db
    },
    "/pdb/sql/llmquery": {
        "query": can_create_db
    },
}
