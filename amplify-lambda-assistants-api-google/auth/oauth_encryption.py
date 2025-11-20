from cryptography.fernet import Fernet
import os
import boto3
import json
import base64


def decrypt_oauth_data(data):
    ssm_client = boto3.client("ssm")
    parameter_name = os.getenv("OAUTH_ENCRYPTION_PARAMETER")
    print("Parameter name being accessed:", parameter_name)
    if not parameter_name:
        raise ValueError(
            "The environment variable OAUTH_ENCRYPTION_PARAMETER is not set"
        )

    try:

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
