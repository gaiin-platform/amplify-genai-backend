// Tests the execute_code tool loop (truncated calls, Python errors, retries, fallback).
// Needs module mocking, which Node only exposes behind a flag. Run with:
//   node --experimental-test-module-mocks --test assistants/codeInterpreter.loop.test.js
// Without the flag the suite skips itself.
import test, { mock } from 'node:test';
import assert from 'node:assert/strict';

const canMock = typeof mock.module === 'function';
const rel = (p) => new URL(p, import.meta.url).href;

const state = { events: [], llm: [], llmCalls: [], exec: [], execBodies: [], finals: [] };
let codeInterpreterAssistant;

if (canMock) {
    mock.module(rel('../common/streams.js'), { namedExports: {
        sendStatusEventToStream: (s, e) => state.events.push({ status: e }),
        sendStateEventToStream: (s, e) => state.events.push({ state: e }),
        forceFlush: () => {}
    } });
    mock.module(rel('../common/status.js'), { namedExports: { newStatus: (x) => x } });
    mock.module(rel('../common/logging.js'), { namedExports: { getLogger: () => ({ info() {}, warn() {}, error() {}, debug() {} }) } });
    mock.module(rel('../requests/requestState.js'), { namedExports: { isKilled: async () => false } });
    mock.module(rel('../common/criticalLogger.js'), { namedExports: { logCriticalError: async () => {} } });
    mock.module(rel('../common/systemPrompts.js'), { namedExports: { getFeaturePrompt: () => '' } });
    mock.module(rel('../llm/UnifiedLLMClient.js'), { namedExports: {
        callUnifiedLLM: async (params, messages, _x, opts) => {
            state.llmCalls.push({ messages, opts });
            return state.llm[Math.min(state.llmCalls.length - 1, state.llm.length - 1)];
        }
    } });
    ({ codeInterpreterAssistant } = await import('./codeInterpreter.js'));
}

const toolCall = (args) => ({ content: '', tool_calls: [{ id: 'tc', type: 'function', function: { name: 'execute_code', arguments: args } }] });
const good = (code = 'print(1)') => toolCall(JSON.stringify({ code }));
const truncated = () => toolCall('{"code": "from pptx import Presentation\\nprs = Presentation()\\nslide = prs.slides.add_sl');
const okExec = { success: true, data: { data: { textContent: 'done', content: [{ type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', values: { file_key: 'u/1-deck.pptx', file_size: 5 } }] } } };
const pyErr = (m) => ({ success: false, error: 'Code execution error: ' + m });
const isFallback = (f) => (f.messages || []).some(m => m.role === 'system' && /did not successfully complete/.test(m.content));
const reuploadAsked = (f) => (f.messages || []).some(m => m.role === 'system' && /re-upload/.test(m.content));

async function run({ llm, exec }) {
    Object.assign(state, { events: [], llm, llmCalls: [], exec, execBodies: [], finals: [] });
    let execIdx = 0;
    process.env.API_BASE_URL = 'https://api.test';
    globalThis.fetch = async (url, init) => {
        const body = JSON.parse(init.body).data;
        if (url.endsWith('/assistant/create/codeinterpreter')) return { ok: true, json: async () => ({ success: true, data: { codeInterpreterRecordId: 'rec' } }) };
        state.execBodies.push(body);
        const r = exec[Math.min(execIdx++, exec.length - 1)];
        return { ok: true, json: async () => r };
    };
    const assistant = await codeInterpreterAssistant({ handler: async (p, b) => { state.finals.push(b); } });
    await assistant.handler(
        { account: { accessToken: 't', user: 'u', accountId: 'a' }, options: {} },
        { options: { model: { id: 'm', outputTokenLimit: 8192 }, maxTokens: 2048, requestId: 'r' }, messages: [{ role: 'user', content: 'generate a ppt on AI' }], dataSources: [] },
        [], {});
    return state;
}

const opts = { skip: canMock ? false : 'needs --experimental-test-module-mocks' };

test('truncated script is never sent as empty code; the model retries and succeeds', opts, async () => {
    const s = await run({ llm: [truncated(), good('print(1)'), { content: 'Here is your deck.' }], exec: [okExec] });
    assert.equal(s.execBodies.length, 1);
    assert.equal(s.execBodies[0].messages[0].content, 'print(1)');
    assert.equal(s.llmCalls[0].opts.max_tokens, 8192);
    assert.equal(s.llmCalls[1].opts.tool_choice, 'required');
    assert.match(s.llmCalls[1].messages.find(m => m.role === 'tool').content, /cut off/);
    assert.equal(isFallback(s.finals[0]), false);
});

test('python error is shown to the model, the fix succeeds, and the file is returned', opts, async () => {
    const s = await run({ llm: [good('bad()'), good('good()'), { content: 'Done.' }], exec: [pyErr("AttributeError: 'Slide' has no attribute 'foo'"), okExec] });
    assert.equal(s.execBodies.length, 2);
    const toolMsg = s.llmCalls[1].messages.find(m => m.role === 'tool').content;
    assert.match(toolMsg, /AttributeError/);
    assert.match(toolMsg, /different filename/);
    assert.equal(isFallback(s.finals[0]), false);
    const st = s.events.filter(e => e.state?.codeInterpreter?.content).pop();
    assert.equal(st.state.codeInterpreter.content.length, 1);
});

test('three failed fixes -> falls back; a traceback does not ask the user to re-upload', opts, async () => {
    const tb = 'Traceback (most recent call last):\n  File "run.py", line 3, in <module>\nNameError: x';
    const s = await run({ llm: [good('x()')], exec: [pyErr(tb)] });
    assert.equal(s.execBodies.length, 4); // 1 attempt + 3 retries
    assert.equal(isFallback(s.finals[0]), true);
    assert.equal(reuploadAsked(s.finals[0]), false);
});

test('a missing-file error does ask for a re-upload', opts, async () => {
    const s = await run({ llm: [good('x()')], exec: [{ success: false, error: 'You are not authorized to access the referenced files' }] });
    assert.equal(reuploadAsked(s.finals[0]), true);
});

test('infrastructure errors are not retried', opts, async () => {
    const s = await run({ llm: [good('x()')], exec: [{ success: false, error: 'Failed to invoke code interpreter: boom' }] });
    assert.equal(s.execBodies.length, 1);
    assert.equal(isFallback(s.finals[0]), true);
});

test('a script cut off every time never reaches the sandbox', opts, async () => {
    const s = await run({ llm: [truncated()], exec: [okExec] });
    assert.equal(s.execBodies.length, 0);
    assert.equal(isFallback(s.finals[0]), true);
});
