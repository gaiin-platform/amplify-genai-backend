import test, {mock, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import axios from 'axios';
import {DynamoDBClient} from '@aws-sdk/client-dynamodb';
import {marshall} from '@aws-sdk/util-dynamodb';

process.env.LOCAL_DEVELOPMENT = 'true';
process.env.LOCAL_SECRET_ = '{"models":[]}';
process.env.API_BASE_URL = 'https://test.invalid';
process.env.ASSISTANTS_DYNAMODB_TABLE = 'assistants-table';
process.env.ASSISTANTS_ALIASES_DYNAMODB_TABLE = 'aliases-table';
process.env.ASSISTANT_GROUPS_DYNAMO_TABLE = 'groups-table';
process.env.ASSISTANT_LOOKUP_DYNAMODB_TABLE = 'lookup-table';
process.env.SKILLS_DYNAMODB_TABLE = 'skills-table';

const {getUserDefinedAssistant, fillInAssistant} = await import('./userDefinedAssistants.js');
const {CacheManager} = await import('../common/cache.js');

afterEach(() => mock.restoreAll());

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const GROUP_AST_ID = 'astgp/group-assistant';
const base = {handler: async () => {}};

let seq = 0;
// Unique user/token per test: CacheManager is a process-wide in-memory cache.
const freshIdentity = () => {
    seq += 1;
    return {user: `user${seq}@example.com`, token: `token-${seq}`, groupId: `group-${seq}`};
};

/**
 * Stubs DynamoDB for the group-assistant lookup path.
 * `group === undefined` -> no group record; `groupError` -> group lookup throws.
 */
function stubDynamo({groupId, group, groupError = false}) {
    const calls = [];
    mock.method(DynamoDBClient.prototype, 'send', async (command) => {
        const type = command.constructor.name;
        const table = command.input.TableName;
        calls.push({type, table});

        if (table === 'assistants-table' && type === 'QueryCommand') {
            return {Count: 1, Items: [marshall({assistantId: GROUP_AST_ID, version: 1, data: {groupId}})]};
        }
        if (table === 'groups-table') {
            if (groupError) throw new Error('simulated dynamo failure');
            return group ? {Item: marshall(group, {removeUndefinedValues: true})} : {};
        }
        if (table === 'aliases-table') {
            return {Item: marshall({data: {id: 'db-1'}})};
        }
        if (table === 'assistants-table' && type === 'GetItemCommand') {
            return {Item: marshall({id: 'db-1', name: 'Group Assistant', description: 'd', data: {}})};
        }
        return {};
    });
    return {
        calls,
        groupLookups: () => calls.filter(c => c.table === 'groups-table').length,
        aliasLookups: () => calls.filter(c => c.table === 'aliases-table').length,
    };
}

/** Stubs fetch for /amplifymin/verify_amp_member. */
function stubVerifyAmpMember({status = 200, body = {success: true, isMember: true}, reject = false} = {}) {
    return mock.method(globalThis, 'fetch', async (url) => {
        if (reject) throw new Error('network down');
        assert.match(String(url), /\/amplifymin\/verify_amp_member$/);
        return {status, json: async () => body};
    });
}

const getAst = (id) => getUserDefinedAssistant(id.user, base, GROUP_AST_ID, id.token);

// ---------------------------------------------------------------------------
// Bug 1: group-assistant membership check
// ---------------------------------------------------------------------------

test('group assistant: non-member of a non-public group with empty amplifyGroups is denied', async () => {
    const id = freshIdentity();
    const dynamo = stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: false, members: {'someone@else': 'read'}, amplifyGroups: []}});
    const fetchMock = stubVerifyAmpMember();
    const setSpy = mock.method(CacheManager, 'setCachedGroupMembership');

    const result = await getAst(id);

    assert.equal(result, null, 'non-member must not receive the group assistant');
    assert.equal(dynamo.aliasLookups(), 0, 'assistant must not be loaded for a non-member');
    assert.equal(fetchMock.mock.callCount(), 0, 'no verify_amp_member call when amplifyGroups is empty');
    assert.equal(setSpy.mock.callCount(), 1);
    assert.strictEqual(setSpy.mock.calls[0].arguments[3], false, 'denial must be cached as boolean false');
});

test('group assistant: non-member with no amplifyGroups attribute at all is denied', async () => {
    const id = freshIdentity();
    stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, members: {}}});
    stubVerifyAmpMember();

    assert.equal(await getAst(id), null);
});

