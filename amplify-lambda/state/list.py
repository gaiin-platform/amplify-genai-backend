# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import json
import os

from pycommon.encoders import dumps_lossy

import boto3

dynamodb = boto3.resource("dynamodb")


def list(event: dict, context: dict) -> dict:
    """
    List all items from the DynamoDB table.

    Args:
        event (dict): The event data passed to the Lambda function.
        context (dict): The runtime information of the Lambda function.

    Returns:
        dict: A response containing the status code and the list of items from the table.
    """
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

    # fetch all todos from the database
    result = table.scan()

    # create a response
    response = {
        "statusCode": 200,
        "body": dumps_lossy(result["Items"]),
    }

    return response
