# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import json
import time
import logging
import os
import uuid

from boto3.dynamodb.conditions import Key
from pycommon.encoders import dumps_lossy

import boto3

from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)

dynamodb = boto3.resource("dynamodb")


def update(event, context):
    data = json.loads(event["body"])
    if "data" not in data or "user" not in data or "name" not in data:
        logging.error("Validation Failed")
        raise Exception("Couldn't update the data item.")
        return

    timestamp = int(time.time() * 1000)

    table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

    result = table.update_item(
        Key={"id": event["pathParameters"]["id"]},
        ExpressionAttributeNames={
            "#data": "data",
        },
        ExpressionAttributeValues={
            ":data": data["data"],
            ":updatedAt": timestamp,
        },
        UpdateExpression="SET #data = :data, " "updatedAt = :updatedAt",
        ReturnValues="ALL_NEW",
    )

    # create a response
    response = {"statusCode": 200, "body": dumps_lossy(result["Attributes"])}

    return response


@validated("append")
def append_using_user_and_name(event, context, user, name, data):

    new_data = data["data"]

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

    # Step 1: Query using the secondary index to get the primary key
    response = table.query(
        IndexName="UserNameIndex",
        KeyConditionExpression=Key("user").eq(user) & Key("name").eq(name),
    )

    items = response.get("Items")
    timestamp = int(time.time() * 1000)

    if not items:
        # No item found with user and name, create a new item
        new_item = {
            "id": str(
                uuid.uuid4()
            ),  # For the purposes of this example, generating a new UUID for id
            "user": user,
            "name": name,
            "data": [new_data],  # Here, new_data is wrapped with []
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        table.put_item(Item=new_item)
        return {"statusCode": 200, "body": dumps_lossy(new_item)}

    elif len(items) > 1:
        raise Exception(f"More than one item found with user: {user} and name: {name}")

    # Otherwise, update the existing item
    item = items[0]

    result = table.update_item(
        Key={"id": item["id"]},
        ExpressionAttributeNames={"#data": "data"},
        ExpressionAttributeValues={":data": [new_data], ":updatedAt": timestamp},
        UpdateExpression="SET #data = list_append(#data, :data), updatedAt = :updatedAt",
        ReturnValues="ALL_NEW",
    )

    return result["Attributes"]
