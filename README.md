```
# Amplify-Lambda-Mono

## Overview

This repository serves as a Mono Repo for managing all Amplify Lambda functions.

## Setup Requirements

Initial setup requires the creation of a `/var` directory at the root level of the repository. The environment-specific variables should be placed in the following files within the `/var` directory:

- `dev-var.yml` for Developer environment variables
- `staging-var.yml` for Staging environment variables
- `prod-var.yml` for Production environment variables

## Deployment Process

### Deploying From the Repository Root

To deploy a service directly from the root of the repository, use the command structure below, replacing `service-name` with your specific service name and `stage` with the appropriate deployment stage ('dev', 'staging', 'prod'):

serverless service-name:deploy --stage <stage>

### Example deploying from this repository specifically

serverless amplify-lambda:deploy --stage dev

## Deploying from the Service Directory

If you need to deploy from within a service’s directory, first navigate to that directory, then use the serverless deploy command as shown:
cd service-name
serverless deploy --stage <stage>
Make sure to replace service-name with the actual service’s directory name and <stage> with the targeted deployment stage. ```
