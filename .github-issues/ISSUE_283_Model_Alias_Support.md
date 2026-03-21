# Issue #1: Add Model Alias Support for Auto-Updating to Latest Versions

## Problem Statement

Currently, Amplify requires exact AWS Bedrock model IDs (e.g., `us.anthropic.claude-opus-4-5-20251101-v1:0`). When models are deprecated, deployments may silently fall back to older models, causing performance degradation without notification.

**Real-world impact**: Production deployments may fall back from Claude 3.5 Sonnet (retired Jan 2026) to Claude 3 Opus (Feb 2024), running a 2-year-old model without anyone noticing the degradation.

## Proposed Solution

Add support for user-friendly aliases that automatically resolve to the latest model versions:
- `opus-latest` → current Claude Opus model
- `sonnet-latest` → current Claude Sonnet model
- `haiku-latest` → current Claude Haiku model

## Benefits

### For Users
✅ **Simple names**: Use `opus-latest` instead of `us.anthropic.claude-opus-4-6-v1:0`
✅ **Always latest**: Automatic updates when maintainers deploy new versions
✅ **No breaking changes**: Old model IDs still work

### For Admins
✅ **Easy updates**: Edit JSON file, git commit, deploy
✅ **Version control**: Full audit trail via git
✅ **Testing**: Deploy to dev/staging before production
✅ **Fast rollback**: Simple code change if needed

### For Deployments
✅ **Prevents silent fallbacks**: Controlled model updates via deployment (testable in dev/staging)
✅ **Future-proof**: Updates without user intervention
✅ **Monitoring**: Logs show alias resolution for analytics

## Implementation Overview

### Core Components

1. **model_aliases.json** - Centralized alias mapping configuration
   ```json
   {
     "version": "1.0.0",
     "aliases": {
       "opus-latest": {
         "resolves_to": "us.anthropic.claude-opus-4-6-v1:0",
         "description": "Latest Opus model",
         "category": "claude",
         "tier": "premium"
       }
     }
   }
   ```

2. **modelAliases.js** - Resolution logic module
   - `resolveModelAlias()` - Main resolution function
   - `isAlias()` - Check if string is an alias
   - `getAllAliases()` - Get all available aliases
   - `getReverseMapping()` - Map model IDs to their aliases

3. **router.js integration** - Single centralized integration point (line 229)
   - Resolves alias BEFORE validation
   - Tracks alias usage for analytics
   - <0.01ms overhead

## Technical Details

**Architecture:**
```
User Request (model.id = "opus-latest")
    ↓
Router (resolve alias)
    ↓
modelAliases.resolveModelAlias() → "us.anthropic.claude-opus-4-6-v1:0"
    ↓
Validation (existing)
    ↓
Bedrock API
```

**Performance:**
- Load time: ~2ms (on cold start, cached thereafter)
- Resolution time: <0.01ms per call
- Memory overhead: <1KB
- API latency impact: Negligible (<0.1%)

**Files to Create/Modify:**
- `chat-billing/model_rates/model_aliases.json` (NEW)
- `amplify-lambda-js/models/modelAliases.js` (NEW)
- `amplify-lambda-js/router.js` (MODIFY - add import and resolution logic)

## Success Criteria

- [ ] User can request chat with `opus-latest` and it works
- [ ] Logs show alias resolution: `opus-latest → us.anthropic.claude-opus-4-6-v1:0`
- [ ] Bedrock receives resolved ID (not alias)
- [ ] Existing direct model IDs continue working unchanged
- [ ] Unit tests pass
- [ ] Performance: <1ms overhead for alias resolution
- [ ] Zero breaking changes to existing API

## Testing Plan

1. **Unit tests**: Test all resolution scenarios
2. **Integration tests**: End-to-end chat requests with aliases
3. **Performance tests**: Verify <1ms overhead
4. **Backward compatibility tests**: Existing model IDs still work

## Rollback Plan

If issues arise:
- Quick fix: Comment out alias resolution in router.js (5-line change)
- Full rollback: Git revert and redeploy

## Labels

`enhancement`, `models`, `backend`, `high-priority`

## Dependencies

None - this is a standalone feature

## Estimated Effort

3-5 hours for core implementation + testing
