# Issue #2: Add Admin API Endpoints for Model Alias Management

## Description

To support the model alias feature (Issue #1), we need API endpoints that allow admins and frontend applications to:
1. Retrieve current alias mappings
2. Get model list with alias information

## Proposed Endpoints

### 1. GET /model_aliases

Returns all alias mappings from the configuration file.

**Authentication:** Required (validated read operation)

**Example Response:**
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
      },
      "sonnet-latest": {
        "resolves_to": "us.anthropic.claude-sonnet-4-6-v1:0",
        "description": "Latest Sonnet (simplified naming)",
        "category": "claude",
        "tier": "balanced"
      }
    }
  }
}
```

### 2. GET /models_with_aliases

Enhanced version of `/available_models` that includes which aliases point to each model.

**Authentication:** Required (validated read operation)

**Example Response:**
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
          },
          {
            "alias": "claude-opus-latest",
            "description": "Latest Claude Opus model",
            "category": "claude",
            "tier": "premium"
          }
        ],
        "description": "...",
        "capabilities": [...]
      }
    ]
  }
}
```

## Use Cases

### Admin UI
- Display available aliases in a management panel
- Show which models have aliases
- Help admins understand alias mappings

### Frontend Model Selector
- Show both aliases and full IDs in dropdown
- Group models by alias availability
- Recommend using aliases for better user experience

### Analytics & Monitoring
- Track which aliases are being used
- Identify popular aliases
- Plan alias deprecation strategy

## Implementation Details

**File:** `chat-billing/service/core.py`

Add two new functions using the existing `@api_tool` decorator pattern:
- `get_model_aliases()` - Read and return model_aliases.json
- `get_models_with_aliases()` - Call existing `get_user_available_models()`, then enhance with alias info

**Implementation Size:** ~60-80 lines total

## Benefits

✅ **Programmatic access** to alias configuration
✅ **Frontend integration** support
✅ **Admin tooling** enablement
✅ **Documentation/monitoring** capabilities

## Success Criteria

- [ ] GET /model_aliases returns valid JSON
- [ ] GET /models_with_aliases enhances existing model list
- [ ] Both endpoints require authentication
- [ ] Error handling for missing/corrupt alias file
- [ ] Response follows existing API patterns

## Testing

1. **Manual testing**: Use curl/Postman to verify responses
2. **Integration testing**: Verify frontend can consume endpoints
3. **Error testing**: Test with missing/corrupt alias file

## Dependencies

**Requires:** Issue #1 (Model Alias Support) to be completed first

## Labels

`enhancement`, `api`, `backend`

## Estimated Effort

1-2 hours
