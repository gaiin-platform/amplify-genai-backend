import test from 'node:test';
import assert from 'node:assert/strict';
import {
    getBedrockCachingProfile,
    getBedrockCachingConfig,
    planCachePoints,
    applyBedrockCaching,
    applyCachePlan,
    SYSTEM_PLACEHOLDER,
    IMAGE_TOKEN_ESTIMATE,
} from './bedrockCaching.js';

const HAIKU = 'us.anthropic.claude-haiku-4-5-20251001-v1:0';
const SONNET46 = 'us.anthropic.claude-sonnet-4-6';
const SONNET5 = 'us.anthropic.claude-sonnet-5';
const OPUS5 = 'us.anthropic.claude-opus-5';

// 1 token per character keeps the arithmetic obvious.
const count = (s) => (s ? String(s).length : 0);
const big = (n) => 'x'.repeat(n);
const ON = { enabled: true, points: ['tools', 'system', 'history', 'tail'], modelPatterns: [] };
const CP = { cachePoint: { type: 'default' } };

const tool = (name, size = 0) => ({ toolSpec: { name, description: big(size), inputSchema: { json: { type: 'object', properties: {} } } } });
const U = (text) => ({ role: 'user', content: [{ text }] });
const A = (text) => ({ role: 'assistant', content: [{ text }] });
const AUse = (id) => ({ role: 'assistant', content: [{ toolUse: { toolUseId: id, name: 'f', input: { a: 1 } } }] });
const UResult = (id, text) => ({ role: 'user', content: [{ toolResult: { toolUseId: id, content: [{ text }] } }] });

const plan = (over) => planCachePoints({ modelId: SONNET46, countTokens: count, config: ON, messages: [U('hi')], ...over });
const clone = (v) => JSON.parse(JSON.stringify(v));

// ---------------------------------------------------------------- profiles

test('profile: supported Claude models and minimums', () => {
    assert.equal(getBedrockCachingProfile(HAIKU).minTokens, 4096);
    assert.equal(getBedrockCachingProfile(SONNET46).minTokens, 1024);
    assert.equal(getBedrockCachingProfile(SONNET5).minTokens, 1024);
    assert.equal(getBedrockCachingProfile(OPUS5).minTokens, 512);
});

test('profile: region prefixes and bare ids', () => {
    for (const id of ['us.anthropic.claude-sonnet-4-6', 'global.anthropic.claude-sonnet-4-6', 'eu.anthropic.claude-sonnet-4-6', 'anthropic.claude-sonnet-4-6', 'claude-sonnet-4-6']) {
        assert.equal(getBedrockCachingProfile(id)?.family, 'claude-sonnet', id);
    }
});

test('profile: tolerant of future ids (conservative minimum for unlisted versions)', () => {
    assert.equal(getBedrockCachingProfile('us.anthropic.claude-opus-5-5').minTokens, 512);
    assert.equal(getBedrockCachingProfile('us.anthropic.claude-sonnet-5-7').minTokens, 1024);
    assert.equal(getBedrockCachingProfile('us.anthropic.claude-sonnet-6').minTokens, 1024);
    assert.equal(getBedrockCachingProfile('us.anthropic.claude-haiku-5-5').minTokens, 512);
    assert.equal(getBedrockCachingProfile('us.anthropic.claude-opus-4-7').minTokens, 4096);
    assert.equal(getBedrockCachingProfile('us.anthropic.claude-opus-4-8').minTokens, 1024);
});

test('profile: trailing date is not read as a minor version', () => {
    // claude-sonnet-4-20250514 is Sonnet 4.0 (not in the doc table) -> null, not "4.20"
    assert.equal(getBedrockCachingProfile('us.anthropic.claude-sonnet-4-20250514-v1:0'), null);
});

test('profile: unsupported / unknown models -> null', () => {
    for (const id of [
        'us.amazon.nova-pro-v1:0', 'us.openai.gpt-5.6-luna', 'global.openai.gpt-5.6-terra', 'gpt-5.2',
        'meta.llama3-70b', 'mistral.mistral-large', 'deepseek.r1-v1:0',
        'anthropic.claude-3-5-sonnet-20241022-v2:0', 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'us.anthropic.claude-opus-4-1-20250805-v1:0', 'us.anthropic.claude-3-5-haiku-20241022-v1:0',
        '', undefined, null, 42,
    ]) assert.equal(getBedrockCachingProfile(id), null, String(id));
});

// ---------------------------------------------------------------- config

