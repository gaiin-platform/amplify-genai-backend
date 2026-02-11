# Rollback Runbook - Async RAG Architecture

**Purpose**: Detailed procedure for rolling back async RAG implementation if critical issues occur.

---

## 🚨 When to Rollback

Rollback immediately if:
- **Success rate drops below 80%** for > 15 minutes
- **Lambda errors exceed 10%** of invocations
- **DynamoDB throttling** occurs consistently
- **Data corruption** detected in database
- **Security breach** detected
- **Cost spike** > 3X expected rate

Monitor for 15 minutes before rollback unless:
- Security breach (rollback immediately)
- Data corruption (rollback immediately)

---

## 📋 Rollback Levels

### Level 1: Traffic Rollback (Recommended)
**Time**: 5-10 minutes
**Risk**: Low
**Scope**: Route traffic back to old system without infrastructure changes

### Level 2: Infrastructure Rollback
**Time**: 15-30 minutes
**Risk**: Medium
**Scope**: Remove new Lambda functions, keep data

### Level 3: Full Rollback + Data Cleanup
**Time**: 1-2 hours
**Risk**: High
**Scope**: Complete revert including database changes

---

## 🔄 Level 1: Traffic Rollback

### Step 1: Stop New Document Processing

```bash
# Pause SQS queues (stop new processing)
aws sqs set-queue-attributes \
  --queue-url <VDR_QUEUE_URL> \
  --attributes '{"ReceiveMessageWaitTimeSeconds":"0","VisibilityTimeout":"0"}'

aws sqs set-queue-attributes \
  --queue-url <TEXT_RAG_QUEUE_URL> \
  --attributes '{"ReceiveMessageWaitTimeSeconds":"0","VisibilityTimeout":"0"}'
```

**Verification**:
```bash
# Check queue is paused
aws sqs get-queue-attributes \
  --queue-url <VDR_QUEUE_URL> \
  --attribute-names All
```

### Step 2: Re-route S3 Trigger to Old Function

```bash
# Update S3 event notification
# Option A: Via AWS Console
# 1. Go to S3 bucket → Properties → Event notifications
# 2. Find RAG document upload trigger
# 3. Change destination from async_document_processor to process_document_for_rag

# Option B: Via AWS CLI
aws s3api put-bucket-notification-configuration \
  --bucket amplify-files-dev \
  --notification-configuration file://s3-notification-old.json
```

**s3-notification-old.json**:
```json
{
  "LambdaFunctionConfigurations": [
    {
      "Id": "RagDocumentProcessing",
      "LambdaFunctionArn": "arn:aws:lambda:region:account:function:amplify-rag-dev-process_document_for_rag",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {
        "Key": {
          "FilterRules": [
            {"Name": "prefix", "Value": ""},
            {"Name": "suffix", "Value": ".pdf"}
          ]
        }
      }
    }
  ]
}
```

**Verification**:
```bash
# Test old function
aws lambda invoke \
  --function-name amplify-rag-dev-process_document_for_rag \
  --payload '{"test": true}' \
  response.json

cat response.json
```

### Step 3: Verify Old System Working

```bash
# Upload test document
aws s3 cp test.pdf s3://amplify-files-dev/test-user/rollback-test.pdf

# Monitor old function logs
aws logs tail /aws/lambda/amplify-rag-dev-process_document_for_rag --follow

# Check document processed successfully (wait 2-3 minutes)
psql -h <RDS> -U <USER> -d <DB> -c \
  "SELECT id, bucket, key, created_at FROM documents ORDER BY created_at DESC LIMIT 5;"
```

**Expected**: Document appears in database within 3 minutes

### Step 4: Drain Remaining Messages

```bash
# Check messages in queue
aws sqs get-queue-attributes \
  --queue-url <VDR_QUEUE_URL> \
  --attribute-names ApproximateNumberOfMessages

# If messages remain, re-enable queues to drain
aws sqs set-queue-attributes \
  --queue-url <VDR_QUEUE_URL> \
  --attributes '{"ReceiveMessageWaitTimeSeconds":"20","VisibilityTimeout":"900"}'

# Monitor until queue empty
watch -n 5 "aws sqs get-queue-attributes \
  --queue-url <VDR_QUEUE_URL> \
  --attribute-names ApproximateNumberOfMessages \
  --query 'Attributes.ApproximateNumberOfMessages'"
```

