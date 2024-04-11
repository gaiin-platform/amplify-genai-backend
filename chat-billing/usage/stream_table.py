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
    elif isinstance(value, (int, float, Decimal)):
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

                    # Parse the session to find the latest operation
                    # TODO: ensure this parsing is correct
                    session = new_image["details"].get("session", [])
                    if session:
                        latest_operation = session[
                            -1
                        ]  # Assume last operation is the latest
                        operation_type = latest_operation.get("operation")
                        if operation_type == "LIST_MESSAGE":
                            new_image["outputTokens"] = latest_operation.get(
                                "outputTokens", 0
                            )
                            new_image["inputTokens"] = 0
                        elif operation_type == "ADD_MESSAGE":
                            new_image["inputTokens"] = latest_operation.get(
                                "inputTokens", 0
                            )
                            new_image["outputTokens"] = 0
                else:
                    new_image["itemType"] = "other"
            else:
                new_image["itemType"] = "chat"

            # Prepare the item for insertion into the destination table
            item = {k: python_to_dynamodb(v) for k, v in new_image.items()}

            # Insert the item into the destination table
            try:
                dynamodb_client.put_item(TableName=destination_table, Item=item)
                print(
                    f"Inserted item with id: {new_image['id']} into {destination_table}"
                )
            except Exception as e:
                print(f"Error inserting item: {e}")

    return {"statusCode": 200, "body": "Stream processed successfully"}
