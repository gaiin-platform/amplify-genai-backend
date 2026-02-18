# Model Alias System

## Overview

The Model Alias system provides user-friendly, auto-updating aliases for AWS Bedrock model IDs, solving the problem of silent model degradation when models are deprecated.

### Problem Statement

**Before**: Users had to specify complex model IDs like `us.anthropic.claude-opus-4-6-v1:0`. When models were deprecated, deployments would silently fall back to older models, causing performance degradation without notification.

**After**: Users can specify simple aliases like `opus-latest`, which automatically resolve to the latest model version. Updates are controlled via deployment, ensuring they're testable in dev/staging before production.

### Benefits

- ✅ **User-friendly names**: `opus-latest` instead of `us.anthropic.claude-opus-4-6-v1:0`
- ✅ **Auto-updating**: Aliases update when maintainers deploy new versions
- ✅ **Backward compatible**: Existing exact model IDs continue working
- ✅ **Deployment-controlled**: Updates happen via code deploy (testable, auditable)
- ✅ **Fast**: <1ms overhead for alias resolution
- ✅ **Zero breaking changes**: Completely optional feature

## Architecture

### Flow Diagram

```
User Request (model.id = "opus-latest")
    ↓
Router (line 229)
    ↓
resolveModelAlias() → {resolvedId: "us.anthropic.claude-opus-4-6-v1:0", wasAlias: true}
    ↓
Update options.model.id = resolved ID
    ↓
Validation (uses resolved ID)
    ↓
Bedrock API (receives exact model ID)
```

### Key Components

1. **model_aliases.json** (`chat-billing/model_rates/model_aliases.json`)
   - JSON file containing alias mappings
   - Version controlled via git
   - Loaded once at module initialization (cached)

2. **modelAliases.js** (`amplify-lambda-js/models/modelAliases.js`)
   - Core resolution logic
   - Functions: `resolveModelAlias()`, `isAlias()`, `getAllAliases()`, `getReverseMapping()`
   - Graceful error handling

3. **router.js Integration** (`amplify-lambda-js/router.js` line 229)
   - Single centralized integration point
   - Resolves alias BEFORE validation
   - Tracks alias usage for analytics

4. **API Endpoints** (`chat-billing/service/core.py`)
   - `GET /model_aliases` - Returns all alias mappings
   - `GET /models_with_aliases` - Enhanced model list with alias info

## Available Aliases

### Current Aliases (as of 2026-02-18)

| Alias | Resolves To | Description | Tier |
|-------|-------------|-------------|------|
| `opus-latest` | `us.anthropic.claude-opus-4-6-v1:0` | Latest Opus (simplified) | Premium |
| `claude-opus-latest` | `us.anthropic.claude-opus-4-6-v1:0` | Latest Claude Opus | Premium |
| `sonnet-latest` | `us.anthropic.claude-sonnet-4-6-v1:0` | Latest Sonnet (simplified) | Balanced |
| `claude-sonnet-latest` | `us.anthropic.claude-sonnet-4-6-v1:0` | Latest Claude Sonnet | Balanced |
| `haiku-latest` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Latest Haiku (simplified) | Fast |
| `claude-haiku-latest` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Latest Claude Haiku | Fast |

## How to Use Aliases

### For Users

In your chat request, use the alias instead of the full model ID:

```json
{
  "model": {
    "id": "opus-latest"
  },
  "messages": [
    {"role": "user", "content": "Hello!"}
  ]
}
```

The system will automatically resolve `opus-latest` to the current latest Opus model.

### For Developers

```javascript
import { resolveModelAlias } from './models/modelAliases.js';

// Resolve an alias
const result = resolveModelAlias('opus-latest');
console.log(result.resolvedId);  // "us.anthropic.claude-opus-4-6-v1:0"
console.log(result.wasAlias);     // true
console.log(result.aliasInfo);    // { alias: "opus-latest", category: "claude", tier: "premium" }

// Pass through non-alias
const result2 = resolveModelAlias('us.anthropic.claude-3-5-sonnet-20241022-v2:0');
console.log(result2.resolvedId);  // "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
console.log(result2.wasAlias);    // false
```

## How to Update Aliases

### When to Update

Update aliases when:
- A new model version is released (e.g., Claude Opus 4.7)
- An existing model is deprecated
- You want to change which model an alias points to

### Update Process

1. **Edit the JSON file**
   ```bash
   vim amplify-genai-backend/chat-billing/model_rates/model_aliases.json
   ```

