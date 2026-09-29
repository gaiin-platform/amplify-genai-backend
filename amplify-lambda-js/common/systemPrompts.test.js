import test from 'node:test';
import assert from 'node:assert/strict';
import { BUILTIN_PROMPTS, addModelSystemPrompt, composeOrdinaryChatMessages, normalizeSystemMessagesForProvider } from './systemPrompts.js';
import { createDeploymentConfigLoader, normalizeDeploymentConfig } from './adminConfig.js';

test('normalizes missing prompts to safe runtime defaults and defaults highlighter/memory off', () => {
    const config = normalizeDeploymentConfig(null, null);
    assert.deepEqual(config.availability, {
        promptHighlighter: false, artifacts: true, webSearch: true, codeInterpreter: true, memory: false
    });
    assert.equal(config.prompts['ordinaryChat.base'], '');
    assert.equal(config.allowClassicUiSwitch, true);
});

test('runtime accepts prompts at 16 KiB UTF-8 and falls back for oversized Unicode values', () => {
    const config = normalizeDeploymentConfig({
        schemaVersion: 1,
        prompts: {
            'ordinaryChat.base': { version: 1, text: 'a'.repeat(16 * 1024) },
            'webSearch.use': { version: 1, text: '界'.repeat(5461) + 'aa' },
            'artifacts.generate': { version: '1', text: 'bad version' },
            'amplifyHelper.base': { version: 1, text: '😀'.repeat(4097) }
        }
    }, null);
    assert.equal(config.prompts['ordinaryChat.base'].length, 16 * 1024);
    assert.equal(config.prompts['webSearch.use'], '');
    assert.equal(config.prompts['artifacts.generate'], '');
    assert.equal(config.prompts['amplifyHelper.base'], '');
});

test('runtime reader preserves exact CJK and emoji UTF-8 byte boundaries', () => {
    const config = normalizeDeploymentConfig({
        schemaVersion: 1,
        prompts: {
            'ordinaryChat.base': { version: 1, text: '界'.repeat(5461) + 'a' },
            'webSearch.use': { version: 1, text: '😀'.repeat(4096) }
        }
    }, null);
    assert.equal(Buffer.byteLength(config.prompts['ordinaryChat.base'], 'utf8'), 16 * 1024);
    assert.equal(Buffer.byteLength(config.prompts['webSearch.use'], 'utf8'), 16 * 1024);
});

test('fails optional features closed without a readable or last-known-good feature record', async () => {
    const loader = createDeploymentConfigLoader({ getConfigItem: async () => { throw new Error('offline'); } });
    const config = await loader();
    assert.equal(config.availability.webSearch, false);
    assert.equal(config.availability.artifacts, false);
    assert.equal(config.status, 'unavailable');
});

test('deduplicates concurrent config reads and caches loaded config', async () => {
    let calls = 0;
    let now = 100;
    const loader = createDeploymentConfigLoader({
        ttlMs: 1000,
        now: () => now,
        getConfigItem: async (key) => {
            calls++;
            await new Promise(resolve => setImmediate(resolve));
            return key === 'systemPrompts'
                ? { schemaVersion: 1, prompts: { 'ordinaryChat.base': { version: 1, text: 'custom' } } }
                : { schemaVersion: 1, availability: { webSearch: false } };
        }
    });
    const [a, b] = await Promise.all([loader(), loader()]);
    assert.equal(a, b);
    assert.equal(calls, 2);
    assert.equal((await loader()).prompts['ordinaryChat.base'], 'custom');
    assert.equal(calls, 2);
    now += 1001;
    await loader();
    assert.equal(calls, 4);
});

test('composes base, request systems, model prompt, then enabled feature prompt without duplicates', () => {
    const messages = composeOrdinaryChatMessages([
        { role: 'system', content: 'request system' },
        { role: 'user', content: 'hello' }
    ], {
        promptSettings: { prompts: { 'ordinaryChat.base': 'admin base', 'webSearch.use': 'search rules' } },
        requestPrompt: 'request system',
        modelSystemPrompt: 'model rules',
        activeFeatures: { webSearch: true, artifacts: false }
    });
    assert.deepEqual(messages.slice(0, 4).map(m => m.content), ['admin base', 'request system', 'model rules', 'search rules']);
    assert.deepEqual(messages[4], { role: 'user', content: 'hello' });
});

test('keeps model prompt on internal and specialized calls without duplicating request systems', () => {
    const messages = addModelSystemPrompt([
        { role: 'system', content: 'specialized instructions' },
        { role: 'user', content: 'run the task' }
    ], 'model rules');
    assert.equal(messages[0].content, 'specialized instructions\n\nmodel rules');
    assert.equal(addModelSystemPrompt(messages, 'model rules').filter(m => m.content.includes('model rules')).length, 1);
    assert.deepEqual(addModelSystemPrompt([{ role: 'user', content: 'plain' }], ''), [{ role: 'user', content: 'plain' }]);
});

test('ordinary chat composes its model prompt once and internal/specialized prompt composition excludes ordinary defaults', () => {
    const ordinary = composeOrdinaryChatMessages([{ role: 'user', content: 'ordinary' }], { modelSystemPrompt: 'model rules' });
    assert.equal(ordinary.filter(message => message.content === 'model rules').length, 1);
    const specialized = addModelSystemPrompt([{ role: 'system', content: 'specialized rules' }, { role: 'user', content: 'task' }], 'model rules');
    assert.deepEqual(specialized.slice(0, 2).map(message => message.content), ['specialized rules\n\nmodel rules', 'task']);
});

test('preserves system prompt content for providers without system-role support', () => {
    const messages = normalizeSystemMessagesForProvider([
        { role: 'system', content: 'base and model instructions' },
        { role: 'user', content: 'question' }
    ], false);
    assert.equal(messages.length, 1);
    assert.equal(messages[0].role, 'user');
    assert.match(messages[0].content, /base and model instructions/);
    assert.match(messages[0].content, /question/);
});

test('falls back to built-in prompts when config values are empty', () => {
    const messages = composeOrdinaryChatMessages([{ role: 'user', content: 'hi' }], {
        promptSettings: { prompts: { 'ordinaryChat.base': '' } }
    });
    assert.equal(messages[0].content, BUILTIN_PROMPTS['ordinaryChat.base']);
});
