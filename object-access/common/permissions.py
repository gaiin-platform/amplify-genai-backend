
def can_update_permissions(user, data):
  return True

def can_get_permissions(user, data):
  return True

def get_permission_checker(user, type, op, data):
  print("Checking permissions for user: {} and type: {} and op: {}".format(user, type, op))
  return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)

def get_user(event, data):
  return data['user']

def get_data_owner(event, data):
  return data['user']

permissions_by_state_type = {
  "/embedding-dual-retrieval": {
    "dual-retrieval": can_update_permissions
  },
  "/utilities/update_object_permissions": {
    "update_object_permissions": can_update_permissions
  },
  "/utilities/can_access_objects": {
    "can_access_objects": can_get_permissions
  },
}
