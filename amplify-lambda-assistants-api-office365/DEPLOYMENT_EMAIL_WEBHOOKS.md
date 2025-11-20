# Email Webhooks System - Deployment Guide

## Overview

This guide covers the deployment of the Microsoft Graph Email Webhook System for Phase 1 (single user testing). The system monitors incoming emails for university users and processes them through an AI-powered workflow.

## Prerequisites

### 1. Microsoft Graph Application Permissions

Ensure your Azure AD application has the following **application permissions** (not delegated):
- `Mail.Read` - Read mail in all mailboxes
- `User.Read.All` - Read all users' profiles (for user lookup)

### 2. Webhook Subscription Limits

- Maximum subscription duration: 72 hours (3 days)
- Microsoft Graph requires HTTPS webhook endpoints
- Webhook responses must return within 3 seconds

## Pre-Deployment Setup

### 1. Create Required SSM Parameters

Before deploying, you must create the webhook client state secret:

```bash
# Generate a secure random client state (follows existing oauth pattern)
aws ssm put-parameter \
  --name "/oauth/integrations/microsoft/dev/email-webhook-client-state" \
  --value "$(openssl rand -base64 32)" \
  --type "SecureString" \
  --description "Client state secret for email webhook validation"
```

**Note**: Replace `dev` with your target stage (dev/staging/prod).

### 2. Verify Existing Graph Credentials

The system uses existing Microsoft Graph credentials stored at:
- `/oauth/integrations/microsoft/dev`

Verify these exist:
```bash
aws ssm get-parameter --name "/oauth/integrations/microsoft/dev"
```

The parameter should contain JSON in this format:
```json
{
  "client_config": {
    "web": {
      "client_id": "your-client-id",
      "client_secret": "your-client-secret", 
      "tenant_id": "your-tenant-id"
    }
  }
}
```

## Deployment Steps

### 1. Deploy the Service

```bash
cd /Users/maxmoundas/Amplify/amplify-genai-backend/amplify-lambda-assistants-api-office365

# Deploy to dev stage
serverless deploy --stage dev

# Check deployment status
serverless info --stage dev
```

### 2. Verify Resources Created

After deployment, verify these resources exist:
- ✅ Lambda functions: `webhookHandler`, `emailProcessor`, `createEmailSubscription`
- ✅ SQS Queue: `amplify-assistants-office365-office365-dev-email-notifications`
- ✅ DynamoDB Table: `amplify-assistants-office365-office365-dev-email-subscriptions`
- ✅ API Gateway endpoints added to existing shared API

### 3. Get API Endpoints

```bash
# Get the API Gateway URL
aws apigateway get-rest-apis --query "items[?name=='dev-RestApi'].id" --output text

# Or check serverless info output for endpoints
serverless info --stage dev
```

Your webhook endpoints will be:
- **Webhook Handler**: `https://{api-id}.execute-api.{region}.amazonaws.com/dev/integrations/email/webhook`
- **Create Subscription**: `https://{api-id}.execute-api.{region}.amazonaws.com/dev/integrations/email/subscription/create`

## Initial Testing

### 1. Test Webhook Validation

Microsoft Graph validates webhook endpoints before creating subscriptions. Test this manually:

```bash
# Test validation endpoint (simulate Microsoft's validation)
curl -X GET "https://{your-api-domain}/dev/integrations/email/webhook?validationToken=test-token-123"

# Expected response: "test-token-123" (plain text, not JSON)
```

### 2. Create Your First Subscription

**You need your Azure AD User GUID for this step.** If you don't have it yet:

```bash
# Find your User GUID (requires Azure CLI)
az ad user show --id "max.moundas@vanderbilt.edu" --query "id" --output tsv
```

Create subscription via API:
```bash
curl -X POST "https://{your-api-domain}/dev/integrations/email/subscription/create" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {your-auth-token}" \
  -d '{
    "userId": "YOUR-USER-GUID-HERE",
    "userEmail": "max.moundas@vanderbilt.edu"
  }'
```

**Expected response:**
```json
{
  "success": true,
  "data": {
    "subscriptionId": "abc-123-def-456",
    "userId": "YOUR-USER-GUID",
    "userEmail": "max.moundas@vanderbilt.edu",
    "expirationDateTime": "2024-01-20T15:30:00Z"
  }
}
```

