import test from 'node:test';
import assert from 'node:assert/strict';
import { AMPLIFY_HELPER_ASSISTANT_ID, BUILTIN_PROMPTS, addModelSystemPrompt, composeOrdinaryChatMessages, normalizeSystemMessagesForProvider } from './systemPrompts.js';
import { createDeploymentConfigLoader, isAmplifyHelperAllowed, normalizeDeploymentConfig } from './adminConfig.js';
import { isEligibleOrdinaryChat } from './ordinaryChatRouter.js';

test('normalizes missing prompts to safe runtime defaults and defaults highlighter/memory off', () => {
    const config = normalizeDeploymentConfig(null, null);
    assert.deepEqual(config.availability, {
        promptHighlighter: false, artifacts: true, webSearch: true, codeInterpreter: true, memory: false
    });
    assert.equal(config.prompts['ordinaryChat.base'], '');
    assert.equal(config.allowClassicUiSwitch, true);
});

test('helper access follows the feature flag, including user exceptions', () => {
    const cfg = (flag) => normalizeDeploymentConfig(null, null, { featureFlagsRecord: { amplifyHelper: flag } });
    assert.equal(isAmplifyHelperAllowed(cfg({ enabled: false }), 'u1'), false);
    assert.equal(isAmplifyHelperAllowed(cfg({ enabled: true }), 'u1'), true);
    assert.equal(isAmplifyHelperAllowed(cfg({ enabled: false, userExceptions: ['u1'] }), 'u1'), true);
    assert.equal(isAmplifyHelperAllowed(cfg({ enabled: true, userExceptions: ['u1'] }), 'u1'), false);
    assert.equal(isAmplifyHelperAllowed(cfg({ enabled: false, userExceptions: ['u1'] }), 'u2'), false);
    assert.equal(isAmplifyHelperAllowed(normalizeDeploymentConfig(null, null), 'u1'), false);
    assert.equal(isAmplifyHelperAllowed(null, 'u1'), false);
});

test('an unreadable featureFlags record only disables the helper and leaves other config intact', async () => {
    const loader = createDeploymentConfigLoader({
        getConfigItem: async (key) => {
            if (key === 'featureFlags') throw new Error('flags offline');
            if (key === 'deploymentFeatures') return { schemaVersion: 1, availability: { webSearch: true, artifacts: false } };
            return { schemaVersion: 1, prompts: { 'ordinaryChat.base': { version: 1, text: 'custom' } } };
        }
    });
    const config = await loader();
    assert.equal(config.status, 'loaded');
    assert.equal(config.prompts['ordinaryChat.base'], 'custom');
    assert.equal(config.availability.artifacts, false);
    assert.equal(isAmplifyHelperAllowed(config, 'u1'), false);
});

test('the built-in helper stays an eligible ordinary chat; other assistant ids do not', () => {
    const body = (options) => ({ messages: [{ role: 'user', content: 'hi' }], options });
    assert.equal(isEligibleOrdinaryChat({}, body({ assistantId: AMPLIFY_HELPER_ASSISTANT_ID, amplifyHelper: true })), true);
    assert.equal(isEligibleOrdinaryChat({}, body({ assistantId: AMPLIFY_HELPER_ASSISTANT_ID })), false);
    assert.equal(isEligibleOrdinaryChat({}, body({ assistantId: 'astp/other', amplifyHelper: true })), false);
    assert.equal(isEligibleOrdinaryChat({}, body({ assistantId: AMPLIFY_HELPER_ASSISTANT_ID, amplifyHelper: true, groupId: 'g' })), false);
    assert.equal(isEligibleOrdinaryChat({}, body({})), true);
});

test('helper falls back to the built-in prompt when the admin text is empty', () => {
    const messages = composeOrdinaryChatMessages([{ role: 'user', content: 'hi' }], {
        promptSettings: { prompts: { 'amplifyHelper.base': '' } },
        includeAmplifyHelper: true
    });
    assert.ok(messages.some(m => m.content === BUILTIN_PROMPTS['amplifyHelper.base']));
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
            if (key === 'systemPrompts') {
                return { schemaVersion: 1, prompts: { 'ordinaryChat.base': { version: 1, text: 'custom' } } };
            }
            if (key === 'deploymentFeatures') {
                return { schemaVersion: 1, availability: { webSearch: false } };
            }
            return { amplifyHelper: { enabled: true } };
        }
    });
    const [a, b] = await Promise.all([loader(), loader()]);
    assert.equal(a, b);
    assert.equal(calls, 3);
    assert.equal((await loader()).prompts['ordinaryChat.base'], 'custom');
    assert.equal(calls, 3);
    now += 1001;
    await loader();
    assert.equal(calls, 6);
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

test('includes the configured Amplify Helper prompt only when explicitly selected', () => {
    const settings = { prompts: {
        'ordinaryChat.base': 'ordinary',
        'amplifyHelper.base': 'helper policy'
    } };
    const ordinary = composeOrdinaryChatMessages([{ role: 'user', content: 'hello' }], { promptSettings: settings });
    assert.equal(ordinary.some(message => message.content === 'helper policy'), false);

    const helper = composeOrdinaryChatMessages([{ role: 'user', content: 'hello' }], {
        promptSettings: settings,
        includeAmplifyHelper: true
    });
    assert.deepEqual(helper.slice(0, 3).map(message => message.content), ['ordinary', 'helper policy', 'hello']);
});

test('deduplicates an Amplify Helper prompt already present in request systems', () => {
    const messages = composeOrdinaryChatMessages([
        { role: 'system', content: 'helper policy' },
        { role: 'user', content: 'hello' }
    ], {
        promptSettings: { prompts: {
            'ordinaryChat.base': 'ordinary',
            'amplifyHelper.base': 'helper policy'
        } },
        includeAmplifyHelper: true
    });
    assert.equal(messages.filter(message => message.content === 'helper policy').length, 1);
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
