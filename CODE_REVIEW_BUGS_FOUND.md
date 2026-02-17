# Code Review: Bugs & Issues Found ("Code Farts")

## Critical Bugs 🔴

### 1. **NoneType Error in `status_manager.py` line 257**

**File:** `amplify-lambda/rag/status_manager.py:257`

**Issue:**
```python
status = stage if stage else get_document_status(bucket, key).get('status', DocumentStatus.PROCESSING_STARTED)
```

**Problem:** `get_document_status()` can return `None` (see line 127), but we're calling `.get()` on it without checking. This will cause:
```
AttributeError: 'NoneType' object has no attribute 'get'
```

**Fix:**
```python
def update_progress(bucket, key, progress, stage=None, details=None):
    """Update progress percentage"""
    metadata = {'progress': progress}

    if details:
        metadata.update(details)

    # FIX: Handle None return value
    if stage:
        status = stage
    else:
        current_status = get_document_status(bucket, key)
        status = current_status.get('status', DocumentStatus.PROCESSING_STARTED) if current_status else DocumentStatus.PROCESSING_STARTED

    update_document_status(bucket, key, status, metadata)
```

---

## High Priority Issues 🟠

### 2. **Missing Frontend Component Files**

**Referenced but not created:**
- `amplify-genai-frontend/components/Documents/DocumentUploadProgress.tsx`
- `amplify-genai-frontend/components/Documents/UploadQueueManager.tsx`
- `amplify-genai-frontend/services/documentStatusService.ts`
- `amplify-genai-frontend/services/fileServiceWebSocket.ts`

**Problem:** Frontend code in `fileServiceRouted.ts` imports these files:
```typescript
import { documentStatusService } from './documentStatusService';
```

But the file doesn't exist! This will cause compilation errors.

**Status:** These were mentioned in documentation but never created. Need to implement them.

---

### 3. **Serverless Config: Missing VDR Requirements Layer**

**File:** `serverless-async-separate.yml`

**Issue:** VDR processor uses heavy ML libraries (transformers, torch, colpali) but the layer definition only includes `requirements.txt`:

```yaml
layers:
  PythonRequirementsLambdaLayer:
    path: layers/python-requirements
    # This will be too large for Lambda!
```

**Problem:**
- VDR model dependencies (torch, transformers) are **HUGE** (1-2GB)
- Lambda layers have a **250MB unzipped** limit
- This will fail deployment

**Fix Options:**
1. Use Lambda container images (10GB limit):
```yaml
functions:
  async-v2-vdr-processor:
    image:
      name: vdr-processor
      uri: ${aws:accountId}.dkr.ecr.${aws:region}.amazonaws.com/vdr-processor:latest
```

2. Use ECS Fargate for VDR processing (unlimited size)

3. Use pre-built model layer from S3

**Recommendation:** Migrate VDR to ECS Fargate for production (no Lambda limits).

---

### 4. **WebSocket API Endpoint Configuration Issue**

**File:** `serverless-async-separate.yml:40-41`

**Issue:**
```yaml
WEBSOCKET_API_ENDPOINT:
  Fn::Sub: "wss://${WebSocketApi}.execute-api.${AWS::Region}.amazonaws.com/${sls:stage}"
```

**Problem:** This constructs the WebSocket endpoint URL BUT the `WebSocketApi` resource is defined later in the file. This works in CloudFormation, but the endpoint needs to be in HTTP format for the API Gateway Management API client, not WSS format.

**Fix:**
```yaml
# For Lambda functions to send messages, use HTTP endpoint
WEBSOCKET_API_ENDPOINT:
  Fn::Sub: "https://${WebSocketApi}.execute-api.${AWS::Region}.amazonaws.com/${sls:stage}"

# For frontend connections, use WSS endpoint (in outputs section)
```

**Impact:** `status_manager.py` line 49-52 tries to use this endpoint with `apigatewaymanagementapi` client, which expects HTTPS, not WSS.

---

### 5. **Missing Error Handling in `async_processor.py`**

**File:** `amplify-lambda/rag/async_processor.py:48`

**Issue:**
```python
for record in event["Records"]:
    try:
        # Parse S3 event
        s3_event = json.loads(record["body"])
        s3_record = s3_event["Records"][0] if "Records" in s3_event else s3_event
```

**Problem:** What if `record["body"]` is not valid JSON? What if it's already a dict (Lambda can invoke with dict or JSON string)?

