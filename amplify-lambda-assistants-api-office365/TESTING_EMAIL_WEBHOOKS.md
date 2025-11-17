# Email Webhooks System - Testing Guide

## Overview

This guide provides comprehensive testing procedures for the Microsoft Graph Email Webhook System Phase 1. Follow these steps to verify end-to-end functionality.

## Prerequisites

- System deployed successfully (see DEPLOYMENT_EMAIL_WEBHOOKS.md)
- Your Azure AD User GUID obtained
- Access to CloudWatch logs
- Ability to send/receive test emails

## Test Plan

### Phase 1: Basic Infrastructure Tests

#### Test 1.1: Webhook Validation Endpoint

**Purpose**: Verify Microsoft Graph can validate your webhook endpoint.

```bash
# Test GET request with validation token (simulates Microsoft's validation)
curl -X GET "https://{your-api-domain}/dev/integrations/email/webhook?validationToken=my-test-token-12345"
```

**Expected Response**: 
- Status: 200 OK
- Body: `my-test-token-12345` (plain text, not JSON)
- Content-Type: `text/plain`

**Verify in CloudWatch**:
- Log group: `/aws/lambda/amplify-assistants-office365-office365-dev-webhookHandler`
- Look for: `"Handling webhook validation request"`

#### Test 1.2: Subscription Creation API

**Purpose**: Verify you can create webhook subscriptions.

```bash
# Replace YOUR-USER-GUID with your actual Azure AD User GUID
curl -X POST "https://{your-api-domain}/dev/integrations/email/subscription/create" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {your-auth-token}" \
  -d '{
    "userId": "YOUR-USER-GUID-HERE",
    "userEmail": "max.moundas@vanderbilt.edu"
  }'
```

**Expected Response**:
```json
{
  "success": true,
  "data": {
    "subscriptionId": "abc-123-def-456",
    "userId": "YOUR-USER-GUID",
    "userEmail": "max.moundas@vanderbilt.edu",
    "resource": "users/YOUR-USER-GUID/mailFolders('Inbox')/messages",
    "expirationDateTime": "2024-01-20T15:30:00Z",
    "notificationUrl": "https://{your-api-domain}/dev/integrations/email/webhook"
  }
}
```

**Verify in CloudWatch**:
- Log group: `/aws/lambda/amplify-assistants-office365-office365-dev-createEmailSubscription`
- Look for: `"Successfully created subscription"`

**Verify in DynamoDB**:
```bash
aws dynamodb scan --table-name "amplify-assistants-office365-office365-dev-email-subscriptions" --max-items 5
```

### Phase 2: End-to-End Email Flow Tests

#### Test 2.1: Single Email Processing

**Purpose**: Verify complete email webhook flow from receipt to processing.

**Steps**:
1. **Send test email** to `max.moundas@vanderbilt.edu` with subject: `"Test Email 1 - Webhook System"`

2. **Monitor webhook handler logs** (should receive notification within 60 seconds):
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/amplify-assistants-office365-office365-dev-webhookHandler" \
  --start-time $(date -d '5 minutes ago' +%s)000
```

3. **Monitor email processor logs** (should process within 2-3 minutes):
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/amplify-assistants-office365-office365-dev-emailProcessor" \
  --start-time $(date -d '5 minutes ago' +%s)000
```

**Expected Log Output (Email Processor)**:
```
==================================================
EMAIL RECEIVED
==================================================
User ID: YOUR-USER-GUID
Subscription ID: abc-123-def-456
Subject: Test Email 1 - Webhook System
From: sender@example.com
Received: 2024-01-17T10:30:00Z
Has Attachments: false
Body Preview: This is a test email to verify the webhook system is working correctly...
==================================================
```

**Verify SQS Processing**:
```bash
# Check main queue (should be empty after processing)
aws sqs get-queue-attributes \
  --queue-url "https://sqs.{region}.amazonaws.com/{account}/amplify-assistants-office365-office365-dev-email-notifications" \
  --attribute-names ApproximateNumberOfMessages

# Check DLQ (should remain empty)
aws sqs get-queue-attributes \
  --queue-url "https://sqs.{region}.amazonaws.com/{account}/amplify-assistants-office365-office365-dev-email-notifications-dlq" \
  --attribute-names ApproximateNumberOfMessages
```

