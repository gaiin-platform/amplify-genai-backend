def get_permission_checker(user, type, op, data):
    print("Checking permissions for user: {} and type: {} and op: {}".format(user, type, op))
    return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)


def always_allowed(event, data):
    return True


permissions_by_state_type = {
  "/dar/execute_sql_query": {
    "execute_sql": always_allowed
  },
}