2. **Update the mapping**
   ```json
   {
     "opus-latest": {
       "resolves_to": "us.anthropic.claude-opus-4-7-v1:0",  // NEW VERSION
       "description": "Latest Opus (simplified naming)",
       "category": "claude",
       "tier": "premium"
     }
   }
   ```

3. **Update version and date**
   ```json
   {
     "version": "1.1.0",
     "lastUpdated": "2026-03-01"
   }
   ```

4. **Test locally**
   ```bash
   cd amplify-genai-backend/amplify-lambda-js
   node models/__tests__/manual-test-aliases.js
   ```

5. **Commit changes**
   ```bash
   git add chat-billing/model_rates/model_aliases.json
   git commit -m "Update opus-latest to Claude Opus 4.7"
   ```

6. **Deploy to dev**
   ```bash
   cd amplify-genai-backend
   serverless amplify-lambda-js:deploy --stage dev
   ```

7. **Test in dev**
   ```bash
   # Make a chat request with alias
   curl -X POST https://dev-api.amplify/chat \
     -H "Authorization: Bearer $DEV_TOKEN" \
     -d '{"model": {"id": "opus-latest"}, "messages": [...]}'

   # Check CloudWatch logs
   aws logs tail /aws/lambda/amplify-lambda-js-dev-chat --follow | grep "alias resolved"
   ```

8. **Deploy to staging → production**
   ```bash
   serverless amplify-lambda-js:deploy --stage staging
   # Test thoroughly
   serverless amplify-lambda-js:deploy --stage prod
   ```

## API Reference

### `resolveModelAlias(modelIdOrAlias)`

Resolves a model ID or alias to the actual model ID.

**Parameters:**
- `modelIdOrAlias` (string): The model ID or alias to resolve

**Returns:**
```typescript
{
  resolvedId: string,      // The actual model ID to use
  wasAlias: boolean,       // True if resolution occurred
  aliasInfo: {            // Metadata (null if not an alias)
    alias: string,
    category: string,
    tier: string,
    description: string
  } | null
}
```

**Examples:**
```javascript
resolveModelAlias('opus-latest')
// → { resolvedId: "us.anthropic.claude-opus-4-6-v1:0", wasAlias: true, aliasInfo: {...} }

resolveModelAlias('us.anthropic.claude-opus-4-6-v1:0')
// → { resolvedId: "us.anthropic.claude-opus-4-6-v1:0", wasAlias: false, aliasInfo: null }

resolveModelAlias(null)
// → { resolvedId: null, wasAlias: false, aliasInfo: null }
```

### `isAlias(modelId)`

Check if a string is a known alias.

**Parameters:**
- `modelId` (string): The model ID to check

**Returns:** `boolean`

**Example:**
```javascript
isAlias('opus-latest')  // true
isAlias('us.anthropic.claude-opus-4-6-v1:0')  // false
```

### `getAllAliases()`

Get all available aliases.

**Returns:**
```typescript
{
  error: boolean,
  message?: string,  // Error message if error=true
  aliases: {
    [aliasName: string]: {
      resolves_to: string,
      description: string,
      category: string,
      tier: string
    }
  }
}
```

### `getReverseMapping()`

Get reverse mapping (model ID → aliases that point to it).

**Returns:**
```typescript
{
  [modelId: string]: Array<{
    alias: string,
    description: string,
    category: string,
    tier: string
  }>
}
```

## REST API Endpoints

### GET /model_aliases

Returns all model alias mappings.

**Authentication:** Required (validated read operation)

**Response:**
```json
{
  "success": true,
  "data": {
    "version": "1.0.0",
    "lastUpdated": "2026-02-18",
    "aliases": {
      "opus-latest": {
        "resolves_to": "us.anthropic.claude-opus-4-6-v1:0",
        "description": "Latest Opus (simplified naming)",
        "category": "claude",
        "tier": "premium"
      }
    }
  }
}
```

### GET /models_with_aliases

Returns available models enhanced with alias information.

**Authentication:** Required (validated read operation)

**Response:**
```json
{
  "success": true,
  "data": {
    "models": [
      {
        "id": "us.anthropic.claude-opus-4-6-v1:0",
        "name": "Claude Opus 4.6",
        "aliases": [
          {
            "alias": "opus-latest",
            "description": "Latest Opus (simplified naming)",
            "category": "claude",
            "tier": "premium"
          }
        ]
      }
    ]
  }
}
```

