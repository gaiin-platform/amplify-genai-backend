import test, {mock, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {DynamoDBClient} from '@aws-sdk/client-dynamodb';
import {marshall} from '@aws-sdk/util-dynamodb';

process.env.LOCAL_DEVELOPMENT = 'true';
process.env.LOCAL_SECRET_ = '{"models":[]}';
process.env.API_BASE_URL = 'https://test.invalid';
process.env.ASSISTANT_GROUPS_DYNAMO_TABLE = 'groups-table';
process.env.ASSISTANT_LOOKUP_DYNAMODB_TABLE = 'lookup-table';
process.env.LAYERED_ASSISTANTS_DYNAMODB_TABLE = 'layered-table';

const {isMemberOfGroup, userInAmplifyGroup} = await import('./groupMembership.js');
const {resolveLayeredAssistant} = await import('./layeredAssistantRouter.js');

afterEach(() => mock.restoreAll());

const stubGroup = (group, {error = false} = {}) => mock.method(DynamoDBClient.prototype, 'send', async (command) => {
    assert.equal(command.input.TableName, 'groups-table');
    if (error) throw new Error('simulated dynamo failure');
    return group ? {Item: marshall(group, {removeUndefinedValues: true})} : {};
});

const stubFetch = ({status = 200, body = {success: true, isMember: true}, reject = false} = {}) =>
    mock.method(globalThis, 'fetch', async () => {
        if (reject) throw new Error('network down');
        return {status, json: async () => body};
    });

// ---------------------------------------------------------------------------
// userInAmplifyGroup: always a real boolean
// ---------------------------------------------------------------------------

test('userInAmplifyGroup resolves strict booleans on every path', async () => {
    const fetchMock = stubFetch();
    assert.strictEqual(await userInAmplifyGroup([], 't'), false);
    assert.strictEqual(await userInAmplifyGroup(undefined, 't'), false);
    assert.equal(fetchMock.mock.callCount(), 0, 'no request for an empty group list');

    assert.strictEqual(await userInAmplifyGroup(['g'], 't'), true);

    for (const stub of [
        {body: {success: true, isMember: false}},
        {body: {success: true}},
        {body: {success: true, isMember: 'yes'}},   // only a real true counts
        {body: {success: false, isMember: true}},
        {status: 500, body: {success: true, isMember: true}},
        {reject: true},
    ]) {
        mock.restoreAll();
        stubFetch(stub);
        assert.strictEqual(await userInAmplifyGroup(['g'], 't'), false, JSON.stringify(stub));
    }

    mock.restoreAll();
    const saved = process.env.API_BASE_URL;
    delete process.env.API_BASE_URL;
    try {
        assert.strictEqual(await userInAmplifyGroup(['g'], 't'), false);
    } finally {
        process.env.API_BASE_URL = saved;
    }
});

// ---------------------------------------------------------------------------
// isMemberOfGroup
// ---------------------------------------------------------------------------

test('isMemberOfGroup: allows public, direct member, system user, verified amplify group', async () => {
    stubFetch({body: {success: true, isMember: true}});
    stubGroup({group_id: 'g', isPublic: true});
    assert.strictEqual(await isMemberOfGroup('u', 'g', 't'), true);
    mock.restoreAll();

    stubFetch();
    stubGroup({group_id: 'g', members: {u: 'read'}});
    assert.strictEqual(await isMemberOfGroup('u', 'g', 't'), true);
    mock.restoreAll();

    stubFetch();
    stubGroup({group_id: 'g', systemUsers: ['u']});
    assert.strictEqual(await isMemberOfGroup('u', 'g', 't'), true);
    mock.restoreAll();

    stubFetch({body: {success: true, isMember: true}});
    stubGroup({group_id: 'g', amplifyGroups: ['amp']});
    assert.strictEqual(await isMemberOfGroup('u', 'g', 't'), true);
});

test('isMemberOfGroup: denies with strict false for non-member, unverified amplify group, no record, error, no table', async () => {
    const cases = [
        ['non-member / empty amplifyGroups', () => stubGroup({group_id: 'g', members: {other: 'read'}, amplifyGroups: []})],
        ['amplify group not verified', () => { stubFetch({body: {success: true, isMember: false}}); stubGroup({group_id: 'g', amplifyGroups: ['amp']}); }],
        ['no group record', () => stubGroup(undefined)],
        ['dynamo error', () => stubGroup(undefined, {error: true})],
    ];
    for (const [name, setup] of cases) {
        mock.restoreAll();
        setup();
        assert.strictEqual(await isMemberOfGroup('u', 'g', 't'), false, name);
    }

    mock.restoreAll();
    const saved = process.env.ASSISTANT_GROUPS_DYNAMO_TABLE;
    delete process.env.ASSISTANT_GROUPS_DYNAMO_TABLE;
    try {
        const sendMock = mock.method(DynamoDBClient.prototype, 'send', async () => ({}));
        assert.strictEqual(await isMemberOfGroup('u', 'g', 't'), false, 'table env var unset');
        assert.equal(sendMock.mock.callCount(), 0);
    } finally {
        process.env.ASSISTANT_GROUPS_DYNAMO_TABLE = saved;
    }
});

// ---------------------------------------------------------------------------
// Layered router gates use the shared implementation (behaviour pinned end to end)
// ---------------------------------------------------------------------------

const fakeStream = () => ({writableEnded: false, write() { return true; }, end() { this.writableEnded = true; }});
const leafRoot = {type: 'leaf', name: 'Leaf', assistantId: 'ast/leaf-1'};

function stubLayered({layeredItem, group, lookupItem}) {
    return mock.method(DynamoDBClient.prototype, 'send', async (command) => {
        const table = command.input.TableName;
        if (table === 'layered-table') return {Item: marshall(layeredItem)};
        if (table === 'groups-table') return group ? {Item: marshall(group, {removeUndefinedValues: true})} : {};
        if (table === 'lookup-table') return lookupItem ? {Items: [marshall(lookupItem, {removeUndefinedValues: true})]} : {Items: []};
        return {};
    });
}

const resolveAs = (user, assistantId) => resolveLayeredAssistant(
    {user, accessToken: 'tok'}, {id: 'm'}, {messages: [{role: 'user', content: 'hi'}], options: {}}, assistantId, fakeStream());

test('layered group assistant gate: member and public pass, non-member and missing groupId are denied', async () => {
    const layeredItem = {assistantId: 'astgr/la', groupId: 'g1', rootNode: leafRoot};
    const fetchMock = stubFetch({body: {success: true, isMember: false}});

    stubLayered({layeredItem, group: {group_id: 'g1', members: {alice: 'read'}, amplifyGroups: []}});
    assert.equal(await resolveAs('alice', 'astgr/la'), 'ast/leaf-1');
    assert.equal(await resolveAs('mallory', 'astgr/la'), null);
    assert.equal(fetchMock.mock.callCount(), 0, 'empty amplifyGroups never triggers a verify call');

    mock.restoreAll();
    stubFetch({body: {success: true, isMember: true}});
    stubLayered({layeredItem, group: {group_id: 'g1', members: {}, amplifyGroups: ['amp']}});
    assert.equal(await resolveAs('bob', 'astgr/la'), 'ast/leaf-1', 'member via verified amplify group');

    mock.restoreAll();
    stubFetch({body: {success: true, isMember: false}});
    stubLayered({layeredItem, group: {group_id: 'g1', members: {}, amplifyGroups: ['amp']}});
    assert.equal(await resolveAs('bob', 'astgr/la'), null, 'amplify group not verified');

    mock.restoreAll();
    stubLayered({layeredItem, group: {group_id: 'g1', isPublic: true}});
    assert.equal(await resolveAs('anyone', 'astgr/la'), 'ast/leaf-1');

    mock.restoreAll();
    stubLayered({layeredItem: {assistantId: 'astgr/la', rootNode: leafRoot}, group: {group_id: 'g1', isPublic: true}});
    assert.equal(await resolveAs('anyone', 'astgr/la'), null, 'group LA without groupId');
});

test('layered personal assistant standalone path: amplify-group access follows verify_amp_member', async () => {
    const layeredItem = {assistantId: 'astr/la', createdBy: 'owner', rootNode: leafRoot};
    const lookupItem = {assistantId: 'astr/la', accessTo: {users: ['carol'], amplifyGroups: ['amp']}};

    stubFetch({body: {success: true, isMember: true}});
    stubLayered({layeredItem, lookupItem});
    assert.equal(await resolveAs('owner', 'astr/la'), 'ast/leaf-1');
    assert.equal(await resolveAs('carol', 'astr/la'), 'ast/leaf-1');
    assert.equal(await resolveAs('dave', 'astr/la'), 'ast/leaf-1', 'dave verified via amplify group');

    mock.restoreAll();
    stubFetch({body: {success: true, isMember: false}});
    stubLayered({layeredItem, lookupItem});
    assert.equal(await resolveAs('dave', 'astr/la'), null);

    mock.restoreAll();
    const fetchMock = stubFetch({body: {success: true, isMember: true}});
    stubLayered({layeredItem, lookupItem: {assistantId: 'astr/la', accessTo: {users: []}}});
    assert.equal(await resolveAs('dave', 'astr/la'), null, 'no amplifyGroups on the path');
    assert.equal(fetchMock.mock.callCount(), 0);
});
