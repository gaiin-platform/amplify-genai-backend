
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import boto3
import json
import logging
import random
from botocore.exceptions import ClientError

def get_endpoint(model_name, endpoint_arn):
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager')

    # Retrieve the secret from Secrets Manager
    try:
        get_secret_value_response = client.get_secret_value(SecretId=endpoint_arn)
        secret = get_secret_value_response['SecretString']
        secret_dict = json.loads(secret)
    except ClientError as e:
        logging.error(f"Error retrieving secret: {e}")
        raise e

    # Parse the secret JSON to find the model
    for model_dict in secret_dict['models']:
        if model_name in model_dict:
            # Select a random endpoint from the model's endpoints
            random_endpoint = random.choice(model_dict[model_name]['endpoints'])
            endpoint = random_endpoint['url']
            api_key = random_endpoint['key']
            return endpoint, api_key
    else:
        raise ValueError(f"Model named '{model_name}' not found in secret")