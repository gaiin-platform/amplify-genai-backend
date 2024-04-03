import boto3
import json
import os
from dotenv import load_dotenv

# Configuration
env_file = '.env.dev'
sync_mode = 'SYNC'  # Set to 'PULL' to pull vars from AWS or 'SYNC' to sync local vars to AWS
alphabetize_env = True 

# Load environment variables from the .env file at the specified path
# This will look for the .env file in the project root directory
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env_file)
load_dotenv(dotenv_path)

# Retrieve environment variables
secret_arn = os.environ['LOCAL_VAR_SECRET_ARN']
varFile = dotenv_path

# Function definitions
def get_secrets(secrets_manager_arn):
    client = boto3.client('secretsmanager')
    get_secret_value_response = client.get_secret_value(SecretId=secrets_manager_arn)
    secret_string = get_secret_value_response['SecretString']
    secret_json = json.loads(secret_string)
    return secret_json

def read_env_file(filename):
    env_vars = {}
    if os.path.exists(filename):
        with open(filename, 'r') as env_file:
            for line in env_file:
                if '=' in line:
                    key, value = line.strip().split('=', 1)
                    env_vars[key] = value.strip('"')
    return env_vars

def update_env_file(secrets, current_env, filename, alphabetize=False):
    updated_env = current_env.copy()
    for secret_key, secret_value in secrets.items():
        if secret_key not in current_env or current_env[secret_key] != secret_value:
            updated_env[secret_key] = secret_value
    if alphabetize:
        sorted_keys = sorted(updated_env)
        with open(filename, 'w') as env_file:
            for key in sorted_keys:
                env_file.write(f'{key}="{updated_env[key]}"\n')
    else:
        with open(filename, 'w') as env_file:
            for key, value in updated_env.items():
                env_file.write(f'{key}="{value}"\n')

def update_aws_secret(secrets_manager_arn, new_secrets):
    client = boto3.client('secretsmanager')
    client.update_secret(SecretId=secrets_manager_arn, SecretString=json.dumps(new_secrets))

# Main function
def main(sync_mode, alphabetize_env):
    secrets_manager_arn = secret_arn
    env_filename = varFile
    aws_secrets = get_secrets(secrets_manager_arn)
    local_env = read_env_file(env_filename)

    if sync_mode.upper() == 'PULL':
        update_env_file(aws_secrets, local_env, env_filename, alphabetize=alphabetize_env)
        print("Local .env file has been updated with the latest values from AWS Secrets Manager.")
    elif sync_mode.upper() == 'SYNC':
        new_secrets = {k: v for k, v in local_env.items() if k not in aws_secrets}
        if new_secrets:
            update_aws_secret(secrets_manager_arn, {**aws_secrets, **new_secrets})
            print(f"Updated AWS secret with new local variables: {list(new_secrets.keys())}")
        else:
            print("No new local variables to update in AWS.")

if __name__ == '__main__':
    main(sync_mode, alphabetize_env)