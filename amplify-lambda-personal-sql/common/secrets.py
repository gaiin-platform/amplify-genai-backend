import os
import uuid

import boto3
import json

from botocore.exceptions import ClientError

DEFAULT_PREFIX = os.getenv('DEFAULT_SECRET_PARAMETER_PREFIX', '/pdb')


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

    ssm_client = boto3.client('ssm')

    try:
        response = ssm_client.put_parameter(
            Name=full_parameter_name,
            Value=secret_value,
            Type='SecureString',
            Overwrite=True  # Overwrites the parameter if it already exists
        )
        return response
    except ClientError as e:
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
        print(f"An error occurred: {e}")
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


