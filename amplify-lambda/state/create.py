# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
import time
import uuid

import boto3

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)


dynamodb = boto3.resource("dynamodb")


@validated("create")
def create(event, context, user, name, data):
    timestamp = str(time.time())

    table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

    item = {
        "id": str(uuid.uuid1()),
        "data": data["data"],
        "user": user,
        "name": name,
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }

    # write the todo to the database
    table.put_item(Item=item)

    return item
