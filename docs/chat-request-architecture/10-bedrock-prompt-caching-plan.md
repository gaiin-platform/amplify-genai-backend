# 10. Bedrock Prompt Caching: Implementation Plan

Follows [09-prompt-caching-audit.md](09-prompt-caching-audit.md). Scope is **Bedrock only**. Written 2026-11-04 against commit `c0d5e822` on `dev`. Line numbers drift, so re-grep symbol names before editing.

Markers: **[verified]** = I read the code or doc. **[inferred]** = reasoning from code or from a doc that is silent.

## 1. Decisions

| Question | Decision |
|---|---|
| Models in scope | `us.anthropic.claude-haiku-4-5-20251001-v1:0`, `us.anthropic.claude-sonnet-4-6`, `us.anthropic.claude-sonnet-5`, `us.anthropic.claude-opus-5` (explicit `cachePoint`). The model list changes over time, so gating is by **model-family regex**, not a fixed list. |
| Out of scope | `us.openai.gpt-5.6-luna/terra` on Bedrock: Converse has no explicit caching for them (Bedrock doc), so they only get the usage-parser fix. `gpt-5.2`: not a Bedrock-cache target. The seed CSV lists it with `Provider=Bedrock`, which is suspicious (`bedrock.js` would send the id `gpt-5.2` to Converse), so confirm the live provider value. |
| Mechanism | **Explicit `cachePoint`** blocks, not Bedrock "automatic caching" (`additionalModelRequestFields.cache_control`). All four Claude models also support automatic mode, but explicit placement lets us share the tools and system prefix across conversations and users, is unit-testable as a pure function, and avoids merging into the reasoning-config `additionalModelRequestFields`. Automatic mode is the fallback if explicit placement misbehaves. |
| TTL | 5m only (the default). Send **no** `ttl` field. A 1h write costs more and the rate table has one write rate. |
| Leading delimiter (`bedrock.js` `combineMessages`) | **Remove it.** Git history: it began in Dec 2024 as `delimiter + "Respond to the following inquiry: " + msg` (commit `a6a540e5`) and later shrank to a bare delimiter. Merged same-role messages already get a delimiter between them, so the prefix is redundant and produces a doubled delimiter. It also makes the last user message render differently on the next turn, which defeats caching. |
| Release shape | Ship behind env flags, default off, enable per stage. Checkpoint classes are independently switchable. |

## 2. Facts the implementation relies on (checked 2026-11-04)

Source: https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html and the installed SDK types (`@aws-sdk/client-bedrock-runtime` 3.1050.0, `dist-types/models/models_0.d.ts`).

- Converse and ConverseStream accept `{ cachePoint: { type: 'default' } }` as a block in `system[]`, in `messages[].content[]` and in `toolConfig.tools[]`. SDK members: `SystemContentBlock.CachePointMember`, `ContentBlock.CachePointMember`, `Tool.CachePointMember`. [verified]
- Order is tools, then system, then messages. A change at one level invalidates the later levels. Max 4 checkpoints per request. [verified]
- Minimum tokens in the prefix **before** each checkpoint (cumulative): Haiku 4.5 = **4,096**; Sonnet 4.6 = 1,024; Sonnet 5 = 1,024; Opus 5 = 512 (Claude 5.x). Re-read the table at the URL when implementing. Use the conservative value if the page is ambiguous. [verified from the doc, with a small ambiguity for Sonnet 5]
- Stream usage: `ConverseStreamMetadataEvent.usage` is `TokenUsage` with `inputTokens`, `outputTokens`, `totalTokens`, `cacheReadInputTokens`, `cacheWriteInputTokens` (camelCase) and `cacheDetails`. **`inputTokens` excludes cached tokens.** [verified]
- Rates: cache reads are billed at roughly 0.1x input and cache writes at 1.25x. For Bedrock, `common/accounting.js:154-163` (`in*inRate + out*outRate + cached*cachedRate + write*writeRate`) is **correct** because `inputTokens` is already exclusive. The OpenAI-family double-count bug is a separate task and must not be mixed into this change. [verified]
- A cache write costs 25 percent more than uncached input and a read saves 90 percent, so a checkpoint pays off if it is read at least once per 3.6 writes. Hence the "loop continuation only" rule for the tail checkpoint below. [inferred from the rate ratios]
- Unknown, to confirm in dev: (a) whether a prompt below the model minimum is silently not cached or rejected; (b) whether a `cachePoint` after a `toolResult` block inside a user message is accepted; (c) Guardrails (`BEDROCK_GUARDRAIL_ID`) with caching.

