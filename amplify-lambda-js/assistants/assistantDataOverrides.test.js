import test from 'node:test';
import assert from 'node:assert/strict';

process.env.LOCAL_DEVELOPMENT = 'true';
process.env.LOCAL_SECRET_ = '{"models":[]}';
process.env.API_BASE_URL = 'https://test.invalid';

const {fillInAssistant} = await import('./userDefinedAssistants.js');
const {withAssistantDataOverrides} = await import('./assistants.js');

const base = {handler: async () => {}};

test('fillInAssistant wrappers expose withDataOverrides and re-wrap without mutating the source', () => {
    const source = {name: 'My Ast', description: 'd', data: {opsLanguageVersion: 'v1', keep: 1}};
    const wrapped = fillInAssistant(source, base);
    const overridden = withAssistantDataOverrides(wrapped, {opsLanguageVersion: 'v4'});

    assert.notEqual(overridden, wrapped);
    assert.equal(typeof overridden.handler, 'function');
    assert.equal(overridden.name, 'My Ast');
    assert.equal(typeof overridden.withDataOverrides, 'function');
    // The cached source assistant must stay untouched.
    assert.deepEqual(source.data, {opsLanguageVersion: 'v1', keep: 1});
});

test('withAssistantDataOverrides merges into data for plain assistant objects', () => {
    const plain = {name: 'x', data: {a: 1}};
    const out = withAssistantDataOverrides(plain, {opsLanguageVersion: 'v4'});
    assert.deepEqual(out.data, {a: 1, opsLanguageVersion: 'v4'});
    assert.deepEqual(plain.data, {a: 1});
});

test('withAssistantDataOverrides handles assistants with no data', () => {
    assert.deepEqual(withAssistantDataOverrides({name: 'x'}, {b: 2}).data, {b: 2});
});