**Fix:**
```python
for record in event["Records"]:
    try:
        # Parse SQS message body
        body = record["body"]
        s3_event = json.loads(body) if isinstance(body, str) else body

        # Handle both direct S3 events and wrapped events
        if "Records" in s3_event:
            s3_record = s3_event["Records"][0]
        else:
            s3_record = s3_event

        # Validate required fields
        if "s3" not in s3_record:
            logger.error(f"Invalid event format: missing 's3' field")
            continue
```

---

### 6. **Race Condition in WebSocket Connection Tracking**

**File:** `amplify-lambda/websocket/handlers.py:127-134`

**Issue:**
```python
table.update_item(
    Key={'connectionId': connection_id},
    UpdateExpression='SET statusId = :sid, subscribedAt = :ts',
    ExpressionAttributeValues={
        ':sid': status_id,
        ':ts': datetime.utcnow().isoformat()
    }
)
```

**Problem:** If the connection was just established, there's a small window where:
1. User connects → connection stored in DynamoDB
2. User subscribes immediately → update_item called
3. DynamoDB eventual consistency means item might not be visible yet

**Fix:** Use `ConditionExpression` to ensure connection exists:
```python
table.update_item(
    Key={'connectionId': connection_id},
    UpdateExpression='SET statusId = :sid, subscribedAt = :ts',
    ConditionExpression='attribute_exists(connectionId)',
    ExpressionAttributeValues={
        ':sid': status_id,
        ':ts': datetime.utcnow().isoformat()
    }
)
```

---

## Medium Priority Issues 🟡

### 7. **Frontend: Empty String Handling in Beta Users**

**File:** `amplify-genai-frontend/services/ragRoutingService.ts:55`

**Issue:**
```typescript
betaUsers: process.env.REACT_APP_ASYNC_RAG_BETA_USERS?.split(',') || [],
```

**Problem:** If `REACT_APP_ASYNC_RAG_BETA_USERS=""` (empty string), this produces `['']` (array with empty string), not `[]` (empty array).

**Fix:**
```typescript
betaUsers: process.env.REACT_APP_ASYNC_RAG_BETA_USERS
  ?.split(',')
  .filter(email => email.trim().length > 0) || [],
```

---

### 8. **Frontend: No Cleanup of WebSocket on Unmount**

**File:** `amplify-genai-frontend/services/fileServiceRouted.ts:160-164`

**Issue:**
```typescript
documentStatusService.subscribe(statusId, (update) => {
  const progress = update.metadata?.progress || 40;
  onProgress?.(progress);
  // ...
});
```

**Problem:** No cleanup! If the component unmounts, the WebSocket subscription remains active, causing memory leaks.

**Fix:**
```typescript
const unsubscribe = documentStatusService.subscribe(statusId, (update) => {
  const progress = update.metadata?.progress || 40;
  onProgress?.(progress);
});

// Store unsubscribe function and call on error/completion
// Or return it to caller
```

---

### 9. **TypeScript: Missing Error Type Annotation**

**File:** `amplify-genai-frontend/services/fileServiceRouted.ts:196`

**Issue:**
```typescript
} catch (error) {
    console.error('[Async V2 Pipeline] Error:', error);
    return {
      // ...
      error: error instanceof Error ? error.message : 'Unknown error',
    };
}
```

**Problem:** TypeScript doesn't know what type `error` is. Should use proper type guard.

**Fix:**
```typescript
} catch (error: unknown) {
    console.error('[Async V2 Pipeline] Error:', error);

    const errorMessage = error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : 'Unknown error';

    return {
      // ...
      error: errorMessage,
    };
}
```

---

### 10. **Missing DynamoDB Index in Serverless Config**

**File:** `serverless-async-separate.yml:195-201`

**Issue:**
```yaml
GlobalSecondaryIndexes:
  - IndexName: StatusIdIndex
    KeySchema:
      - AttributeName: statusId
        KeyType: HASH
    Projection:
      ProjectionType: ALL
```

**Problem:** The `StatusIdIndex` is queried in `status_manager.py:202-206`, but there's no TTL on this index. If connections go stale (client crashes), they remain in the index forever.

**Fix:** Already has TTL on main table, but should also add a secondary index on `user` + `connectedAt` for cleanup queries:
```yaml
- IndexName: UserConnectionsIndex
  KeySchema:
    - AttributeName: user
      KeyType: HASH
    - AttributeName: connectedAt
      KeyType: RANGE
  Projection:
    ProjectionType: ALL
```