## Troubleshooting

### Alias not resolving

**Symptom:** Alias passes through unchanged instead of resolving

**Possible causes:**
1. Alias not in `model_aliases.json`
2. JSON file syntax error
3. Module cache issue (after hot reload)

**Solution:**
```bash
# Check if alias exists in JSON
cat chat-billing/model_rates/model_aliases.json | grep "your-alias"

# Validate JSON syntax
python3 -m json.tool chat-billing/model_rates/model_aliases.json

# Check logs
aws logs tail /aws/lambda/amplify-lambda-js-dev-chat --follow | grep modelAliases
```

### JSON file not loading

**Symptom:** Logs show "Failed to load model aliases"

**Possible causes:**
1. File path incorrect
2. File permissions issue
3. JSON syntax error

**Solution:**
```bash
# Check file exists
ls -la amplify-genai-backend/chat-billing/model_rates/model_aliases.json

# Validate JSON
python3 -m json.tool chat-billing/model_rates/model_aliases.json

# Check Lambda environment
aws lambda get-function --function-name amplify-lambda-js-prod-chat
```

### Alias resolves to wrong model

**Symptom:** Alias resolves but to an unexpected model ID

**Possible causes:**
1. Stale deployment (old version still running)
2. Wrong environment variable

**Solution:**
```bash
# Verify deployed version
curl https://api.amplify/model_aliases | jq '.data.version'

# Check CloudWatch logs for resolution
aws logs tail /aws/lambda/amplify-lambda-js-prod-chat --follow | grep "alias resolved"
```

## Performance

- **Load time**: ~2ms (on cold start, cached thereafter)
- **Resolution time**: <0.01ms per call (in-memory lookup)
- **Memory overhead**: <1KB (6 aliases)
- **API latency impact**: Negligible (<0.1%)

## Testing

### Run Unit Tests

```bash
cd amplify-genai-backend/amplify-lambda-js

# Manual tests (no dependencies needed)
node models/__tests__/manual-test-aliases.js

# Jest tests (requires Jest installation)
npm install --save-dev jest
npm test models/__tests__/modelAliases.test.js
```

### Integration Testing

```bash
# Test in local development
cd amplify-genai-backend/amplify-lambda-js
node local/localServer.js

# In another terminal, test with curl
curl -X POST http://localhost:3000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": {"id": "opus-latest"},
    "messages": [{"role": "user", "content": "test"}]
  }'
```

## Rollback Plan

If issues arise after deployment:

### Option 1: Quick Fix (Remove Resolution)

Edit `router.js` to bypass alias resolution:

```javascript
// Comment out alias resolution
// const { resolvedId: modelId, wasAlias, aliasInfo } = resolveModelAlias(rawModelId);
const modelId = rawModelId;  // Pass through unchanged
```

Redeploy:
```bash
serverless amplify-lambda-js:deploy --stage prod
```

### Option 2: Git Revert

```bash
git revert <commit-hash>
git push
serverless amplify-lambda-js:deploy --stage prod
```

## Future Enhancements

Potential improvements:

1. **Admin UI for alias management**
   - Edit aliases via web interface
   - No code deployment needed

2. **Deprecation warnings**
   - Notify users when using deprecated aliases
   - Suggest migration to new aliases

3. **Analytics dashboard**
   - Track alias usage metrics
   - Identify popular aliases

4. **Dynamic alias loading**
   - Load from DynamoDB instead of JSON file
   - Enable runtime updates without deployment

5. **Alias versioning**
   - Allow users to pin to specific alias versions
   - Example: `opus-latest@v1.0` vs `opus-latest@v1.1`

## Security Considerations

- ✅ **No user input**: Aliases are pre-defined in config file
- ✅ **Validation still occurs**: Resolved IDs go through normal validation
- ✅ **Read-only**: No ability to create/modify aliases via API (admin only via code)
- ✅ **Audit trail**: All alias changes tracked via git commits
- ✅ **No injection risk**: Simple dictionary lookup, no dynamic code execution

## Support

For issues or questions:

1. **Check logs**: CloudWatch Logs for resolution debugging
2. **Run tests**: `node models/__tests__/manual-test-aliases.js`
3. **GitHub Issues**: https://github.com/gaiin-platform/amplify-genai-backend/issues
4. **Documentation**: This file

## License

Copyright (c) 2024 Vanderbilt University
Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas
