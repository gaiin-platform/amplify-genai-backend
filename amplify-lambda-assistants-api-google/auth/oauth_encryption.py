from cryptography.fernet import Fernet
import os
import boto3
import json
import base64

from pycommon.logger import getLogger
logger = getLogger("google_oauth_encryption")

def decrypt_oauth_data(data):
    ssm_client = boto3.client("ssm")
    parameter_name = os.getenv("OAUTH_ENCRYPTION_PARAMETER")
    logger.debug("Parameter name being accessed: %s", parameter_name)
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
        logger.error("Error during parameter retrieval or decryption for %s: %s", parameter_name, str(e))
        return None
