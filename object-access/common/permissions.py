def can_update_permissions(user, data):
    return True


def can_get_permissions(user, data):
    return True


def can_create_cognito_group(user, data):
    return True


def get_permission_checker(user, type, op, data):
    print("Checking permissions for user: {} and type: {} and op: {}".format(user, type, op))
    return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)


def get_user(event, data):
    return data['user']


def get_data_owner(event, data):
    return data['user']

def can_read_emails(user, data):
  return True

def can_read_cognito_groups(user, data):
  return True

permissions_by_state_type = {
    "/utilities/emails": {
    "read": can_read_emails
    },
    "/utilities/update_object_permissions": {
        "update_object_permissions": can_update_permissions
    },
    "/utilities/can_access_objects": {
        "can_access_objects": can_get_permissions
    },
    "/utilities/simulate_access_to_objects": {
        "simulate_access_to_objects": can_get_permissions
    },
    "/utilities/create_cognito_group": {
        "create_cognito_group": can_create_cognito_group
    },
    "/utilities/in_cognito_group": {
    "read": can_read_cognito_groups
    }
}
