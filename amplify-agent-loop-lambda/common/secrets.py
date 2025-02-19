import random
import os
import uuid

import boto3
import json

from botocore.exceptions import ClientError

DEFAULT_PREFIX = os.getenv('DEFAULT_SECRET_PARAMETER_PREFIX', '/oauth')


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


def store_secret_parameter(parameter_name, secret_value, prefix=DEFAULT_PREFIX):
  """
  Stores a secret in AWS Parameter Store as a SecureString with a specified prefix.

  Parameters:
  parameter_name (str): The name of the parameter to create or update.
  secret_value (str): The secret value to store.
  prefix (str): The prefix for the parameter name.

  Returns:
  dict: The response from the Parameter Store.
  """

  full_parameter_name = f"{prefix}/{parameter_name}"

  print(f"Creating client to store secret parameter '{full_parameter_name}'")

  ssm_client = boto3.client('ssm')

  try:
    print(f"Storing secret parameter '{full_parameter_name}'")

    response = ssm_client.put_parameter(
      Name=full_parameter_name,
      Value=secret_value,
      Type='SecureString',
      Overwrite=True  # Overwrites the parameter if it already exists
    )

    print(f"Stored secret parameter '{full_parameter_name}'")

    return response
  except Exception as e:
    print(f"An error occurred: {e}")
    return None


def get_secret_parameter(parameter_name, prefix=DEFAULT_PREFIX):
  """
  Retrieves and decrypts a secret from AWS Parameter Store with a specified prefix.

  Parameters:
  parameter_name (str): The name of the parameter to retrieve.
  prefix (str): The prefix for the parameter name.

  Returns:
  str: The decrypted secret value.
  """

  full_parameter_name = f"{prefix}/{parameter_name}"

  ssm_client = boto3.client('ssm')

  try:
    response = ssm_client.get_parameter(
      Name=full_parameter_name,
      WithDecryption=True
    )
    return response['Parameter']['Value']
  except ClientError as e:
    print(f"An error occurred fetching parameter {parameter_name}: {e}")
    return None


def update_dict_with_secrets(input_dict):
  """
  Updates the input dictionary by replacing keys that start with 's_xyz' with 'xyz' and their corresponding secret values.

  Parameters:
  input_dict (dict): The input dictionary to update.

  Returns:
  dict: The updated dictionary.
  """

  updated_dict = input_dict.copy()  # Copy the original dictionary to avoid modifying it

  for key in list(updated_dict.keys()):  # Use list to avoid RuntimeError due to dictionary size change during iteration
    if key.startswith("s_"):
      secret_parameter_name = updated_dict[key]
      secret_value = get_secret_parameter(secret_parameter_name)
      if secret_value is not None:
        new_key = key[2:]  # Remove the 's_' prefix
        updated_dict[new_key] = secret_value
        del updated_dict[key]  # Remove the old key

  return updated_dict


def store_secrets_in_dict(input_dict):
  """
  Stores keys that start with 's_' in AWS Parameter Store and replaces their values with the parameter names.

  Parameters:
  input_dict (dict): The input dictionary containing keys to store as secrets.

  Returns:
  dict: The updated dictionary with values replaced by parameter names.
  """

  updated_dict = input_dict.copy()  # Copy the original dictionary to avoid modifying it

  for key in updated_dict.keys():
    if key.startswith("s_"):
      secret_value = updated_dict[key]
      parameter_name = str(uuid.uuid4())  # Generate a unique parameter name
      store_secret_parameter(parameter_name, secret_value)
      updated_parameter_name = parameter_name
      if updated_parameter_name:
        updated_dict[key] = updated_parameter_name  # Replace the value with the parameter name

  return updated_dict


def delete_secrets_in_dict(input_dict):
  """
  Deletes secrets from AWS Parameter Store or AWS Secrets Manager that were stored using `store_secrets_in_dict`.

  Parameters:
      input_dict (dict): The dictionary containing stored secret parameter names.

  Returns:
      dict: A result dictionary indicating which secrets were successfully deleted or failed.
  """
  ssm_client = boto3.client('ssm')
  secrets_manager_client = boto3.client('secretsmanager')

  deletion_results = {"deleted": [], "failed": []}

  for key, secret_name in input_dict.items():
    if key.startswith("s_"):
      try:
        # Attempt to delete from AWS Parameter Store (SSM)
        full_parameter_name = f"{DEFAULT_PREFIX}/{secret_name}"
        ssm_client.delete_parameter(Name=full_parameter_name)
        print(f"Deleted secret parameter: {full_parameter_name}")
        deletion_results["deleted"].append(full_parameter_name)

      except ClientError as e:
        print(f"Failed to delete parameter from SSM: {full_parameter_name} - {e}")
        deletion_results["failed"].append(full_parameter_name)

      try:
        # Attempt to delete from AWS Secrets Manager
        secrets_manager_client.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
        print(f"Deleted secret from Secrets Manager: {secret_name}")
        deletion_results["deleted"].append(secret_name)

      except ClientError as e:
        print(f"Failed to delete secret from Secrets Manager: {secret_name} - {e}")
        deletion_results["failed"].append(secret_name)

  return deletion_results


def get_secret(secret_name):
  client = boto3.client('secretsmanager', region_name='us-east-1')

  try:
    response = client.get_secret_value(SecretId=secret_name)
    if 'SecretString' in response:
      return response['SecretString']
    else:
      return response['SecretBinary'].decode('ascii')
  except Exception as e:
    raise e


def get_endpoint_data(parsed_data, model_name):
  if model_name in ['gpt-4-1106-Preview', 'gpt-4-1106-preview']:
    model_name = 'gpt-4-turbo'
  elif model_name in ['gpt-35-1106', 'gpt-35-1106']:
    model_name = 'gpt-35-turbo'
  elif model_name in ['gpt-4o', 'gpt-4o']:
    model_name = 'gpt-4o'

  endpoint_data = next((model for model in parsed_data['models'] if model_name in model), None)
  if not endpoint_data:
    raise ValueError("Model name not found in the secret data")

  endpoint_info = random.choice(endpoint_data[model_name]['endpoints'])
  return endpoint_info['key'], endpoint_info['url']


def get_llm_config(model_name):
  secret_name = os.environ.get('LLM_ENDPOINTS_SECRETS_NAME')
  secret_data = get_secret(secret_name)
  parsed_secret = json.loads(secret_data)
  return get_endpoint_data(parsed_secret, model_name)

