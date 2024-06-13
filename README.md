# Amplify-Lambda-Mono

## Overview

This repository serves as a Mono Repo for managing all Amplify Lambda functions.

## Setup Requirements

Initial setup requires the creation of a `/var` directory at the root level of the repository. The environment-specific variables should be placed in the following files within the `/var` directory:

- `dev-var.yml` for Developer environment variables
- `staging-var.yml` for Staging environment variable    s
- `prod-var.yml` for Production environment variables

### Vars
- OAUTH_AUDIENCE:
- OAUTH_ISSUER_BASE_URL: 
- MIN_ACU:  
- MAX_ACU: 
- VPC_ID: 
- PRIVATE_SUBNET_ONE: 
- PRIVATE_SUBNET_TWO: 
- VPC_CIDR: 
- AMPLIFY_LAMBDA_SERVICE: 
- SES_SECRET_ARN: 
- OPENAI_API_KEY_ARN: 
- COGNITO_USER_POOL_ID: 
- COGNITO_CLIENT_ID: 
- SECRETS_ARN_NAME: 
- SECRETS_NAME: 
- OPENAI_ENDPOINT: 
- USAGE_ENDPOINT: 
- LLM_ENDPOINTS_SECRETS_NAME_ARN: 
- EMBEDDING_MODEL_NAME: 
- S3_RAG_CHUNKS_BUCKET_NAME: 
- SENDER_EMAIL: 
- API_ID: 
- API_ROOT_RESOURCE_ID:
- RAG_POSTGRES_DB_READ_ENDPOINT:
- RAG_POSTGRES_DB_USERNAME:
- RAG_POSTGRES_DB_NAME:
- RAG_POSTGRES_DB_SECRET:


## Deployment Process

### Deploying All Services From the Repository Root

To deploy a service directly from the root of the repository, use the command structure below, replacing `service-name` with your specific service name and `stage` with the appropriate deployment stage ('dev', 'staging', 'prod'):

serverless service-name:deploy --stage <stage>

### Example Deploying a Specific Service

serverless amplify-lambda:deploy --stage dev

## Deploying from the Service Directory

Because we are using serverless-compose to import variables across services, you have to deploy from the root of the repo or you could have issues resolving variables within the application


