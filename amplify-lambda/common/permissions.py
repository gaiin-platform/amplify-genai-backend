
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

def can_read(user, data):
  return True

def can_chat(user, data):
  return True

def get_permission_checker(user, type, op, data):
  print("Checking permissions for user: {} and type: {} and op: {}".format(user, type, op))
  return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)

def get_user(event, data):
  return data['user']

def get_data_owner(event, data):
  return data['user']

permissions_by_state_type = {
  "/state/share": {
    "append": can_share,
    "read": can_read_share
  },
  "/state/share/load": {
    "load": can_read_share
  },
  "/state/base-prompts/get": {
    "get": can_read_share
  },
  "/datasource/metadata/set": {
    "set": can_upload
  },
  "/files/upload": {
    "upload": can_upload
  },
  "/files/set_tags": {
    "set_tags": can_upload
  },
  "/files/tags/create": {
    "create": can_upload
  },
  "/files/tags/delete": {
    "delete": can_upload
  },
  "/files/tags/list": {
    "list": can_upload
  },
  "/files/query": {
    "query": can_upload
  },
  "/files/download": {
    "download": can_upload
  },
  "/chat/convert": {
    "convert": can_publish_item
  },
  "/state/accounts/charge": {
    "create_charge": can_publish_item
    },
  "/state/accounts/get": {
    "get": can_read
  },
  "/state/accounts/save": {
    "save": can_publish_item
  },
  "/state/conversation/upload": {
    "conversation_upload": can_upload
  },
  "/state/conversation/get_multiple": {
    "get_multiple_conversations": can_read
  },
  "/state/conversation/get": {
    "read": can_read
  },
  "/state/conversation/get_all": {
    "read": can_read
  },
  "/state/conversation/delete": {
    "delete": can_delete_item
  },
  "/state/conversation/delete_multiple": {
    "delete_multiple_conversations": can_delete_item
  },
  "/chat": {
    "chat": can_chat
  },
   "/state/settings/save": {
        "save": can_upload
    },
    "/state/settings/get": {
        "get": can_read
    },
}
