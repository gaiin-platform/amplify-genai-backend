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


def can_update(user, data):
    return True


def can_delete(user, data):
    return True


def can_upload(user, data):
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
    "/amplifymin/configs": {"read": can_read},
    "/amplifymin/configs/update": {"update": can_update},
    "/amplifymin/feature_flags": {"read": can_read},
    "/amplifymin/auth": {"read": can_read},
    "/amplifymin/pptx_templates": {"read": can_read},
    "/amplifymin/pptx_templates/delete": {"delete": can_delete},
    "/amplifymin/pptx_templates/upload": {"upload": can_upload},
    "/amplifymin/verify_amp_member": {"read": can_read},
    "/amplifymin/amplify_groups/list": {"read": can_read},
    "/amplifymin/user_app_configs": {"read": can_read},
    "/amplifymin/amplify_groups/affiliated": {"read": can_read}
}