test('config: defaults are off, all points, no model narrowing', () => {
    const c = getBedrockCachingConfig({});
    assert.equal(c.enabled, false);
    assert.deepEqual(c.points, ['tools', 'system', 'history', 'tail']);
    assert.deepEqual(c.modelPatterns, []);
});

test('config: on/off parsing', () => {
    assert.equal(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING: 'on' }).enabled, true);
    assert.equal(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING: 'ON ' }).enabled, true);
    assert.equal(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING: 'off' }).enabled, false);
    assert.equal(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING: '' }).enabled, false);
    assert.equal(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING: 'banana' }).enabled, false);
});

test('config: points subset, trimming, unknown names dropped, empty string = default', () => {
    assert.deepEqual(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING_POINTS: 'system, history' }).points, ['system', 'history']);
    assert.deepEqual(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING_POINTS: 'system,bogus,system' }).points, ['system']);
    assert.deepEqual(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING_POINTS: 'bogus' }).points, []);
    assert.deepEqual(getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING_POINTS: '' }).points, ['tools', 'system', 'history', 'tail']);
});

test('config: model regexes narrow the match; invalid regex is ignored', () => {
    const c = getBedrockCachingConfig({ BEDROCK_PROMPT_CACHING: 'on', BEDROCK_PROMPT_CACHING_MODELS: 'sonnet-5, (' });
    assert.equal(c.modelPatterns.length, 1);
    const sys = [{ text: big(2000) }];
    assert.equal(planCachePoints({ modelId: SONNET5, system: sys, messages: [U('hi')], countTokens: count, config: c }).enabled, true);
    const narrowed = planCachePoints({ modelId: SONNET46, system: sys, messages: [U('hi')], countTokens: count, config: c });
    assert.equal(narrowed.enabled, false);
    assert.equal(narrowed.reason, 'model-not-in-allowlist');
});

// ---------------------------------------------------------------- planning

test('plan: disabled config -> nothing, and apply returns the same input reference', () => {
    const input = { modelId: SONNET46, system: [{ text: big(5000) }], messages: [U('hi')] };
    const p = planCachePoints({ modelId: SONNET46, system: input.system, messages: input.messages, countTokens: count, config: { ...ON, enabled: false } });
    assert.equal(p.enabled, false);
    assert.equal(applyBedrockCaching(input, { countTokens: count, config: { ...ON, enabled: false } }), input);
});

test('plan: models without a profile get no checkpoints', () => {
    for (const modelId of ['us.amazon.nova-pro-v1:0', 'us.openai.gpt-5.6-luna', 'meta.llama3-70b', undefined]) {
        const input = { modelId, system: [{ text: big(9000) }], messages: [U('hi'), A('yo'), U('again')] };
        assert.equal(applyBedrockCaching(input, { countTokens: count, config: ON }), input, String(modelId));
    }
});

test('plan C2 system: placed when tools+system meet the minimum, skipped below it', () => {
    assert.deepEqual(plan({ system: [{ text: big(1024) }] }).points, ['system']);
    assert.deepEqual(plan({ system: [{ text: big(1023) }] }).points, []);
});

test('plan C2 system: placeholder is never checkpointed, even when huge tools exist', () => {
    const p = plan({ system: [{ text: SYSTEM_PLACEHOLDER }], tools: [tool('a', 5000)] });
    assert.ok(!p.points.includes('system'));
    assert.ok(p.points.includes('tools'));
    assert.ok(!plan({ system: [{ text: SYSTEM_PLACEHOLDER }] }).points.includes('system'));
    assert.ok(!plan({ system: undefined }).points.includes('system'));
});

test('plan C2 system: tools tokens count toward the system minimum', () => {
    const p = plan({ system: [{ text: big(600) }], tools: [tool('a', 600)] });
    assert.deepEqual(p.points, ['system']); // tools alone < 1024, tools+system >= 1024
});

test('plan C1 tools: placed when tools alone meet the minimum', () => {
    const p = plan({ system: [{ text: big(10) }], tools: [tool('a', 1500)] });
    assert.ok(p.points.includes('tools'));
    assert.ok(p.points.includes('system'));
});

test('plan C1 tools: skipped for stub tools and for tools below the minimum', () => {
    assert.ok(!plan({ tools: [tool('a', 5000)], toolsAreStub: true }).points.includes('tools'));
    assert.ok(!plan({ tools: [tool('a', 10)] }).points.includes('tools'));
    assert.ok(!plan({ tools: [] }).points.includes('tools'));
});

