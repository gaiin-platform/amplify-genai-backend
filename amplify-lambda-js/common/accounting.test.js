import test from 'node:test';
import assert from 'node:assert/strict';

process.env.CHAT_USAGE_DYNAMO_TABLE = 'usage-test';
process.env.COST_CALCULATIONS_DYNAMO_TABLE = 'cost-test';
process.env.MODEL_RATE_TABLE = 'rate-test';

const { recordUsage } = await import('./accounting.js');

test('recordUsage with no account returns false instead of throwing', async () => {
    const result = await recordUsage(undefined, 'req-1', { id: 'm' }, 10, 5, 0, 0, {});
    assert.equal(result, false);
});

test('recordUsage with an account that has no user returns false', async () => {
    const result = await recordUsage({ accessToken: 'x' }, 'req-1', { id: 'm' }, 10, 5, 0, 0, {});
    assert.equal(result, false);
});
