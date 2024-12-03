
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas



def can_share(user, data):
  return True

def can_publish_item(user, data):
  return True

def can_delete_item(user, data):
  return True

def can_upload(user, data):
  return True

def can_create_assistant(user, data):
  return True
def can_create_assistant_thread(user, data):
  return True

def can_read_share(user, data):
  # Read share automatically pulls data for the authenticated user
  return True

def get_permission_checker(user, type, op, data):
  print("Checking permissions for user: {} and type: {} and op: {}".format(user, type, op))
  return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)

def get_user(event, data):
  return data['user']

def get_data_owner(event, data):
  return data['user']

permissions_by_state_type = {
  "/embedding-retrieval": {
    "retrieval": can_publish_item
  },
  "/embedding-dual-retrieval": {
    "dual-retrieval": can_publish_item
  }
}
