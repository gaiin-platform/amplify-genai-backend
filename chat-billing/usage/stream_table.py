import os
import json
import boto3
from boto3.dynamodb.types import TypeDeserializer

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
    elif isinstance(value, int) or isinstance(value, float):
        return {"N": str(value)}
    elif isinstance(value, dict):
        return {"M": {k: python_to_dynamodb(v) for k, v in value.items()}}
    elif isinstance(value, list):
        return {"L": [python_to_dynamodb(v) for v in value]}
    elif value is None:
        return {"NULL": True}
    else:
        raise TypeError(
            "Unsupported Python type for conversion to DynamoDB AttributeValue"
        )


def handler(event, context):
    for record in event["Records"]:
        if record["eventName"] == "INSERT":
            new_image = deserialize_dynamodb_stream_image(
                record["dynamodb"]["NewImage"]
            )

            # Add the 'itemType' attribute
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
