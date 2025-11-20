import os
import boto3
from botocore.exceptions import ClientError


# Initialize DynamoDB client
dynamodb_client = boto3.client("dynamodb")
requests_table = os.environ.get("REQUEST_STATE_DYNAMO_TABLE")


def request_killed(user, request_id):
    """
    Check if a request should be killed based on its state in DynamoDB.

    Args:
        user (str): The user identifier
        request_id (str): The unique request identifier

    Returns:
        bool: True if the request should be killed, False otherwise
    """
    if not requests_table:
        print(
            "REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables."
        )
        raise ValueError(
            "REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables."
        )

    try:
        print("Checking requests table for killswitch state.")
        response = dynamodb_client.get_item(
            TableName=requests_table,
            Key={"user": {"S": user}, "requestId": {"S": request_id}},
        )

        if "Item" not in response:
            print("Request state not found, assuming was killed/deleted in chat js")
            return True

        killswitch = response["Item"]["exit"]["BOOL"]

        print(f"Killswitch state is {'kill' if killswitch else 'continue'}.")

        return killswitch

    except ClientError as e:
        print(f"Error checking killswitch state: {e}")
        return False
