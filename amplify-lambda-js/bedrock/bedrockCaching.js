/**
 * Explicit prompt caching (Converse `cachePoint` blocks) for Bedrock Claude models.
 *
 * Pure functions only: no AWS calls, no module-level state, no logger/secret imports, so the whole
 * thing is unit-testable. See docs/chat-request-architecture/10-bedrock-prompt-caching-plan.md.
 *
 * Checkpoints (max 4, processed by Bedrock in the order tools -> system -> messages):
 *   tools    last element of toolConfig.tools        (real tools only, never the stub rebuilt from history)
 *   system   last element of system[]                (never the BLANK placeholder)
 *   history  last block of the last assistant message with no toolUse that has a later message
 *   tail     last block of the last message, only when it is a tool-loop continuation (has a toolResult)
 *
 * Minimum-token rules (Bedrock doc, checked 2026-11-04) apply to the CUMULATIVE prefix before each
 * checkpoint. A checkpoint below the minimum does not fail the request, it is just not cached, so
 * skipping them here only avoids pointless payload / cache-write attempts.
 *
 * We never send `ttl` (5-minute default).
 */

export const CACHE_POINT_KINDS = ['tools', 'system', 'history', 'tail'];
export const SYSTEM_PLACEHOLDER = 'Intentionally Left Blank, please ignore';
export const IMAGE_TOKEN_ESTIMATE = 1600;
export const MAX_CACHE_POINTS = 4;

const CACHE_POINT = () => ({ cachePoint: { type: 'default' } });

// `us.anthropic.claude-sonnet-4-6`, `global.anthropic.claude-opus-5`, `anthropic.claude-haiku-4-5-2025...-v1:0`.
// The minor version is 1-2 digits NOT followed by another digit, so a trailing 8-digit date is never read as a minor.
const CLAUDE_ID = /(?:^|[./])claude-(haiku|sonnet|opus)-(\d+)(?:-(\d{1,2})(?!\d))?/i;

/**
 * Minimum cumulative prefix tokens per checkpoint, from the Bedrock doc table (2026-11-04).
 * Keyed `family-major.minor`.
 */
const DOC_MIN_TOKENS = {
    'haiku-4.5': 4096,
    'haiku-5.5': 512,
    'sonnet-4.6': 1024,
    'sonnet-5.0': 1024,
    'sonnet-5.5': 512,
    'opus-4.5': 4096,
    'opus-4.6': 4096,
    'opus-4.7': 4096,
    'opus-4.8': 1024,
    'opus-5.0': 512,
    'opus-5.5': 512,
};
// Oldest version of each family that supports explicit caching per the doc table. Older versions
// (Sonnet 4.5 and below, Haiku <4.5, Opus <4.5) are not listed, so they get NO checkpoints.
const FIRST_SUPPORTED = { haiku: [4, 5], sonnet: [4, 6], opus: [4, 5] };
const CONSERVATIVE_MIN_TOKENS = 1024;

function minTokensFor(family, major, minor) {
    const first = FIRST_SUPPORTED[family];
    if (!first) return null;
    if (major < first[0] || (major === first[0] && minor < first[1])) return null;
    // Versions the doc does not list (e.g. a future Sonnet 5.7) use the conservative value.
    return DOC_MIN_TOKENS[`${family}-${major}.${minor}`] ?? CONSERVATIVE_MIN_TOKENS;
}

/**
 * @param {string} modelId
 * @returns {{family: string, version: string, minTokens: number}|null}  null = no caching for this model
 */
export function getBedrockCachingProfile(modelId) {
    if (typeof modelId !== 'string' || !modelId) return null;
    const m = CLAUDE_ID.exec(modelId);
    if (!m) return null;
    const family = m[1].toLowerCase();
    const major = parseInt(m[2], 10);
    const minor = m[3] !== undefined ? parseInt(m[3], 10) : 0;
    const minTokens = minTokensFor(family, major, minor);
    if (!minTokens) return null;
    return { family: `claude-${family}`, version: `${major}.${minor}`, minTokens };
}

const TRUE_VALUES = new Set(['on', 'true', '1', 'yes']);

/**
 * Parse the env flags. Defaults: off, all four points, built-in model families.
 * @returns {{enabled: boolean, points: string[], modelPatterns: RegExp[]}}
 */