#### Test 2.2: Multiple Email Burst Test

**Purpose**: Verify system handles multiple emails rapidly without dropping notifications.

**Steps**:
1. **Send 5 emails rapidly** (within 30 seconds) with subjects:
   - `"Burst Test 1 - First"`
   - `"Burst Test 2 - Second"`  
   - `"Burst Test 3 - Third"`
   - `"Burst Test 4 - Fourth"`
   - `"Burst Test 5 - Fifth"`

2. **Wait 5 minutes** for all processing to complete

3. **Verify all 5 emails processed**:
```bash
# Count processed emails in logs (should show 5 entries)
aws logs filter-log-events \
  --log-group-name "/aws/lambda/amplify-assistants-office365-office365-dev-emailProcessor" \
  --filter-pattern "\"EMAIL RECEIVED\"" \
  --start-time $(date -d '10 minutes ago' +%s)000 | grep "EMAIL RECEIVED" | wc -l
```

4. **Verify no messages in DLQ**:
```bash
aws sqs get-queue-attributes \
  --queue-url "https://sqs.{region}.amazonaws.com/{account}/amplify-assistants-office365-office365-dev-email-notifications-dlq" \
  --attribute-names ApproximateNumberOfMessages
```

#### Test 2.3: Email with Attachments

**Purpose**: Verify system handles emails with attachments correctly.

**Steps**:
1. **Send email with PDF attachment** to `max.moundas@vanderbilt.edu`
   - Subject: `"Attachment Test - PDF Document"`
   - Attach any small PDF file

2. **Verify processing shows attachment flag**:
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/amplify-assistants-office365-office365-dev-emailProcessor" \
  --filter-pattern "\"Has Attachments: true\"" \
  --start-time $(date -d '5 minutes ago' +%s)000
```

### Phase 3: Error Handling Tests

#### Test 3.1: Invalid Client State

**Purpose**: Verify security - webhook rejects notifications with wrong clientState.

**Steps**:
1. **Send fake webhook notification** with wrong clientState:
```bash
curl -X POST "https://{your-api-domain}/dev/integrations/email/webhook" \
  -H "Content-Type: application/json" \
  -d '{
    "value": [{
      "subscriptionId": "fake-sub-id",
      "clientState": "wrong-client-state",
      "changeType": "created",
      "resource": "users/test@example.com/messages/fake-id"
    }]
  }'
```

**Expected Response**: 
- Status: 500 (or error status)
- Should NOT create SQS messages

**Verify in Logs**: 
- Look for: `"Invalid clientState"` error

#### Test 3.2: Malformed Notification

**Purpose**: Verify system handles malformed webhook notifications gracefully.

```bash
curl -X POST "https://{your-api-domain}/dev/integrations/email/webhook" \
  -H "Content-Type: application/json" \
  -d '{"invalid": "json structure"}'
```

**Expected Response**:
- Status: 200 OK (graceful handling)
- No SQS messages created

### Phase 4: Monitoring Tests

#### Test 4.1: CloudWatch Metrics

**Verify Lambda metrics**:
```bash
# Check webhook handler invocations
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=amplify-assistants-office365-office365-dev-webhookHandler \
  --statistics Sum \
  --start-time $(date -d '1 hour ago' --iso-8601) \
  --end-time $(date --iso-8601) \
  --period 3600

# Check email processor invocations  
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=amplify-assistants-office365-office365-dev-emailProcessor \
  --statistics Sum \
  --start-time $(date -d '1 hour ago' --iso-8601) \
  --end-time $(date --iso-8601) \
  --period 3600
```

#### Test 4.2: DynamoDB Records

**Verify notification records are stored**:
```bash
aws dynamodb scan \
  --table-name "amplify-assistants-office365-office365-dev-email-subscriptions" \
  --projection-expression "subscription_id,user_id,#status,processed_at" \
  --expression-attribute-names '{"#status":"status"}' \
  --max-items 10
