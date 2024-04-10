import os
import json
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
    elif isinstance(
        value, (int, float, Decimal)
    ):
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

            # Check if 'details' field is not empty and exists
            if "details" in new_image and new_image["details"]:
                if (
                    "itemType" in new_image["details"]
                    and new_image["details"]["itemType"] == "codeInterpreter" # conversation with code interpreter
                ):
                    new_image["itemType"] = "codeInterpreter"
                    # TODO: set values of new_image["inputTokens"] and new_image["outputTokens"]
                    #       need to parse/look in new_image["details"]["session"]
                    #       need to pull the latest operation out of the latest session
                    #       if the operation is LIST_MESSAGE, collect outputTokens
                    #       if the operation is ADD_MESSAGE, collect inputTokens
                
                # other hanlding other itemTypes will be implemented here
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
