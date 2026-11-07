import test from 'node:test';
import assert from 'node:assert/strict';
import { hasConfiguredWebSearchProvider, resolveWebSearchAvailability } from './webSearchAvailability.js';
import { Writable } from 'node:stream';

test('recognizes supported user-configured web-search providers', () => {
    for (const provider of ['brave_search', 'tavily', 'serper', 'serpapi', 'bedrock_agentcore']) {
        assert.equal(hasConfiguredWebSearchProvider({ [provider]: 'configured-key' }), true, provider);
    }
});

test('ignores absent, empty, and unsupported providers', () => {
    assert.equal(hasConfiguredWebSearchProvider(), false);
    assert.equal(hasConfiguredWebSearchProvider({ brave_search: '' }), false);
    assert.equal(hasConfiguredWebSearchProvider({ unrelated: 'configured-key' }), false);
});

test('uses a user key when admin web search is not configured', () => {
    const result = resolveWebSearchAvailability({
        requested: true,
        allowed: true,
        apiKeys: { tavily: 'user-key' }
    });
    assert.equal(result.enabled, true);
    assert.deepEqual(result.apiKeys, { tavily: 'user-key' });
    assert.equal(result.keylessAvailable, false);
});

test('admin provider credentials augment or override existing user keys', () => {
    const result = resolveWebSearchAvailability({
        requested: true,
        allowed: true,
        apiKeys: { tavily: 'user-key' },
        adminKey: { provider: 'brave_search', api_key: 'admin-key' }
    });
    assert.equal(result.enabled, true);
    assert.deepEqual(result.apiKeys, { tavily: 'user-key', brave_search: 'admin-key' });
});

test('honors admin disablement and recognizes keyless AgentCore configuration', () => {
    assert.equal(resolveWebSearchAvailability({
        requested: true,
        allowed: false,
        apiKeys: { tavily: 'user-key' }
    }).enabled, false);
    const agentCore = resolveWebSearchAvailability({
        requested: true,
        allowed: true,
        adminKey: { provider: 'bedrock_agentcore', config: { gatewayUrl: 'https://gateway.invalid' } }
    });
    assert.equal(agentCore.enabled, true);
    assert.equal(agentCore.keylessAvailable, true);
});

test('MCP-only requests with web search disabled reach the model call without an adminKey scope error', async () => {
    process.env.LOCAL_DEVELOPMENT = 'true';
    process.env.LOCAL_SECRET_ = '{"models":[]}';
    process.env.API_BASE_URL = 'https://test.invalid';
    delete process.env.USER_STORAGE_TABLE;

    const { executeToolLoop } = await import('./toolLoop.js');
    await assert.rejects(
        executeToolLoop(
            { account: { user: 'test-user' }, options: {} },
            [],
            undefined,
            null,
            {
                webSearchEnabled: false,
                mcpClientSide: true,
                tools: [{ type: 'function', function: { name: 'mcp_server_tool', parameters: { type: 'object' } } }]
            }
        ),
        error => error.message === 'Model not specified'
    );
});

test('tool-loop exhaustion returns its user-facing message and writes it to the stream', async () => {
    process.env.LOCAL_DEVELOPMENT = 'true';
    process.env.LOCAL_SECRET_ = '{"models":[]}';
    process.env.API_BASE_URL = 'https://test.invalid';
    const { sendToolLoopFallback } = await import('./toolLoop.js');
    const chunks = [];
    const stream = new Writable({ write(chunk, _encoding, callback) { chunks.push(chunk.toString()); callback(); } });

    const result = sendToolLoopFallback(stream, 'Search could not complete. Please try again.');
    assert.equal(result.content, 'Search could not complete. Please try again.');
    assert.match(chunks.join(''), /Search could not complete/);
});