## 3. Current state in the code (what is wrong today)

| Layer | State | Where |
|---|---|---|
| Request | No `cachePoint` anywhere | `bedrock/bedrock.js`: `input` built at ~`:102`, `system` ~`:175`, `toolConfig` ~`:219`, `ConverseStreamCommand` ~`:288` |
| Usage parse | Reads `cache_read_input_tokens` and `cache_creation_input_tokens` (snake_case). Converse returns camelCase, so both are always 0. | `common/chat/events/bedrock.js:191-231` |
| Pricing | Correct for Bedrock | `common/accounting.js:116-163` |
| Rendering stability | `combineMessages` prefixes the last user message with a delimiter, so turn N+1 renders that message differently. Inside a tool loop, iteration 1 differs from iterations 2+. | `bedrock/bedrock.js:435-474` |
| Model metadata | No caching fields on the model object. `llm/bedrockStructuredOutput.js` is the precedent for a model-family regex helper plus a `node:test` file. | `llm/bedrockStructuredOutput.js` |
| Tests | No unit tests for `chatBedrock`. `node:test` is the convention. Run with `cd amplify-lambda-js && node --test <files>`. No npm test script. | |
| Single caller | `chatBedrock` is called only from `llm/UnifiedLLMClient.js:76`. `prompt_tokens` is consumed only by `recordUsage`. | |

## 4. Design

### 4.1 Checkpoint placement (max 4, applied after messages and `toolConfig` are final)

| ID | Position | Condition |
|---|---|---|
| C1 `tools` | Last element of `toolConfig.tools` | Real tools offered (not the stub rebuilt from history at `bedrock.js:204-217`), and estimated tokens of tools alone meet the model minimum |
| C2 `system` | Last element of `system[]` | System is not the `BLANK_MSG` placeholder, and estimated tokens of tools + system meet the minimum |
| C3 `history` | Last content block of the last **assistant message without `toolUse`** that is followed by at least one more message | Estimated prefix tokens through it meet the minimum. Stable across turns once rendering is deterministic, even when RAG or document blocks are merged into the final user message. |
| C4 `tail` | Last content block of the last message | Only when that message is a **tool-loop continuation** (it contains a `toolResult` block), and the prefix meets the minimum. Does not apply to the first call of a turn. |

Rules:
- If C3 and C4 target the same message, emit one.
- Estimate tokens with `createTokenCounter()` from `azure/tokens.js` (cl100k). It undercounts Claude, which is conservative here. Count image blocks as a fixed 1,600 tokens each.
- Models with no profile (Nova, GPT-5.6, DeepSeek, Llama, Mistral, unknown) get **no** checkpoints. Never send `ttl`.
- Flags (in `serverless.yml` `provider.environment`, same `${ssm:/amplify/${sls:stage}/NAME, 'default'}` pattern as `BEDROCK_GUARDRAIL_ID`, around `:57`):
  - `BEDROCK_PROMPT_CACHING` = `on` | `off` (default `off`).
  - `BEDROCK_PROMPT_CACHING_POINTS` = comma list of `tools,system,history,tail` (default all four).
  - `BEDROCK_PROMPT_CACHING_MODELS` = optional comma list of regexes to narrow the family match (default: built-in families).

