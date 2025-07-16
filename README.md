# amplify-genai-backend

## Overview

This repository serves as a Mono Repo for managing all Amplify Lambda functions. It is part of a larger deployment for Amplify GenAI which can be found at https://github.com/gaiin-platform.

## Setup Requirements

Initial setup requires the creation of a `/var` directory at the root level of the repository. The environment-specific variables should be placed in the following files within the `/var` directory:

- `dev-var.yml` for Developer environment variables
- `staging-var.yml` for Staging environment variables
- `prod-var.yml` for Production environment variables

### Vars

Variables should be configured inside your `amplify-genai-backend/<environment>/<environment>-var.yml` file. Comments are provided in `dev-var.yml-example` for each variable.

## Deployment Process


### Deploying All Services From the Repository Root

To deploy a service directly from the root of the repository, use the command structure below, replacing `service-name` with your specific service name and `stage` with the appropriate deployment stage ('dev', 'staging', 'prod'):

```serverless service-name:deploy --stage <stage>```

### Example Deploying a Specific Service

```serverless amplify-lambda:deploy --stage dev```

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

Serverless offline does not support Serverless Compose. Because of this limitation, the only way to run serverless offline is to 


1. By navigating to the `amplify-lambda` directory:

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



