def get_permission_checker(user, ptype, op, data):
    print(f"Checking permissions for user: {user} and type: {ptype} and op: {op}")
    permission_func = permissions_by_state_type.get(ptype, {}).get(op)
    
    if not permission_func:
        raise PermissionError(f"No permission function found for path: {ptype} and operation: {op}")
    
    def checker(for_user, with_data):
        if not permission_func(for_user, with_data):
            raise PermissionError(f"User {for_user} does not have permission for {op} on {ptype}")
        return True
    
    return checker

# The rest of your code remains unchanged
def can_send_email(user, data):
    """
    Sample permission checker
    :param user: the user to check
    :param data: the request data
    :return: if the user can do the operation
    """
    return True

permissions_by_state_type = {
    "/ses/send-email": {
        "send_email": can_send_email,
    },
}