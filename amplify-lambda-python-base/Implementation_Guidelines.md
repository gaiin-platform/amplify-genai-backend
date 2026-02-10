# Lambda Implementation Guidelines - PyCommon Decorators & Usage Tracking

This document provides comprehensive guidelines for implementing Lambda functions with proper validation, environment variable management, and usage tracking across the Amplify platform.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Getting Started with Python Sample Base](#getting-started-with-python-sample-base)
3. [Required Imports](#required-imports)
4. [Lambda Implementation Patterns](#lambda-implementation-patterns)
   - [📱 Pattern 1: Standard REST API Endpoints](#-pattern-1-standard-rest-api-endpoints)
   - [🔀 Pattern 2: Router/Proxy Pattern (Agent Loop)](#-pattern-2-routerproxy-pattern-agent-loop)
   - [⚡ Pattern 3: Event-Driven Functions](#-pattern-3-event-driven-functions)
5. [Environment Variable Management](#environment-variable-management)
6. [Usage Tracking and Metrics](#usage-tracking-and-metrics)
7. [Schema and Validation System (Pattern 1 Only)](#schema-and-validation-system-pattern-1-only)
8. [Permission System (Pattern 1 Only)](#permission-system-pattern-1-only)
9. [Complete Examples](#complete-examples)
10. [Best Practices](#best-practices)
11. [Quick Reference](#quick-reference)

---

## System Overview

The PyCommon library provides three core decorators for Lambda functions:

1. **`@required_env_vars`** - Declares environment variables and AWS operations
2. **`@validated`** - Validates request schema and checks permissions (Pattern 1)
3. **`@track_execution`** - Tracks Lambda execution metrics for cost calculation (Pattern 3)

### When to Use Each Decorator

| Pattern | Decorators | Use Case |
|---------|-----------|----------|
| **Standard REST API** | `@required_env_vars` + `@validated` | Direct HTTP endpoints with validation |
| **Router/Proxy** | `@validated("route", False)` + manual tracking | Single endpoint routing to many operations |
| **Event-Driven** | `@required_env_vars` + `@track_execution` | SQS, DynamoDB Streams, S3, Scheduled |

---

## Getting Started with Python Sample Base

### 🚀 Recommended Starting Point for New Services

**Location:** `/amplify-lambda-python-base`

The Python sample base is the **recommended starting point** for creating new Lambda services in the Amplify platform. It contains all the essential IAM roles, environment variables, and boilerplate code needed for proper integration with the PyCommon library.

### Why Start with the Sample Base?

1. **✅ IAM Roles Pre-configured**: Contains all required IAM policies mapped to environment variables
2. **✅ Environment Variables**: Includes all standard env vars (authentication, cost tracking, etc.)
3. **✅ Proper Decorator Usage**: Shows correct `@required_env_vars` and `@validated` patterns
4. **✅ Schema System**: Includes working examples of `schemata/` folder structure
5. **✅ Permission System**: Contains example permission checkers
6. **✅ DynamoDB Table**: Includes sample table with 800-171 compliance settings
7. **✅ 800-171 Compliance**: Pre-configured with encryption, PITR, log retention

### What's Included

**serverless.yml:**
- Managed IAM policy with all required permissions
- Standard environment variables (OAuth, API keys, cost calculations, etc.)
- API Gateway integration
- DynamoDB table with encryption and PITR enabled
- CloudFormation changesets for safe deployments

**service/core.py:**
- Proper `@required_env_vars` usage declaring DynamoDB operations
- Correct `@validated` decorator pattern
- Logger setup
- Complete function signature with all injected parameters

**schemata/ folder:**
- `schema_validation_rules.py` - Central schema registration
- `permissions.py` - Permission checker functions
- `sample_schema.py` - Example schema with documentation

### How IAM Roles Map to Environment Variables

The sample base demonstrates the critical relationship between environment variables and IAM permissions:

**Every environment variable used = IAM permission needed**

```yaml
# Environment variable declaration
environment:
  SAMPLE_DYNAMODB_TABLE: ${self:service}-${sls:stage}-sample-table

# Corresponding IAM permission
Statement:
  - Effect: Allow
    Action:
      - dynamodb:GetItem
      - dynamodb:PutItem
    Resource:
      - "arn:aws:dynamodb:${aws:region}:*:table/${self:provider.environment.SAMPLE_DYNAMODB_TABLE}"
```

**The `@required_env_vars` decorator makes this explicit:**

```python
@required_env_vars({
    "SAMPLE_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
```

This declares:
1. ✅ What environment variables the function needs
2. ✅ What AWS operations will be performed
3. ✅ What IAM permissions are required

### Standard Environment Variables (Always Needed)

The sample base includes these essential variables that **every service must have**:

```yaml
# Authentication & Authorization
OAUTH_AUDIENCE: ${ssm:/amplify/${sls:stage}/shared/OAUTH_AUDIENCE}
OAUTH_ISSUER_BASE_URL: ${ssm:/amplify/${sls:stage}/shared/OAUTH_ISSUER_BASE_URL}
IDP_PREFIX: ${ssm:/amplify/${sls:stage}/shared/IDP_PREFIX}

# User & Account Management
ACCOUNTS_DYNAMO_TABLE: ${ssm:${self:custom.ssmBasePath}-lambda/ACCOUNTS_DYNAMO_TABLE}
API_KEYS_DYNAMODB_TABLE: ${ssm:${self:custom.ssmBasePath}-object-access/API_KEYS_DYNAMODB_TABLE}
COGNITO_USERS_DYNAMODB_TABLE: ${ssm:${self:custom.ssmBasePath}-object-access/COGNITO_USERS_DYNAMODB_TABLE}

# Cost Tracking & Rate Limiting
COST_CALCULATIONS_DYNAMO_TABLE: ${ssm:${self:custom.ssmBasePath}-amplify-js/COST_CALCULATIONS_DYNAMO_TABLE}

# Environment Variable Tracking (for @required_env_vars)
ENV_VARS_TRACKING_TABLE: ${ssm:${self:custom.ssmBasePath}-lambda/ENV_VARS_TRACKING_TABLE}

# Error Handling
CRITICAL_ERRORS_SQS_QUEUE_NAME: ${ssm:${self:custom.ssmBasePath}-admin/CRITICAL_ERRORS_SQS_QUEUE_NAME}

# Service Metadata
SERVICE_NAME: ${self:service}
STAGE: ${sls:stage}
API_BASE_URL: https://${ssm:/amplify/${sls:stage}/shared/CUSTOM_API_DOMAIN}
```

### Standard IAM Permissions (Always Needed)

The sample base includes IAM policies for these standard operations:

1. **DynamoDB Access** (Authentication & Cost Tracking):
   ```yaml
   - dynamodb:Query
   - dynamodb:GetItem
   - dynamodb:PutItem
   - dynamodb:UpdateItem
   ```
   For tables: `API_KEYS_DYNAMODB_TABLE`, `ACCOUNTS_DYNAMO_TABLE`, `COST_CALCULATIONS_DYNAMO_TABLE`, `COGNITO_USERS_DYNAMODB_TABLE`

2. **Environment Variable Tracking**:
   ```yaml
   - dynamodb:GetItem
   - dynamodb:PutItem
   - dynamodb:UpdateItem
   - dynamodb:Query
   ```
   For table: `ENV_VARS_TRACKING_TABLE`

3. **Parameter Store Access**:
   ```yaml
   - ssm:GetParameter
   - ssm:GetParameters
   - ssm:GetParametersByPath
   ```
   For path: `${self:custom.ssmBasePath}-*`

4. **Error Reporting**:
   ```yaml
   - sqs:SendMessage
   ```
   For queue: `CRITICAL_ERRORS_SQS_QUEUE_NAME`

### How to Use the Sample Base for New Services

1. **Copy the entire directory:**
   ```bash
   cp -r amplify-lambda-python-base amplify-lambda-your-new-service
   cd amplify-lambda-your-new-service
   ```

2. **Update service name in serverless.yml:**
   ```yaml
   service: amplify-${self:custom.depName}-your-new-service
   ```

3. **Add your service-specific resources:**
   - Add environment variables for your tables/buckets/queues
   - Add corresponding IAM permissions
   - Update `@required_env_vars` decorators to match

4. **Create your schemas:**
   - Add schema files to `schemata/` folder
   - Register them in `schema_validation_rules.py`
   - Define permissions in `permissions.py`

5. **Implement your functions:**
   - Follow Pattern 1, 2, or 3 as appropriate
   - Always use `@required_env_vars` for AWS resources
   - Use `@validated` for HTTP endpoints (Pattern 1)
   - Use `@track_execution` for event-driven functions (Pattern 3)

### Key Principle: Environment Variables ↔ IAM Permissions

**The sample base demonstrates this critical rule:**

```
Every environment variable → Requires IAM permission
Every @required_env_vars declaration → Documents IAM needs
```

**Example:**
```python
# Function declares it needs S3 access
@required_env_vars({
    "MY_BUCKET": [S3Operation.GET_OBJECT, S3Operation.PUT_OBJECT],
})
```

**serverless.yml must include:**
```yaml
# Environment variable
environment:
  MY_BUCKET: my-bucket-name

# IAM permission
Statement:
  - Effect: Allow
    Action:
      - s3:GetObject
      - s3:PutObject
    Resource:
      - "arn:aws:s3:::${self:provider.environment.MY_BUCKET}/*"
```

This ensures:
- ✅ Security teams can review exact permissions needed
- ✅ Functions document their AWS dependencies
- ✅ IAM policies match actual usage
- ✅ Usage tracking records all AWS operations

### Next Steps

After setting up from the sample base, continue with the patterns below based on your use case:
- 📱 **Pattern 1** for standard REST endpoints
- 🔀 **Pattern 2** for router/proxy endpoints
- ⚡ **Pattern 3** for event-driven functions

---

## Required Imports

### Core PyCommon Imports (All Patterns)

```python
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    DynamoDBOperation,
    S3Operation,
    SQSOperation,
    SecretsManagerOperation,
    BedrockOperation,
    SSMOperation,
    LambdaOperation,
    CognitoOperation
)
from pycommon.logger import getLogger
```

### Pattern 1 & 2: Validation Imports

```python
from pycommon.authz import validated, setup_validated, add_api_access_types
from pycommon.const import APIAccessType
```

### Pattern 1 Only: Schema and Permissions

```python
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
```

### Pattern 2 Only: API Tool

```python
from pycommon.api.ops import api_tool, set_route_data
```

### Pattern 3 Only: Execution Tracking

```python
from pycommon.decorators import track_execution
```

---

## Lambda Implementation Patterns

### 📱 Pattern 1: Standard REST API Endpoints

---

#### **Use Case:** Direct HTTP endpoints with request validation

**When to use:**
- ✅ Individual HTTP API Gateway endpoint
- ✅ Request body validation required
- ✅ Permission checks required
- ✅ Direct 1:1 mapping between endpoint and function

---

**Characteristics:**
- HTTP API Gateway triggers
- Uses `schemata/` folder for schemas and permissions
- `@validated` automatically handles: authentication, validation, permissions, AND usage tracking
- Do NOT use `@track_execution` (redundant)

**Setup (Once Per Module):**

```python
from pycommon.authz import validated, setup_validated
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

# Initialize validation system (once at module level)
setup_validated(rules, get_permission_checker)
```

**Function Implementation:**

```python
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, S3Operation

@required_env_vars({
    "ACCOUNTS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
    "S3_SHARE_BUCKET_NAME": [S3Operation.PUT_OBJECT, S3Operation.GET_OBJECT],
})
@validated("create_charge")
def charge_request(event, context, user, name, data):
    """
    Standard REST endpoint with validation.

    The @validated decorator:
    - Validates request against schema (from schemata/)
    - Checks user permissions (from schemata/)
    - Automatically tracks usage metrics
    - Injects: user, name, data parameters
    """
    account_id = data["data"]["accountId"]
    charge = data["data"]["charge"]

    # Environment variables are automatically resolved
    table_name = os.environ["ACCOUNTS_DYNAMO_TABLE"]

    # Your business logic here
    return create_charge(account_id, charge, user)
```

**Function Signature:**
```python
def your_function(event, context, user, name, data):
    # event: Original Lambda event
    # context: Lambda context
    # user: Authenticated username (from token/API key)
    # name: User's display name
    # data: Validated request body
```

**Schema and Permissions:**

See [Schema and Validation System](#schema-and-validation-system-pattern-1-only) and [Permission System](#permission-system-pattern-1-only) sections below for details on:
- Creating schemas in `schemata/` folder
- Registering schemas in `schema_validation_rules.py`
- Defining permissions in `permissions.py`

**Key Points:**
- ✅ `@validated` handles authentication, validation, permissions, AND usage tracking
- ✅ Schemas and permissions defined in `schemata/` folder
- ✅ Use `@required_env_vars` to declare AWS resources
- ✅ Do NOT add `@track_execution` (redundant - already tracked by `@validated`)

---

### 🔀 Pattern 2: Router/Proxy Pattern (Agent Loop)

---

#### **Use Case:** Single endpoint that routes to multiple operations

**When to use:**
- ✅ Single HTTP endpoint routes to many different operations
- ✅ Dynamic operation routing based on path (`/vu-agent/create-workflow`, `/vu-agent/list-tasks`, etc.)
- ✅ Agent loop or complex routing logic
- ✅ Operations defined with `@api_tool` decorator (NOT `schemata/` folder)

---

**Characteristics:**
- Uses `@validated("route", False)` with `validate_body=False` (authentication only)
- Routes defined with `@api_tool` decorator (contains path, schema, description)
- Nested operations handle their own validation
- Manual usage tracking in `common_handler`
- Does NOT use `schemata/` folder

**Setup (Once Per Module):**

```python
from pycommon.authz import validated, setup_validated
from pycommon.api.ops import api_tool, set_route_data
from service.routes import route_data
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

# Set route data BEFORE importing handlers
set_route_data(route_data)

# Import handlers (they will register with route_data)
from service.handlers import *
from service.workflow_handlers import *

# Initialize validation system
setup_validated(rules, get_permission_checker)
```

**Define Operations with `@api_tool`:**

```python
from pycommon.api.ops import api_tool
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation

@required_env_vars({
    "WORKFLOW_TEMPLATES_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@api_tool(
    path="/vu-agent/create-workflow",
    tags=["workflows", "default"],
    name="createWorkflow",
    description="Create a new workflow template.",
    parameters={
        "type": "object",
        "properties": {
            "workflowName": {"type": "string", "description": "Name of the workflow"},
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Workflow steps"
            }
        },
        "required": ["workflowName", "steps"]
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "workflowId": {"type": "string"}
        }
    }
)
def create_workflow_handler(current_user, workflow_name, steps):
    """
    Handler function for creating workflows.

    Note: Schema is defined in @api_tool decorator above.
    This is registered in route_data automatically.
    """
    # Your business logic
    workflow_id = save_workflow(current_user, workflow_name, steps)
    return {"success": True, "workflowId": workflow_id}
```

**Router Function with Manual Tracking:**

```python
def common_handler(operation, func_schema, **optional_params):
    """
    Wrapper that handles validation and tracking for routed operations.
    """
    def handler(event, context, current_user, name, data):
        from pycommon.metrics import get_usage_tracker
        tracker = get_usage_tracker()
        op_tracking_context = {}

        try:
            # Validate data against schema (from @api_tool)
            validate(data, wrapper_schema)

            # Start tracking the operation
            op_tracking_context = tracker.start_tracking(
                user=current_user,
                operation=operation.__name__,
                endpoint='agent_operation',
                api_accessed=data.get("api_accessed", False),
                context=context,
            )

            # Execute the operation
            result = operation(current_user, **args, **optional_params)

            # End tracking
            success = result.get("success", True) if isinstance(result, dict) else True
            metrics = tracker.end_tracking(
                tracking_context=op_tracking_context,
                result={"statusCode": 200 if success else 500},
                claims={"account": data.get("account", "unknown"), "username": current_user},
                error_type=None if success else "OperationFailure",
            )
            tracker.record_metrics(metrics)

            return result

        except Exception as e:
            # Track error if tracking was started
            if op_tracking_context:
                metrics = tracker.end_tracking(
                    tracking_context=op_tracking_context,
                    result={"statusCode": 500},
                    claims={"account": data.get("account", "unknown"), "username": current_user},
                    error_type=type(e).__name__,
                )
                tracker.record_metrics(metrics)
            return {"success": False, "error": str(e)}

    return handler


@validated("route", False)  # validate_body=False skips automatic tracking
def route(event, context, current_user, name, data):
    """
    Router endpoint that dispatches to specific handlers.

    @validated with False:
    - Handles authentication only
    - Does NOT validate schema (done by common_handler using @api_tool schema)
    - Does NOT track usage (done by common_handler)
    """
    target_path = event.get("path", "")
    route_info = route_data.get(target_path)

    if not route_info:
        return {"success": False, "error": "Invalid path"}

    handler_func = route_info["handler"]
    func_schema = route_info["parameters"]  # From @api_tool

    # Delegate to common_handler which does validation + tracking
    return common_handler(handler_func, func_schema)(
        event, context, current_user, name, data
    )
```

**Key Differences from Pattern 1:**
- ❌ Does NOT use `schemata/` folder for schemas
- ✅ Uses `@api_tool` decorator to define routes and schemas
- ✅ Schemas are part of the `@api_tool` decorator's `parameters` field
- ✅ Manual tracking in `common_handler` using `start_tracking`/`end_tracking`
- ✅ `@validated("route", False)` only authenticates, doesn't validate or track

---

### ⚡ Pattern 3: Event-Driven Functions

---

#### **Use Case:** SQS, DynamoDB Streams, S3 triggers, Scheduled events

**When to use:**
- ✅ SQS queue trigger
- ✅ DynamoDB Stream trigger
- ✅ S3 event trigger
- ✅ CloudWatch Events (scheduled/cron)
- ✅ SNS trigger
- ✅ NO HTTP API Gateway involved

---

**Characteristics:**
- No HTTP API Gateway
- No authentication/validation needed
- Must manually track usage with `@track_execution`
- Event source automatically detected (SQS, DynamoDB Stream, S3, Scheduled)

**Implementation Examples:**

#### Example 1: 📬 SQS Trigger

```python
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.logger import getLogger

logger = getLogger("sqs_processor")

@required_env_vars({
    "USAGE_METRICS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
    "FILES_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@track_execution
def process_document_for_rag(event, context):
    """
    SQS-triggered function.

    @track_execution:
    - Automatically detects SQS event source
    - Tracks execution time and memory
    - Calculates Lambda costs
    - Records metrics to USAGE_METRICS_DYNAMO_TABLE
    """
    logger.info("Processing %d SQS messages", len(event["Records"]))

    for record in event["Records"]:
        body = json.loads(record["body"])
        process_message(body)

    return {"statusCode": 200}
```

#### Example 2: 💾 DynamoDB Stream Trigger

```python
@required_env_vars({
    "USAGE_METRICS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@track_execution
def notify_critical_error(event, context):
    """
    DynamoDB Stream-triggered function.

    @track_execution automatically:
    - Detects DynamoDB Stream event source
    - Tracks execution metrics
    - Records to USAGE_METRICS_DYNAMO_TABLE
    """
    for record in event.get("Records", []):
        if record.get("eventName") == "INSERT":
            new_image = record["dynamodb"]["NewImage"]
            send_notification(new_image)

    return {"statusCode": 200}
```

#### Example 3: ⏰ Scheduled Event (Cron)

```python
@required_env_vars({
    "USAGE_METRICS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
    "SCHEDULED_TASKS_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.UPDATE_ITEM],
})
@track_execution
def execute_scheduled_tasks(event, context):
    """
    CloudWatch Events scheduled trigger (every 3 minutes).

    @track_execution automatically:
    - Detects Scheduled Event source
    - Tracks execution metrics
    - Records to USAGE_METRICS_DYNAMO_TABLE
    """
    tasks = get_pending_tasks()

    for task in tasks:
        execute_task(task)

    return {"statusCode": 200}
```

#### Example 4: 🪣 S3 Trigger

```python
@required_env_vars({
    "USAGE_METRICS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@track_execution
def handle_pptx_upload(event, context):
    """
    S3-triggered function.

    @track_execution automatically:
    - Detects S3 event source
    - Tracks execution metrics
    - Records to USAGE_METRICS_DYNAMO_TABLE
    """
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        process_upload(bucket, key)

    return {"statusCode": 200}
```

**Key Points:**
- ✅ Always use `@track_execution` for event-driven functions
- ✅ Always include `"USAGE_METRICS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM]`
- ✅ Event source is automatically detected
- ✅ No authentication/validation needed (not HTTP endpoints)
- ✅ Do NOT use `@validated` decorator

---

## Environment Variable Management

### The `@required_env_vars` Decorator

The `@required_env_vars` decorator serves three critical purposes:

1. **🔄 Automatic Resolution**: Lambda env vars → Parameter Store → Error
2. **📊 Usage Tracking**: Records all environment variable usage to DynamoDB
3. **📝 IAM Documentation**: Documents precise AWS operations needed for security reviews

### Available AWS Operations

```python
# DynamoDB Operations
DynamoDBOperation.GET_ITEM
DynamoDBOperation.PUT_ITEM
DynamoDBOperation.UPDATE_ITEM
DynamoDBOperation.DELETE_ITEM
DynamoDBOperation.QUERY
DynamoDBOperation.SCAN
DynamoDBOperation.BATCH_GET_ITEM
DynamoDBOperation.BATCH_WRITE_ITEM

# S3 Operations
S3Operation.GET_OBJECT
S3Operation.PUT_OBJECT
S3Operation.DELETE_OBJECT
S3Operation.LIST_BUCKET
S3Operation.HEAD_OBJECT

# SQS Operations
SQSOperation.SEND_MESSAGE
SQSOperation.RECEIVE_MESSAGE
SQSOperation.DELETE_MESSAGE
SQSOperation.GET_QUEUE_ATTRIBUTES
SQSOperation.CHANGE_MESSAGE_VISIBILITY

# Secrets Manager Operations
SecretsManagerOperation.GET_SECRET_VALUE
SecretsManagerOperation.PUT_SECRET_VALUE

# Bedrock Operations
BedrockOperation.INVOKE_MODEL
BedrockOperation.INVOKE_MODEL_WITH_RESPONSE_STREAM
BedrockOperation.INVOKE_GUARDRAIL

# SSM Parameter Store Operations
SSMOperation.GET_PARAMETER
SSMOperation.PUT_PARAMETER
SSMOperation.DELETE_PARAMETER

# Lambda Operations
LambdaOperation.INVOKE_FUNCTION
LambdaOperation.GET_FUNCTION

# Cognito Operations
CognitoOperation.ADMIN_GET_USER
CognitoOperation.ADMIN_CREATE_USER
CognitoOperation.ADMIN_UPDATE_USER
```

### Usage Examples

```python
# Simple: Single operation per resource
@required_env_vars({
    "ACCOUNTS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
})

# Multiple operations on same resource
@required_env_vars({
    "FILES_DYNAMO_TABLE": [
        DynamoDBOperation.GET_ITEM,
        DynamoDBOperation.PUT_ITEM,
        DynamoDBOperation.QUERY
    ],
})

# Multiple resources
@required_env_vars({
    "ACCOUNTS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
    "S3_SHARE_BUCKET_NAME": [S3Operation.PUT_OBJECT, S3Operation.GET_OBJECT],
    "ASSISTANT_QUEUE_URL": [SQSOperation.SEND_MESSAGE],
    "LLM_ENDPOINTS_SECRETS_NAME": [SecretsManagerOperation.GET_SECRET_VALUE],
})

# Configuration-only (no AWS operations)
@required_env_vars({
    "API_BASE_URL": [],  # URL configuration, no AWS API calls
    "STAGE": [],         # Environment name
})
```

### Resolution Chain

1. **Lambda Environment Variables** (serverless.yml) - **Fastest**
   ```yaml
   environment:
     ACCOUNTS_DYNAMO_TABLE: my-service-dev-accounts
   ```

2. **AWS Parameter Store** (fallback) - **Automatic**
   - Path: `/amplify/{stage}/{service_name}/{var_name}`
   - Example: `/amplify/dev/amplify-lambda/ACCOUNTS_DYNAMO_TABLE`

3. **Error** if not found in either location

---

## Usage Tracking and Metrics

### Metrics Collected

All Lambda executions are tracked with:

- ⏱️ **Execution Time** (milliseconds)
- 💾 **Memory Used** (MB)
- 📦 **Memory Allocated** (MB)
- 💰 **Lambda Cost** (calculated using AWS pricing)
- 👤 **User/Account** (from authentication)
- 🔑 **API Key** (if API key authentication)
- 🎯 **Event Source** (HTTP, SQS, DynamoDB Stream, S3, Scheduled)
- 🔗 **Endpoint/Operation**
- ✅ **Success/Failure**
- ❌ **Error Type** (if failed)

### Cost Calculation

```python
# Formula used by @track_execution
cost = (memory_gb * duration_seconds * $0.0000166667)

# Example:
# Memory: 1024 MB = 1 GB
# Duration: 500ms = 0.5 seconds
# Cost: 1 * 0.5 * 0.0000166667 = $0.0000083334
```

### Metrics Storage

**DynamoDB Table:** `USAGE_METRICS_DYNAMO_TABLE`

```python
{
    "account": "user@example.com",           # Partition key
    "timestamp": "2024-01-15T10:30:00Z",     # Sort key
    "user": "user@example.com",
    "api_key_id": "amp-xyz123",              # If API key used
    "operation": "create_charge",
    "endpoint": "/state/accounts/charge",
    "event_source": "HTTP",
    "duration_ms": 245,
    "memory_used_mb": 128,
    "memory_allocated_mb": 1024,
    "lambda_cost": 0.000005,
    "success": true,
    "error_type": null,
    "ttl": 1738243200                        # Auto-expire old records
}
```

### Automatic vs Manual Tracking

| Pattern | Tracking Method | Notes |
|---------|----------------|-------|
| Pattern 1 (REST API) | ✅ Automatic via `@validated` | No extra code needed |
| Pattern 2 (Router) | 🔧 Manual via `common_handler` | Uses `start_tracking`/`end_tracking` |
| Pattern 3 (Event-Driven) | ✅ Automatic via `@track_execution` | No extra code needed |

---

## Schema and Validation System (Pattern 1 Only)

> **⚠️ Note:** This section applies **ONLY to Pattern 1** (Standard REST API). Pattern 2 uses `@api_tool` for schemas.

### File Structure

```
service/
├── schemata/
│   ├── __init__.py
│   ├── schema_validation_rules.py    # Central registration
│   ├── permissions.py                # Permission checks
│   ├── add_charge_schema.py         # Individual schemas
│   ├── create_user_schema.py
│   └── ...
```

### 1. Define Schema

**File: `schemata/add_charge_schema.py`**

```python
add_charge_schema = {
    "type": "object",
    "properties": {
        "accountId": {"type": "string"},
        "charge": {"type": "number"},
        "description": {"type": "string"},
        "details": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "reference": {"type": "string"}
            }
        },
    },
    "required": ["accountId", "charge", "description"],
}
```

### 2. Register Schema

**File: `schemata/schema_validation_rules.py`**

```python
from .add_charge_schema import add_charge_schema
from .create_user_schema import create_user_schema

rules = {
    "validators": {
        # OAuth token-based access
        "/state/accounts/charge": {
            "create_charge": add_charge_schema
        },
        "/users/create": {
            "create_user": create_user_schema
        },
    },
    "api_validators": {
        # API key-based access (can have different schemas)
        "/state/accounts/charge": {
            "create_charge": add_charge_schema
        },
    },
}
```

**Key Points:**
- Path must match API Gateway endpoint exactly
- Operation name must match `@validated("operation_name")`
- `validators`: OAuth token authentication
- `api_validators`: API key authentication

### 3. How @validated Connects Everything

```python
# Your Lambda function
@validated("create_charge")
def charge_request(event, context, user, name, data):
    # ...

# Maps to schema in schema_validation_rules.py
rules = {
    "validators": {
        "/state/accounts/charge": {"create_charge": add_charge_schema}
        # Path from event ^        ^ Operation name    ^ Schema
    }
}

# Maps to permission in permissions.py
permissions_by_state_type = {
    "/state/accounts/charge": {"create_charge": can_create_charge}
    # Path from event ^        ^ Operation name    ^ Permission function
}
```

**Lookup Process:**
1. Request comes to `/state/accounts/charge`
2. `@validated("create_charge")` extracts path from event
3. Looks up `rules["validators"]["/state/accounts/charge"]["create_charge"]`
4. Validates request against `add_charge_schema`
5. Looks up `permissions_by_state_type["/state/accounts/charge"]["create_charge"]`
6. Calls `can_create_charge(user, data)` to check permissions
7. Calls your function if everything passes

---

## Permission System (Pattern 1 Only)

> **⚠️ Note:** This section applies **ONLY to Pattern 1** (Standard REST API). Pattern 2 handles permissions differently.

### Define Permissions

**File: `schemata/permissions.py`**

```python
def can_create_charge(user, data):
    """
    Check if user can create a charge.

    Args:
        user: Username from authentication
        data: Validated request data

    Returns:
        bool: True if allowed, False otherwise
    """
    account_id = data["data"]["accountId"]

    # Check if user owns the account
    if not user_owns_account(user, account_id):
        return False

    # Check if charge amount is within limits
    charge = data["data"]["charge"]
    if charge > 1000:
        return is_admin(user)

    return True


def can_view_users(user, data):
    """Admin-only permission."""
    return is_admin(user)


# Central permission registry
permissions_by_state_type = {
    "/state/accounts/charge": {
        "create_charge": can_create_charge
    },
    "/users/list": {
        "list_users": can_view_users
    },
}


def get_permission_checker():
    """
    Returns the permission checker function.
    Called by setup_validated().
    """
    def permission_checker(path, operation, user, data):
        permissions = permissions_by_state_type.get(path, {})
        permission_func = permissions.get(operation)

        if not permission_func:
            # No permission defined = deny by default
            return False

        return permission_func(user, data)

    return permission_checker
```

**Permission Function Patterns:**

```python
# Simple: Always allow
def always_allow(user, data):
    return True

# Check user ownership
def can_modify_own_data(user, data):
    owner = data["data"].get("owner")
    return user == owner

# Admin check
def admin_only(user, data):
    return is_admin_user(user)

# Complex: Multiple conditions
def can_delete_file(user, data):
    file_id = data["data"]["fileId"]
    file_info = get_file_info(file_id)

    # Allow if user is owner OR admin
    return file_info["owner"] == user or is_admin_user(user)
```

---

## Complete Examples

### Example 1: 📱 Standard REST Endpoint

```python
"""
Service: amplify-lambda
File: files/file.py
Endpoint: POST /files/upload
Pattern: Standard REST API
"""

from pycommon.authz import validated, setup_validated
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, S3Operation
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker
from pycommon.logger import getLogger

logger = getLogger("file_upload")

# Initialize validation system (once per module)
setup_validated(rules, get_permission_checker)

@required_env_vars({
    "FILES_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
    "S3_RAG_INPUT_BUCKET_NAME": [S3Operation.PUT_OBJECT],
})
@validated("upload_file")
def get_presigned_url(event, context, user, name, data):
    """
    Generate presigned URL for file upload.

    Schema in schemata/upload_file_schema.py:
    {
        "type": "object",
        "properties": {
            "fileName": {"type": "string"},
            "fileType": {"type": "string"},
            "fileSize": {"type": "number"}
        },
        "required": ["fileName", "fileType"]
    }

    Permission in schemata/permissions.py:
    def can_upload_file(user, data):
        file_size = data["data"].get("fileSize", 0)
        return file_size < 100_000_000  # 100MB limit
    """
    file_name = data["data"]["fileName"]
    file_type = data["data"]["fileType"]

    bucket = os.environ["S3_RAG_INPUT_BUCKET_NAME"]
    table = os.environ["FILES_DYNAMO_TABLE"]

    s3_key = f"{user}/{file_name}"
    presigned_url = generate_presigned_upload_url(bucket, s3_key)
    record_file_metadata(table, user, file_name, file_type)

    return {
        "success": True,
        "uploadUrl": presigned_url,
        "fileId": s3_key
    }
```

### Example 2: 🔀 Agent Loop Router Pattern

```python
"""
Service: amplify-agent-loop-lambda
File: service/core.py & service/workflow_handlers.py
Endpoint: POST /vu-agent/{proxy+}
Pattern: Router/Proxy with @api_tool
"""

# === FILE: service/workflow_handlers.py ===
from pycommon.api.ops import api_tool
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation

@required_env_vars({
    "WORKFLOW_TEMPLATES_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@api_tool(
    path="/vu-agent/create-workflow",
    tags=["workflows", "default"],
    name="createWorkflow",
    description="Create a new workflow template.",
    parameters={
        "type": "object",
        "properties": {
            "workflowName": {"type": "string", "description": "Name of the workflow"},
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Workflow steps"
            }
        },
        "required": ["workflowName", "steps"]
    },
    output={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "workflowId": {"type": "string"}
        }
    }
)
def create_workflow_handler(current_user, workflow_name, steps):
    """
    Handler function for creating workflows.
    Schema is defined in @api_tool decorator above.
    """
    workflow_id = save_workflow(current_user, workflow_name, steps)
    return {"success": True, "workflowId": workflow_id}


# === FILE: service/core.py ===
from pycommon.authz import validated, setup_validated
from pycommon.api.ops import set_route_data
from service.routes import route_data
from jsonschema import validate, ValidationError

# Set route data BEFORE importing handlers
set_route_data(route_data)

# Import handlers (they register themselves)
from service.workflow_handlers import *

setup_validated(rules, get_permission_checker)

def common_handler(operation, func_schema, **optional_params):
    """Handles validation and tracking for routed operations."""
    def handler(event, context, current_user, name, data):
        from pycommon.metrics import get_usage_tracker
        tracker = get_usage_tracker()
        op_tracking_context = {}

        try:
            # Validate against schema from @api_tool
            wrapper_schema = {
                "type": "object",
                "properties": {"data": func_schema},
                "required": ["data"],
            }
            validate(data, wrapper_schema)

            # Start tracking
            op_tracking_context = tracker.start_tracking(
                user=current_user,
                operation=operation.__name__,
                endpoint='agent_operation',
                api_accessed=data.get("api_accessed", False),
                context=context,
            )

            # Execute operation
            result = operation(current_user, **args, **optional_params)

            # End tracking
            success = result.get("success", True) if isinstance(result, dict) else True
            metrics = tracker.end_tracking(
                tracking_context=op_tracking_context,
                result={"statusCode": 200 if success else 500},
                claims={"account": data.get("account", "unknown"), "username": current_user},
                error_type=None if success else "OperationFailure",
            )
            tracker.record_metrics(metrics)

            return result

        except Exception as e:
            if op_tracking_context:
                metrics = tracker.end_tracking(
                    tracking_context=op_tracking_context,
                    result={"statusCode": 500},
                    claims={"account": data.get("account", "unknown"), "username": current_user},
                    error_type=type(e).__name__,
                )
                tracker.record_metrics(metrics)
            return {"success": False, "error": str(e)}

    return handler


@validated("route", False)  # Authentication only
def route(event, context, current_user, name, data):
    """Router endpoint - dispatches to handlers based on path."""
    target_path = event.get("path", "")
    route_info = route_data.get(target_path)

    if not route_info:
        return {"success": False, "error": "Invalid path"}

    handler_func = route_info["handler"]
    func_schema = route_info["parameters"]  # From @api_tool

    return common_handler(handler_func, func_schema)(
        event, context, current_user, name, data
    )
```

### Example 3: ⚡ SQS Event-Driven Function

```python
"""
Service: amplify-lambda
File: rag/core.py
Trigger: SQS queue
Pattern: Event-Driven
"""

from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, S3Operation, SQSOperation
from pycommon.logger import getLogger
import json

logger = getLogger("rag_processor")

@required_env_vars({
    "USAGE_METRICS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
    "FILES_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
    "S3_RAG_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "RAG_CHUNK_DOCUMENT_QUEUE_URL": [SQSOperation.SEND_MESSAGE],
})
@track_execution
def process_document_for_rag(event, context):
    """
    Process documents from SQS queue for RAG.

    @track_execution:
    - Automatically detects SQS event source
    - Tracks execution time and memory usage
    - Calculates Lambda cost
    - Records metrics to USAGE_METRICS_DYNAMO_TABLE
    """
    logger.info("Processing %d SQS messages", len(event["Records"]))

    s3 = boto3.client("s3")
    sqs = boto3.client("sqs")
    dynamodb = boto3.resource("dynamodb")

    files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
    bucket = os.environ["S3_RAG_INPUT_BUCKET_NAME"]
    queue_url = os.environ["RAG_CHUNK_DOCUMENT_QUEUE_URL"]

    for record in event["Records"]:
        try:
            s3_event = json.loads(record["body"])
            s3_key = s3_event["Records"][0]["s3"]["object"]["key"]

            response = s3.get_object(Bucket=bucket, Key=s3_key)
            content = response["Body"].read()

            chunks = chunk_document(content)

            for chunk in chunks:
                sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(chunk))

            files_table.update_item(
                Key={"id": s3_key},
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":status": "chunked"}
            )

        except Exception as e:
            logger.error("Failed to process message: %s", str(e))

    return {"statusCode": 200}
```

---

## Best Practices

### 1. ✅ Decorator Order Matters

**For HTTP endpoints:**
```python
@required_env_vars({...})    # 1. Resolve environment variables first
@validated("operation")      # 2. Validate and authenticate
def your_function(...):      # 3. Your function
    pass
```

**For event-driven functions:**
```python
@required_env_vars({...})    # 1. Resolve environment variables first
@track_execution             # 2. Track execution metrics
def your_function(...):      # 3. Your function
    pass
```

### 2. ❌ Don't Mix Patterns

**Wrong:**
```python
@required_env_vars({...})
@validated("operation")
@track_execution  # ← REDUNDANT! @validated already tracks
def your_function(...):
    pass
```

**Correct:**
```python
# For HTTP endpoints - Pattern 1 or 2
@required_env_vars({...})
@validated("operation")  # ← Tracks automatically
def your_function(...):
    pass

# For event-driven - Pattern 3
@required_env_vars({...})
@track_execution  # ← Manual tracking needed
def your_function(...):
    pass
```

### 3. 🎯 Always Include USAGE_METRICS_DYNAMO_TABLE

For event-driven functions (Pattern 3):
```python
@required_env_vars({
    "USAGE_METRICS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],  # ← Always required
    "YOUR_TABLE": [DynamoDBOperation.GET_ITEM],
})
@track_execution
def your_event_function(event, context):
    pass
```

### 4. 🔒 Permission Security

**Insecure:**
```python
def can_delete_user(user, data):
    return True  # ← Anyone can delete anyone!
```

**Secure:**
```python
def can_delete_user(user, data):
    target_user = data["data"]["userId"]
    return user == target_user or is_admin(user)
```

### 5. 📝 Environment Variable Precision

**Too vague:**
```python
@required_env_vars({
    "DYNAMO_TABLE": [],  # ← What operations?
})
```

**Precise:**
```python
@required_env_vars({
    "DYNAMO_TABLE": [
        DynamoDBOperation.GET_ITEM,
        DynamoDBOperation.PUT_ITEM,
        DynamoDBOperation.QUERY
    ],
})
```

### 6. 🛡️ Error Handling

**For HTTP endpoints (Pattern 1 & 2):**
```python
@validated("operation")
def your_function(event, context, user, name, data):
    # @validated handles exceptions and returns proper HTTP errors
    result = might_throw_exception()
    return result
```

**For event-driven functions (Pattern 3):**
```python
@track_execution
def your_function(event, context):
    # You MUST handle exceptions
    try:
        result = process_event()
        return {"statusCode": 200}
    except Exception as e:
        logger.error("Processing failed: %s", str(e))
        return {"statusCode": 500}
```

### 7. 📋 Testing Checklist

**Before deploying Pattern 1 (Standard REST API):**
- [ ] `setup_validated(rules, get_permission_checker)` called once per module
- [ ] Operation name matches in `@validated`, `schema_validation_rules.py`, and `permissions.py`
- [ ] Schema file exists in `schemata/` folder
- [ ] Permission function exists in `permissions.py`
- [ ] `@required_env_vars` lists all environment variables used
- [ ] AWS operations are precise (GET_ITEM vs QUERY vs SCAN)

**Before deploying Pattern 2 (Router/Proxy):**
- [ ] `set_route_data(route_data)` called before importing handlers
- [ ] Operations defined with `@api_tool` decorator
- [ ] `@validated("route", False)` used on router function
- [ ] `common_handler` implements manual tracking

**Before deploying Pattern 3 (Event-Driven):**
- [ ] `@track_execution` decorator used
- [ ] `USAGE_METRICS_DYNAMO_TABLE` included in `@required_env_vars`
- [ ] Error handling implemented (no automatic HTTP responses)

---

## Quick Reference

### 🎯 Which Pattern to Use?

| Scenario | Pattern | Key Indicators |
|----------|---------|---------------|
| Individual REST endpoint with validation | 📱 Pattern 1 | One endpoint = One operation, needs schema validation |
| Many operations behind single endpoint | 🔀 Pattern 2 | `/vu-agent/{proxy+}` routing, uses `@api_tool` |
| SQS, DynamoDB Stream, S3, Cron | ⚡ Pattern 3 | No HTTP, event-driven trigger |

### 📊 Decorator Comparison

| Decorator | Purpose | Use With |
|-----------|---------|----------|
| `@required_env_vars` | Resolve & track env vars | **All patterns** |
| `@validated("op")` | Validate + authenticate + track | **Pattern 1** (HTTP endpoints) |
| `@validated("op", False)` | Authenticate only | **Pattern 2** (Router) |
| `@track_execution` | Track execution metrics | **Pattern 3** (Event-driven) |

### 🔧 Pattern Summary

| Pattern | Decorators | Tracking | Validation | Schema Location |
|---------|-----------|----------|------------|-----------------|
| 📱 Pattern 1 | `@required_env_vars` + `@validated` | Automatic | Schema-based | `schemata/` folder |
| 🔀 Pattern 2 | `@validated("route", False)` | Manual (`common_handler`) | Manual | `@api_tool` decorator |
| ⚡ Pattern 3 | `@required_env_vars` + `@track_execution` | Automatic | None | N/A |

---

**Document Version:** 1.1
**Last Updated:** 2024
**PyCommon Version:** v0.1.1