---

## Low Priority Issues / Code Smells 🟢

### 11. **Magic Numbers in VDR Pipeline**

**File:** `amplify-lambda/vdr/vdr_pipeline.py:131`

**Issue:**
```python
progress = 30 + int((page_num / num_pages) * 50)  # 30% → 80%
```

**Problem:** Magic numbers (`30`, `50`) are hardcoded. Should be constants.

**Fix:**
```python
PROGRESS_EMBEDDING_START = 30
PROGRESS_EMBEDDING_RANGE = 50

progress = PROGRESS_EMBEDDING_START + int((page_num / num_pages) * PROGRESS_EMBEDDING_RANGE)
```

---

### 12. **Inconsistent Error Logging**

**Throughout codebase**

**Issue:** Some functions use `logger.error()`, some use `logger.warning()`, some use `print()`.

**Fix:** Standardize on `logger.error()` for all errors with proper context.

---

### 13. **No Input Validation on Query Handler**

**File:** `amplify-lambda/rag/query_handler_hybrid.py:76-81`

**Issue:**
```python
top_k = body.get('top_k', 10)
dense_weight = body.get('dense_weight', 0.7)
sparse_weight = body.get('sparse_weight', 0.3)
```

**Problem:** No validation! User could send:
- `top_k: -1` (negative)
- `top_k: 999999` (DOS attack)
- `dense_weight: 5.0` (invalid weight)

**Fix:**
```python
top_k = max(1, min(body.get('top_k', 10), 100))  # Clamp to 1-100
dense_weight = max(0.0, min(body.get('dense_weight', 0.7), 1.0))
sparse_weight = max(0.0, min(body.get('sparse_weight', 0.3), 1.0))

# Normalize weights if they don't sum to 1.0
total_weight = dense_weight + sparse_weight
if total_weight > 0:
    dense_weight /= total_weight
    sparse_weight /= total_weight
```

---

### 14. **Frontend: Global Window Object Pollution**

**File:** `amplify-genai-frontend/services/ragRoutingService.ts:443-451`

**Issue:**
```typescript
if (typeof window !== 'undefined') {
  (window as any).__ragRouting = {
    routeDocument,
    forceRoutingOverride,
    // ...
  };
}
```

**Problem:** Pollutes global namespace. Could conflict with other libraries.

**Fix:** Use `window._DEBUG` namespace:
```typescript
if (typeof window !== 'undefined' && process.env.NODE_ENV === 'development') {
  (window as any)._DEBUG = (window as any)._DEBUG || {};
  (window as any)._DEBUG.ragRouting = {
    routeDocument,
    forceRoutingOverride,
    // ...
  };
}
```

---

### 15. **Missing TypeScript Return Type Annotations**

**File:** `amplify-genai-frontend/services/ragRoutingService.ts:224`

**Issue:**
```typescript
export function getPipelineEndpoints(pipeline: RagPipeline) {
  // No return type specified
```

**Fix:**
```typescript
interface PipelineEndpoints {
  processUrl: string | null;
  queryUrl: string;
  websocketUrl: string | null;
}

export function getPipelineEndpoints(pipeline: RagPipeline): PipelineEndpoints {
```

---

## Documentation Issues 📝

### 16. **Incorrect API Path in Documentation**

**File:** `SEPARATE_DEPLOYMENT_GUIDE.md:305`

**Issue:** Documentation says:
```markdown
POST /api/v2/process-document
```

But serverless config defines:
```yaml
path: /v2/process-document
```

These are different! (missing `/api` prefix)

**Fix:** Update docs to match actual endpoint: `/v2/process-document`

---

### 17. **Missing Prerequisites in Deployment Guide**

**File:** `SEPARATE_DEPLOYMENT_GUIDE.md`

**Issue:** Missing these prerequisites:
- Python 3.11 installed locally
- Node.js 18+ for Serverless Framework
- Docker running (for layer packaging)
- PostgreSQL access credentials

---

## Security Issues 🔒

### 18. **No Rate Limiting on WebSocket Connections**

**File:** `amplify-lambda/websocket/handlers.py:25-56`

**Issue:** No rate limiting! A malicious user could:
- Open 10,000 WebSocket connections
- Spam subscribe/unsubscribe messages
- DOS the system

