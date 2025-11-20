# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


def can_retrieve(user, data):
    return True


def can_terminate(user, data):
    return True


def can_delete(user, data):
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
    "/embedding-retrieval": {"retrieval": can_retrieve},
    "/embedding-dual-retrieval": {"dual-retrieval": can_retrieve},
    "/embedding/terminate": {"terminate": can_terminate},
    "/embedding/sqs/get": {"get": can_retrieve},
    "/embedding-delete": {"embedding-delete": can_delete},
    "/embedding/check-completion": {"embeddings-check": can_retrieve},
    "/embedding/register_ops": {"register_ops": can_retrieve},
    "/embedding/status": {"get_status": can_retrieve},
}
