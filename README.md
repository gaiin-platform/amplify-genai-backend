# amplify-genai-backend

## Overview

This repository serves as a Mono Repo for managing all Amplify Lambda functions.

## Setup Requirements

Initial setup requires the creation of a `/var` directory at the root level of the repository. The environment-specific variables should be placed in the following files within the `/var` directory:

- `dev-var.yml` for Developer environment variables
- `staging-var.yml` for Staging environment variables
- `prod-var.yml` for Production environment variables

### Vars

These variables should be configured inside your `amplify-genai-backend/<environment>/<environment>-var.yml` file:
- DEP_NAME: name of the deployment; must be less than 10 characters and not contain spaces
- COGNITO_USER_POOL_ID:
- COGNITO_CLIENT_ID:
- OAUTH_AUDIENCE: base of application
- OAUTH_ISSUER_BASE_URL: cognito user pool url
- VPC_ID: vpc id of deployment from terraform
- VPC_CIDR: vpc cidr of deployment from terraform
- PRIVATE_SUBNET_ONE: private subnet one id from terraform
- PRIVATE_SUBNET_TWO: private subnet two id from terraform
- OPENAI_API_KEY: secret name from AWS for $env-openai-api-key
- LLM_ENDPOINTS_SECRETS_NAME_ARN: secret ARN from AWS for $env-openai-endopoints
- SECRETS_ARN_NAME: secret ARN from AWS for $env-amplify-app-secrets
- LLM_ENDPOINTS_SECRETS_NAME: secret name from AWS for $env-openai-endopoints
- HOSTED_ZONE_ID: app_route53_zone_id from terraform
- AWS_ACCOUNT_ID:
- RDS_HOSTED_ZONE_ID: 'Z2R2ITUGPM61AM' is us-east-1, use the RDS Hosted Zone ID for your region
- CUSTOM_API_DOMAIN: domain used for API gateway; for example: <environment>-api.<domain>.com
- PANDOC_LAMBDA_LAYER_ARN:
- ORGANIZATION_EMAIL_DOMAIN:
- IDP_PREFIX: should match the value for provider_name in cognito vars of the terraform deployment
- API_VERSION:
- ASSISTANTS_OPENAI_PROVIDER: can be 'azure' or switched to 'openai' if using the OpenAI service APIs
- RAG_ASSISTANT_MODEL_ID:
- QA_MODEL_NAME:
- EMBEDDING_MODEL_NAME:
- MIN_ACU:
- MAX_ACU:

## Deployment Process

### Deploying All Services From the Repository Root

To deploy a service directly from the root of the repository, use the command structure below, replacing `service-name` with your specific service name and `stage` with the appropriate deployment stage ('dev', 'staging', 'prod'):

serverless service-name:deploy --stage <stage>

### Example Deploying a Specific Service

serverless amplify-lambda:deploy --stage dev

## Deploying from the Service Directory

Because we are using serverless-compose to import variables across services, you have to deploy from the root of the repo or you could have issues resolving variables within the application

## Installing Dependencies

1. Navigate to each cloned directory and install the Node.js dependencies:

```bash
cd amplify-genai-backend
npm i
cd ../amplify
npm i
```

## Running `lambda-js` Locally 

To run `lambda-js` with `localServer.js`:

1. Navigate to the `amplify-lambda-js` directory:

```bash
cd amplify-lambda-js/
```

2. Install the dependencies if you haven't already:

```bash
npm i
```

3. Ensure AWS credentials are located in ~/.aws/credentials and AWS_PROFILE env var matches

4. Run the local server from root:

```bash
node amplify-lambda-js/local/localServer.js
```

## Running Lambda with Serverless Offline

### Install Serverless

First, install Serverless globally:

```bash
npm install -g serverless
```

Then, install the necessary Serverless plugins:

```bash
npm install --save-dev @serverless/compose

npm install --save-dev serverless-offline

sls plugin install -n serverless-python-requirements
```

After navigating to the `amplify-lambda` directory, install any additional dependencies:

```bash
npm i
```

### Run Serverless Offline

You can run Serverless offline for `amplify-lambda` using one of the following methods,
where <stage> corresponds to the appropriate deployment stage ('dev', 'staging', 'prod'):

1. Directly from the repository root directory:

```bash
serverless amplify-lambda:offline --httpPort 3015 --stage <stage> 
```

2. By navigating to the `amplify-lambda` directory:

```bash
cd amplify-lambda
serverless offline --httpPort 3015 --stage <stage>
```


## Running Amplify

To have Amplify running and pointed to your local lambda versions, follow these steps:

1. Add the necessary variables to your `.env.local` file:

```
API_BASE_URL=http://localhost:3015
CHAT_ENDPOINT=http://localhost:8000
```

2. Start the development server:

```bash
npm run dev
```

3. Open [http://localhost:3000](http://localhost:3000) in your browser to view the application.



