// Bedrock Converse prompt caching for Claude models (pure helpers, no AWS imports so they are unit-testable).
//
// Converse caches everything BEFORE a { cachePoint } block, in the order tools -> system -> messages.
// Cache reads cost ~0.1x input, cache writes ~1.25x, so a checkpoint only pays off if it is read again.
// Prefixes shorter than the model minimum (1,024-4,096 tokens) are ignored by Bedrock (no write, no charge).
//
// Checkpoints (3 of the 4 allowed):
//   tools   - stable across a whole conversation/assistant
//   system  - stable across a whole conversation/assistant
//   tail    - end of the last message, so the next turn / next tool-loop iteration reads the whole prefix.
//             Only added when it can plausibly be re-read (multi-turn chat or a tool-loop continuation);
//             a one-shot call would pay the 1.25x write premium for nothing.

const CLAUDE_MODEL = /anthropic\.claude/i;
const cachePoint = () => ({ cachePoint: { type: 'default' } });

export const supportsPromptCaching = (modelId) => CLAUDE_MODEL.test(modelId || '');

const hasToolResult = (message) =>
    Array.isArray(message?.content) && message.content.some(block => block?.toolResult);

/** True when this request's tail is likely to be a prefix of a later request. */
export function tailIsReusable(messages, isInternalCall = false) {
    if (isInternalCall || !Array.isArray(messages) || messages.length === 0) return false;
    return messages.length >= 3 || hasToolResult(messages[messages.length - 1]);
}

/**
 * Return a NEW Converse input with cache points added (the input is never mutated), or the same
 * object when the model is not a Claude model or there is nothing to cache.
 * @returns {{input: object, points: string[]}}
 */
export function addCachePoints(input, { isInternalCall = false } = {}) {
    if (!input || !supportsPromptCaching(input.modelId)) return { input, points: [] };

    const points = [];
    const out = { ...input };

    if (Array.isArray(out.toolConfig?.tools) && out.toolConfig.tools.length > 0) {
        out.toolConfig = { ...out.toolConfig, tools: [...out.toolConfig.tools, cachePoint()] };
        points.push('tools');
    }

    if (Array.isArray(out.system) && out.system.length > 0) {
        out.system = [...out.system, cachePoint()];
        points.push('system');
    }

    const messages = out.messages;
    const last = messages?.[messages.length - 1];
    if (tailIsReusable(messages, isInternalCall) && Array.isArray(last?.content) && last.content.length > 0) {
        out.messages = [...messages.slice(0, -1), { ...last, content: [...last.content, cachePoint()] }];
        points.push('tail');
    }

    return points.length > 0 ? { input: out, points } : { input, points };
}

/** A Bedrock rejection caused by a cache point, so the caller can retry once without them. */
export const isCachePointRejection = (error) =>
    error?.name === 'ValidationException' && /cache/i.test(error?.message || '');