test('group assistant: cached denial is honoured and read back as boolean false', async () => {
    const id = freshIdentity();
    const dynamo = stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: false, members: {}, amplifyGroups: []}});
    stubVerifyAmpMember();

    assert.equal(await getAst(id), null);
    assert.strictEqual(await CacheManager.getCachedGroupMembership(id.user, id.groupId, id.token), false);

    assert.equal(await getAst(id), null, 'second call is still denied');
    assert.equal(dynamo.groupLookups(), 1, 'second call must use the cached denial, not re-query the group table');
});

test('group assistant: a cached allow (true) is honoured', async () => {
    const id = freshIdentity();
    const dynamo = stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, members: {}}});
    CacheManager.setCachedGroupMembership(id.user, id.groupId, id.token, true);

    const result = await getAst(id);

    assert.equal(result?.name, 'Group Assistant');
    assert.equal(dynamo.groupLookups(), 0);
});

test('group assistant: direct member is allowed', async () => {
    const id = freshIdentity();
    stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: false, members: {[id.user]: 'read'}, amplifyGroups: []}});
    stubVerifyAmpMember();
    const setSpy = mock.method(CacheManager, 'setCachedGroupMembership');

    assert.equal((await getAst(id))?.name, 'Group Assistant');
    assert.strictEqual(setSpy.mock.calls[0].arguments[3], true);
});

test('group assistant: system user is allowed', async () => {
    const id = freshIdentity();
    stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: false, members: {}, systemUsers: [id.user], amplifyGroups: []}});
    stubVerifyAmpMember();

    assert.equal((await getAst(id))?.name, 'Group Assistant');
});

test('group assistant: public group is allowed', async () => {
    const id = freshIdentity();
    stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: true, members: {}, amplifyGroups: []}});
    stubVerifyAmpMember();

    assert.equal((await getAst(id))?.name, 'Group Assistant');
});

test('group assistant: member via amplify group is allowed only when verify_amp_member says isMember', async () => {
    const id = freshIdentity();
    stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: false, members: {}, amplifyGroups: ['amp-1']}});
    const fetchMock = stubVerifyAmpMember({body: {success: true, isMember: true}});

    assert.equal((await getAst(id))?.name, 'Group Assistant');
    assert.equal(fetchMock.mock.callCount(), 1);
    const [, init] = fetchMock.mock.calls[0].arguments;
    assert.equal(init.headers.Authorization, `Bearer ${id.token}`);
    assert.deepEqual(JSON.parse(init.body), {data: {groups: ['amp-1']}});
});

test('group assistant: amplify group check resolving false denies and caches boolean false', async () => {
    const id = freshIdentity();
    const dynamo = stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: false, members: {}, amplifyGroups: ['amp-1']}});
    const fetchMock = stubVerifyAmpMember({body: {success: true, isMember: false}});
    const setSpy = mock.method(CacheManager, 'setCachedGroupMembership');

    assert.equal(await getAst(id), null);
    assert.equal(dynamo.aliasLookups(), 0);
    assert.strictEqual(setSpy.mock.calls[0].arguments[3], false);

    assert.equal(await getAst(id), null, 'cached denial honoured');
    assert.equal(fetchMock.mock.callCount(), 1, 'second call must not re-verify');
});

test('group assistant: amplify group check failing (non-200, success=false, network error) denies', async () => {
    for (const stub of [
        {status: 500, body: {success: false}},
        {status: 200, body: {success: false, isMember: true}},
        {reject: true},
    ]) {
        mock.restoreAll();
        const id = freshIdentity();
        stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: false, members: {}, amplifyGroups: ['amp-1']}});
        stubVerifyAmpMember(stub);
        const setSpy = mock.method(CacheManager, 'setCachedGroupMembership');

        assert.equal(await getAst(id), null, `denied for ${JSON.stringify(stub)}`);
        assert.strictEqual(setSpy.mock.calls[0].arguments[3], false);
    }
});

test('group assistant: missing group record and DynamoDB errors deny and cache a real boolean false', async () => {
    for (const opts of [{group: undefined}, {groupError: true}]) {
        mock.restoreAll();
        const id = freshIdentity();
        stubDynamo({groupId: id.groupId, ...opts});
        stubVerifyAmpMember();
        const setSpy = mock.method(CacheManager, 'setCachedGroupMembership');

        assert.equal(await getAst(id), null);
        assert.strictEqual(setSpy.mock.calls[0].arguments[3], false, `cached value for ${JSON.stringify(opts)} must be boolean false, not undefined`);
        assert.strictEqual(await CacheManager.getCachedGroupMembership(id.user, id.groupId, id.token), false);
    }
});

// ---------------------------------------------------------------------------
// Layered requests must not make the leaf assistant servable to direct requests
// ---------------------------------------------------------------------------

