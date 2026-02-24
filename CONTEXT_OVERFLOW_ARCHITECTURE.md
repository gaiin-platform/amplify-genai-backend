# Context Overflow Architecture

## Philosophy: FAIL-FIRST with PROACTIVE CACHING

- **callUnifiedLLM** checks cache FIRST (no token counting!) for long conversations
- **chatWithData** handles large document contexts proactively (85% threshold)
- **contextOverflow.js** handles recovery + caching for long conversations
- **Zero overhead** for 99% of users who don't overflow

## System Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER REQUEST                                 │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  router.js → chatWithData.js (if data sources) OR direct LLM        │
│                                                                      │
│  chatWithData handles document contexts:                             │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ IF contextFullness >= 85%:                                   │    │
│  │   • Process contexts SEPARATELY (internal LLM call)          │    │
│  │   • Get summary, include in main call                        │    │
│  │                                                              │    │
│  │ ELSE (< 85%):                                                │    │
│  │   • Merge contexts with conversation (single call)           │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  callUnifiedLLM() - PROACTIVE CACHE CHECK (NO TOKEN COUNTING!)      │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ IF conversationId exists                                     │    │
│  │    AND messages.length >= 20                                 │    │
│  │    AND smartMessages didn't filter                           │    │
│  │    AND NOT internal call:                                    │    │
│  │                                                              │    │
│  │   Check cache by conversationId                              │    │
│  │   IF cache HIT:                                              │    │
│  │     • Split at cached historicalEndIndex                     │    │
│  │     • Use cached extraction + intact messages                │    │
│  │     • AVOID OVERFLOW ENTIRELY!                               │    │
│  │                                                              │    │
│  │   IF cache MISS:                                             │    │
│  │     • Send normally, let fail-first handle it                │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Provider Call (Bedrock/OpenAI/Azure/Gemini)                        │
└─────────────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
          ┌─────────┐                 ┌─────────┐
          │ SUCCESS │                 │  ERROR  │
          │  (99%)  │                 │         │
          └─────────┘                 └─────────┘
                │                           │
                ▼                           ▼
          ┌─────────┐              ┌────────────────┐
          │  Done!  │              │ detectContext  │
          │  Zero   │              │ Overflow()     │
          │overhead │              └────────────────┘
          └─────────┘                      │
                                    Is it overflow?
                                           │
                            ┌──────────────┴──────────────┐
                            ▼                             ▼
                     ┌──────────┐                  ┌──────────┐
                     │    NO    │                  │   YES    │
                     │ (other   │                  │(overflow)│
                     │  error)  │                  └──────────┘
                     └──────────┘                        │
                            │                           ▼
                            ▼                  ┌────────────────┐
                     ┌──────────┐              │ First attempt? │
                     │  Throw   │              │ (one-strike)   │
                     │  error   │              └────────────────┘
                     └──────────┘                      │
                                         ┌────────────┴────────────┐
                                         ▼                         ▼
                                  ┌──────────┐              ┌──────────┐
                                  │ YES (1st)│              │ NO (2nd) │
                                  │   Try    │              │ Already  │
                                  │ recovery │              │  tried   │
                                  └──────────┘              └──────────┘
                                         │                        │
                                         ▼                        ▼
                           ┌──────────────────────┐        ┌──────────┐
                           │ handleContextOverflow│        │ Critical │
                           │                      │        │  Log +   │
                           │ Check existing cache │        │  Throw   │
                           │ for INCREMENTAL      │        └──────────┘
                           │ extraction           │
                           └──────────────────────┘
                                         │
                                         ▼
                           ┌──────────────────────┐
                           │ HISTORICAL EXTRACTION│
                           │                      │
                           │ IF cache exists:     │
                           │   • INCREMENTAL:     │
                           │     Only extract NEW │
                           │     messages since   │
                           │     last cache       │
                           │                      │
                           │ ELSE:                │
                           │   • FULL extraction  │
                           │     from all old     │
                           │     messages         │
                           └──────────────────────┘
                                         │
                                         ▼
                           ┌──────────────────────┐
                           │ CACHE the result     │
                           │                      │
                           │ {                    │
                           │   conversationId,    │
                           │   historicalEndIndex,│
                           │   extractedContext,  │
                           │   messageCount       │
                           │ }                    │
                           └──────────────────────┘
                                         │
                                         ▼
                           ┌──────────────────────┐
                           │ Build recovered msgs │
                           │                      │
                           │ 1. System msg with   │
                           │    extracted context │
                           │ 2. Intact recent     │
                           │    messages          │
                           └──────────────────────┘
                                         │
                                         ▼
                           ┌──────────────────────┐
                           │    RETRY LLM CALL    │
                           └──────────────────────┘
                                         │
                           ┌─────────────┴─────────────┐
                           ▼                           ▼
                    ┌─────────────┐           ┌─────────────┐
                    │   SUCCESS   │           │ STILL OVER  │
                    │             │           │             │
                    │ Clear       │           │ Critical    │
                    │ tracking,   │           │ log, throw  │
                    │ return      │           │ error       │
                    └─────────────┘           └─────────────┘