### 4.2 Known limits after this change (Phase 2, not in this task)

These cap the hit rate but are out of scope here: the final tool-loop iteration deletes `tools` and flips reasoning on (`toolLoop.js:559-562`, `bedrock.js:89`); routing-driven feature prompts and tools change per turn; skills are prepended at index 0; RAG and document context sit at the tail; history is rewritten per turn once a conversation passes 20 messages; the 1h TTL needs a separate write rate. See doc 09, section 4.

## 5. Work breakdown (each step independently shippable)

1. **Usage parser** (`common/chat/events/bedrock.js`): read `cacheReadInputTokens` and `cacheWriteInputTokens`, keep the snake_case fallbacks, keep `prompt_tokens = inputTokens`. Add a `node:test` file for `bedrockTokenUsageTransform`.
2. **Per-call cache log** (`llm/UnifiedLLMClient.js`, next to the `recordUsage` call at ~`:728`): one structured `[CACHE-USAGE]` info line with `requestId`, `llmCallId`, `modelId`, `provider`, `promptTokens`, `cacheRead`, `cacheWrite`, `completionTokens`, `hasTools` and `isInternalCall`.
3. **Deterministic rendering** (`bedrock/bedrock.js` `combineMessages`): remove the leading-delimiter block, keep the inter-message delimiter on merge, export the function for tests.
4. **`bedrock/bedrockCaching.js`**: pure functions (profile lookup, placement planning, application) plus `bedrock/bedrockCaching.test.js`.
5. **Wire-in** (`bedrock/bedrock.js`): call `applyBedrockCaching` once, just before `trace(...)` / `client.send`.
6. **Flags** in `serverless.yml`.
7. **Dev smoke script** (not part of CI): two identical large requests, expect `cacheWriteInputTokens > 0` then `cacheReadInputTokens > 0`.
8. **Rollout**: deploy with `off`, enable in dev, run the smoke script, enable in prod, watch `[CACHE-USAGE]`. Rollback is the env var.

Out of scope: OpenAI-family billing fix, Gemini, Azure, Python/litellm, rate-table edits (flag the Sonnet 4.5/4.6 rows, cached 0.00033 and write 0.004125, which are about 10 percent above the 0.1x and 1.25x ratios; ask the owner before changing).

## 6. Measuring success

From `[CACHE-USAGE]` logs, per model: hit rate `cacheRead / (promptTokens + cacheRead + cacheWrite)`; write tokens never read back; cost per turn before and after; time to first token for tool-loop turns. Disable the `tail` point first if its writes are rarely read.

## 7. Prompt for a new AI chat

Copy everything inside the block below into a new chat opened in `/Users/moundamr/Amplify/amplify-genai-backend`.

````text
You are implementing prompt caching for AWS Bedrock Claude models in the Amplify GenAI backend (repo root: /Users/moundamr/Amplify/amplify-genai-backend, branch dev). Work in amplify-lambda-js/. Read docs/chat-request-architecture/09-prompt-caching-audit.md and docs/chat-request-architecture/10-bedrock-prompt-caching-plan.md first; they contain the evidence behind everything below. Line numbers were taken at commit c0d5e822 and may have drifted, so re-grep symbol names before editing. Do not commit, push or deploy unless I explicitly ask. Make no live LLM or AWS calls except where a step says the user will run them.

## Goal
Make Bedrock requests for these Claude models use explicit prompt caching, make the cache usage show up correctly in billing records and logs, and make the prompt bytes stable enough that cache reads can actually happen. Bedrock only.

Models in scope today (the list changes over time, so gate by model-id regex family, not by a fixed list):
- us.anthropic.claude-haiku-4-5-20251001-v1:0 (min 4,096 tokens per checkpoint)
- us.anthropic.claude-sonnet-4-6 (min 1,024)
- us.anthropic.claude-sonnet-5 (min 1,024)
- us.anthropic.claude-opus-5 (doc says 512; use 1,024 to be conservative unless you re-verify)
Out of scope: us.openai.gpt-5.6-* on Bedrock (Converse has no explicit caching for them), gpt-5.2, Nova, Llama, Mistral, DeepSeek, OpenAI/Azure/Gemini adapters, the Python/litellm agent loop, the OpenAI-family billing bug. Models with no caching profile must get NO checkpoints.

