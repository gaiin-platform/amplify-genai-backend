#!/bin/bash
# Run this script to set env vars and start the local Python agent server
# Usage: source local_run_vars.sh && python3 local_server.py

export AWS_PROFILE=654654422653_VU_PowerUserAccess
export AWS_DEFAULT_REGION=us-east-1
export STAGE=dev

# API / Auth
export API_BASE_URL=https://dev-api.dev-amplify.vanderbilt.ai
export OAUTH_ISSUER_BASE_URL=https://cognito-idp.us-east-1.amazonaws.com/us-east-1_n0ESPoUk4
export OAUTH_AUDIENCE=https://dev-amplify.vanderbilt.ai
export IDP_PREFIX=dev-amplifygenai
export ORGANIZATION_EMAIL_DOMAIN=vanderbilt.edu

# Secrets (fetched from AWS Secrets Manager at runtime)
export LLM_ENDPOINTS_SECRETS_NAME=dev-openai-endpoints
export LLM_ENDPOINTS_SECRETS_NAME_ARN=arn:aws:secretsmanager:us-east-1:654654422653:secret:dev-openai-endpoints-Gv6nFM
export SECRETS_ARN_NAME=dev-amplify-app-secrets
export APP_ARN_NAME=dev-amplify-app-vars

# Logging
export LOG_LEVEL=DEBUG
export SERVICE_NAME=amplify-v6-agent-loop
export DEFAULT_SECRET_PARAMETER_PREFIX=/agent

# Assistants tables (read from SSM in prod, set explicitly for local dev)
export ASSISTANTS_ALIASES_DYNAMODB_TABLE=amplify-v6-assistants-dev-assistant-aliases
export ASSISTANTS_DYNAMODB_TABLE=amplify-v6-assistants-dev-assistants

# Agent Loop Tables/Buckets
export AGENT_STATE_DYNAMODB_TABLE=amplify-v6-agent-loop-dev-agent-state
export AGENT_STATE_BUCKET=amplify-v6-agent-loop-dev-agent-state
export AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE=amplify-v6-agent-loop-dev-agent-event-templates
export WORKFLOW_TEMPLATES_TABLE=amplify-v6-agent-loop-dev-workflow-registry
export WORKFLOW_TEMPLATES_BUCKET=amplify-v6-agent-loop-dev-workflow-templates
export SCHEDULED_TASKS_TABLE=amplify-v6-agent-loop-dev-scheduled-tasks
export SCHEDULED_TASKS_LOGS_BUCKET=amplify-v6-agent-loop-dev-scheduled-tasks-logs
export EMAIL_SETTINGS_DYNAMO_TABLE=amplify-v6-agent-loop-dev-email-allowed-senders
export RAW_EMAILS_BUCKET=amplify-v6-agent-loop-dev-raw-emails

# Lambda shared tables/buckets
export S3_CONSOLIDATION_BUCKET_NAME=amplify-v6-lambda-dev-consolidation
export ACCOUNTS_DYNAMO_TABLE=amplify-v6-lambda-dev-accounts
export CHAT_USAGE_DYNAMO_TABLE=amplify-v6-lambda-dev-chat-usage
export FILES_DYNAMO_TABLE=amplify-v6-lambda-dev-user-files
export USER_TAGS_DYNAMO_TABLE=amplify-v6-lambda-dev-user-tags
export ENV_VARS_TRACKING_TABLE=amplify-v6-lambda-dev-env-vars-tracking
export DB_CONNECTIONS_TABLE=amplify-v6-lambda-dev-db-connections
export S3_RAG_INPUT_BUCKET_NAME=amplify-v6-lambda-dev-rag-input
export S3_IMAGE_INPUT_BUCKET_NAME=amplify-v6-lambda-dev-image-input

# Other service tables
export REQUEST_STATE_DYNAMO_TABLE=amplify-v6-amplify-js-dev-request-state
export API_KEYS_DYNAMODB_TABLE=amplify-v6-object-access-dev-api-keys
export COGNITO_USERS_DYNAMODB_TABLE=amplify-v6-object-access-dev-cognito-users
export OPS_DYNAMODB_TABLE=amplify-v6-lambda-ops-dev-ops
export MODEL_RATE_TABLE=amplify-v6-chat-billing-dev-model-rates
export ADDITIONAL_CHARGES_TABLE=amplify-v6-chat-billing-dev-additional-charges
export COST_CALCULATIONS_DYNAMO_TABLE=amplify-v6-lambda-dev-cost-calculations
export CRITICAL_ERRORS_SQS_QUEUE_NAME=amplify-v6-admin-dev-critical-errors
export AGENT_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/654654422653/amplify-v6-agent-loop-dev-agent-queue

# Notes
export NOTES_ENABLED=true
export NOTES_EMAIL=notes@dev.vanderbilt.ai
export NOTES_INGEST_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/654654422653/amplify-notes-ingest-queue-dev
export S3_NOTES_RAW_FILES_BUCKET=amplify-notes-raw-files-dev

# AI Scheduler
export AI_SCHEDULER_STORAGE_TABLE=ai-scheduler-dev-user-storage

echo "✅ Environment variables set. Now run:"
echo "   python3 local_server.py"