```

## Cache Structure

```javascript
// Stored by conversationId
{
    conversationId: "abc123",
    historicalEndIndex: 35,      // Messages 0-35 are summarized
    extractedContext: "...",     // The LLM extraction summary
    messageCount: 46,            // Total messages when cached
    modelId: "gpt-4o-mini"       // Model used for extraction (for invalidation)
}
```

### Model Change Invalidation

Cache is invalidated when the user switches models because different models have different context windows:

```javascript
// In getCachedHistoricalContext:
if (modelId && cached.modelId && cached.modelId !== modelId) {
    // Cache INVALIDATED - extraction budget was calculated for different model
    return null;
}
```

## Smart Messages + Caching Rules

| Scenario | Cache Safe? | Why |
|----------|-------------|-----|
| Smart messages OFF | ✅ YES | Full conversation, consistent |
| Smart messages ON, no filtering | ✅ YES | Full conversation, consistent |
| Smart messages ON, filtered messages | ❌ NO | Different messages each time |

When smart messages filters, we skip:
- Proactive cache check
- Cache read in extraction
- Cache write after extraction

## skipHistoricalContext Flag

For LLM calls that don't need conversation history:

```javascript
// RAG document analysis
promptUnifiedLLMForData(params, messages, schema, null, {
    skipHistoricalContext: true  // Don't extract historical context
});
```

Used in:
- `getExtractedRelevantContext()` in datasources.js - RAG document relevance
- `getContextMessages()` → internal LLM call in rag.js - RAG question extraction

These calls only need: system prompt + user question + documents

## The Flows

### Flow 1: Cache Hit (Fast Path)
```
1. User sends 48-message conversation
2. callUnifiedLLM: Check cache by conversationId → HIT!
3. Split at cached index (e.g., 35)
4. Use: cached extraction + messages 36-48
5. Send to LLM → Success! (NO overflow)
```

### Flow 2: First Overflow (Cache Population)
```
1. User sends 46-message conversation (first time)
2. callUnifiedLLM: Check cache → MISS
3. Send to LLM → OVERFLOW!
4. handleContextOverflow: Full extraction from messages 0-35
5. Cache: { endIndex: 35, extraction: "...", count: 46 }
6. Retry with intact messages + extraction → Success!
```

### Flow 3: Incremental Update (Cache Stale)
```
1. User now has 60 messages (grew from 46)
2. callUnifiedLLM: Cache hit, use cached split
3. Send to LLM → OVERFLOW! (conversation grew too much)
4. handleContextOverflow: Find existing cache
5. INCREMENTAL: Only extract messages 36-50 (new historical)
6. Prompt: "Previous summary + new messages → updated summary"
7. Cache: { endIndex: 50, extraction: "...", count: 60 }
8. Retry → Success!
```

## Files

| File | Role |
|------|------|
| `/llm/UnifiedLLMClient.js` | Proactive cache check (lines 252-285) + triggers recovery (lines 509-548) |
| `/llm/contextOverflow.js` | Recovery + incremental extraction + caching |
| `/common/cache.js` | Cache storage (getCachedHistoricalContext, setCachedHistoricalContext) - lines 470-496 |
| `/common/chatWithData.js` | 85% threshold for document contexts (lines 398-470) + passes flags to callUnifiedLLM (lines 503-550) |
| `/datasource/datasources.js` | RAG extraction with skipHistoricalContext flag |
| `/common/chat/rag/rag.js` | RAG question extraction with skipHistoricalContext flag |

## Provider Error Patterns

| Provider | Pattern |
|----------|---------|
| Bedrock | `prompt is too long: X tokens > Y`, `ValidationException` |
| OpenAI | `maximum context length is X tokens, however you requested Y`, `context_length_exceeded` |
| Azure | `maximum context length is X...resulted in Y` |
| Gemini | `input context is too long`, `RESOURCE_EXHAUSTED`, `exceeds the maximum` |

## Key Functions

```javascript
// Proactive cache check (in callUnifiedLLM - lines 252-285)
// NO TOKEN COUNTING - just message count + conversationId lookup
if (!smartMessagesFiltered && conversationId && messages.length >= 20 && !options._isInternalCall) {
    const cached = await CacheManager.getCachedHistoricalContext(userId, conversationId);
    if (cached && messages.length >= cached.messageCount) {
        const intactMessages = messages.slice(cached.historicalEndIndex + 1);
        finalMessages = buildMessagesWithHistoricalContext(intactMessages, cached.extractedContext);
    }
}