## Verified background
- Every Bedrock call goes through callUnifiedLLM (llm/UnifiedLLMClient.js) -> chatBedrock (bedrock/bedrock.js), which builds a ConverseStreamCommand input: `input.messages` (from sanitizeMessages), `input.system` (array of {text}), optional `input.toolConfig.tools`, `inferenceConfig`, optional `additionalModelRequestFields` for reasoning, and `client.send(new ConverseStreamCommand(input))`. Nothing requests caching today.
- Converse accepts `{ cachePoint: { type: 'default' } }` as a block in system[], in messages[].content[] and in toolConfig.tools[] (SDK 3.1050.0 types: SystemContentBlock.CachePointMember, ContentBlock.CachePointMember, Tool.CachePointMember). Processing order is tools, then system, then messages; a change at one level invalidates the later ones. Max 4 checkpoints per request. TTL default is 5 minutes: do NOT send a `ttl` field. Doc: https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html (re-read the model/minimum-token table before you finalise the numbers).
- Converse stream usage (event.metadata.usage, type TokenUsage) has camelCase `inputTokens`, `outputTokens`, `cacheReadInputTokens`, `cacheWriteInputTokens`, `cacheDetails`. `inputTokens` EXCLUDES cached tokens.
- BUG 1: common/chat/events/bedrock.js `bedrockTokenUsageTransform` (~lines 191-231) reads snake_case `cache_read_input_tokens` / `cache_creation_input_tokens`, so cache tokens are always 0.
- common/accounting.js recordUsage (~154-163) computes in*inRate + out*outRate + cached*cachedRate + write*writeRate. That is correct for Bedrock because inputTokens is exclusive. Do not change accounting.js. UnifiedLLMClient passes requestState.totalUsage.{prompt_tokens, completion_tokens, inputCachedTokens, inputWriteCachedTokens} to recordUsage (~lines 722-740). Keep `prompt_tokens = inputTokens`.
- BUG 2 (cache-defeating rendering): bedrock/bedrock.js `combineMessages` (~lines 435-474) prefixes the LAST user message with a delimiter (`if (j === oldMessages.length - 1 && role === 'user') content = delimiter + content`) and merges consecutive same-role messages with the delimiter between them. The leading delimiter was originally `delimiter + "Respond to the following inquiry: " + msg` (commit a6a540e5, Dec 2024) and later shrank to a bare delimiter; it is redundant (merge already inserts one, causing a doubled delimiter) and makes the same user message render differently on the next turn, and in a tool loop makes iteration 1 differ from iterations 2+. Cache writes on it would never be read. Decision: REMOVE the leading-delimiter block; keep the delimiter used when merging same-role messages; export `combineMessages` so it can be tested. Note RAG/document context messages (role user) are spliced before the final user message by common/chatWithData.js and get merged into the final user message by combineMessages; the next turn's history contains only the bare user text. That is expected and why we also use a "history boundary" checkpoint (C3 below).
- Cost trade-off: a cache write costs 1.25x uncached input, a read 0.1x, so a checkpoint must be read about once per 3.6 writes to break even. Hence the "tail" checkpoint applies only to tool-loop continuation calls.
- Tool loop (tools/toolLoop.js) appends assistant{tool_calls} and role:'tool' messages and re-calls callUnifiedLLM up to 5 times; the final iteration deletes `tools` (known limit, out of scope). Code interpreter (assistants/codeInterpreter.js) and artifact rounds also re-send prompts. bedrock.js sanitizeMessages converts tool messages to user messages with `toolResult` blocks (consecutive ones merged into one user message) and assistant tool_calls to `toolUse` blocks. Images are appended to the last message by includeImageSources inside sanitizeMessages.
- When tools are missing but history has toolUse/toolResult, bedrock.js reconstructs a STUB toolConfig (~lines 204-217) with placeholder descriptions. Never place a tools checkpoint on a stub.
- System: bedrock.js builds `systemPrompts` ([{text}]) and uses BLANK_MSG ("Intentionally Left Blank, please ignore") when empty. Do not checkpoint the placeholder.
- Tests use node:test (see llm/bedrockStructuredOutput.test.js for a model-family-helper precedent: llm/bedrockStructuredOutput.js + .test.js). Run: `cd amplify-lambda-js && node --test <files>`. There is no npm test script and no existing unit test for chatBedrock.
- Env flags are declared in amplify-lambda-js/serverless.yml under provider.environment using `${ssm:/amplify/${sls:stage}/NAME, 'default'}` (see BEDROCK_GUARDRAIL_ID around line 57).

