def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )


def can_execute_custom_auto(user, data): 
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
    "/integrations/google/sheets/get-rows": {
        "get_rows": lambda for_user, with_data: True
    },
    "/integrations/google/sheets/get-info": {
        "get_google_sheets_info": lambda for_user, with_data: True
    },
    "/integrations/google/sheets/get-sheet-names": {
        "get_sheet_names": lambda for_user, with_data: True
    },
    "/integrations/google/sheets/insert-rows": {
        "insert_rows": lambda for_user, with_data: True
    },
    "/integrations/google/sheets/delete-rows": {
        "delete_rows": lambda for_user, with_data: True
    },
    "/integrations/google/sheets/update-rows": {
        "update_rows": lambda for_user, with_data: True
    },
    "/integrations/google/sheets/create-spreadsheet": {
        "create_spreadsheet": lambda for_user, with_data: True
    }
}
