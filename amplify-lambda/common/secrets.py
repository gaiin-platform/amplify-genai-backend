
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import boto3
import json

def get_secret_value(secret_name):
  # Create a Secrets Manager client
  client = boto3.client('secretsmanager')

  try:
    # Retrieve the secret value
    response = client.get_secret_value(SecretId=secret_name)
    secret_value = response['SecretString']
    return secret_value

  except Exception as e:
    raise ValueError(f"Failed to retrieve secret '{secret_name}': {str(e)}")
