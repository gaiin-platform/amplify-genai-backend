def can_check_decision(user, data):
    return True


def can_save_decision(user, data):
    return True


def can_get_latest_disclosure(user, data):
    return True


def get_permission_checker(user, type, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, type, op
        )
    )
    return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)


def get_user(event, data):
    return data["user"]


def get_data_owner(event, data):
    return data["user"]


permissions_by_state_type = {
    "/data-disclosure/latest": {
        "get_latest_data_disclosure": can_get_latest_disclosure,
    },
    "/data-disclosure/save": {
        "save_data_disclosure_decision": can_save_decision,
    },
    "/data-disclosure/check": {
        "check_data_disclosure_decision": can_check_decision,
    },
}
