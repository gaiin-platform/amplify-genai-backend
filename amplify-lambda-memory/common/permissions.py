def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )


def can_save_memory(user, data):
    return True


def can_extract_facts(user, data):
    return True


def can_read_memory(user, data):
    return True


def can_remove_memory(user, data):
    return True

def can_create_project(user, data):
    return True

def can_get_projects(user, data):
    return True


def can_delete_project(user, data):
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
    "/memory/save-memory": {"save_memory": can_save_memory},
    "/memory/extract-facts": {"extract_facts": can_extract_facts},
    "/memory/read-memory": {"read_memory": can_read_memory},
    "/memory/remove-memory": {"remove_memory": can_remove_memory},
    "/memory/create-project": {"create_project": can_create_project},
    "/memory/get-projects": {"get_projects": can_get_projects},
    "/memory/delete-project": {"delete_project": can_delete_project},
}
