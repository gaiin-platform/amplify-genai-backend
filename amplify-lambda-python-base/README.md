# Amplify Lambda Python Base - Sample Service Template

## 🚀 Purpose

This is the **recommended starting point** for creating new Lambda services in the Amplify platform. It contains all the essential IAM roles, environment variables, and boilerplate code needed for proper integration with the PyCommon library (v0.1.1).

**Use this as a template when creating new services.**

---

## ✅ What's Included

### serverless.yml
- ✅ Managed IAM policy with all required permissions
- ✅ Standard environment variables (OAuth, API keys, cost calculations, etc.)
- ✅ API Gateway integration
- ✅ DynamoDB table with encryption and PITR enabled
- ✅ CloudFormation changesets for safe deployments
- ✅ 800-171 compliance settings (encryption, PITR, log retention)

### service/core.py
- ✅ Proper `@required_env_vars` usage declaring DynamoDB operations
- ✅ Correct `@validated` decorator pattern (Pattern 1: Standard REST API)
- ✅ Logger setup
- ✅ Complete function signature with all injected parameters
- ✅ Usage tracking (automatic via `@validated`)

### schemata/ folder
- ✅ `schema_validation_rules.py` - Central schema registration
- ✅ `permissions.py` - Permission checker functions with examples
- ✅ `sample_schema.py` - Example schema with documentation

---

## 📚 Key Concepts Demonstrated

### 1. Environment Variables ↔ IAM Permissions

**Every environment variable requires a corresponding IAM permission.**

```yaml
# serverless.yml - Environment variable declaration
environment:
  SAMPLE_DYNAMODB_TABLE: ${self:service}-${sls:stage}-sample-table

# serverless.yml - Corresponding IAM permission
Statement:
  - Effect: Allow
    Action:
      - dynamodb:GetItem
      - dynamodb:PutItem
    Resource:
      - "arn:aws:dynamodb:${aws:region}:*:table/${self:provider.environment.SAMPLE_DYNAMODB_TABLE}"
```

### 2. @required_env_vars Documents IAM Needs

```python
@required_env_vars({
    "SAMPLE_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
```

This decorator declares:
- ✅ What environment variables the function needs
- ✅ What AWS operations will be performed
- ✅ What IAM permissions are required
- ✅ Enables automatic env var resolution (Lambda env → Parameter Store → Error)
- ✅ Tracks env var usage for auditing

### 3. Standard Variables Every Service Needs

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

---

## 🛠️ How to Use This as a Template

### Step 1: Copy the Directory

```bash
cp -r amplify-lambda-python-base amplify-lambda-your-new-service
cd amplify-lambda-your-new-service
```

### Step 2: Update Service Name

In `serverless.yml`:
```yaml
service: amplify-${self:custom.depName}-your-new-service
```

### Step 3: Add Service-Specific Resources

**Add environment variables:**
```yaml
environment:
  YOUR_TABLE: ${self:service}-${sls:stage}-your-table
  YOUR_BUCKET: ${self:service}-${sls:stage}-your-bucket
```

**Add corresponding IAM permissions:**
```yaml
Statement:
  - Effect: Allow
    Action:
      - dynamodb:GetItem
      - dynamodb:PutItem
    Resource:
      - "arn:aws:dynamodb:${aws:region}:*:table/${self:provider.environment.YOUR_TABLE}"
  - Effect: Allow
    Action:
      - s3:GetObject
      - s3:PutObject
    Resource:
      - "arn:aws:s3:::${self:provider.environment.YOUR_BUCKET}/*"
```

**Add DynamoDB/S3 resources:**
```yaml
Resources:
  YourTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: ${self:provider.environment.YOUR_TABLE}
      BillingMode: PAY_PER_REQUEST
      PointInTimeRecoverySpecification:
        PointInTimeRecoveryEnabled: true
      SSESpecification:
        SSEEnabled: true
      AttributeDefinitions:
        - AttributeName: id
          AttributeType: S
      KeySchema:
        - AttributeName: id
          KeyType: HASH
```

### Step 4: Create Your Schemas

**Add schema file** (`schemata/your_operation_schema.py`):
```python
your_operation_schema = {
    "type": "object",
    "properties": {
        "field1": {"type": "string", "description": "Description of field1"},
        "field2": {"type": "number", "description": "Description of field2"}
    },
    "required": ["field1"]
}
```

**Register in `schema_validation_rules.py`:**
```python
from .your_operation_schema import your_operation_schema

rules = {
    "validators": {
        "/yourservice/operation": {
            "your_operation": your_operation_schema
        }
    },
    "api_validators": {}
}
```

**Define permissions in `permissions.py`:**
```python
def can_do_your_operation(user, data):
    """
    Permission checker for your operation.

    Args:
        user: Username from authentication
        data: Validated request data

    Returns:
        bool: True if allowed, False otherwise
    """
    # Example: Check if user owns the resource
    owner = data["data"].get("owner")
    return user == owner

permissions_by_state_type = {
    "/yourservice/operation": {
        "your_operation": can_do_your_operation
    }
}
```

### Step 5: Implement Your Functions

**Pattern 1: Standard REST API** (like this sample):
```python
from pycommon.decorators import required_env_vars
from pycommon.authz import validated
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation

@required_env_vars({
    "YOUR_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM],
})
@validated("your_operation")
def your_function(event, context, current_user, name, data):
    """
    Standard REST endpoint with validation.

    @validated automatically:
    - Validates request schema
    - Checks user permissions
    - Tracks usage metrics
    """
    import os
    table_name = os.environ["YOUR_TABLE"]

    # Your business logic here
    return {"success": True, "data": {}}
```

