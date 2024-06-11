
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
import boto3
from boto3.dynamodb.types import TypeDeserializer
from decimal import Decimal

# Initialize DynamoDB client
dynamodb_client = boto3.client("dynamodb")

# Get the destination table name from the environment variable in serverless.yml
destination_table = os.environ["DESTINATION_TABLE"]


# Helper function to deserialize DynamoDB stream image to Python dictionary
def deserialize_dynamodb_stream_image(stream_image):
    deserializer = TypeDeserializer()
    return {k: deserializer.deserialize(v) for k, v in stream_image.items()}


# Helper function to convert Python types to DynamoDB's AttributeValue
def python_to_dynamodb(value):
    if isinstance(value, str):
        return {"S": value}
    elif isinstance(value, Decimal):  # Convert Decimal to int or float
        if value % 1 == 0:
            return {"N": str(int(value))}
        else:
            return {"N": str(float(value))}
    elif isinstance(value, (int, float)):
        return {"N": str(value)}
    elif isinstance(value, dict):
        return {"M": {k: python_to_dynamodb(v) for k, v in value.items()}}
    elif isinstance(value, list):
        return {"L": [python_to_dynamodb(v) for v in value]}
    elif isinstance(value, bool):
        return {"BOOL": value}
    elif value is None:
        return {"NULL": True}
    else:
        raise TypeError(f"Unsupported Python type: {type(value)}")


def handler(event, context):
    for record in event["Records"]:
        # TODO: ensure that we need to be doing INSERT check and not another kind of check (for code interpreter); might need to be MODIFY
        if record["eventName"] == "INSERT":
            new_image = deserialize_dynamodb_stream_image(
                record["dynamodb"]["NewImage"]
            )

            # ensure the details key exists in the new_image dictionary and that its value is not None or an empty structure
            if "details" in new_image and new_image["details"]:
                if (
                    "itemType" in new_image["details"]
                    and new_image["details"]["itemType"] == "codeInterpreter"
                ):
                    new_image["itemType"] = "codeInterpreter"

                    # Check if sessions is a list or a dictionary with a key 'L'
                    sessions = new_image["details"].get("sessions", [])
                    if isinstance(sessions, dict):
                        sessions = sessions.get("L", [])

                    if sessions:
                        latest_session = sessions[
                            -1
                        ]  # Assume last session is the latest
                        operations = (
                            latest_session.get("M", {})
                            .get("operations", {})
                            .get("L", [])
                        )
                        if operations:
                            latest_operation = operations[
                                -1
                            ]  # Assume last operation is the latest
                            operation_type = (
                                latest_operation.get("M", {}).get("type", {}).get("S")
                            )
                            if operation_type == "LIST_MESSAGE":
                                new_image["outputTokens"] = int(
                                    latest_operation.get("M", {})
                                    .get("outputTokens", {})
                                    .get("N", "0")
                                )
                                new_image["inputTokens"] = 0
                                if isinstance(new_image["inputTokens"], Decimal):
                                    new_image["inputTokens"] = int(
                                        new_image["inputTokens"]
                                    )
                                if isinstance(new_image["outputTokens"], Decimal):
                                    new_image["outputTokens"] = int(
                                        new_image["outputTokens"]
                                    )
                            elif operation_type == "ADD_MESSAGE":
                                new_image["inputTokens"] = int(
                                    latest_operation.get("M", {})
                                    .get("inputTokens", {})
                                    .get("N", "0")
                                )
                                new_image["outputTokens"] = 0
                                if isinstance(new_image["inputTokens"], Decimal):
                                    new_image["inputTokens"] = int(
                                        new_image["inputTokens"]
                                    )
                                if isinstance(new_image["outputTokens"], Decimal):
                                    new_image["outputTokens"] = int(
                                        new_image["outputTokens"]
                                    )
                else:
                    new_image["itemType"] = "other"
            else:
                new_image["itemType"] = "chat"

            # Prepare the item for insertion into the destination table
            item = {k: python_to_dynamodb(v) for k, v in new_image.items()}

            # Insert the item into the destination table
            try:
                dynamodb_client.put_item(TableName=destination_table, Item=item)
                # print(
                #     f"Inserted item with id: {new_image['id']} into {destination_table}"
                # )
                print(f"Inserted item: {new_image} into {destination_table}")
            except Exception as e:
                print(f"Error inserting item: {e}")

    return {"statusCode": 200, "body": "Stream processed successfully"}
