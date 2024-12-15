import json
import os
import time

from google_auth_oauthlib.flow import Flow

from common.ops import vop
from common.secrets import store_secret_parameter
from common.validate import validated
import boto3
from botocore.exceptions import ClientError


# Define a custom error for missing credentials
class MissingCredentialsError(Exception):
    pass

def get_user_credentials(current_user, integration):
    ssm = boto3.client('ssm')
    safe_user = current_user.replace("@", "__at__")
    parameter_name = f"/oauth/{integration}/{safe_user}"

    print(f"Retrieving credentials for user {current_user} and integration {integration}")
    print(f"Parameter name: {parameter_name}")

    try:
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        credentials_json = response['Parameter']['Value']
        return json.loads(credentials_json)
    except ssm.exceptions.ParameterNotFound:
        raise MissingCredentialsError(f"No credentials found for user {current_user} and integration {integration}")
    except Exception as e:
        print(f"Error retrieving credentials: {str(e)}")
        raise e


def get_oauth_client_for_integration(integration):
    stage = os.environ.get('INTEGRATION_STAGE')
    if not stage:
        raise ValueError("INTEGRATION_STAGE environment variable is not set")

    ssm = boto3.client('ssm')
    parameter_name = f"/oauth/integrations/{integration}/{stage}"

    try:
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        config = json.loads(response['Parameter']['Value'])
        client_config = config['client_config']
        scopes = config['scopes']

        print(f"Retrieved client config for integration '{integration}' in stage '{stage}' with scopes: {scopes}")

    except ssm.exceptions.ParameterNotFound:
        raise ValueError(f"No configuration found for integration '{integration}' in stage '{stage}'")
    except KeyError:
        raise ValueError(f"Invalid configuration format for integration '{integration}' in stage '{stage}'")

    flow = Flow.from_client_config(client_config, scopes=scopes)

    redirect_uris = client_config.get('web', {}).get('redirect_uris', [])
    if len(redirect_uris) == 1:
        flow.redirect_uri = redirect_uris[0]

    return flow


def get_oauth_client_credentials(integration):
    flow = get_oauth_client_for_integration(integration)
    client_id = flow.client_config['web']['client_id']
    client_secret = flow.client_config['web']['client_secret']
    return client_id, client_secret
