import boto3
import json

def get_secret_value(secret_name):
  client = boto3.client('secretsmanager')

  try:
    response = client.get_secret_value(SecretId=secret_name)
    secret_value = response['SecretString']
    return secret_value

  except Exception as e:
    raise ValueError(f"Failed to retrieve secret '{secret_name}': {str(e)}")