**Pattern 3: Event-Driven** (SQS, DynamoDB Streams, S3):
```python
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation

@required_env_vars({
    "USAGE_METRICS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
    "YOUR_TABLE": [DynamoDBOperation.GET_ITEM],
})
@track_execution
def your_event_function(event, context):
    """
    Event-driven function for SQS, DynamoDB Streams, S3, or Scheduled events.

    @track_execution automatically:
    - Detects event source
    - Tracks execution metrics
    - Calculates Lambda costs
    """
    # Your business logic here
    return {"statusCode": 200}
```

---

## 📖 Standard IAM Permissions (Always Needed)

The sample includes these **required** IAM policies that every service must have:

### 1. Authentication & Cost Tracking
```yaml
- dynamodb:Query
- dynamodb:Scan
- dynamodb:GetItem
- dynamodb:PutItem
- dynamodb:UpdateItem
- dynamodb:DeleteItem
```
**For tables:** `API_KEYS_DYNAMODB_TABLE`, `ACCOUNTS_DYNAMO_TABLE`, `COST_CALCULATIONS_DYNAMO_TABLE`, `COGNITO_USERS_DYNAMODB_TABLE`

**Why needed:**
- API key and OAuth token validation
- User account lookups
- Rate limiting checks
- Usage cost calculations

### 2. Environment Variable Tracking
```yaml
- dynamodb:GetItem
- dynamodb:PutItem
- dynamodb:UpdateItem
- dynamodb:Query
```
**For table:** `ENV_VARS_TRACKING_TABLE`

**Why needed:**
- Tracks all env var usage for auditing
- Required by `@required_env_vars` decorator

### 3. Parameter Store Access
```yaml
- ssm:GetParameter
- ssm:GetParameters
- ssm:GetParametersByPath
```
**For path:** `${self:custom.ssmBasePath}-*`

**Why needed:**
- Automatic env var resolution fallback
- Required by `@required_env_vars` decorator

### 4. Error Reporting
```yaml
- sqs:SendMessage
```
**For queue:** `CRITICAL_ERRORS_SQS_QUEUE_NAME`

**Why needed:**
- Send critical errors to admin notification system
- Used by PyCommon error handling

---

## 🎯 Key Principles

1. **Every env var = IAM permission**: Document what your function needs
2. **Use @required_env_vars**: Declare dependencies explicitly
3. **Follow patterns**: Use Pattern 1 (REST), Pattern 2 (Router/Proxy), or Pattern 3 (Event-Driven)
4. **Include standard vars**: Authentication, cost tracking, error handling
5. **Enable compliance**: PITR, encryption, log retention (800-171)

---

## Requirements

- AWS CLI
- Serverless Framework v3
- Python 3.11
- Docker (for packaging Python requirements)
- Node.js and npm

## Setup

Install Serverless Framework:
```bash
npm install -g serverless
```

Install Node.js dependencies:
```bash
npm install
```

Install Serverless plugins:
```bash
npm install
sls plugin install -n serverless-python-requirements
sls plugin install -n serverless-cloudformation-changesets
```

Create Python virtual environment:
```bash
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Deployment

Deploy to dev:
```bash
sls deploy --stage dev
```

Deploy to other stages:
```bash
sls deploy --stage staging
sls deploy --stage prod
```

## Local Development

Start local server with serverless-offline:
```bash
sls offline
```

## Testing

Test the sample endpoint:
```bash
curl -X POST https://your-api-endpoint/dev/someservice/sample \
  --header "Content-Type: application/json" \
  --header "Authorization: Bearer YOUR_TOKEN" \
  --data '{"data": {"msg": "Hello, World!"}}'
```

Expected response:
```json
{
  "success": true,
  "message": "Sample response",
  "data": {"msg": "Hello, World!"}
}
```

## Monitoring

View function logs:
```bash
sls logs -f sample -t
```

Monitor CloudWatch metrics in AWS Console.

## Cleanup

Remove deployed service:
```bash
sls remove --stage dev
```

---

## 🔗 Related Documentation

For comprehensive implementation details, see:
**Implementation Guidelines:** `/Users/rodrikm1/Documents/Amplify Read Mes/Implementation_Guidelines.md`

This guide covers:
- Three Lambda invocation patterns (REST API, Router/Proxy, Event-Driven)
- Complete decorator usage (`@required_env_vars`, `@validated`, `@track_execution`)
- Schema and validation system
- Permission system
- Usage tracking and metrics
- Complete working examples
- Best practices and checklists

---

## 🚀 Quick Start

```bash
# 1. Copy template
cp -r amplify-lambda-python-base amplify-lambda-my-service
cd amplify-lambda-my-service

# 2. Update service name in serverless.yml
# 3. Add your env vars, IAM permissions, and resources
# 4. Create your schemas in schemata/
# 5. Implement your functions in service/

# 6. Deploy
serverless deploy --stage dev
```

---

**This sample demonstrates Pattern 1: Standard REST API Endpoints**

For Pattern 2 (Router/Proxy) and Pattern 3 (Event-Driven), see the Implementation Guidelines document.

---

## Additional Resources

- [Serverless Framework AWS Guide](https://www.serverless.com/framework/docs/providers/aws/)
- [AWS Lambda Developer Guide](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html)
- [AWS DynamoDB Developer Guide](https://docs.aws.amazon.com/dynamodb/latest/developerguide/Introduction.html)
- [boto3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- [PyCommon Library](https://github.com/gaiin-platform/pycommon) - v0.1.1

---

**PyCommon Version:** v0.1.1
**Document Version:** 1.0
**Last Updated:** 2024
