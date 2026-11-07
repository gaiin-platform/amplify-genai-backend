import test from 'node:test';
import assert from 'node:assert/strict';
import { addCachePoints, supportsPromptCaching, tailIsReusable, isCachePointRejection } from './promptCache.js';

const POINT = { cachePoint: { type: 'default' } };
const user = (text) => ({ role: 'user', content: [{ text }] });
const assistant = (text) => ({ role: 'assistant', content: [{ text }] });
const claude = (extra = {}) => ({
    modelId: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    system: [{ text: 'You are helpful' }],
    messages: [user('hi')],
    ...extra,
});

test('only Claude models are cached', () => {
    assert.equal(supportsPromptCaching('us.anthropic.claude-sonnet-5'), true);
    assert.equal(supportsPromptCaching('global.anthropic.claude-opus-4-7'), true);
    for (const id of ['us.openai.gpt-5.6-luna', 'us.amazon.nova-pro-v1:0', 'us.meta.llama3', undefined]) {
        assert.equal(supportsPromptCaching(id), false);
    }
    const input = claude({ modelId: 'us.openai.gpt-5.6-luna' });
    const result = addCachePoints(input);
    assert.equal(result.input, input);
    assert.deepEqual(result.points, []);
});

test('system is checkpointed on a single-turn request, no tail', () => {
    const { input, points } = addCachePoints(claude());
    assert.deepEqual(points, ['system']);
    assert.deepEqual(input.system.at(-1), POINT);
    assert.equal(input.messages.at(-1).content.length, 1);
});

test('tools get a checkpoint after the last tool', () => {
    const tools = [{ toolSpec: { name: 'a' } }, { toolSpec: { name: 'b' } }];
    const { input, points } = addCachePoints(claude({ toolConfig: { tools, toolChoice: { auto: {} } } }));
    assert.deepEqual(points, ['tools', 'system']);
    assert.deepEqual(input.toolConfig.tools.at(-1), POINT);
    assert.equal(input.toolConfig.tools.length, 3);
    assert.deepEqual(input.toolConfig.toolChoice, { auto: {} });
});

test('tail is added for multi-turn conversations', () => {
    const messages = [user('q1'), assistant('a1'), user('q2')];
    const { input, points } = addCachePoints(claude({ messages }));
    assert.deepEqual(points, ['system', 'tail']);
    assert.deepEqual(input.messages.at(-1).content, [{ text: 'q2' }, POINT]);
    assert.equal(input.messages.length, 3);
});

test('tail is added for a tool-loop continuation', () => {
    const messages = [
        user('find it'),
        { role: 'assistant', content: [{ toolUse: { toolUseId: 't1', name: 'search', input: {} } }] },
        { role: 'user', content: [{ toolResult: { toolUseId: 't1', content: [{ text: 'result' }] } }] },
    ];
    assert.equal(tailIsReusable(messages), true);
    const { input } = addCachePoints(claude({ messages }));
    assert.deepEqual(input.messages.at(-1).content.at(-1), POINT);
});

test('no tail for internal one-shot calls or a single user message', () => {
    assert.equal(tailIsReusable([user('hi')]), false);
    assert.equal(tailIsReusable([user('a'), assistant('b'), user('c')], true), false);
    assert.equal(tailIsReusable([]), false);
    const { points } = addCachePoints(claude({ messages: [user('a'), assistant('b'), user('c')] }), { isInternalCall: true });
    assert.deepEqual(points, ['system']);
});

test('the input is never mutated', () => {
    const messages = [user('q1'), assistant('a1'), user('q2')];
    const tools = [{ toolSpec: { name: 'a' } }];
    const original = claude({ messages, toolConfig: { tools } });
    const snapshot = JSON.stringify(original);
    addCachePoints(original);
    assert.equal(JSON.stringify(original), snapshot);
});

test('never exceeds 4 checkpoints and never adds one to empty blocks', () => {
    const full = addCachePoints(claude({
        toolConfig: { tools: [{ toolSpec: { name: 'a' } }] },
        messages: [user('q1'), assistant('a1'), user('q2')],
    }));
    const count = JSON.stringify(full.input).split('cachePoint').length - 1;
    assert.equal(count, 3);

    const empty = addCachePoints(claude({ system: [], messages: [{ role: 'user', content: [] }] }));
    assert.deepEqual(empty.points, []);
});

test('cache point rejection detection', () => {
    assert.equal(isCachePointRejection({ name: 'ValidationException', message: 'cachePoint is not supported for this model' }), true);
    assert.equal(isCachePointRejection({ name: 'ValidationException', message: 'Malformed input request' }), false);
    assert.equal(isCachePointRejection({ name: 'ThrottlingException', message: 'cache' }), false);
    assert.equal(isCachePointRejection(null), false);
});
