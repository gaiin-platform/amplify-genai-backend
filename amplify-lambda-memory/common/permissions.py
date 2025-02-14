def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )


def can_extract_facts(user, data):
    return True


def can_remove_memory(user, data):
    return True


def can_edit_memory(user, data):
    return True


def can_save_memory_batch(user, data):
    return True


def can_read_memory_by_taxonomy(user, data):
    return True


def can_update_memory_taxonomy(user, data):
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
    "/memory/extract-facts": {"extract_facts": can_extract_facts},
    "/memory/remove-memory": {"remove_memory": can_remove_memory},
    "/memory/edit-memory": {"edit_memory": can_edit_memory},
    "/memory/save-memory-batch": {"save_memory_batch": can_save_memory_batch},
    "/memory/read-memory-by-taxonomy": {
        "read_memory_by_taxonomy": can_read_memory_by_taxonomy
    },
    "/memory/update-memory-taxonomy": {
        "update_memory_taxonomy": can_update_memory_taxonomy
    },
}
