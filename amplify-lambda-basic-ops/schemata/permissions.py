def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )


def can_prompt(user, data):
    """
    Sample permission checker
    :param user: the user to check
    :param data: the request data
    :return: if the user can do the operation
    """
    return True


def can_create(user, data):
    return True


def can_read(user, data):
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
    "/llm/query": {"query": can_prompt},
    "/llm/qa_check": {"qa_check": can_read},
    "/llm/workflow": {"llm_workflow": can_prompt},
    "/llm/workflow-start": {"llm_workflow_async": can_prompt},
    "/work/echo": {"echo": can_read},
    "/work/session/create": {"create": can_create},
    "/work/session/add_record": {"add_record": can_create},
    "/work/session/list_records": {"list_records": can_read},
    "/work/session/delete_record": {"delete_record": can_delete},
    "/work/session/stitch_records": {"stitch_records": can_create},
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
