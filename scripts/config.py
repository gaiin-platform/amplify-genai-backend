CONFIG = {
    "ACCOUNTS_DYNAMO_TABLE": "amplify-v6-lambda-dev-accounts",
    "ADDITIONAL_CHARGES_TABLE": "amplify-v6-chat-billing-dev-additional-charges",
    "AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE": "amplify-v6-agent-loop-dev-agent-event-templates",
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": "amplify-v6-admin-dev-admin-configs",
    "API_KEYS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-api-keys",
    "ARTIFACTS_DYNAMODB_TABLE": "amplify-v6-artifacts-dev-user-artifacts",
    "ASSISTANTS_ALIASES_DYNAMODB_TABLE": "amplify-v6-assistants-dev-assistant-aliases",
    "ASSISTANTS_DYNAMODB_TABLE": "amplify-v6-assistants-dev-assistants",
    "ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE": "amplify-v6-assistants-dev-code-interpreter-assistants",
    "ASSISTANT_GROUPS_DYNAMO_TABLE": "amplify-v6-object-access-dev-amplify-groups",
    "ASSISTANT_LOOKUP_DYNAMODB_TABLE": "amplify-v6-assistants-dev-assistant-lookup",
    "ASSISTANT_THREADS_DYNAMODB_TABLE": "amplify-v6-assistants-dev-assistant-threads",
    "ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE": "amplify-v6-assistants-dev-assistant-thread-runs",
    "CHAT_USAGE_DYNAMO_TABLE": "amplify-v6-lambda-dev-chat-usages",
    "COGNITO_USERS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-cognito-users",
    "CONVERSATION_METADATA_TABLE": "amplify-v6-lambda-dev-conversation-metadata",
    "COST_CALCULATIONS_DYNAMO_TABLE": "amplify-v6-lambda-dev-cost-calculations",
    "DATASOURCE_REGISTRY_DYNAMO_TABLE": "amplify-v6-amplify-js-dev-datasource-registry",
    "DATA_DISCLOSURE_ACCEPTANCE_TABLE": "amplify-v6-data-disclosure-dev-acceptance",
    "DB_CONNECTIONS_TABLE": "amplify-v6-lambda-dev-db-connections",
    "EMAIL_SETTINGS_DYNAMO_TABLE": "amplify-v6-agent-loop-dev-email-allowed-senders",
    "FILES_DYNAMO_TABLE": "amplify-v6-lambda-dev-user-files",
    "GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE": "amplify-v6-assistants-dev-group-assistant-conversations",
    "HASH_FILES_DYNAMO_TABLE": "amplify-v6-lambda-dev-hash-files",
    "HISTORY_COST_CALCULATIONS_DYNAMO_TABLE": "amplify-v6-lambda-dev-history-cost-calculations",
    "OAUTH_STATE_TABLE": "amplify-v6-assistants-api-dev-oauth-state",
    "OAUTH_USER_TABLE": "amplify-v6-assistants-api-dev-user-oauth-integrations",
    "OBJECT_ACCESS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-object-access",
    "OPS_DYNAMODB_TABLE": "amplify-v6-lambda-ops-dev-ops",
    "SCHEDULED_TASKS_TABLE": "amplify-v6-agent-loop-dev-scheduled-tasks",
    "SHARES_DYNAMODB_TABLE": "amplify-v6-lambda-dev",
    "USER_STORAGE_TABLE": "amplify-v6-lambda-basic-ops-dev-user-storage",
    "USER_TAGS_DYNAMO_TABLE": "amplify-v6-lambda-dev-user-tags",
    "WORKFLOW_TEMPLATES_TABLE": "amplify-v6-agent-loop-dev-workflow-registry",
    "AGENT_STATE_DYNAMODB_TABLE": "amplify-v6-agent-loop-dev-agent-state",
    "AMPLIFY_ADMIN_LOGS_DYNAMODB_TABLE": "amplify-v6-admin-dev-admin-logs",
    "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": "amplify-v6-object-access-dev-amplify-group-logs",
    "OP_LOG_DYNAMO_TABLE": "amplify-v6-assistants-api-dev-op-log",
    "REQUEST_STATE_DYNAMO_TABLE": "amplify-v6-amplify-js-dev-request-state",
    "MEMORY_DYNAMO_TABLE": "amplify-v6-memory-dev-memory",
    "needs_edit": {
        "non_user_related_tables": {
            "DATA_DISCLOSURE_VERSIONS_TABLE": "amplify-v6-data-disclosure-dev-versions",
            "MODEL_RATE_TABLE": "amplify-v6-chat-billing-dev-model-rates",
            "DATASOURCE_REGISTRY_DYNAMO_TABLE": "amplify-v6-amplify-js-dev-datasource-registry",
        },
        "consolidated_buckets": {
            "S3_CONVERSATIONS_BUCKET_NAME": "amplify-v6-lambda-dev-user-conversations", #REQUIRES MIGRATION  ***
            "S3_SHARE_BUCKET_NAME": "amplify-v6-lambda-dev-share", # POINTER   ***
            "ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME": "amplify-v6-assistants-dev-code-interpreter-files", # NOT CURRENTLY TRACKED  - RETURNED TO USER IN CHAT RESPONSE BODY AND FORGOTTEN  ***
            "AGENT_STATE_BUCKET": "amplify-v6-agent-loop-dev-agent-state", # POINTER  *** 
            "S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME": "amplify-v6-assistants-dev-group-conversations-content", # POINTER ***
            "S3_CONVERSION_INPUT_BUCKET_NAME": "amplify-v6-lambda-dev-document-conversion-input", # *** 
            "S3_CONVERSION_OUTPUT_BUCKET_NAME": "amplify-v6-lambda-dev-document-conversion-output", # templates/ folder contents must remain unchanged ***
            "S3_ZIP_FILE_BUCKET_NAME": "amplify-v6-lambda-dev-zip-files", # used for uploading individual files only   NOT DONE YET
           
            # non user related buckets
            "DATA_DISCLOSURE_STORAGE_BUCKET": "amplify-v6-data-disclosure-dev-storage", # ***
            # "S3_ACCESS_LOGS_BUCKET_NAME": "amplify-v6-lambda-dev-access-logs",
            "S3_API_DOCUMENTATION_BUCKET": "amplify-v6-api-dev-documentation-bucket", # ***
            
        },
        "to_user_storage_table": {
            "SCHEDULED_TASKS_LOGS_BUCKET": "amplify-v6-agent-loop-dev-scheduled-tasks-logs", # POINTER   *****
            "S3_ARTIFACTS_BUCKET": "amplify-v6-artifacts-dev-bucket", # POINTER    *****
            "WORKFLOW_TEMPLATES_BUCKET": "amplify-v6-agent-loop-dev-workflow-templates", # POINTER    *****
        },
        "skip_possibly": {
            "TRACE_BUCKET_NAME": "amplify-v6-lambda-dev-chat-traces", # not even turned on at this time  Can SKIP
        },
        "cannot_change": {
            "S3_IMAGE_INPUT_BUCKET_NAME": "amplify-v6-lambda-dev-image-input",
            "S3_RAG_INPUT_BUCKET_NAME": "amplify-v6-lambda-dev-rag-input",
            "S3_FILE_TEXT_BUCKET_NAME": "amplify-v6-lambda-dev-file-text",
            "S3_RAG_CHUNKS_BUCKET_NAME": "amplify-v6-lambda-dev-rag-chunks", # double check
        }
    }
}