test('plan C3 history: last assistant message (no toolUse) followed by another message', () => {
    const messages = [U(big(800)), A(big(800)), U('q2')];
    const p = plan({ messages });
    assert.deepEqual(p.points, ['history']);
    assert.equal(p.messageIndexes.history, 1);
});

test('plan C3 history: picks the LAST eligible assistant message', () => {
    const p = plan({ messages: [U(big(800)), A(big(400)), U('q2'), A('a2'), U('q3')] });
    assert.equal(p.messageIndexes.history, 3);
});

test('plan C3 history: skips toolUse assistants, skips a trailing assistant, skips below minimum, none without assistants', () => {
    // toolUse assistant is not eligible; falls back to the earlier plain assistant
    assert.equal(plan({ messages: [U(big(900)), A(big(300)), U('q'), AUse('t1'), UResult('t1', 'r')] }).messageIndexes.history, 1);
    // an assistant as the LAST message has nothing after it
    assert.ok(!plan({ messages: [U(big(2000)), A(big(2000))] }).points.includes('history'));
    // below minimum
    assert.ok(!plan({ messages: [U('a'), A('b'), U('c')] }).points.includes('history'));
    // no assistant message at all
    assert.ok(!plan({ messages: [U(big(5000))] }).points.includes('history'));
});

test('plan C4 tail: only on tool-loop continuation (last message has toolResult)', () => {
    const cont = [U(big(500)), AUse('t1'), UResult('t1', big(700))];
    const p = plan({ messages: cont });
    assert.deepEqual(p.points, ['tail']);
    assert.equal(p.messageIndexes.tail, 2);
    // the first call of a turn (plain user last message) never gets a tail point
    assert.ok(!plan({ messages: [U(big(5000))] }).points.includes('tail'));
    assert.ok(!plan({ messages: [U(big(2000)), A('a'), U(big(2000))] }).points.includes('tail'));
});

test('plan C4 tail: skipped when the prefix is below the minimum', () => {
    assert.ok(!plan({ messages: [U('q'), AUse('t1'), UResult('t1', 'small')] }).points.includes('tail'));
});

test('plan C3+C4: never target the same message', () => {
    // history is an assistant index, tail is the last (user) index; they cannot coincide, and the planner enforces it.
    const p = plan({ messages: [U(big(600)), A(big(600)), U('q'), AUse('t1'), UResult('t1', big(100))] });
    assert.deepEqual(p.points.sort(), ['history', 'tail']);
    assert.notEqual(p.messageIndexes.history, p.messageIndexes.tail);
});

test('plan: all four points and never more than 4', () => {
    const p = plan({
        system: [{ text: big(1500) }],
        tools: [tool('a', 1500)],
        messages: [U(big(300)), A(big(300)), U('q'), AUse('t1'), UResult('t1', big(300))],
    });
    assert.deepEqual([...p.points].sort(), ['history', 'system', 'tail', 'tools']);
    assert.ok(p.points.length <= 4);
});

test('plan: points flag restricts what is planned', () => {
    const base = {
        system: [{ text: big(1500) }],
        tools: [tool('a', 1500)],
        messages: [U(big(300)), A(big(300)), U('q'), AUse('t1'), UResult('t1', big(300))],
    };
    assert.deepEqual(plan({ ...base, config: { ...ON, points: ['system'] } }).points, ['system']);
    assert.deepEqual(plan({ ...base, config: { ...ON, points: ['history', 'tail'] } }).points.sort(), ['history', 'tail']);
    assert.deepEqual(plan({ ...base, config: { ...ON, points: [] } }).points, []);
});

test('plan: images count as a fixed estimate and are never stringified', () => {
    const imageMsg = { role: 'user', content: [{ text: 'look' }, { image: { format: 'png', source: { bytes: new Uint8Array(10_000_000) } } }] };
    const p = plan({ messages: [imageMsg, A('ok'), U('next')] });
    assert.ok(IMAGE_TOKEN_ESTIMATE >= 1024);
    assert.deepEqual(p.points, ['history']);
});

test('plan: Haiku 4.5 needs 4096 tokens', () => {
    assert.deepEqual(plan({ modelId: HAIKU, system: [{ text: big(4095) }] }).points, []);
    assert.deepEqual(plan({ modelId: HAIKU, system: [{ text: big(4096) }] }).points, ['system']);
});

// ---------------------------------------------------------------- application

const fullInput = () => ({
    modelId: SONNET46,
    system: [{ text: big(1500) }],
    toolConfig: { tools: [tool('a', 1500)], toolChoice: { auto: {} } },
    messages: [U(big(300)), A(big(300)), U('q'), AUse('t1'), UResult('t1', big(300))],
    inferenceConfig: { maxTokens: 100 },
});

