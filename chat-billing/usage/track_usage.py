import boto3
from boto3.dynamodb.types import TypeDeserializer
from .helpers.track_usage_helper import (
    handle_chat_item,
    handle_code_interpreter_item,
    handle_code_interpreter_session_item,
    handle_other_item_types,
)

# Initialize DynamoDB client
dynamodb_client = boto3.client("dynamodb")
# Initialize DynamoDB resource
dynamodb = boto3.resource("dynamodb")


# Helper function to deserialize DynamoDB stream image to Python dictionary
def deserialize_dynamodb_stream_image(stream_image):
    deserializer = TypeDeserializer()
    return {k: deserializer.deserialize(v) for k, v in stream_image.items()}


def handler(event, context):
    try:
        for record in event["Records"]:
            if record["eventName"] == "INSERT":
                new_image = deserialize_dynamodb_stream_image(
                    record["dynamodb"]["NewImage"]
                )

                identifier = new_image.get("accountId", "UnknownAccountId")
                item_type = new_image.get("itemType", "UnknownItemType")
                user = new_image.get("user", "UnknownUser")
                # print(f"COA String: {identifier}, Item Type: {item_type}, User: {user}")

                account_type = "coa"  # assume coa is passed, we will check if it is not
                if (
                    identifier == "general_account"
                    or identifier == "270.05.27780.XXXX.200.000.000.0.0."
                    # meaning coa has not been provided and we must bill user directly
                ):
                    identifier = new_image.get("user", "UnknownUser")
                    account_type = "user"
                    # print(f"User: {identifier}, Item Type: {item_type}")

                if item_type == "chat":
                    handle_chat_item(
                        dynamodb, new_image, account_type, identifier, user
                    )
                elif item_type == "codeInterpreter":
                    handle_code_interpreter_item(
                        dynamodb, new_image, account_type, identifier, user
                    )
                elif item_type == "codeInterpreterSession":
                    handle_code_interpreter_session_item(
                        dynamodb, new_image, account_type, identifier, user
                    )
                else:
                    handle_other_item_types(
                        dynamodb, new_image, account_type, identifier, user
                    )
    except Exception as e:
        print(f"An error occurred in the handler: {e}")
        raise

    return {"statusCode": 200, "body": "Usage tracked successfully"}
