import os
import boto3

from boto3.dynamodb.types import TypeDeserializer
from common.validate import validated
from common.ops import op

dynamodb = boto3.client('dynamodb')


@op(
    path="/ops/get",
    tags=["ops", "default"],
    name="getOperations",
    description="Get a list of available operations for an assistant.",
    params={
        "tag": "The optional tag to search for.",
    }
)
@validated(op="get")
def get_ops(event, context, current_user, name, data):
    data = data['data']
    # Get the 'tag' parameter from the request data
    tag = data.get('tag', 'default')
    return fetch_user_ops(current_user, tag)


def fetch_user_ops(current_user, tag):
    # Get the DynamoDB table name from the environment variable
    table_name = os.environ.get('OPS_DYNAMODB_TABLE')

    print(f"Finding operations for user {current_user} with tag {tag}")

    # Build the DynamoDB query parameters
    query_params = {
        'TableName': table_name,
        'KeyConditionExpression': '#usr = :user AND tag = :tag',
        'ExpressionAttributeValues': {
            ':user': {'S': current_user},
            ':tag': {'S': tag}
        },
        'ExpressionAttributeNames': {
            '#usr': 'user'
        }
    }
    # Execute the DynamoDB query
    response = dynamodb.query(**query_params)

    # Extract the data from the DynamoDB response
    data_from_dynamo = [item['ops'] for item in response['Items']]
    data_from_dynamo = [TypeDeserializer().deserialize(item) for item in data_from_dynamo]
    # Flatten the list of operations
    data_from_dynamo = [op for sublist in data_from_dynamo for op in sublist]

    print(f"Found operations {data_from_dynamo} for user {current_user} with tag {tag}")

    if current_user != "system":
        try:
            system_ops = fetch_user_ops("system", tag)
            print(f"System operations: {system_ops}")
            system_ops = system_ops['data']
            data_from_dynamo.extend(system_ops)
        except Exception as e:
            print(f"Failed to retrieve system operations: {e}")

    return {
        "success": True,
        "message": "Successfully retrieved available operations for user",
        "data":  data_from_dynamo
    }