**Fix:** Add rate limiting:
```python
def connect(event, context):
    # Check connection count for this user
    user_id = query_params.get('user')

    if user_id:
        existing_connections = count_user_connections(user_id)
        if existing_connections >= MAX_CONNECTIONS_PER_USER:
            logger.warning(f"User {user_id} exceeded connection limit")
            return {
                'statusCode': 429,
                'body': json.dumps({'error': 'Too many connections'})
            }
```

---

### 19. **No Input Sanitization in Query Handler**

**File:** `amplify-lambda/rag/query_handler_hybrid.py:69`

**Issue:**
```python
query = body.get('query')
```

**Problem:** Query string is directly passed to database/embeddings without sanitization. Could enable:
- SQL injection (if query builder doesn't escape)
- Prompt injection
- Path traversal

**Fix:** Add input sanitization:
```python
import re

def sanitize_query(query: str, max_length: int = 500) -> str:
    """Sanitize user query input"""
    if not query:
        return ""

    # Limit length
    query = query[:max_length]

    # Remove control characters
    query = re.sub(r'[\x00-\x1F\x7F]', '', query)

    # Remove SQL injection attempts
    query = re.sub(r'(--|;|\/\*|\*\/)', '', query)

    return query.strip()

query = sanitize_query(body.get('query'))
```

---

### 20. **WebSocket Authentication Not Enforced**

**File:** `amplify-lambda/websocket/handlers.py:36`

**Issue:**
```python
user_id = query_params.get('user')  # Just trust the user parameter?!
```

**Problem:** Any user can claim to be any other user! No authentication!

**Fix:** Verify JWT token:
```python
def connect(event, context):
    # Extract token from query string
    token = query_params.get('token')

    if not token:
        return {
            'statusCode': 401,
            'body': json.dumps({'error': 'Authentication required'})
        }

    # Verify token (use auth.py functions)
    try:
        user_info = verify_jwt_token(token)
        user_id = user_info['user_id']
    except Exception as e:
        logger.error(f"Auth failed: {str(e)}")
        return {
            'statusCode': 401,
            'body': json.dumps({'error': 'Invalid token'})
        }
```

---

## Performance Issues ⚡

### 21. **N+1 Query Problem in Status Manager**

**File:** `amplify-lambda/rag/status_manager.py:185-220`

**Issue:**
```python
def get_active_connections(status_id, user=None):
    # Query connections subscribed to this status_id
    response = connections_table.query(
        IndexName='StatusIdIndex',
        KeyConditionExpression='statusId = :sid',
        # ...
    )
```

**Problem:** This queries DynamoDB for EVERY status update. If processing 1000-page document with status updates every 10 pages, that's 100 DynamoDB queries.

**Optimization:** Cache active connections for 30 seconds:
```python
from functools import lru_cache
from datetime import datetime, timedelta

_connection_cache = {}
CACHE_TTL = 30  # seconds

def get_active_connections_cached(status_id, user=None):
    cache_key = f"{status_id}:{user or 'all'}"

    # Check cache
    if cache_key in _connection_cache:
        cached_data, timestamp = _connection_cache[cache_key]
        if (datetime.utcnow() - timestamp).total_seconds() < CACHE_TTL:
            return cached_data

    # Cache miss - query database
    connections = get_active_connections(status_id, user)
    _connection_cache[cache_key] = (connections, datetime.utcnow())

    return connections
```

---

## Summary

**Total Issues Found: 21**

- 🔴 Critical Bugs: 1
- 🟠 High Priority: 5
- 🟡 Medium Priority: 4
- 🟢 Low Priority/Code Smells: 4
- 📝 Documentation: 2
- 🔒 Security: 3
- ⚡ Performance: 2

## Recommended Action Plan

### Immediate (Before Deployment):
1. ✅ Fix Critical Bug #1 (NoneType error)
2. ✅ Create missing frontend files (#2)
3. ✅ Fix WebSocket endpoint config (#4)
4. ✅ Add WebSocket authentication (#20)

### Before Production:
5. ✅ Fix VDR layer size issue (#3) - Use ECS Fargate
6. ✅ Add input validation (#13, #19)
7. ✅ Add rate limiting (#18)
8. ✅ Fix error handling (#5, #6)

### Nice to Have:
9. ✅ Clean up code smells (#11-15)
10. ✅ Update documentation (#16-17)
11. ✅ Optimize performance (#21)

---

**Review completed with "ultra think" mode** ✅

All issues documented with severity, location, problem explanation, and concrete fixes provided.