## Work items (do them in this order; each must be independently shippable)

1. Usage parser fix. In common/chat/events/bedrock.js read `cacheReadInputTokens` and `cacheWriteInputTokens` into `inputCachedTokens` / `inputWriteCachedTokens`, keeping the old snake_case names as fallbacks. Keep `prompt_tokens = inputTokens`. Add common/chat/events/bedrock.test.js covering: camelCase fields, snake_case fallback, missing fields -> 0, the legacy `event.d.usage` and `event.usage` shapes still working.

2. Per-call cache log. In llm/UnifiedLLMClient.js next to the recordUsage call, add one structured info log line tagged `[CACHE-USAGE]` with requestId, llmCallId, modelId, provider, promptTokens, cacheRead, cacheWrite, completionTokens, hasTools, isInternalCall. No behaviour change.

3. Deterministic rendering. Edit combineMessages as described under BUG 2 and export it. Add tests (new file bedrock/combineMessages.test.js) asserting: (a) for histories [U1,A1,U2] and [U1,A1,U2,A2,U3] the first three rendered messages are deep-equal; (b) a lone final user message has no leading delimiter; (c) consecutive same-role messages are still merged with the delimiter between; (d) tool and assistant-tool_calls messages are preserved untouched; (e) the "first message must be user" failsafe still works.

4. Create bedrock/bedrockCaching.js (pure, no AWS calls) with:
   - `getBedrockCachingProfile(modelId)`: regex families -> { minTokens, family } or null. Handle `us.`/`global.`/`eu.` prefixes and ids like claude-haiku-4-5-..., claude-sonnet-4-6, claude-sonnet-5, claude-opus-5; be tolerant of future ids of the form claude-(haiku|sonnet|opus)-N[-M]; unknown -> null. Minimums as above (Haiku 4.5 = 4096; other Claude = 1024 unless the doc says otherwise).
   - `getBedrockCachingConfig(env = process.env)`: parse BEDROCK_PROMPT_CACHING (on/off, default off), BEDROCK_PROMPT_CACHING_POINTS (subset of tools,system,history,tail; default all), BEDROCK_PROMPT_CACHING_MODELS (optional comma list of regexes narrowing the match).
   - `planCachePoints({ modelId, system, tools, toolsAreStub, messages, countTokens, config })` returning which of C1..C4 apply, and `applyBedrockCaching(input, opts)` that returns a NEW input object with `{ cachePoint: { type: 'default' } }` blocks inserted (never mutate shared arrays, never exceed 4, never send `ttl`).
   Placement rules:
   - C1 tools: after the last element of toolConfig.tools; only if real tools (not stub) and estimated tokens of tools alone >= minTokens.
   - C2 system: after the last element of system[]; skip if the only system text is the placeholder; require estimated tokens of tools+system >= minTokens.
   - C3 history: last content block of the last assistant message that has no toolUse block and is followed by at least one more message; require estimated prefix tokens through it >= minTokens.
   - C4 tail: last content block of the LAST message, only if that message is role user and contains a toolResult block (tool-loop continuation); require estimated prefix >= minTokens. If C3 and C4 target the same message emit one.
   Token estimate: use createTokenCounter() from azure/tokens.js (cl100k; it undercounts Claude, which is conservative), count text, toolUse input JSON, toolResult text, tool specs JSON; count each image block as 1600 tokens. Accept `countTokens` as a parameter so tests can inject a fake.
   Write bedrock/bedrockCaching.test.js with cases: each model family (and prefixed ids) recognised or null (Nova, gpt-5.6 on Bedrock, Llama, Mistral, DeepSeek, undefined all -> null); flag parsing; each checkpoint rule including below-minimum skipping, stub tools, placeholder system, no assistant message, tool-loop continuation, C3==C4 dedupe, 4-point cap; input not mutated; no `ttl` ever present; disabled config returns the input unchanged.