### 3. Test End-to-End Flow

1. **Send yourself a test email** to `max.moundas@vanderbilt.edu`
2. **Check CloudWatch Logs** for the `emailProcessor` function
3. **Look for the detailed email log** output

Expected log output:
```
==================================================
EMAIL RECEIVED  
==================================================
User ID: YOUR-USER-GUID
Subscription ID: abc-123-def-456
Subject: Test Email
From: sender@example.com
Received: 2024-01-17T10:30:00Z
Has Attachments: false
Body Preview: This is a test email to verify...
==================================================
```

## Monitoring and Troubleshooting

### CloudWatch Log Groups

Monitor these log groups:
- `/aws/lambda/amplify-assistants-office365-office365-dev-webhookHandler`
- `/aws/lambda/amplify-assistants-office365-office365-dev-emailProcessor`  
- `/aws/lambda/amplify-assistants-office365-office365-dev-createEmailSubscription`

### Common Issues

#### 1. Subscription Creation Fails
```bash
# Check Graph API credentials
aws ssm get-parameter --name "/oauth/integrations/microsoft/dev"

# Check webhook client state
aws ssm get-parameter --name "/oauth/integrations/microsoft/dev/email-webhook-client-state" --with-decryption
```

#### 2. Webhook Validation Fails
- Ensure API Gateway is deployed and accessible
- Check that webhook endpoint returns plain text (not JSON)
- Verify HTTPS is working (Microsoft requires HTTPS)

#### 3. No Email Notifications Received
```bash
# Check SQS queue for messages
aws sqs get-queue-attributes \
  --queue-url "https://sqs.{region}.amazonaws.com/{account}/amplify-assistants-office365-office365-dev-email-notifications" \
  --attribute-names ApproximateNumberOfMessages

# Check DLQ for failed messages
aws sqs get-queue-attributes \
  --queue-url "https://sqs.{region}.amazonaws.com/{account}/amplify-assistants-office365-office365-dev-email-notifications-dlq" \
  --attribute-names ApproximateNumberOfMessages
```

#### 4. Graph API Authentication Errors
- Verify your Azure AD app has application permissions (not delegated)
- Ensure `Mail.Read` permission is granted and admin-consented
- Check if application permissions are correctly configured

### Useful Commands

```bash
# View recent webhook handler logs
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/amplify-assistants-office365-office365-dev-webhookHandler"

# View SQS queue messages (for debugging)
aws sqs receive-message --queue-url "https://sqs.{region}.amazonaws.com/{account}/amplify-assistants-office365-office365-dev-email-notifications"

# Check DynamoDB for subscription records
aws dynamodb scan --table-name "amplify-assistants-office365-office365-dev-email-subscriptions" --max-items 5
```

## Security Considerations

### 1. Client State Validation
- The webhook validates `clientState` on every notification
- This prevents malicious webhook requests
- Keep the client state secret secure in SSM Parameter Store

### 2. Application Permissions
- Uses application-level Graph API permissions
- More secure than delegated permissions for organization-wide access
- Requires admin consent in Azure AD

### 3. HTTPS Required
- Microsoft Graph only sends webhooks to HTTPS endpoints
- API Gateway provides HTTPS automatically

## Next Steps After Successful Deployment

1. **Monitor for 48 hours** - Ensure stability and no dropped notifications
2. **Test high email volume** - Send 20+ emails rapidly to test performance  
3. **Document subscription renewal** - Subscriptions expire in 72 hours
4. **Plan Phase 2** - Multi-user subscription management

## Subscription Renewal (Manual for Phase 1)

Subscriptions expire every 72 hours. For Phase 1, renewal is manual:

```bash
# 1. Delete existing subscription (optional - will auto-expire)
curl -X DELETE "https://graph.microsoft.com/v1.0/subscriptions/{subscription-id}" \
  -H "Authorization: Bearer {app-token}"

# 2. Create new subscription using the same API call as above
curl -X POST "https://{your-api-domain}/dev/integrations/email/subscription/create" ...
```

**Phase 2 will automate renewal using EventBridge scheduled functions.**