test('apply: inserts cachePoint blocks in the right places', () => {
    const input = fullInput();
    const out = applyBedrockCaching(input, { countTokens: count, config: ON });
    assert.deepEqual(out.toolConfig.tools.at(-1), CP);
    assert.equal(out.toolConfig.tools.length, 2);
    assert.deepEqual(out.toolConfig.toolChoice, { auto: {} });
    assert.deepEqual(out.system.at(-1), CP);
    assert.deepEqual(out.messages[1].content.at(-1), CP); // history: last assistant without toolUse
    assert.deepEqual(out.messages[4].content.at(-1), CP); // tail: toolResult continuation
    assert.deepEqual(out.messages[4].content[0].toolResult.toolUseId, 't1');
    // untouched messages
    assert.deepEqual(out.messages[0], input.messages[0]);
    assert.deepEqual(out.messages[2], input.messages[2]);
    assert.deepEqual(out.messages[3], input.messages[3]);
    assert.deepEqual(out.inferenceConfig, input.inferenceConfig);
});

test('apply: input is not mutated (including shared arrays/objects)', () => {
    const input = fullInput();
    const snapshot = clone(input);
    const sharedMsgs = input.messages;
    const sharedTools = input.toolConfig.tools;
    const sharedSystem = input.system;
    const out = applyBedrockCaching(input, { countTokens: count, config: ON });
    assert.notEqual(out, input);
    assert.deepEqual(clone(input), snapshot);
    assert.equal(input.messages, sharedMsgs);
    assert.equal(input.toolConfig.tools, sharedTools);
    assert.equal(input.system, sharedSystem);
    assert.notEqual(out.messages, input.messages);
    assert.notEqual(out.system, input.system);
    assert.notEqual(out.toolConfig, input.toolConfig);
    assert.notEqual(out.toolConfig.tools, input.toolConfig.tools);
});

test('apply: never emits ttl and never more than 4 cachePoints', () => {
    const out = applyBedrockCaching(fullInput(), { countTokens: count, config: ON });
    const all = [];
    const walk = (v) => {
        if (Array.isArray(v)) v.forEach(walk);
        else if (v && typeof v === 'object') {
            if (v.cachePoint) all.push(v.cachePoint);
            Object.values(v).forEach(walk);
        }
    };
    walk(out);
    assert.ok(all.length >= 1 && all.length <= 4, `count=${all.length}`);
    for (const cp of all) {
        assert.deepEqual(cp, { type: 'default' });
        assert.ok(!('ttl' in cp));
    }
    assert.ok(!JSON.stringify(out).includes('"ttl"'));
});

test('apply: input without optional parts (no system/toolConfig) is handled', () => {
    const input = { modelId: SONNET46, messages: [U(big(1500)), A(big(10)), U('q')] };
    const out = applyBedrockCaching(input, { countTokens: count, config: ON });
    assert.equal(out.system, undefined);
    assert.equal(out.toolConfig, undefined);
    assert.deepEqual(out.messages[1].content.at(-1), CP);
});

test('apply: stub tools and placeholder system get no checkpoint on them', () => {
    const input = {
        modelId: SONNET46,
        system: [{ text: SYSTEM_PLACEHOLDER }],
        toolConfig: { tools: [tool('a', 5000)] },
        messages: [U('q'), AUse('t1'), UResult('t1', 'r')],
    };
    const out = applyBedrockCaching(input, { countTokens: count, config: ON, toolsAreStub: true });
    assert.equal(out.toolConfig.tools.length, 1);
    assert.equal(out.system.length, 1);
});

test('apply: onPlan callback receives the plan', () => {
    let seen;
    applyBedrockCaching(fullInput(), { countTokens: count, config: ON, onPlan: (p) => { seen = p; } });
    assert.ok(seen.enabled);
    assert.ok(Array.isArray(seen.points));
});

test('applyCachePlan: tolerates a stale plan (out-of-range index / non-array content)', () => {
    const input = { modelId: SONNET46, messages: [{ role: 'assistant', content: 'plain string' }] };
    const out = applyCachePlan(input, { enabled: true, points: ['history'], messageIndexes: { history: 0 } });
    assert.deepEqual(out.messages, input.messages);
    const out2 = applyCachePlan(input, { enabled: true, points: ['tail'], messageIndexes: { tail: 9 } });
    assert.deepEqual(out2.messages, input.messages);
});
