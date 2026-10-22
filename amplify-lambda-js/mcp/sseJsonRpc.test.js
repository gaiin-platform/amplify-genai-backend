import test from 'node:test';
import assert from 'node:assert/strict';
import { parseSseJsonRpc, sseDataEvents } from './sseJsonRpc.js';

const reply = { jsonrpc: '2.0', id: 'req_1', result: { ok: true } };

test('reads the DeepWiki style body: event line + data line', () => {
    const body = `event: message\ndata: ${JSON.stringify(reply)}\n\n`;
    assert.deepEqual(parseSseJsonRpc(body, 'req_1'), reply);
});

test('handles CRLF line endings and a missing trailing blank line', () => {
    const body = `event: message\r\ndata: ${JSON.stringify(reply)}`;
    assert.deepEqual(parseSseJsonRpc(body, 'req_1'), reply);
});

test('joins multi-line data fields into one JSON document', () => {
    const body = 'data: {"jsonrpc":"2.0",\ndata: "id":"req_1","result":{"a":1}}\n\n';
    assert.deepEqual(parseSseJsonRpc(body, 'req_1'), { jsonrpc: '2.0', id: 'req_1', result: { a: 1 } });
});

test('skips comments, keep-alives and non-JSON events', () => {
    const body = `: keep-alive\n\nevent: ping\ndata: hello\n\ndata: ${JSON.stringify(reply)}\n\n`;
    assert.deepEqual(parseSseJsonRpc(body, 'req_1'), reply);
});

test('prefers the message with the matching id over earlier responses and notifications', () => {
    const other = { jsonrpc: '2.0', id: 'req_0', result: { stale: true } };
    const note = { jsonrpc: '2.0', method: 'notifications/progress', params: {} };
    const body = [note, other, reply].map((m) => `data: ${JSON.stringify(m)}\n\n`).join('');
    assert.deepEqual(parseSseJsonRpc(body, 'req_1'), reply);
});

test('falls back to the first response when no id matches', () => {
    assert.deepEqual(parseSseJsonRpc(`data: ${JSON.stringify(reply)}\n\n`, 'req_9'), reply);
});

test('returns an error response as is', () => {
    const err = { jsonrpc: '2.0', id: 'req_1', error: { code: -32601, message: 'nope' } };
    assert.deepEqual(parseSseJsonRpc(`data: ${JSON.stringify(err)}\n\n`, 'req_1'), err);
});

test('returns null for empty, non-string or response-less input', () => {
    assert.equal(parseSseJsonRpc('', 'req_1'), null);
    assert.equal(parseSseJsonRpc(undefined, 'req_1'), null);
    assert.equal(parseSseJsonRpc('event: message\n\n', 'req_1'), null);
    assert.equal(parseSseJsonRpc('data: {"jsonrpc":"2.0","method":"x"}\n\n', 'req_1'), null);
    assert.equal(parseSseJsonRpc('data: [not json\n\n', 'req_1'), null);
});

test('sseDataEvents keeps the value after "data:" with or without the optional space', () => {
    assert.deepEqual(sseDataEvents('data:a\n\ndata: b\n\n'), ['a', 'b']);
});
