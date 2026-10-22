import test from 'node:test';
import assert from 'node:assert/strict';
import { MCP_RUN_LIMITS, buildMcpRunRecord, clipArgs, describeMedia } from './mcpRun.js';

test('a successful call keeps tool, server, args, result and duration', () => {
    const run = buildMcpRunRecord({
        id: 'call_1',
        ok: true,
        tool: 'search_issues',
        serverName: 'GitHub',
        serverId: 'mcp_1767629390657_b1c2812d7',
        args: { query: 'is:open', limit: 5, draft: false },
        result: 'Found 2 issues',
        ms: 412.6,
    });
    assert.deepEqual(run, {
        id: 'call_1',
        ok: true,
        tool: 'search_issues',
        serverName: 'GitHub',
        serverId: 'mcp_1767629390657_b1c2812d7',
        args: { query: 'is:open', limit: 5, draft: false },
        result: 'Found 2 issues',
        ms: 413,
    });
});

test('empty fields are omitted and a failure keeps its error, not a result', () => {
    const run = buildMcpRunRecord({ id: 'c', ok: false, tool: 't', args: {}, result: '  \n', error: 'boom' });
    assert.deepEqual(run, { id: 'c', ok: false, tool: 't', error: 'boom' });
});

test('ok is only true for an explicit true', () => {
    assert.equal(buildMcpRunRecord({ id: 'c', tool: 't', ok: 'yes' }).ok, false);
    assert.equal(buildMcpRunRecord({ id: 'c', tool: 't' }).ok, false);
});

test('result and error are cut at their caps and flagged', () => {
    const run = buildMcpRunRecord({
        id: 'c',
        ok: false,
        tool: 't',
        result: 'r'.repeat(MCP_RUN_LIMITS.result + 50),
        error: 'e'.repeat(MCP_RUN_LIMITS.error + 50),
    });
    assert.equal(run.result.length, MCP_RUN_LIMITS.result);
    assert.equal(run.error.length, MCP_RUN_LIMITS.error);
    assert.deepEqual(run.truncated, { result: true, error: true });
});

test('args: strings clipped, nested values become JSON strings, junk dropped, keys bounded', () => {
    const many = Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`k${i}`, i]));
    const { args, truncated } = clipArgs({
        long: 'x'.repeat(MCP_RUN_LIMITS.argValue + 10),
        nested: { a: [1, 2] },
        n: null,
        bad: undefined,
        inf: Infinity,
    });
    assert.equal(args.long.length, MCP_RUN_LIMITS.argValue);
    assert.equal(args.nested, '{"a":[1,2]}');
    assert.equal(args.n, null);
    assert.equal(args.inf, null);
    assert.ok(!('bad' in args));
    assert.equal(truncated, true);

    const bounded = clipArgs(many);
    assert.equal(Object.keys(bounded.args).length, MCP_RUN_LIMITS.argKeys);
    assert.equal(bounded.truncated, true);
});

test('args that are not an object produce nothing', () => {
    for (const bad of [null, undefined, 'str', 5, [1, 2]]) {
        assert.deepEqual(clipArgs(bad), { args: {}, truncated: false });
    }
    assert.ok(!('args' in buildMcpRunRecord({ id: 'c', tool: 't', args: 'nope', ok: true })));
});

test('a cyclic argument value does not throw', () => {
    const loop = {};
    loop.self = loop;
    const { args } = clipArgs({ loop, fine: 'ok' });
    assert.deepEqual(args, { fine: 'ok' });
});

test('media is described without ever reading or storing the payload', () => {
    const media = describeMedia([
        { type: 'text', text: 'hello' },
        { type: 'image', mimeType: 'image/png', data: 'AAAA'.repeat(10000) },
        { type: 'resource', uri: 'file:///tmp/report.csv', mimeType: 'text/csv' },
        { type: 'audio', data: 'zzzz' },
    ]);
    assert.deepEqual(media, [
        { kind: 'image', mimeType: 'image/png' },
        { kind: 'resource', uri: 'file:///tmp/report.csv', mimeType: 'text/csv' },
        { kind: 'other' },
    ]);
    const run = buildMcpRunRecord({
        id: 'c',
        ok: true,
        tool: 't',
        rawContent: [{ type: 'image', mimeType: 'image/png', data: 'AAAA'.repeat(10000) }],
    });
    assert.ok(JSON.stringify(run).length < 300);
    assert.ok(!JSON.stringify(run).includes('AAAA'));
});

test('media is capped and non-arrays are ignored', () => {
    const many = Array.from({ length: 12 }, () => ({ type: 'image', mimeType: 'image/png' }));
    assert.equal(describeMedia(many).length, MCP_RUN_LIMITS.media);
    assert.deepEqual(describeMedia(undefined), []);
    assert.deepEqual(describeMedia('x'), []);
});

test('the record is JSON-safe and survives a round trip', () => {
    const run = buildMcpRunRecord({
        id: 'c',
        ok: true,
        tool: 't',
        args: { a: { b: 1 } },
        result: 'ok',
        rawContent: [{ type: 'image', mimeType: 'image/png', data: 'x' }],
        ms: 5,
    });
    assert.deepEqual(JSON.parse(JSON.stringify(run)), run);
});

test('missing identity fields do not throw', () => {
    const run = buildMcpRunRecord({});
    assert.deepEqual(run, { id: '', ok: false, tool: '' });
});
