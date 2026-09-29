from pycommon.logger import getLogger

logger = getLogger("presentation_agent_permissions")


def allow_authenticated(user, data):
    # Ownership (status) and admin rights (template analysis) are checked in the handlers.
    return True


permissions_by_state_type = {
    "/presentation/start": {"start": allow_authenticated},
    "/presentation/status": {"status": allow_authenticated},
    "/presentation/template/analyze": {"analyze": allow_authenticated},
    "/presentation/template/status": {"read": allow_authenticated},
}


def get_permission_checker(user, ptype, op, data):
    logger.debug("Checking permissions for user: %s and type: %s and op: %s", user, ptype, op)
    return permissions_by_state_type.get(ptype, {}).get(op, lambda for_user, with_data: False)