export function getBedrockCachingConfig(env = process.env) {
    const enabled = TRUE_VALUES.has(String(env?.BEDROCK_PROMPT_CACHING ?? '').trim().toLowerCase());

    const rawPoints = String(env?.BEDROCK_PROMPT_CACHING_POINTS ?? '').trim();
    const points = rawPoints
        ? rawPoints.split(',').map(p => p.trim().toLowerCase()).filter(p => CACHE_POINT_KINDS.includes(p))
        : [...CACHE_POINT_KINDS];

    const modelPatterns = [];
    for (const raw of String(env?.BEDROCK_PROMPT_CACHING_MODELS ?? '').split(',')) {
        const src = raw.trim();
        if (!src) continue;
        try {
            modelPatterns.push(new RegExp(src, 'i'));
        } catch {
            // An invalid pattern must never break a chat request; it simply narrows nothing.
        }
    }
    return { enabled, points: [...new Set(points)], modelPatterns };
}

// ---------------------------------------------------------------------------------------------
// Token estimation
// ---------------------------------------------------------------------------------------------

const safeStringify = (v) => {
    try { return JSON.stringify(v) ?? ''; } catch { return ''; }
};

function countToolResultContent(content, countTokens) {
    if (!Array.isArray(content)) return countTokens(typeof content === 'string' ? content : safeStringify(content));
    let total = 0;
    for (const c of content) {
        if (c?.text !== undefined) total += countTokens(c.text);
        else if (c?.json !== undefined) total += countTokens(safeStringify(c.json));
        else if (c?.image || c?.document || c?.video) total += IMAGE_TOKEN_ESTIMATE;
        else total += countTokens(safeStringify(c));
    }
    return total;
}

export function estimateBlockTokens(block, countTokens) {
    if (!block || typeof block !== 'object') return 0;
    if (block.cachePoint) return 0;
    if (block.text !== undefined) return countTokens(block.text);
    // Binary payloads (Uint8Array) must never be stringified.
    if (block.image || block.document || block.video) return IMAGE_TOKEN_ESTIMATE;
    if (block.toolUse) return countTokens(`${block.toolUse.name ?? ''}${safeStringify(block.toolUse.input ?? {})}`);
    if (block.toolResult) return countToolResultContent(block.toolResult.content, countTokens);
    if (block.reasoningContent) return countTokens(safeStringify(block.reasoningContent));
    return countTokens(safeStringify(block));
}

export function estimateMessageTokens(message, countTokens) {
    const content = message?.content;
    if (typeof content === 'string') return countTokens(content);
    if (!Array.isArray(content)) return 0;
    return content.reduce((sum, b) => sum + estimateBlockTokens(b, countTokens), 0);
}

const systemText = (system) => (Array.isArray(system) ? system : [])
    .map(b => (typeof b?.text === 'string' ? b.text.trim() : ''))
    .filter(Boolean);

// ---------------------------------------------------------------------------------------------
// Planning
// ---------------------------------------------------------------------------------------------

const modelAllowedByConfig = (modelId, config) =>
    !config.modelPatterns?.length || config.modelPatterns.some(re => re.test(modelId));

const isRealBlockArray = (content) => Array.isArray(content) && content.length > 0;

/**
 * Decide which checkpoints apply. Pure: does not touch its inputs.
 *
 * @param {object} p
 * @param {string} p.modelId
 * @param {Array<{text?:string}>} [p.system]       Converse system blocks
 * @param {Array<object>} [p.tools]                Converse toolConfig.tools
 * @param {boolean} [p.toolsAreStub]               tools were reconstructed from history (placeholder specs)
 * @param {Array<object>} p.messages               Converse messages
 * @param {(text:string)=>number} p.countTokens
 * @param {{enabled:boolean, points:string[], modelPatterns:RegExp[]}} [p.config]
 * @param {string} [p.systemPlaceholder]
 * @returns {{enabled:boolean, reason?:string, profile?:object, points:string[], messageIndexes:{history?:number, tail?:number}, estimates:object}}
 */
