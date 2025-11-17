# Email Webhooks System - Phase 1

## Overview

This system monitors incoming emails for university users and processes them through Microsoft Graph API webhooks. It's integrated into the existing `amplify-lambda-assistants-api-office365` service.

## Architecture

```
Microsoft Graph API (monitors user mailboxes)
           ↓
    Webhook Subscription (per user)
           ↓
    API Gateway + webhookHandler Lambda
           ↓ 
    SQS Queue
           ↓
    emailProcessor Lambda
           ↓
    CloudWatch Logs + DynamoDB Storage
           ↓
    [Future: AI Assistant Integration]
```

## New API Endpoints

### 1. Webhook Handler
- **Path**: `/integrations/email/webhook`
- **Methods**: GET (validation), POST (notifications)
- **Purpose**: Receives Microsoft Graph webhook notifications
- **Handler**: `integrations/email_webhooks.webhook_handler`

### 2. Create Email Subscription  
- **Path**: `/integrations/email/subscription/create`
- **Method**: POST
- **Purpose**: Creates webhook subscriptions for users
- **Handler**: `integrations/email_webhooks.create_subscription`

**Request Body**:
```json
{
  "userId": "azure-ad-user-guid", 
  "userEmail": "user@vanderbilt.edu"
}
```

## New AWS Resources

### Lambda Functions
1. **webhookHandler** - Receives webhooks from Microsoft Graph
2. **emailProcessor** - Processes emails from SQS queue  
3. **createEmailSubscription** - Creates webhook subscriptions

### Infrastructure
1. **SQS Queue**: `amplify-assistants-office365-office365-{stage}-email-notifications`
2. **DLQ**: `amplify-assistants-office365-office365-{stage}-email-notifications-dlq`
3. **DynamoDB Table**: `amplify-assistants-office365-office365-{stage}-email-subscriptions`

## Environment Variables Added

```yaml
EMAIL_NOTIFICATIONS_QUEUE: Queue name for notifications
EMAIL_SUBSCRIPTIONS_TABLE: DynamoDB table for subscription tracking
EMAIL_WEBHOOK_CLIENT_STATE: SSM parameter for webhook security
```

## Key Features

### Phase 1 (Current)
- ✅ Single user webhook subscriptions
- ✅ Real-time email notification processing
- ✅ Complete email content fetching from Graph API
- ✅ Detailed logging for monitoring
- ✅ Error handling and DLQ for failed messages
- ✅ Subscription tracking in DynamoDB

### Phase 2 (Future)
- 🔄 Multi-user automation (sync from Azure AD)
- 🔄 Automatic subscription renewal (EventBridge)
- 🔄 AI assistant integration for email processing
- 🔄 Draft response generation
- 🔄 CloudWatch alarms and monitoring

## Security Features

1. **Client State Validation** - All webhook notifications validated with secret
2. **Application Permissions** - Uses app-level Graph access (not user delegated)  
3. **HTTPS Only** - Microsoft Graph requires HTTPS webhook endpoints
4. **IAM Least Privilege** - Specific permissions for each AWS resource

## Getting Started

### 1. Prerequisites
- Microsoft Graph application permissions: `Mail.Read`
- Existing Graph credentials in Parameter Store
- Azure AD User GUID for testing

### 2. Deploy
```bash
cd amplify-lambda-assistants-api-office365
serverless deploy --stage dev
```

### 3. Setup
```bash
# Create webhook client state secret
aws ssm put-parameter \
  --name "/amplify-assistants-office365-office365/dev/email/webhook/client-state" \
  --value "$(openssl rand -base64 32)" \
  --type "SecureString"
```

### 4. Create Subscription
```bash
curl -X POST "https://{api-domain}/dev/integrations/email/subscription/create" \
  -H "Content-Type: application/json" \
  -d '{"userId": "YOUR-USER-GUID", "userEmail": "max.moundas@vanderbilt.edu"}'
```

### 5. Test
Send email to `max.moundas@vanderbilt.edu` and check CloudWatch logs for processing.

## Files Added

- `integrations/email_webhooks.py` - Main implementation
- `DEPLOYMENT_EMAIL_WEBHOOKS.md` - Deployment guide  
- `TESTING_EMAIL_WEBHOOKS.md` - Testing procedures
- `EMAIL_WEBHOOKS_README.md` - This overview

## Monitoring

### CloudWatch Log Groups
- `/aws/lambda/amplify-assistants-office365-office365-dev-webhookHandler`
- `/aws/lambda/amplify-assistants-office365-office365-dev-emailProcessor`
- `/aws/lambda/amplify-assistants-office365-office365-dev-createEmailSubscription`

### Key Metrics to Monitor
- Webhook handler invocations and errors
- Email processor success/failure rates
- SQS queue depth and DLQ messages
- DynamoDB subscription records

## Important Notes

### Subscription Expiration
- Microsoft Graph subscriptions expire every 72 hours (3 days)
- Phase 1: Manual renewal required
- Phase 2: Will automate with EventBridge

### Rate Limits
- Microsoft Graph has rate limits for webhook operations
- System implements exponential backoff for failed requests
- Monitor for throttling in CloudWatch logs

### Testing Checklist
- [ ] Webhook validation works (returns validation token)
- [ ] Subscription creation succeeds
- [ ] Test email triggers notification within 60 seconds
- [ ] Email details logged correctly in CloudWatch
- [ ] No messages in DLQ after testing

## Support

For issues:
1. Check CloudWatch logs for error details
2. Verify subscription status in DynamoDB
3. Test webhook endpoint manually
4. Review deployment and testing guides