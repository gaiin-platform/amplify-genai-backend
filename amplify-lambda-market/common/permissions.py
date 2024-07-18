def get_permission_checker(user, type, op, data):
    print("Checking permissions for user: {} and type: {} and op: {}".format(user, type, op))
    return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)


def always_allowed(event, data):
    return True

def can_publish_item(user, data):
  return True

def can_delete_item(user, data):
  return True


permissions_by_state_type = {
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
  }
