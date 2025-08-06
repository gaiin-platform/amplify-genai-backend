# Integration CLI

This Python script is a command-line utility for storing and managing integration configurations as secrets in AWS Systems Manager Parameter Store (SSM).

## Usage

### Storing Integration Configuration

To store the configuration for a specific integration and stage, use the following command:

```bash
python integration_cli.py store --stage <stage> --integration <integration> --config <client_configuration_json>
```

- `--stage`: The stage of the integration.
- `--integration`: The integration name.
- `--config`: The client configuration JSON.

### Listing Integrations

To list all the integrations that have been added, use the following command:

```bash
python integration_cli.py list
```

- `--details`: (Optional) Show details of each integration and the stages configured.

### Listing Integration Details

To list the details of a particular integration and its stages, use the following command:

```bash
python integration_cli.py details <integration> --details
```

- `<integration>`: The name of the integration.
- `--details`: (Optional) Show details of each stage for the integration.

## Requirements

- Python 3.6 or higher
- `boto3` library
- AWS IAM credentials with permissions to access SSM Parameter Store