5. Wire-in. In bedrock/bedrock.js call applyBedrockCaching exactly once, after toolConfig and messages are final (after image sources are included, after the supportsSystemPrompts branch) and BEFORE the `trace(...)` call and `client.send`. If the flag is off or the model has no profile, behaviour must be byte-identical to today. Make sure the error-logging path (`sanitizedInput`) still works. Wrap the planning in try/catch so a bug in caching code can never fail a chat request (log a warning and send the uncached input).

6. Flags in amplify-lambda-js/serverless.yml provider.environment: BEDROCK_PROMPT_CACHING (default 'off'), BEDROCK_PROMPT_CACHING_POINTS (default 'tools,system,history,tail'), BEDROCK_PROMPT_CACHING_MODELS (default ''), following the existing ssm pattern. Check whether there are var files (e.g. ../var/*.yml, populate scripts) that need the new names and update only where the pattern clearly requires it.

7. Dev smoke script (do not run it; the user will run it with AWS credentials): amplify-lambda-js/local/bedrockCacheSmoke.mjs that sends the same >=5,000-token prompt (stable system + stable first user turn) twice to a model id passed on the command line via ConverseStreamCommand with cachePoint on the system block, prints usage from metadata.usage, and states what is expected (first call cacheWriteInputTokens > 0, second call cacheReadInputTokens > 0). Include a second mode that sends a tiny prompt below the minimum to show whether Bedrock ignores or rejects it, and a third mode that puts a cachePoint after a toolResult block, because those three behaviours are unverified.

## Constraints
- Do not change common/accounting.js or the OpenAI/Azure/Gemini adapters or event transforms. Do not edit the rate table. If you notice the Sonnet 4.5/4.6 rows in chat-billing/model_rates/model_rate_values.csv (cached 0.00033, write 0.004125) look about 10% above 0.1x/1.25x of input, just mention it in your final summary.
- Do not change tool-loop, router, skills, RAG or assistant behaviour. Those are listed as Phase 2 in doc 10.
- Keep changes minimal and in the existing code style (ES modules, getLogger from common/logging.js).
- Run the new and existing related tests: `cd amplify-lambda-js && node --test bedrock/ common/chat/events/bedrock.test.js common/accounting.test.js llm/bedrockStructuredOutput.test.js` and report the results. Note the whole repo may have unrelated failing tests; do not "fix" them.
- Before editing any existing symbol, check who calls it (callUnifiedLLM and chatBedrock are central; chatBedrock has a single caller). Before finishing, review `git diff` for unintended changes.

## Deliverable
Code and tests for steps 1-7, then a short summary containing: files changed; test output; what the user must verify in dev (the smoke script results, especially the three unverified behaviours, and a real chat plus a web-search tool loop with BEDROCK_PROMPT_CACHING=on showing nonzero cacheRead in [CACHE-USAGE] logs); and the rollout/rollback steps (enable per stage via the env var; roll back by setting it to off). Do not commit; ask me first.
````
