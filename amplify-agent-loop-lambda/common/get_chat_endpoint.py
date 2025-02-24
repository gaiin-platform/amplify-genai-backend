import json
import os
from botocore.exceptions import ClientError
import boto3


def get_chat_endpoint():
    secret_name = os.environ['APP_ARN_NAME']
    region_name = os.environ.get('AWS_REGION', 'us-east-1')
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )
    try:
        print("Retrieving Chat Endpoint")
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
        secret_string = get_secret_value_response['SecretString']
        secret_dict = json.loads(secret_string)
        if ("CHAT_ENDPOINT" in secret_dict):
            return secret_dict["CHAT_ENDPOINT"]
        print("Chat Endpoint Not Found")
    except ClientError as e:
        print(f"Error getting secret: {e}")
    raise ValueError("Couldnt retrieve 'CHAT_ENDPOINT' from secrets manager.")