```

**Expected fields**:
- `subscription_id`: Your subscription ID
- `user_id`: Your user GUID  
- `status`: "processed"
- `processed_at`: Recent timestamp

### Phase 5: Performance Tests

#### Test 5.1: Latency Test

**Purpose**: Verify notifications are processed quickly.

**Steps**:
1. **Note exact time**: Record when you send test email
2. **Send email** with subject `"Latency Test - $(date)"`
3. **Check processing time** in CloudWatch logs
4. **Verify < 60 seconds** end-to-end (email sent → processed log entry)

#### Test 5.2: 48-Hour Stability Test

**Purpose**: Verify system runs continuously without errors.

**Steps**:
1. **Send 1 email every 2 hours** for 48 hours (total: 24 emails)
2. **Monitor error rates** in CloudWatch
3. **Verify 0 messages in DLQ** after 48 hours
4. **Check all 24 emails processed** successfully

```bash
# After 48 hours, verify processing count
aws logs filter-log-events \
  --log-group-name "/aws/lambda/amplify-assistants-office365-office365-dev-emailProcessor" \
  --filter-pattern "\"EMAIL RECEIVED\"" \
  --start-time $(date -d '48 hours ago' +%s)000 | grep "EMAIL RECEIVED" | wc -l
```

## Success Criteria Checklist

### ✅ Infrastructure Tests
- [ ] Webhook validation endpoint returns correct token  
- [ ] Subscription creation API works and returns subscription ID
- [ ] DynamoDB table stores subscription records
- [ ] SQS queues are created and accessible

### ✅ Email Processing Tests  
- [ ] Single email triggers notification within 60 seconds
- [ ] Email processor fetches and logs complete email details
- [ ] Multiple emails (5+) are all processed without loss
- [ ] Emails with attachments are handled correctly
- [ ] All processed emails appear in CloudWatch logs

### ✅ Error Handling Tests
- [ ] Invalid clientState notifications are rejected
- [ ] Malformed JSON is handled gracefully  
- [ ] Failed messages go to DLQ (not lost)
- [ ] No system crashes or unhandled exceptions

### ✅ Performance Tests
- [ ] End-to-end latency < 60 seconds per email
- [ ] System runs 48+ hours without errors
- [ ] Zero messages lost during high-volume periods
- [ ] Memory and timeout limits are adequate

### ✅ Monitoring Tests
- [ ] CloudWatch logs show detailed email information
- [ ] Lambda metrics show successful invocations
- [ ] DynamoDB contains all notification records
- [ ] SQS DLQ remains empty (no failed messages)

## Troubleshooting Common Issues

### Issue: No webhook notifications received

**Checks**:
```bash
# 1. Verify subscription is active
aws dynamodb get-item \
  --table-name "amplify-assistants-office365-office365-dev-email-subscriptions" \
  --key '{"subscription_id": {"S": "YOUR-SUBSCRIPTION-ID"}}'

# 2. Check subscription expiration in Graph API
curl -X GET "https://graph.microsoft.com/v1.0/subscriptions/YOUR-SUBSCRIPTION-ID" \
  -H "Authorization: Bearer {app-token}"

# 3. Test webhook endpoint directly
curl -X POST "https://{your-api-domain}/dev/integrations/email/webhook" \
  -H "Content-Type: application/json" \
  -d '{"test": "manual"}'
```

### Issue: Emails processed but missing details

**Checks**:
```bash
# Check Graph API permissions  
curl -X GET "https://graph.microsoft.com/v1.0/users/YOUR-USER-GUID/messages?$top=1" \
  -H "Authorization: Bearer {app-token}"

# Verify user GUID is correct
az ad user show --id "max.moundas@vanderbilt.edu" --query "id" --output tsv
```

### Issue: High latency (> 60 seconds)

**Checks**:
- Verify Lambda memory allocation (256MB minimum)
- Check Lambda cold start times in CloudWatch
- Monitor SQS queue depth during processing
- Verify Graph API response times

## Next Steps After Successful Testing

1. **Document any issues found** and solutions applied
2. **Benchmark performance numbers** for Phase 2 scaling
3. **Plan subscription renewal automation** (Phase 2)
4. **Design multi-user provisioning** (Phase 2)
5. **Integration with AI assistant APIs** (Phase 2)