### Step 5: Update Frontend (Optional)

If frontend is deployed with WebSocket integration:

```bash
# Option A: Environment variable rollback
# Update .env to use old polling method
REACT_APP_USE_WEBSOCKET=false

# Redeploy frontend
npm run build && aws s3 sync build/ s3://frontend-bucket/

# Option B: Feature flag
# If you have feature flags, disable WebSocket feature
```

**Verification**: Upload document via frontend, should work with old polling

---

## 🔧 Level 2: Infrastructure Rollback

**Use if**: Level 1 doesn't resolve issues or infrastructure is causing problems

### Step 1: Remove New Lambda Functions

```bash
# Remove new functions from serverless.yml
# Comment out or delete:
# - async_document_processor
# - vdr_processor
# - text_rag_processor
# - websocket_connect
# - websocket_disconnect
# - websocket_subscribe
# - websocket_default

# Redeploy
cd amplify-lambda
serverless deploy --stage dev
```

**Verification**:
```bash
serverless info --stage dev
# Should show only old functions
```

### Step 2: Delete SQS Queues (Optional)

```bash
# Delete queues if causing issues
aws sqs delete-queue --queue-url <VDR_QUEUE_URL>
aws sqs delete-queue --queue-url <VDR_DLQ_URL>
aws sqs delete-queue --queue-url <TEXT_RAG_QUEUE_URL>
aws sqs delete-queue --queue-url <TEXT_RAG_DLQ_URL>
```

### Step 3: Delete DynamoDB Tables (Optional)

```bash
# Only delete if tables are causing issues
aws dynamodb delete-table --table-name document-processing-status-dev
aws dynamodb delete-table --table-name websocket-connections-dev
```

**⚠️ Warning**: This deletes all status history. Export first if needed:

```bash
# Export table before deletion
aws dynamodb scan \
  --table-name document-processing-status-dev \
  --output json > status-export-$(date +%Y%m%d).json
```

### Step 4: Verify Old System

Same as Level 1, Step 3

---

## 🗑️ Level 3: Full Rollback + Data Cleanup

**Use if**: Database corruption or need complete revert

### Step 1: Complete Level 1 & 2

First complete Level 1 and Level 2 steps.

### Step 2: Backup Database

```bash
# Create RDS snapshot
aws rds create-db-snapshot \
  --db-instance-identifier rag-postgres-dev \
  --db-snapshot-identifier rag-rollback-$(date +%Y%m%d-%H%M%S)

# Wait for snapshot to complete
aws rds wait db-snapshot-available \
  --db-snapshot-identifier rag-rollback-$(date +%Y%m%d-%H%M%S)
```

### Step 3: Revert Database Schema

```sql
-- Connect to database
psql -h <RDS_ENDPOINT> -U <USERNAME> -d <DB_NAME>

-- Drop Hybrid Search tables
DROP TABLE IF EXISTS chunk_bm25_index CASCADE;
DROP TABLE IF EXISTS bm25_term_stats CASCADE;
DROP TABLE IF EXISTS document_bm25_metadata CASCADE;
DROP TABLE IF EXISTS hybrid_search_config CASCADE;

-- Drop VDR tables
DROP TABLE IF EXISTS document_vdr_pages CASCADE;

-- Remove pipeline_type column from documents
ALTER TABLE documents DROP COLUMN IF EXISTS pipeline_type;

-- Verify tables dropped
\dt
```

### Step 4: Clean Up Migrated Documents (If Needed)

```bash
# Run cleanup script
python migrations/cleanup_migration.py --confirm

# Or manual SQL
psql -h <RDS_ENDPOINT> -U <USERNAME> -d <DB_NAME> << EOF
-- Delete documents processed by new pipeline only
DELETE FROM documents
WHERE pipeline_type IN ('vdr', 'text_rag')
  AND created_at > '2025-02-11';  -- Deployment date

-- Verify deletion
SELECT COUNT(*) FROM documents;
EOF
```

### Step 5: Restore Old Query Handler (If Updated)

