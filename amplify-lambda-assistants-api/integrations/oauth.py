import json
import os
import time

from google_auth_oauthlib.flow import Flow

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


@validated("start_oauth")
def start_auth(event, context, current_user, name, data):

    integration = data['data']['integration']
    print(f"Starting OAuth flow for integration: {integration}")

    flow = get_oauth_client_for_integration(integration)

    print("Obtained client.")
    print("Creating client redirect url...")
    authorization_url, state = flow.authorization_url(prompt='consent')

    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['OAUTH_STATE_TABLE'])

    try:
        table.put_item(
            Item={
                'state': state,
                'integration': integration,
                'user': current_user,
                'timestamp': int(time.time())
            }
        )
    except ClientError as e:
        print(f"Error storing state in DynamoDB: {e}")
        raise

    return {
        'statusCode': 302,
        'headers': {
            'Location': authorization_url
        },
        'body': {
            'Location': authorization_url
        }
    }

def get_oauth_token_parameter_for_user(service, current_user):
    safe_user = current_user.replace("@", "__at__")
    token_param = f"{service}/{safe_user}"
    return token_param


@validated("list_integrations")
def list_integrations(event, context, current_user, name, data):
    integrations = data['data']['integrations']
    with_token = list_user_integrations(integrations, current_user)
    return {
        "success": True,
        "data": with_token
    }

def list_user_integrations(integrations, current_user):
    ssm = boto3.client('ssm')
    safe_user = current_user.replace("@", "__at__")
    user_integrations = []

    for integration in integrations:
        parameter_name = f"/oauth/{integration}/{safe_user}"
        try:
            ssm.get_parameter(Name=parameter_name, WithDecryption=True)
            user_integrations.append(integration)
        except ssm.exceptions.ParameterNotFound:
            continue
        except Exception as e:
            print(f"Error checking integration {integration}: {str(e)}")

    return user_integrations


@validated("list_integrations")
def list_all_integrations(event, context, current_user, name, data):
    return {
        "success": True,
        "data": list_available_integrations()
    }


def list_available_integrations():
    ssm = boto3.client('ssm')
    stage = os.environ.get('INTEGRATION_STAGE')

    if not stage:
        raise ValueError("INTEGRATION_STAGE environment variable is not set")

    available_integrations = []

    try:
        response = ssm.get_parameters_by_path(
            Path=f"/oauth/integrations/",
            Recursive=True,
            WithDecryption=True
        )

        for param in response['Parameters']:
            integration_name = param['Name'].split('/')[-2]
            if integration_name not in available_integrations:
                available_integrations.append(integration_name)

    except Exception as e:
        print(f"Error retrieving available integrations: {str(e)}")

    return available_integrations


def auth_callback(event, context):

    state = event['queryStringParameters']['state']
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['OAUTH_STATE_TABLE'])

    try:
        response = table.get_item(Key={'state': state})
        item = response.get('Item')
        if item:
            current_user = item['user']
            integration = item['integration']
        else:
            raise ValueError("Invalid OAuth callback.")
    except ClientError as e:
        print(f"Error retrieving state from DynamoDB: {e}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'text/html'},
            'body': '''
                <html>
                <head>
                    <title>Authentication Error</title>
                    <style>
                        body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f0f0f0; }
                        .container { text-align: center; padding: 2rem; background-color: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
                        h1 { color: #e74c3c; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Authentication Error</h1>
                        <p>An error occurred while processing your request.</p>
                    </div>
                </body>
                </html>
            '''
        }

    print("Current user:", current_user)
    print("Integration:", integration)

    flow = get_oauth_client_for_integration(integration)
    flow.fetch_token(code=event['queryStringParameters']['code'])
    credentials = flow.credentials

    print("State found:", state is not None)
    print("Credentials found:", credentials is not None)

    if state is None or credentials is None:
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'text/html'},
            'body': '''
                <html>
                <head>
                    <title>Authentication Failed</title>
                    <style>
                        body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f0f0f0; }
                        .container { text-align: center; padding: 2rem; background-color: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
                        h1 { color: #e74c3c; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Authentication Failed</h1>
                        <p>Invalid OAuth callback, missing parameters.</p>
                    </div>
                </body>
                </html>
            '''
        }

    # replace @ in username with __at__
    token_param = get_oauth_token_parameter_for_user(integration, current_user)

    print("Storing token in Parameter Store")

    try:
        store_secret_parameter(token_param, credentials.to_json(), "/oauth")
    except ClientError as e:
        print(f"Error storing token in Parameter Store: {e}")

    print(f"Credentials stored in Parameter Store {token_param}")

    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'text/html'},
        'body': '''
        <html>
        <head>
            <title>Authentication Successful</title>
            <style>
                body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f0f0f0; }
                .container { text-align: center; padding: 2rem; background-color: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
                h1 { color: #2ecc71; }
                .close-button { margin-top: 1rem; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Authentication Successful</h1>
                <p>You can now close this window and return to the application.</p>
                <button class="close-button" onclick="window.close()">Close</button>
            </div>
        </body>
        </html>
    '''
    }


def delete_integration(current_user, integration):
    ssm = boto3.client('ssm')
    safe_user = current_user.replace("@", "__at__")
    parameter_name = f"/oauth/{integration}/{safe_user}"

    try:
        ssm.delete_parameter(Name=parameter_name)
        print(f"Successfully deleted credentials for user {current_user} and integration {integration}")
        return True
    except ssm.exceptions.ParameterNotFound:
        print(f"No credentials found for user {current_user} and integration {integration}")
        return False
    except ClientError as e:
        print(f"Error deleting credentials: {str(e)}")
        return False


@validated("delete_integration")
def handle_delete_integration(event, context, current_user, name, data):
    integration = data['data']['integration']
    success = delete_integration(current_user, integration)
    return {
        "success": success,
        "message": f"Integration {integration} {'deleted' if success else 'not found'} for user {current_user}"
    }