// Detect overflow from error message
detectContextOverflow(error) → { isOverflow, provider, requested, limit }

// Calculate token budgets (70/30 split)
calculateBudgets(model, maxTokens) → { intactBudget, historicalBudget }

// Extract with incremental support
extractHistoricalContext(params, messages, question, model, llmFn, options)
  → { extractedContext, historicalEndIndex }

// Build final messages
buildMessagesWithHistoricalContext(intactMessages, extractedContext)
  → [{ role: 'system', content: 'Relevant context...' }, ...intactMessages]

// Main recovery orchestration
handleContextOverflow({ error, params, messages, model, ... })

// Cache management
CacheManager.getCachedHistoricalContext(userId, conversationId)
CacheManager.setCachedHistoricalContext(userId, conversationId, cacheData)
```

## Internal Options (filtered from provider calls)

```javascript
// These are stripped before sending to LLM providers (UnifiedLLMClient.js lines 327-334):
const {
    keepStreamOpen,        // Stream control
    _contexts,             // Document contexts for recovery
    _isInternalCall,       // Prevents infinite loops
    smartMessagesFiltered, // Cache safety flag
    conversationId,        // Cache key
    ...providerOptions     // Everything else goes to provider
} = options;
```

## Oversized Message Handling

Messages that exceed the normal batch extraction threshold get special handling:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    OVERSIZED MESSAGE FLOW                           │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
               ┌──────────────────────────┐
               │ Message > oversizedThreshold │
               │ (default: 50K chars)     │
               └──────────────────────────┘
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
    ┌─────────────────────┐       ┌─────────────────────┐
    │ Fits cheapest model │       │ Too big for cheapest│
    │ (4 chars/token)     │       │                     │
    └─────────────────────┘       └─────────────────────┘
               │                             │
               ▼                             ▼
    ┌─────────────────────┐       ┌─────────────────────┐
    │ Use cheapest model  │       │ Fits user's model?  │
    │ for extraction      │       │ (3.5 chars/token)   │
    └─────────────────────┘       └─────────────────────┘
                                             │
                              ┌──────────────┴──────────────┐
                              ▼                             ▼
                   ┌─────────────────────┐       ┌─────────────────────┐
                   │ YES: Use user model │       │ NO: Truncate to fit │
                   │ for extraction      │       │ user model limit    │
                   └─────────────────────┘       └─────────────────────┘
```

### Budget Calculation

```javascript
// Normal messages: use cheapest model budget
const cheapestModelMaxChars = Math.floor(cheapestModelContextWindow * 0.7 * 4);

// Oversized messages: can use user's model with conservative estimate
// 3.5 chars/token accounts for poorly-tokenizing documents (e.g., old English: ~1.5 chars/token)
const userModelMaxChars = Math.floor(userModelContextWindow * 0.7 * 3.5);
```

## Summary

- **Proactive Cache**: callUnifiedLLM checks cache by conversationId (NO token counting!)
- **Proactive Contexts**: chatWithData splits at 85% → prevents context overflows
- **Reactive Recovery**: contextOverflow.js extracts history → handles long conversations
- **Incremental Updates**: Don't redo all work, build on existing cache
- **Smart Messages Safe**: Skip caching when smart messages filters (inconsistent conversations)
- **Model Change Safe**: Cache invalidated when user switches models (different context windows)
- **Oversized Handling**: Model fallback (cheapest → user's → truncate) with conservative 3.5 chars/token
- **One Chance**: First overflow = try recovery, second = critical log + fail
- **Zero Overhead**: Normal users never hit this code

## Data Flow: chatWithData → callUnifiedLLM

```
chatWithData:
  1. Detects smartMessagesFiltered from params.smartMessagesResult?._internal?.removedCount > 0
  2. Gets conversationId from chatRequestOrig.options or params.options
  3. Passes both to callUnifiedLLM in options object (lines 547-549)

callUnifiedLLM:
  1. Extracts smartMessagesFiltered and conversationId from options (lines 254-255)
  2. Uses them for proactive cache check if safe (lines 258-285)
  3. Filters them out before sending to provider (lines 327-334)
  4. Passes them to handleContextOverflow for recovery cache updates (lines 534-538)
```
