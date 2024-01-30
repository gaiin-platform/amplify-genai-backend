

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
  "/assistant/files/upload": {
    "upload": can_upload
  },
  "/assistant/files/download": {
    "download": can_upload
  },
  "/assistant/create": {
    "create": can_create_assistant
  },
  "/assistant/delete": {
    "delete": can_create_assistant
  },
  "/assistant/thread/create": {
    "create": can_create_assistant_thread
  },
  "/assistant/thread/delete": {
    "delete": can_create_assistant_thread
  },
  "/assistant/thread/list": {
    "create": can_create_assistant_thread
  },
  "/assistant/thread/message/create": {
    "add_message": can_create_assistant_thread
  },
  "/assistant/thread/message/list": {
    "get_messages": can_create_assistant_thread
  },
  "/assistant/thread/run": {
    "run": can_create_assistant_thread
  },
  "/assistant/thread/run/status": {
    "run_status": can_create_assistant_thread
  },
  "/assistant/chat" : {
    "chat": can_create_assistant_thread
  },
  "/market/item/publish" : {
    "publish_item": can_publish_item
  },
  "/market/item/delete" : {
    "delete_item": can_delete_item
  },
  "/market/ideate": {
    "ideate": can_publish_item
  },
  "/market/category/get" : {
    "get_category": can_publish_item
  },
  "/market/category/list" : {
    "list_categories": can_publish_item
  },
  "/market/item/get" : {
    "get_item": can_publish_item
  },
  "/market/item/examples/get" : {
    "get_examples": can_publish_item
  },
  "/chat/convert": {
    "convert": can_publish_item
  },
  "/state/accounts/charge": {
    "create_charge": can_publish_item
    },
  "/state/accounts/get": {
    "get": can_publish_item
  },
  "/state/accounts/save": {
    "save": can_publish_item
  },
  "/embeddding-retrieval": {
    "retrieval": can_publish_item
  },
}
