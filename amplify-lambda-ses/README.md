# Amplify SES Service

This service provides email sending functionality for the Amplify platform using Amazon Simple Email Service (SES). It is a serverless service deployed to AWS using the Serverless Framework, written in Python and utilizing AWS Lambda and SES.

## Overview

The amplify-ses service is configured to run on AWS with Python 3.11 runtime. It defines a Lambda function for sending emails through Amazon SES.

## Requirements

- AWS CLI
- Serverless Framework
- Python 3.11
- Docker (for packaging Python requirements)

## Setup

1. Install the Serverless Framework:
```bash
npm install -g serverless
```

2. Install necessary Node.js modules:
```bash
npm install
```

3. Install the serverless-python-requirements plugin:
```bash
sls plugin install -n serverless-python-requirements
```

## Configuration

The service uses stage variables stored in `../var/${stage}-var.yml`. Ensure this file exists and contains the necessary variables, including:

- REST_API_ID
- REST_API_ROOT_RESOURCE_ID
- OAUTH_AUDIENCE
- OAUTH_ISSUER_BASE_URL

## Deployment

Deploy the service using:

```bash
sls deploy --stage <stage>
```

Replace `<stage>` with the desired stage (dev, staging, or prod).

## Function: send_email

This function sends an email using Amazon SES.

### Endpoint

- **POST** `/ses/send-email`

### Input

The function expects a JSON payload with the following structure:

```json
{
  "data": {
    "email_to": "recipient@example.com",
    "email_subject": "Email Subject",
    "email_body": "Email Body"
  }
}
```

### Output

On success, the function returns the Message ID of the sent email.

## IAM Permissions

The service creates a managed IAM policy named `${service}-${stage}-iam-policy` with the following permissions:

- SES: SendEmail, SendRawEmail
- DynamoDB: Query, Scan, GetItem
- S3: GetObject

These permissions are applied to specific resources as defined in the serverless.yml file.

## Local Development

For local development and testing, use the serverless-offline plugin:

```bash
sls offline
```

## Monitoring

Monitor function logs using:

```bash
sls logs -f send_email -t
```

## Cleanup

To remove the deployed service and its resources:

```bash
sls remove
```

## Additional Resources

- [Serverless Framework Documentation](https://www.serverless.com/framework/docs/)
- [AWS Lambda Developer Guide](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html)
- [Amazon SES Developer Guide](https://docs.aws.amazon.com/ses/latest/dg/Welcome.html)

## Contributing

Contributions are welcome. Please fork the repository and create a pull request with your changes.

## License

This project is licensed under the MIT License.