# Configuration variables - UPDATE THESE BEFORE RUNNING MIGRATION SCRIPTS
DEP_NAME = "v6"  # Change this to match your deployment name (e.g., "v6", "v7", etc.)
STAGE = "dev"    # Change this to match your deployment stage (e.g., "dev", "staging", "prod")

def get_config(dep_name: str = DEP_NAME, stage: str = STAGE) -> dict:
    """
    Generate configuration with dynamic deployment name and stage.
    
    Args:
        dep_name: Deployment name (e.g., "v6", "v7")  
        stage: Deployment stage (e.g., "dev", "staging", "prod")
    
    Returns:
        Dictionary containing all table and bucket names with specified dep_name and stage
    """
    return {
        "ACCOUNTS_DYNAMO_TABLE": f"amplify-{dep_name}-lambda-{stage}-accounts",
        "ADDITIONAL_CHARGES_TABLE": f"amplify-{dep_name}-chat-billing-{stage}-additional-charges",
        "AGENT_EVENT_TEMPLATES_DYNAMODB_TABLE": f"amplify-{dep_name}-agent-loop-{stage}-agent-event-templates",
        "AMPLIFY_ADMIN_DYNAMODB_TABLE": f"amplify-{dep_name}-admin-{stage}-admin-configs",
        "API_KEYS_DYNAMODB_TABLE": f"amplify-{dep_name}-object-access-{stage}-api-keys",
        "ARTIFACTS_DYNAMODB_TABLE": f"amplify-{dep_name}-artifacts-{stage}-user-artifacts",
        "ASSISTANTS_ALIASES_DYNAMODB_TABLE": f"amplify-{dep_name}-assistants-{stage}-assistant-aliases",
        "ASSISTANTS_DYNAMODB_TABLE": f"amplify-{dep_name}-assistants-{stage}-assistants",
        "ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE": f"amplify-{dep_name}-assistants-{stage}-code-interpreter-assistants",
        "ASSISTANT_GROUPS_DYNAMO_TABLE": f"amplify-{dep_name}-object-access-{stage}-amplify-groups",
        "ASSISTANT_LOOKUP_DYNAMODB_TABLE": f"amplify-{dep_name}-assistants-{stage}-assistant-lookup",
        "ASSISTANT_THREADS_DYNAMODB_TABLE": f"amplify-{dep_name}-assistants-{stage}-assistant-threads",
        "ASSISTANT_THREAD_RUNS_DYNAMODB_TABLE": f"amplify-{dep_name}-assistants-{stage}-assistant-thread-runs",
        "CHAT_USAGE_DYNAMO_TABLE": f"amplify-{dep_name}-lambda-{stage}-chat-usages",
        "COGNITO_USERS_DYNAMODB_TABLE": f"amplify-{dep_name}-object-access-{stage}-cognito-users",
        "CONVERSATION_METADATA_TABLE": f"amplify-{dep_name}-lambda-{stage}-conversation-metadata",
        "COST_CALCULATIONS_DYNAMO_TABLE": f"amplify-{dep_name}-lambda-{stage}-cost-calculations",
        "DATASOURCE_REGISTRY_DYNAMO_TABLE": f"amplify-{dep_name}-amplify-js-{stage}-datasource-registry",
        "DATA_DISCLOSURE_ACCEPTANCE_TABLE": f"amplify-{dep_name}-data-disclosure-{stage}-acceptance",
        "DB_CONNECTIONS_TABLE": f"amplify-{dep_name}-lambda-{stage}-db-connections",
        "EMAIL_SETTINGS_DYNAMO_TABLE": f"amplify-{dep_name}-agent-loop-{stage}-email-allowed-senders",
        "FILES_DYNAMO_TABLE": f"amplify-{dep_name}-lambda-{stage}-user-files",
        "GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE": f"amplify-{dep_name}-assistants-{stage}-group-assistant-conversations",
        "HASH_FILES_DYNAMO_TABLE": f"amplify-{dep_name}-lambda-{stage}-hash-files",
        "HISTORY_COST_CALCULATIONS_DYNAMO_TABLE": f"amplify-{dep_name}-lambda-{stage}-history-cost-calculations",
        "OAUTH_STATE_TABLE": f"amplify-{dep_name}-assistants-api-{stage}-oauth-state",
        "OAUTH_USER_TABLE": f"amplify-{dep_name}-assistants-api-{stage}-user-oauth-integrations",
        "OBJECT_ACCESS_DYNAMODB_TABLE": f"amplify-{dep_name}-object-access-{stage}-object-access",
        "OPS_DYNAMODB_TABLE": f"amplify-{dep_name}-lambda-ops-{stage}-ops",
        "SCHEDULED_TASKS_TABLE": f"amplify-{dep_name}-agent-loop-{stage}-scheduled-tasks",
        "SHARES_DYNAMODB_TABLE": f"amplify-{dep_name}-lambda-{stage}",
        "USER_STORAGE_TABLE": f"amplify-{dep_name}-lambda-basic-ops-{stage}-user-storage",
        "USER_TAGS_DYNAMO_TABLE": f"amplify-{dep_name}-lambda-{stage}-user-tags",
        "WORKFLOW_TEMPLATES_TABLE": f"amplify-{dep_name}-agent-loop-{stage}-workflow-registry",
        "AGENT_STATE_DYNAMODB_TABLE": f"amplify-{dep_name}-agent-loop-{stage}-agent-state",
        "AMPLIFY_ADMIN_LOGS_DYNAMODB_TABLE": f"amplify-{dep_name}-admin-{stage}-admin-logs",
        "AMPLIFY_GROUP_LOGS_DYNAMODB_TABLE": f"amplify-{dep_name}-object-access-{stage}-amplify-group-logs",
        "OP_LOG_DYNAMO_TABLE": f"amplify-{dep_name}-assistants-api-{stage}-op-log",
        "REQUEST_STATE_DYNAMO_TABLE": f"amplify-{dep_name}-amplify-js-{stage}-request-state",
        "MEMORY_DYNAMO_TABLE": f"amplify-{dep_name}-memory-{stage}-memory",
        "COMMON_DATA_DYNAMO_TABLE": f"amplify-{dep_name}-se-{stage}-ops-common-data",
        "DYNAMO_DYNAMIC_CODE_TABLE": f"amplify-{dep_name}-se-{stage}-ai-code",
        "needs_edit": {
            "non_user_related_tables": {
                "DATA_DISCLOSURE_VERSIONS_TABLE": f"amplify-{dep_name}-data-disclosure-{stage}-versions",
                "MODEL_RATE_TABLE": f"amplify-{dep_name}-chat-billing-{stage}-model-rates",
                "DATASOURCE_REGISTRY_DYNAMO_TABLE": f"amplify-{dep_name}-amplify-js-{stage}-datasource-registry",
            },
            "consolidated_buckets": {
                "S3_CONVERSATIONS_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-user-conversations", #REQUIRES MIGRATION  ***
                "S3_SHARE_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-share", # POINTER   ***
                "ASSISTANTS_CODE_INTERPRETER_FILES_BUCKET_NAME": f"amplify-{dep_name}-assistants-{stage}-code-interpreter-files", # NOT CURRENTLY TRACKED  - RETURNED TO USER IN CHAT RESPONSE BODY AND FORGOTTEN  ***
                "AGENT_STATE_BUCKET": f"amplify-{dep_name}-agent-loop-{stage}-agent-state", # POINTER  *** 
                "S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME": f"amplify-{dep_name}-assistants-{stage}-group-conversations-content", # POINTER ***
                "S3_CONVERSION_INPUT_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-document-conversion-input", # *** 
                "S3_CONVERSION_OUTPUT_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-document-conversion-output", # templates/ folder contents must remain unchanged ***
                "S3_ZIP_FILE_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-zip-files", # used for uploading individual files only   NOT DONE YET
               
                # non user related buckets
                "DATA_DISCLOSURE_STORAGE_BUCKET": f"amplify-{dep_name}-data-disclosure-{stage}-storage", # ***
                # "S3_ACCESS_LOGS_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-access-logs",
                "S3_API_DOCUMENTATION_BUCKET": f"amplify-{dep_name}-api-{stage}-documentation-bucket", # ***
                
            },
            "to_user_storage_table": {
                "SCHEDULED_TASKS_LOGS_BUCKET": f"amplify-{dep_name}-agent-loop-{stage}-scheduled-tasks-logs", # POINTER   *****
                "S3_ARTIFACTS_BUCKET": f"amplify-{dep_name}-artifacts-{stage}-bucket", # POINTER    *****
                "WORKFLOW_TEMPLATES_BUCKET": f"amplify-{dep_name}-agent-loop-{stage}-workflow-templates", # POINTER    *****
            },
            "skip_possibly": {
                "TRACE_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-chat-traces", # not even turned on at this time  Can SKIP
            },
            "cannot_change": {
                "S3_IMAGE_INPUT_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-image-input",
                "S3_RAG_INPUT_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-rag-input",
                "S3_FILE_TEXT_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-file-text",
                "S3_RAG_CHUNKS_BUCKET_NAME": f"amplify-{dep_name}-lambda-{stage}-rag-chunks", # double check
            }
        }
    }

# Backward compatibility - use default values
CONFIG = get_config()