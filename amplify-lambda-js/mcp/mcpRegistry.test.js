import test from 'node:test';
import assert from 'node:assert/strict';

process.env.USER_STORAGE_TABLE = 'user-storage-test';
process.env.IDP_PREFIX = 'dev-amplifygenai';

const { DynamoDBDocumentClient } = await import('@aws-sdk/lib-dynamodb');
const { updateMCPServer, connectMCPServer } = await import('./mcpRegistry.js');
const { MCPClient } = await import('./mcpClient.js');

const USER = '84484458-a081-7063-5367-6f05668660ea';
const SERVER = 'mcp_1771516138546_f43820ea9';
const PREFIXED_PK = `dev-amplifygenai_${USER}#amplify-mcp#mcp_servers`;
const LEGACY_PK = `${USER}#amplify-mcp#mcp_servers`;

const serverData = {
    name: 'Jupyter Notebook',
    url: 'https://example.invalid/mcp',
    transport: 'http',
    enabled: true,
    trustMode: 'trusted',
};

// Stub DynamoDB: `store` maps "PK|SK" -> item. Records every command sent.
function stubDynamo(store, { failPuts = false } = {}) {
    const original = DynamoDBDocumentClient.prototype.send;
    const sent = [];
    DynamoDBDocumentClient.prototype.send = async function (command) {
        const name = command.constructor.name;
        const input = command.input;
        sent.push({ name, input });
        if (name === 'GetCommand') {
            const item = store.get(`${input.Key.PK}|${input.Key.SK}`);
            return item ? { Item: item } : {};
        }
        if (name === 'QueryCommand') {
            const pk = input.ExpressionAttributeValues[':pk'];
            return { Items: [...store.values()].filter(i => i.PK === pk) };
        }
        if (name === 'PutCommand') {
            if (failPuts) throw new Error('write failed');
            store.set(`${input.Item.PK}|${input.Item.SK}`, input.Item);
            return {};
        }
        throw new Error(`unexpected command ${name}`);
    };
    return { sent, restore: () => { DynamoDBDocumentClient.prototype.send = original; } };
}

test('updateMCPServer updates a server stored under the legacy (non-prefixed) PK in place', async () => {
    const store = new Map([[`${LEGACY_PK}|${SERVER}`, { PK: LEGACY_PK, SK: SERVER, data: serverData }]]);
    const stub = stubDynamo(store);
    try {
        const result = await updateMCPServer(USER, SERVER, { status: 'connected' });
        assert.equal(result.status, 'connected');
        assert.equal(store.get(`${LEGACY_PK}|${SERVER}`).data.status, 'connected');
        // Must not create a duplicate record under the prefixed PK.
        assert.equal(store.has(`${PREFIXED_PK}|${SERVER}`), false);
    } finally {
        stub.restore();
    }
});

test('updateMCPServer prefers the prefixed PK when the server exists there', async () => {
    const store = new Map([[`${PREFIXED_PK}|${SERVER}`, { PK: PREFIXED_PK, SK: SERVER, data: serverData }]]);
    const stub = stubDynamo(store);
    try {
        await updateMCPServer(USER, SERVER, { status: 'connected' });
        assert.equal(store.get(`${PREFIXED_PK}|${SERVER}`).data.status, 'connected');
        assert.equal(store.size, 1);
    } finally {
        stub.restore();
    }
});

test('updateMCPServer still throws "not found" when the server is in neither PK', async () => {
    const stub = stubDynamo(new Map());
    try {
        await assert.rejects(() => updateMCPServer(USER, SERVER, { status: 'connected' }), /not found/);
    } finally {
        stub.restore();
    }
});

test('connectMCPServer succeeds for a legacy-stored server even if persisting status fails', async () => {
    const store = new Map([[`${LEGACY_PK}|${SERVER}`, { PK: LEGACY_PK, SK: SERVER, data: serverData }]]);
    const stub = stubDynamo(store, { failPuts: true });
    const originalConnect = MCPClient.prototype.connect;
    MCPClient.prototype.connect = async function () {
        this.connected = true;
        return { tools: [{ name: 'execute_code' }] };
    };
    try {
        const client = await connectMCPServer(USER, SERVER);
        assert.ok(client, 'connection is returned despite the failed bookkeeping write');
    } finally {
        MCPClient.prototype.connect = originalConnect;
        stub.restore();
    }
});

test('connectMCPServer surfaces the real connection error, not a "not found" from bookkeeping', async () => {
    // Own server id: connected clients are cached per user+server across tests.
    const failingServer = 'mcp_failing_server_id';
    const store = new Map([[`${LEGACY_PK}|${failingServer}`, { PK: LEGACY_PK, SK: failingServer, data: serverData }]]);
    const stub = stubDynamo(store, { failPuts: true });
    const originalConnect = MCPClient.prototype.connect;
    MCPClient.prototype.connect = async function () { throw new Error('connection refused'); };
    try {
        await assert.rejects(() => connectMCPServer(USER, failingServer), /connection refused/);
        await assert.rejects(() => connectMCPServer(USER, 'mcp_absent_server_id'), /MCP server mcp_absent_server_id not found/);
    } finally {
        MCPClient.prototype.connect = originalConnect;
        stub.restore();
    }
});