```bash
# Revert query handler changes
git checkout HEAD~1 -- amplify-lambda/rag/query_handler.py

# Redeploy
serverless deploy function -f query_handler --stage dev
```

---

## ✅ Post-Rollback Verification

### Checklist

- [ ] Old document processing working (upload test document)
- [ ] Documents appearing in database within 3 minutes
- [ ] No errors in old Lambda logs
- [ ] Query/search working
- [ ] Frontend functioning normally
- [ ] No DynamoDB throttling
- [ ] No SQS queue buildup
- [ ] CloudWatch alarms cleared
- [ ] Cost rate returned to normal
- [ ] Users notified (if applicable)

### Verification Commands

```bash
# 1. Test document upload
aws s3 cp test.pdf s3://amplify-files-dev/test-user/verify-$(date +%s).pdf

# 2. Check processing logs (should see activity)
aws logs tail /aws/lambda/amplify-rag-dev-process_document_for_rag --follow

# 3. Verify document in database
psql -h <RDS> -U <USER> -d <DB> -c \
  "SELECT COUNT(*) FROM documents WHERE created_at > NOW() - INTERVAL '10 minutes';"

# 4. Check query works
curl -X POST https://api.example.com/query \
  -H "Content-Type: application/json" \
  -d '{"query": "test query", "top_k": 5}'

# 5. Monitor error rate (should be < 1%)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=amplify-rag-dev-process_document_for_rag \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum
```

---

## 📊 Monitoring During Rollback

### Key Metrics to Watch

```bash
# Lambda error rate
aws cloudwatch get-metric-statistics \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Sum

# Success rate (should be > 95%)
# Documents processed / Documents uploaded

# Average processing time (should be < 180s for small docs)
aws logs filter-log-events \
  --log-group-name /aws/lambda/amplify-rag-dev-process_document_for_rag \
  --filter-pattern "Processing complete" \
  --start-time $(($(date +%s) - 600))000
```

---

## 🔍 Root Cause Analysis

After rollback, investigate:

1. **What triggered the rollback?**
   - Save CloudWatch logs: `aws logs create-export-task`
   - Save DynamoDB data: `aws dynamodb scan --table-name ... > backup.json`
   - Save RDS slow query log

2. **Identify the failure mode**
   - Lambda timeout?
   - Database connection pool exhaustion?
   - Queue message format error?
   - Memory leak?
   - Race condition?

3. **Document findings**
   - Create incident report
   - Update runbook with lessons learned
   - Plan fixes before redeployment

---

## 🚀 Re-deployment After Rollback

Before redeploying:

1. **Fix identified issues**
   - Code fixes
   - Configuration adjustments
   - Resource limit increases

2. **Test in staging**
   - Deploy to staging environment
   - Run full test suite
   - Load test with 100 concurrent uploads
   - Monitor for 24 hours

3. **Gradual rollout**
   - 10% traffic → Monitor 6 hours
   - 50% traffic → Monitor 12 hours
   - 100% traffic → Monitor 48 hours

4. **Have rollback ready**
   - Keep this runbook handy
   - Ensure team is available
   - Schedule deployment during low-traffic hours

---

## 📞 Escalation

If rollback fails or issues persist:

1. **Immediate**: Page on-call engineer
2. **Within 15 min**: Escalate to senior engineer
3. **Within 30 min**: Notify engineering manager
4. **Within 1 hour**: Consider complete service pause

**Emergency Contacts**:
- On-call: [Pager Duty/Phone]
- Engineering Lead: [Contact]
- AWS Support: Premium support ticket

---

## 📝 Rollback Log Template

Document every rollback:

```
ROLLBACK LOG
============
Date: 2025-02-XX
Time Started: HH:MM UTC
Time Completed: HH:MM UTC
Level: [1/2/3]
Initiated By: [Name]
Reason: [Brief description]

ACTIONS TAKEN:
1. [Action] - [Time] - [Result]
2. [Action] - [Time] - [Result]
...

VERIFICATION:
- [ ] Old system working
- [ ] Error rate normal
- [ ] Users notified

ROOT CAUSE:
[Detailed analysis]

NEXT STEPS:
[Plans for fix and redeployment]
```

---

**Last Updated**: 2025-02-11
**Version**: 1.0
**Maintained By**: DevOps Team
