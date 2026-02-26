# amplify-genai-backend

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

All services are orchestrated via [Serverless Compose](https://www.serverless.com/framework/docs/guides/compose) (`serverless-compose.yml`) and share a single API Gateway.

## Serverless Services

The backend is composed of the following services, each deployed as an independent Serverless Framework stack. Cross-service references (DynamoDB table names, SQS queue URLs, etc.) are shared through AWS SSM Parameter Store and CloudFormation exports.

| # | Service Directory | Service Name | Runtime | Description |
|---|---|---|---|---|
| 1 | `amplify-lambda` | amplify-lambda | Python 3.11 | **Core Platform** — API Gateway custom domain, file management, conversations, sharing, document conversion, user accounts, tags, and user data routing. Creates foundational S3 buckets (file-text, image-input, rag-input, rag-chunks, consolidation) and DynamoDB tables (accounts, files, chat-usage, hash-files, cost-calculations, user-tags, user-storage, env-vars-tracking, conversation-metadata, db-connections). |
| 2 | `amplify-lambda-js` | amplify-js | Node.js 22.x | **Chat & Streaming** — LLM chat streaming via API Gateway and Lambda Function URLs, Bedrock model invocation, billing reset (daily cron), month-to-date cost reporting, billing group costs, user cost history, and conversation analysis (SQS-driven). Creates cost-calculation tables, request-state table, datasource-registry table, and chat-traces S3 bucket. |
| 3 | `amplify-assistants` | amplify-assistants | Python 3.11 | **Assistants Management** — CRUD operations for AI assistants, assistant sharing, thread/run management, code interpreter sessions, assistant aliases, lookup tables, and group assistant conversations. |
| 4 | `amplify-lambda-admin` | amplify-admin | Python 3.11 | **Admin Panel** — Admin configuration management, feature flags, user app configs, PowerPoint template management, admin authentication, Amplify group membership, and critical error tracking (DynamoDB Streams → SNS notifications, SQS processing with DLQ). Runs a scheduled sync of assistant admins every 3 minutes. |
| 5 | `amplify-lambda-api` | amplify-api | Python 3.11 | **API Key Management** — API key creation, rotation, deactivation, per-assistant key retrieval, system ID lookup, API documentation templates, and tools/ops registration. |
| 6 | `amplify-lambda-artifacts` | amplify-artifacts | Python 3.11 | **User Artifacts** — Save, retrieve, delete, and share user-generated artifacts (code, documents, etc.) stored in DynamoDB. |
| 7 | `amplify-lambda-ops` | amplify-lambda-ops | Python 3.11 | **Operations Registry** — Register, retrieve, update, and delete custom operations/tools. Stores ops in a DynamoDB table keyed by user and tag. |
| 8 | `amplify-lambda-assistants-api` | amplify-assistants-api | Python 3.11 | **Assistants API & Integrations Hub** — OAuth integration management (start auth, callbacks, token refresh, secret registration), third-party drive file listing/upload/download, custom automation execution, job result management, and MCP server endpoints. Creates OAuth state, user-oauth-integrations, op-log, and job-status tables. |
| 9 | `amplify-lambda-assistants-api-google` | amplify-google | Python 3.11 | **Google Integration** — Google Workspace integration router, Google-specific OAuth flows, and admin config ops trigger (via DynamoDB Streams on the admin table). |
| 10 | `amplify-lambda-assistants-api-office365` | amplify-office365 | Python 3.11 | **Microsoft 365 Integration** — Microsoft/Office 365 integration router, Microsoft-specific OAuth flows, and admin config ops trigger (via DynamoDB Streams on the admin table). |
| 11 | `chat-billing` | amplify-chat-billing | Python 3.11 | **Billing & Model Management** — Available models retrieval, supported/default model management, model rate tables, and additional charges tracking (code interpreter, embeddings, infrastructure costs). |
| 12 | `object-access` | amplify-object-access | Python 3.11 | **Access Control & Groups** — Cognito user directory, Amplify group management (create, update members/types/permissions, delete), object-level permission management, user validation, access simulation, and per-function IAM roles. Creates API keys, cognito-users, object-access, amplify-groups, and group-logs tables. |
| 13 | `data-disclosure` | amplify-data-disclosure | Python 3.11 | **Data Disclosure** — Data disclosure versioning, user acceptance/denial tracking, disclosure document upload (presigned URLs), and latest disclosure retrieval. |
| 14 | `embedding` | amplify-embedding | Python 3.11 | **RAG Embedding Pipeline** — Dual-retrieval embedding generation, embedding chunk processing (SQS → Aurora Serverless PostgreSQL with pgvector), embedding deletion, status tracking, DLQ reprocessing, and table creation. Provisions an Aurora Serverless v2 PostgreSQL cluster with KMS encryption for vector storage. |
| 15 | `amplify-agent-loop-lambda` | amplify-agent-loop | Python 3.11 | **Agent Execution Loop** — Agent routing (REST proxy), asynchronous agent event processing (SQS), scheduled tasks execution (every 3 minutes), built-in tools endpoint, and workflow template management. Creates agent-state, agent-event-templates, email-settings, scheduled-tasks, and workflow-registry tables. |

### Service Dependency Graph

Services share resources through SSM Parameter Store. Below are the key dependency relationships:

- **amplify-lambda** (Core) — foundational service; exports table names and bucket names consumed by most other services.
- **amplify-lambda-admin** — exports the admin config table stream ARN (consumed by Google & Office365 integration services) and the critical errors SQS queue/SNS topic (consumed by all services for error reporting).
- **amplify-lambda-js** — exports cost-calculations and request-state table names; depends on amplify-lambda, amplify-admin, object-access, and chat-billing.
- **object-access** — exports API keys, cognito-users, assistant-groups, and object-access table names consumed by nearly all services.
- **chat-billing** — exports model-rate and additional-charges table names consumed by multiple services.
- **amplify-assistants** — exports assistant table names consumed by amplify-lambda-js, embedding, and agent-loop.
- **amplify-lambda-ops** — exports the ops table name consumed by chat-billing, embedding, agent-loop, and integration services.
- **embedding** — exports the embedding chunks index queue; consumes from amplify-lambda's RAG chunk document queue.

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



