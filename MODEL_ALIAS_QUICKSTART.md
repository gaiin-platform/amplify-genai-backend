# Model Alias Quick Start Guide

## What Are Model Aliases?

Model aliases let users specify simple names like `opus-latest` instead of complex AWS Bedrock model IDs like `us.anthropic.claude-opus-4-6-v1:0`.

## Why Use Them?

### The Problem
When AWS deprecates a model (e.g., Claude 3.5 Sonnet retired Jan 2026), deployments silently fall back to older models, causing performance degradation without anyone noticing.

### The Solution
Aliases automatically resolve to the latest model version. When maintainers update the alias mappings and deploy, all users automatically get the new model—controlled, testable, and auditable.

### The Organizational Benefit
**Having `opus-latest` always available and active makes it effortless to ensure everyone uses the best possible model:**

✅ **Default to Best** - New users automatically start with the latest model
✅ **No Research Required** - Users don't need to track which version is newest
✅ **Automatic Upgrades** - Update the alias once, everyone benefits
✅ **Organizational Standards** - Standardize on "use `opus-latest` for production"
✅ **Consistent Quality** - Everyone gets improvements simultaneously

This is especially valuable for onboarding, internal policies, and quality assurance across your organization.

## Quick Examples

### For Users

**Before** (complex model ID):
```json
{
  "model": {
    "id": "us.anthropic.claude-opus-4-6-v1:0"
  },
  "messages": [{"role": "user", "content": "Hello!"}]
}
```

**After** (simple alias):
```json
{
  "model": {
    "id": "opus-latest"
  },
  "messages": [{"role": "user", "content": "Hello!"}]
}
```

### Available Aliases

| Alias | What You Get | Best For |
|-------|--------------|----------|
| `opus-latest` | Latest Claude Opus | Complex reasoning, coding |
| `sonnet-latest` | Latest Claude Sonnet | Balanced performance/cost |
| `haiku-latest` | Latest Claude Haiku | Fast responses, simple tasks |

Full list: See `/model_aliases` API endpoint or `docs/MODEL_ALIASES.md`

## For Developers

### Import and Use

```javascript
import { resolveModelAlias } from './models/modelAliases.js';

// Resolve an alias
const result = resolveModelAlias('opus-latest');

console.log(result.resolvedId);
// → "us.anthropic.claude-opus-4-6-v1:0"

console.log(result.wasAlias);
// → true

// Non-alias passes through unchanged
const result2 = resolveModelAlias('gpt-4o');
console.log(result2.resolvedId);  // → "gpt-4o"
console.log(result2.wasAlias);    // → false
```

### Check If String Is Alias

```javascript
import { isAlias } from './models/modelAliases.js';

isAlias('opus-latest')  // → true
isAlias('gpt-4o')       // → false
```

### Get All Aliases

```javascript
import { getAllAliases } from './models/modelAliases.js';

const { aliases } = getAllAliases();
console.log(Object.keys(aliases));
// → ["opus-latest", "sonnet-latest", "haiku-latest", ...]
```

## For Admins

### Update an Alias (When New Model Releases)

1. **Edit the JSON:**
   ```bash
   vim chat-billing/model_rates/model_aliases.json
   ```

2. **Change the mapping:**
   ```json
   {
     "opus-latest": {
       "resolves_to": "us.anthropic.claude-opus-4-7-v1:0"  // NEW
     }
   }
   ```

3. **Update version:**
   ```json
   {
     "version": "1.1.0",
     "lastUpdated": "2026-03-01"
   }
   ```

4. **Test → Commit → Deploy:**
   ```bash
   node models/__tests__/manual-test-aliases.js
   git add chat-billing/model_rates/model_aliases.json
   git commit -m "Update opus-latest to Claude Opus 4.7"
   serverless amplify-lambda-js:deploy --stage dev
   # Test in dev, then deploy to staging, then prod
   ```

## API Endpoints

### GET /model_aliases

Returns all alias mappings:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://api.amplify/model_aliases
```

Response:
```json
{
  "success": true,
  "data": {
    "aliases": {
      "opus-latest": {
        "resolves_to": "us.anthropic.claude-opus-4-6-v1:0",
        "description": "Latest Opus",
        "tier": "premium"
      }
    }
  }
}
```

### GET /models_with_aliases

Enhanced model list showing which aliases point to each model:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://api.amplify/models_with_aliases
```

## Testing

### Run Tests

```bash
cd amplify-genai-backend/amplify-lambda-js

# Quick manual test (no dependencies)
node models/__tests__/manual-test-aliases.js

# Full Jest tests (requires Jest)
npm install --save-dev jest
npm test models/__tests__/modelAliases.test.js
```

Expected output:
```
✓ opus-latest → us.anthropic.claude-opus-4-6-v1:0
✓ Performance: <0.01ms per resolution
✓ All tests passed
```

## Troubleshooting

### Alias Not Working?

1. **Check if alias exists:**
   ```bash
   curl https://api.amplify/model_aliases | jq '.data.aliases | keys'
   ```

2. **Check logs:**
   ```bash
   aws logs tail /aws/lambda/amplify-lambda-js-prod-chat --follow | grep "alias"
   ```

3. **Verify deployment:**
   ```bash
   curl https://api.amplify/model_aliases | jq '.data.version'
   ```

### Common Issues

| Issue | Solution |
|-------|----------|
| "Model ID not found" | Alias might not exist in JSON file |
| Alias resolves to wrong model | Stale deployment; redeploy latest version |
| No resolution logged | Check CloudWatch logs for errors |

## Key Features

✅ **<1ms overhead** - Blazing fast in-memory lookup
✅ **Zero breaking changes** - Existing IDs still work
✅ **Deployment controlled** - Test in dev/staging first
✅ **Version controlled** - Full git audit trail
✅ **Graceful fallback** - If JSON missing, aliases disabled but app continues

## More Information

- **Full docs:** `docs/MODEL_ALIASES.md`
- **Changelog:** `MODEL_ALIAS_CHANGELOG.md`
- **Tests:** `amplify-lambda-js/models/__tests__/`
- **Config:** `chat-billing/model_rates/model_aliases.json`

## Support

Questions? Issues?
- GitHub: https://github.com/gaiin-platform/amplify-genai-backend/issues
- Docs: `docs/MODEL_ALIASES.md`
- Tests: Run `node models/__tests__/manual-test-aliases.js`

---

**Version:** 1.0.0 | **Status:** ✅ Production Ready | **Updated:** 2026-02-18
