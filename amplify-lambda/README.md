# Amplify Support Service

This repository contains the serverless deployment configuration for "amplify-support" service using the Serverless Framework.

## Overview

The amplify-support service is configured to run on AWS with Python 3.11 runtime. It defines several Lambda functions for managing assistants and threads, user states and sharing, including functionalities for uploading files, creating and deleting assistants, handling threads, and sharing user states.

## Requirements

- AWS CLI
- Serverless Framework
- Python 3.11
- Docker (for packaging Python requirements)

## Install Serverless Framework

```bash
npm install -g serverless
```

## Setup

Before deployment, make sure to install the necessary Node.js modules:

```bash
npm install
```

For packaging Python dependencies with Docker (especially important if you are not running Linux), run:

```bash
sls plugin install -n serverless-python-requirements
```

## Deployment

To deploy the service, use:

```bash
sls deploy
```

This will deploy to the `dev` stage by default. You can specify a different stage by using:

```bash
sls deploy --stage prod
```

## Environment variables

The following environment variables are used:

- `DYNAMODB_TABLE`: The base DynamoDB table name for storing data.
- `ASSISTANTS_DYNAMODB_TABLE`: Stores the assistants' data.
- `S3_BUCKET_NAME`: The S3 bucket for shared files.
- `S3_ASSISTANT_FILES_BUCKET_NAME`: The S3 bucket for assistant files.
- `AUTH0_AUDIENCE`: The Auth0 audience for authentication.
- `AUTH0_ISSUER_BASE_URL`: The base URL of the Auth0 issuer for authentication.

## IAM Configuration

The IAM role assumed by the functions has permissions for accessing secrets in Secrets Manager, performing CRUD operations on DynamoDB, and handling files in S3.

Ensure that the correct permissions are set up as per the `serverless.yml` configuration.

## Functions

The service includes the following endpoints:

----

## Resources

Resources for S3 and DynamoDB are defined within the `serverless.yml` configuration file and are provisioned during deployment.

## Plugins

The following Serverless Framework plugins are used:

- `serverless-offline` for local testing.
- `serverless-python-requirements` for handling Python dependencies.

## Custom Configurations

Python requirements are dockerized to ensure compatibility across operating systems, using the following configuration:

```yaml
custom:
  pythonRequirements:
    dockerizePip: true
```

For any additional information or configuration details, refer to the `serverless.yml` file provided in the repository.
```

## Local Development

For local development and testing, you can use the `serverless-offline` plugin which emulates AWS λ and API Gateway on your local machine to speed up your development cycles.

To start the server locally, run:

```bash
sls offline start
```

## Testing

To test the deployed functions, you can use the AWS Lambda Console or any API testing tools like Postman or cURL to send requests to the endpoints mentioned in the Functions section above.

Here's an example using cURL to test the `create` endpoint:

```bash
curl -X POST https://your-api-endpoint/dev/state \
  --header "Content-Type: application/json" \
  --data '{"key": "value", "another_key": "another_value"}'
```

Replace `https://your-api-endpoint/dev/state` with the actual deployed endpoint URL.

## Monitoring

After deploying your service, you can monitor function logs using the `serverless logs` command:

```bash
sls logs -f functionName -t
```

Where `-f` is used to specify the function name and `-t` enables tailing the logs.

## Cleanup

To remove the deployed service and all its resources from AWS, run:

```bash
sls remove
```

## Additional Resources

Refer to the following documentation for additional information:

- [Serverless Framework AWS Guide](https://www.serverless.com/framework/docs/providers/aws/)
- [AWS CLI User Guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-welcome.html)
- [boto3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- [AWS Lambda Developer Guide](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html)
- [AWS DynamoDB Developer Guide](https://docs.aws.amazon.com/dynamodb/latest/developerguide/Introduction.html)
- [AWS S3 Developer Guide](https://docs.aws.amazon.com/AmazonS3/latest/dev/Welcome.html)
- [Auth0 Documentation](https://auth0.com/docs)

## Contributing

If you'd like to contribute to the project, please fork the repository and use a feature branch. Pull requests are warmly welcome.

## Licensing

"The code in this project is licensed under MIT license - see the LICENSE file for details."