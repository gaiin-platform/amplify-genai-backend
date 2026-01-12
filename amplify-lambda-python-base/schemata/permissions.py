def can_do_sample(user, data):
    """
    Sample permission checker.

    Args:
        user: Username from authentication
        data: Validated request data

    Returns:
        bool: True if allowed, False otherwise
    """
    # Example permission logic:
    # - Check if user owns the resource
    # - Check if user has admin privileges
    # - Validate data constraints

    # For this sample, we allow all authenticated users
    return True


"""
Every service must define the permissions for each operation
here. The permissions are defined as a dictionary of
dictionaries where the top level key is the path to the
service and the second level key is the operation. The value
is a function that takes a user and data and returns if the
user can do the operation.

Example permission patterns:
- Always allow: return True
- User ownership: return user == data["data"].get("owner")
- Admin only: return is_admin(user)
- Complex: return user == owner or is_admin(user)
"""
permissions_by_state_type = {
    "/someservice/sample": {
        "sample": can_do_sample
    },
}


def get_permission_checker():
    """
    Returns the permission checker function.
    Called by setup_validated().
    """
    def permission_checker(path, operation, user, data):
        permissions = permissions_by_state_type.get(path, {})
        permission_func = permissions.get(operation)

        if not permission_func:
            # No permission defined = deny by default
            return False

        return permission_func(user, data)

    return permission_checker
