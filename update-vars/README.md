# README.md for AWS Secrets Manager Sync Function

## Overview

This Python script provides a utility to synchronize environment variables between a local `.env` file and AWS Secrets Manager. It supports two modes of operation:

- `PULL`: Retrieves the latest variables from AWS Secrets Manager and updates the local `.env` file.
- `SYNC`: Identifies local environment variables that are not present in AWS Secrets Manager and updates the AWS secret with these new variables.

The script also has the option to alphabetize the `.env` file, making it easier to manage and review.

## Prerequisites

- Python 3.x installed on your system.
- Boto3 library installed (`pip install boto3`).
- `python-dotenv` library installed (`pip install python-dotenv`).
- AWS CLI installed and configured with the necessary permissions to read and write to AWS Secrets Manager.
- An existing `.env` file in the correct format (`KEY=VALUE` without quotes, unless necessary).

## Configuration

Before running the script, configure the following variables at the top of the script:

- `env_file`: The filename of your local `.env` file. The default is set to `.env.dev`.
- `sync_mode`: The mode of operation. Set to `'PULL'` to pull variables from AWS or `'SYNC'` to sync local variables to AWS.
- `alphabetize_env`: Set to `True` to alphabetize the `.env` file, or `False` to leave it as is.

## Usage

1. Ensure your `.env` file is correctly named and placed in the project root directory, or update the `env_file` variable with the correct relative path.

2. Set the desired `sync_mode` and `alphabetize_env` in the script.

3. Define the `LOCAL_VAR_SECRET_ARN` variable in your `.env` file with the ARN of your AWS secret:

   ```
   LOCAL_VAR_SECRET_ARN=arn:aws:secretsmanager:us-east-1:123456789012:secret:mySecret
   ```

4. Run the script with Python:

   ```bash
   python path/to/script.py
   ```

The script will either pull the latest variables from AWS Secrets Manager or sync local variables to AWS based on the `sync_mode` you've set.

## Output

The script will print messages indicating the actions being taken and their results, such as whether the `.env` file was updated or if new local variables were added to AWS Secrets Manager.

## Caution

This script modifies sensitive data either in AWS Secrets Manager or the local `.env` file. It is recommended to back up your AWS secrets and `.env` files before running this script to avoid accidental data loss. Ensure that your AWS credentials are secured and have the appropriate permissions to access AWS Secrets Manager.

## License

This script is provided "as is", without warranty of any kind. Use it at your own risk.