test('layered request does not populate the direct-request assistant cache (no membership bypass)', async () => {
    const id = freshIdentity();
    const dynamo = stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, isPublic: false, members: {}, amplifyGroups: []}});
    stubVerifyAmpMember();
    const LAYERED_ID = 'astgr/la-1';

    // Layered gate already cleared this user: the leaf is loaded directly.
    const viaLayered = await getUserDefinedAssistant(id.user, base, GROUP_AST_ID, id.token, LAYERED_ID);
    assert.ok(viaLayered, 'layered request is served');
    assert.equal(await CacheManager.getCachedUserDefinedAssistant(id.user, GROUP_AST_ID, id.token), null,
        'direct-request cache key must stay empty');

    // Same user asks for the leaf id directly: normal group membership check applies.
    assert.equal(await getAst(id), null, 'non-member is denied on the direct path');
    assert.equal(dynamo.groupLookups(), 1, 'direct request went through the membership check');
});

test('layered requests are still cached, and do not touch a cached direct-request entry', async () => {
    const id = freshIdentity();
    const dynamo = stubDynamo({groupId: id.groupId, group: {group_id: id.groupId, members: {}}});
    const LAYERED_ID = 'astgr/la-2';
    const directEntry = {name: 'Direct Entry', description: 'd', data: {}};
    CacheManager.setCachedUserDefinedAssistant(id.user, GROUP_AST_ID, id.token, directEntry);

    await getUserDefinedAssistant(id.user, base, GROUP_AST_ID, id.token, LAYERED_ID);
    const queriesAfterFirst = dynamo.calls.filter(c => c.type === 'QueryCommand').length;
    assert.equal(queriesAfterFirst, 1, 'layered path loads the leaf itself (does not reuse the direct entry)');
    assert.equal('_layeredAstId' in directEntry, false, 'shared direct entry must not be stamped with a layered id');

    await getUserDefinedAssistant(id.user, base, GROUP_AST_ID, id.token, LAYERED_ID);
    assert.equal(dynamo.calls.filter(c => c.type === 'QueryCommand').length, queriesAfterFirst,
        'second layered request is served from the layered cache namespace');
});

// ---------------------------------------------------------------------------
// Bug 2: injectedSkills out of scope on the v4 / agent path
// ---------------------------------------------------------------------------

function fakeStream() {
    const writes = [];
    return {writes, writableEnded: false, write(chunk) { writes.push(String(chunk)); return true; }, end() { this.writableEnded = true; }};
}

function runV4Handler({skills, skillSelectionMode = 'manual'}) {
    mock.method(DynamoDBClient.prototype, 'send', async () => ({}));
    const postMock = mock.method(axios, 'post', async () => ({data: {ok: true}}));
    const assistant = {
        name: 'V4 Assistant',
        description: 'd',
        instructions: 'be helpful',
        data: {opsLanguageVersion: 'v4', skills, skillSelectionMode},
    };
    const stream = fakeStream();
    const params = {
        account: {user: 'skill-user@example.com', accessToken: 'tok'},
        model: {id: 'test-model', supportsImages: false},
        options: {conversationId: 'conv-1', requestId: 'req-1'},
    };
    const body = {messages: [{role: 'user', content: 'hello'}], options: {}};
    const run = () => fillInAssistant(assistant, base).handler(params, body, [], stream);
    return {run, postMock, stream};
}

test('v4 agent path with active skills does not throw and passes id/name/content to the agent', async () => {
    const {run, postMock, stream} = runV4Handler({
        skills: [
            {skillId: 'sk1', name: 'Skill One', isRequired: true, overrideContent: 'Do X carefully'},
            {skillId: 'sk2', name: 'Skill Two', isRequired: true, overrideContent: 'Then do Y'},
        ],
    });

    await run(); // pre-fix: rejects with ReferenceError: injectedSkills is not defined

    assert.equal(postMock.mock.callCount(), 1);
    const [url, payload] = postMock.mock.calls[0].arguments;
    assert.match(url, /\/vu-agent\/handle-event$/);
    assert.deepEqual(payload.data.metadata.skills, [
        {id: 'sk1', name: 'Skill One', content: 'Do X carefully'},
        {id: 'sk2', name: 'Skill Two', content: 'Then do Y'},
    ]);
    assert.ok(stream.writes.some(w => w.includes('activeSkills')), 'frontend still gets the activeSkills state event');
    assert.equal(stream.writableEnded, true);
});

test('v4 agent path without skills sends no skills metadata', async () => {
    const {run, postMock} = runV4Handler({skills: []});

    await run();

    assert.equal(postMock.mock.callCount(), 1);
    assert.equal('skills' in postMock.mock.calls[0].arguments[1].data.metadata, false);
});