export function planCachePoints({
    modelId,
    system,
    tools,
    toolsAreStub = false,
    messages,
    countTokens,
    config = getBedrockCachingConfig(),
    systemPlaceholder = SYSTEM_PLACEHOLDER,
}) {
    const none = (reason) => ({ enabled: false, reason, points: [], messageIndexes: {}, estimates: {} });

    if (!config?.enabled) return none('disabled');
    const profile = getBedrockCachingProfile(modelId);
    if (!profile) return none('model-not-supported');
    if (!modelAllowedByConfig(modelId, config)) return none('model-not-in-allowlist');
    if (typeof countTokens !== 'function') return none('no-token-counter');

    const allowed = new Set(config.points ?? CACHE_POINT_KINDS);
    const min = profile.minTokens;
    const msgs = Array.isArray(messages) ? messages : [];
    const toolList = Array.isArray(tools) ? tools.filter(t => !t?.cachePoint) : [];

    const toolsTokens = toolList.length ? countTokens(safeStringify(toolList)) : 0;
    const texts = systemText(system);
    const systemTokens = texts.reduce((s, t) => s + countTokens(t), 0);
    const placeholderOnly = texts.length === 0 || texts.every(t => t === systemPlaceholder);

    const points = [];
    const messageIndexes = {};
    const estimates = { tools: toolsTokens, system: systemTokens };

    // C1: tools
    if (allowed.has('tools') && toolList.length > 0 && !toolsAreStub && toolsTokens >= min) {
        points.push('tools');
    }

    // C2: system
    if (allowed.has('system') && !placeholderOnly && toolsTokens + systemTokens >= min) {
        points.push('system');
    }

    // Per-message prefix sums (prefix[i] = tokens through message i, including tools + system).
    const prefix = [];
    let running = toolsTokens + systemTokens;
    for (const m of msgs) {
        running += estimateMessageTokens(m, countTokens);
        prefix.push(running);
    }

    // C3: history boundary = last assistant message without toolUse that is followed by another message.
    let historyIdx = -1;
    if (allowed.has('history')) {
        for (let i = msgs.length - 2; i >= 0; i--) {
            const m = msgs[i];
            if (m?.role === 'assistant' && isRealBlockArray(m.content) && !m.content.some(b => b?.toolUse)) {
                historyIdx = i;
                break;
            }
        }
        if (historyIdx >= 0 && prefix[historyIdx] >= min) {
            points.push('history');
            messageIndexes.history = historyIdx;
        }
    }

    // C4: tail = last message when it is a tool-loop continuation (user message carrying a toolResult).
    if (allowed.has('tail') && msgs.length > 0) {
        const lastIdx = msgs.length - 1;
        const last = msgs[lastIdx];
        const isContinuation = last?.role === 'user' && isRealBlockArray(last.content) && last.content.some(b => b?.toolResult);
        if (isContinuation && lastIdx !== messageIndexes.history && prefix[lastIdx] >= min) {
            points.push('tail');
            messageIndexes.tail = lastIdx;
        }
    }

    estimates.total = running;
    return {
        enabled: points.length > 0,
        reason: points.length > 0 ? undefined : 'below-minimum-or-no-eligible-points',
        profile,
        points: points.slice(0, MAX_CACHE_POINTS),
        messageIndexes,
        estimates,
    };
}

// ---------------------------------------------------------------------------------------------
// Application
// ---------------------------------------------------------------------------------------------

const withCachePointAt = (messages, index) => {
    const out = [...messages];
    const m = out[index];
    out[index] = { ...m, content: [...m.content, CACHE_POINT()] };
    return out;
};

/**
 * Return a NEW input with cachePoint blocks inserted according to `plan`. Never mutates `input`
 * or any array/object reachable from it.
 */
export function applyCachePlan(input, plan) {
    if (!plan?.enabled || !plan.points?.length) return input;
    const next = { ...input };

    if (plan.points.includes('tools') && Array.isArray(input.toolConfig?.tools)) {
        next.toolConfig = { ...input.toolConfig, tools: [...input.toolConfig.tools, CACHE_POINT()] };
    }
    if (plan.points.includes('system') && Array.isArray(input.system)) {
        next.system = [...input.system, CACHE_POINT()];
    }
    if (Array.isArray(input.messages)) {
        let messages = input.messages;
        for (const kind of ['history', 'tail']) {
            const idx = plan.messageIndexes?.[kind];
            if (plan.points.includes(kind) && Number.isInteger(idx) && messages[idx] && Array.isArray(messages[idx].content)) {
                messages = withCachePointAt(messages, idx);
            }
        }
        if (messages !== input.messages) next.messages = messages;
    }
    return next;
}

/**
 * Plan + apply in one call. Returns the input unchanged (same reference) when caching is
 * disabled, the model has no profile, or nothing qualifies.
 *
 * @param {object} input  Converse(Stream) input: { modelId, messages, system?, toolConfig?, ... }
 * @param {object} opts   { countTokens, toolsAreStub?, config?, systemPlaceholder?, onPlan?(plan) }
 */
export function applyBedrockCaching(input, opts = {}) {
    const plan = planCachePoints({
        modelId: input?.modelId,
        system: input?.system,
        tools: input?.toolConfig?.tools,
        toolsAreStub: opts.toolsAreStub,
        messages: input?.messages,
        countTokens: opts.countTokens,
        config: opts.config,
        systemPlaceholder: opts.systemPlaceholder,
    });
    if (typeof opts.onPlan === 'function') opts.onPlan(plan);
    return applyCachePlan(input, plan);
}
