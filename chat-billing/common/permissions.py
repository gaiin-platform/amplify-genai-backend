def can_generate_report(user, data):
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
    "/billing/report-generator": {
        "report_generator": can_generate_report,
    },
}
