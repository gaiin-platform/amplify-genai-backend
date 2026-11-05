# amplify-genai-backend

> ## :new: v0.9.5 (New UI release)
>
> Version 0.9.5 supports the Amplify New UI. **Deploy the backend before the frontend.** Before upgrading, read [RELEASE_NOTES_NEW_UI.md](./RELEASE_NOTES_NEW_UI.md). It covers the switch to `osls` (run `npm install` at the repo root and use `npx sls`), the per-stack Parameter Store populators, the new admin settings and feature flags, and the `pycommon` v0.1.2 pin.

> ## :warning: v0.9.0 Breaking Change
>
> **Version 0.9.0 migrates all shared environment variables to AWS Parameter Store.** Existing deployments upgrading from v0.8.x **must** run `populate_parameter_store.py` before deploying.
>
> - See the [Migration Guide](./scripts/MIGRATION_README.md) for required steps.
> - Deploy the backend **before** upgrading the frontend.
> - **If you have the `amplify-lambda-basic-ops` service**, you must handle the `/user-data` endpoint migration before deploying `amplify-lambda`. See the [Migration Guide](./scripts/MIGRATION_README.md) for detailed steps.
> - We recommend testing in your development environment first and backing up production resources (DynamoDB tables, etc.) before deploying to prod.
> - Use the CloudFormation changeset plugin (`CHANGE_SET_BOOLEAN: true` in your var file) to review infrastructure changes before applying.

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

```npx sls service-name:deploy --stage <stage>```

`sls` comes from [osls](https://github.com/oss-serverless/osls), which `npm install` at the repo root installs (see [Install Serverless](#install-serverless) below). Run it with `npx sls` so you use the local copy and not a global one.

### Example Deploying a Specific Service

```npx sls amplify-lambda:deploy --stage dev```

### Deploying All Compose Services

```npx sls deploy --stage <stage>```

## Deploying from the Service Directory

Because we are using serverless-compose to import variables across services, you have to deploy the services listed in `serverless-compose.yml` from the root of the repo or you could have issues resolving variables within the application.

### Standalone Services

`amplify-agent-loop-lambda`, `amplify-lambda-assistants-api`, `amplify-lambda-assistants-api-office365` and `amplify-lambda-assistants-api-google` are not in `serverless-compose.yml`. Deploy each one from its own directory, for example:

```bash
cd amplify-lambda-assistants-api
npx sls deploy --stage <stage>
```

(Not tested by us: these directories have no `package.json`, so `npx` has to find the root `node_modules`. If it cannot find `sls`, run `../node_modules/.bin/sls deploy --stage <stage>`.)

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

This repo uses [osls](https://github.com/oss-serverless/osls) (the open-source Serverless Framework v3 fork, v3 line) instead of the upstream `serverless` package. It is installed locally as an npm alias (`"serverless": "npm:osls@^3.79.0"`), so `npm i` at the repo root provides the `sls` command. Run it with `npx sls ...`, and if you previously installed the upstream framework globally, remove it so it does not shadow the local copy (osls does not auto-delegate to a local install):

```bash
npm uninstall -g serverless
```

The Serverless plugins this repo uses (`@osls/compose`, `serverless-offline`, `serverless-python-requirements` and others) are listed in the root `package.json`, so `npm install` at the repo root installs them. You do not need to install them separately. Use Node.js 20.19 or newer.

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



