import os
import boto3

from boto3.dynamodb.types import TypeDeserializer
from common.validate import validated

dynamodb = boto3.client('dynamodb')


@validated(op="get")
def get_ops(event, context, current_user, name, data):
    data = data['data']

    # Get the DynamoDB table name from the environment variable
    table_name = os.environ.get('OPS_DYNAMODB_TABLE')

    # Create a DynamoDB client

    # Get the 'tag' parameter from the request data
    tag = data.get('tag', 'default')

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

    return {
        "success": True,
        "message": "Successfully retrieved available operations for user",
        "data":  data_from_dynamo
    }
