from cryptography.fernet import Fernet
import os
import boto3
from datetime import datetime
import json
import base64


def create_and_store_fernet_key():
    # Read the parameter name from environment variable
    parameter_name = get_oauth_param_env_var()

    # Generate a new Fernet key
    key = Fernet.generate_key()
    key_str = key.decode()  # Converting byte key to string

    # Initialize the SSM client
    ssm_client = boto3.client("ssm")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    ssm_parameter_name = parameter_name
    # Store the key in the SSM Parameter Store
    try:
        response = ssm_client.put_parameter(
            Name=ssm_parameter_name,
            Value=key_str,
            Type="SecureString",
            Overwrite=False,  # Allow overwriting the main parameter
        )
        print(f"Fernet key successfully stored in {ssm_parameter_name}")

        # Create a backup parameter name with a timestamp
        backup_parameter_name = f"{parameter_name}_backup_{timestamp}"
        print(f"Creating backup parameter {backup_parameter_name}")

        backup_response = ssm_client.put_parameter(
            Name=backup_parameter_name,
            Value=key_str,
            Type="SecureString",
            Overwrite=False,  # Do not allow overwriting the backup parameter
        )
        print(f"Backup Fernet key successfully stored in {backup_parameter_name}")

        return response, backup_response

    except Exception as e:
        print(f"Error storing the Fernet key: {str(e)}")
        raise


def parameter_exists(parameter_name):
    ssm_client = boto3.client("ssm")
    try:
        ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
        return True
    except ssm_client.exceptions.ParameterNotFound:
        return False
    except Exception as e:
        raise RuntimeError(f"Error checking parameter existence: {str(e)}")


def verify_oauth_encryption_parameter():
    parameter_name = get_oauth_param_env_var()
    # Check if the parameter exists
    if not parameter_exists(parameter_name):
        print(f"Parameter {parameter_name} does not exist. Creating it now.")
        create_and_store_fernet_key()


def get_oauth_param_env_var():
    parameter_name = os.getenv("OAUTH_ENCRYPTION_PARAMETER")
    if not parameter_name:
        raise ValueError(
            "The environment variable OAUTH_ENCRYPTION_PARAMETER is not set"
        )
    return parameter_name


def encrypt_oauth_data(data):
    ssm_client = boto3.client("ssm")
    parameter_name = get_oauth_param_env_var()

    try:

        # Fetch the parameter securely, which holds the encryption key
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
        # The key needs to be a URL-safe base64-encoded 32-byte key
        key = response["Parameter"]["Value"].encode()
        # Ensure the key is in the correct format for Fernet
        fernet = Fernet(key)

        data_str = json.dumps(data)
        # Encrypt the data
        encrypted_data = fernet.encrypt(data_str.encode())
        encrypted_data_b64 = base64.b64encode(encrypted_data).decode("utf-8")

        return encrypted_data_b64

    except Exception as e:
        print(
            f"Error during parameter retrieval or encryption for {parameter_name}: {str(e)}"
        )
        return None


def decrypt_oauth_data(data):
    ssm_client = boto3.client("ssm")
    parameter_name = os.getenv("OAUTH_ENCRYPTION_PARAMETER")
    print("Parameter name being accessed:", parameter_name)
    if not parameter_name:
        raise ValueError(
            "The environment variable OAUTH_ENCRYPTION_PARAMETER is not set"
        )

    try:
        # Check if the parameter exists
        if not parameter_exists(parameter_name):
            return None

        # Fetch the parameter securely, which holds the encryption key
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
        key = response["Parameter"]["Value"].encode()
        fernet = Fernet(key)

        # Base64-decode the input data, then decrypt using Fernet
        encrypted_data = base64.b64decode(data)
        decrypted_data = fernet.decrypt(encrypted_data)

        # Decode the bytes to string and parse the JSON to reconstruct the original object
        decrypted_str = decrypted_data.decode("utf-8")
        oauth_data = json.loads(decrypted_str)
        return oauth_data

    except Exception as e:
        print(
            f"Error during parameter retrieval or decryption for {parameter_name}: {str(e)}"
        )
        return None
