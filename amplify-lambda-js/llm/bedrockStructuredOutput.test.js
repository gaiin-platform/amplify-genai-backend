import test from 'node:test';
import assert from 'node:assert/strict';
import { getBedrockStructuredOutputFamily, buildBedrockOutputConfig } from './bedrockStructuredOutput.js';

const schema = { type: 'object', properties: { a: { type: 'string' } }, required: ['a'], additionalProperties: false };

test('family detection', () => {
    for (const id of ['us.anthropic.claude-haiku-4-5-20251001-v1:0', 'us.anthropic.claude-sonnet-4-6', 'global.anthropic.claude-opus-5']) assert.equal(getBedrockStructuredOutputFamily(id), 'claude', id);
    for (const id of ['us.openai.gpt-5.6-luna', 'us.openai.gpt-5.6-sol', 'global.openai.gpt-5.6-terra', 'openai.gpt-5.6-luna', 'us.openai.gpt-6-luna', 'us.openai.gpt-6.1-sol']) assert.equal(getBedrockStructuredOutputFamily(id), 'openai-gpt-5.6', id);
    for (const id of ['us.amazon.nova-pro-v1:0', 'meta.llama3-70b', 'mistral.mistral-large', 'us.openai.gpt-oss-120b', 'gpt-5.6-luna', '', undefined, null]) assert.equal(getBedrockStructuredOutputFamily(id), null, String(id));
});

test('GPT-5.6 config includes the REQUIRED schema name (Bedrock 400s without it)', () => {
    const cfg = buildBedrockOutputConfig('us.openai.gpt-5.6-luna', schema);
    assert.deepEqual(cfg, { textFormat: { type: 'json_schema', structure: { jsonSchema: { schema: JSON.stringify(schema), name: 'data_extraction' } } } });
});

test('Claude config is unchanged (no name)', () => {
    const cfg = buildBedrockOutputConfig('us.anthropic.claude-haiku-4-5-20251001-v1:0', schema);
    assert.deepEqual(cfg, { textFormat: { type: 'json_schema', structure: { jsonSchema: { schema: JSON.stringify(schema) } } } });
});

test('unsupported families get no outputConfig', () => {
    assert.equal(buildBedrockOutputConfig('us.amazon.nova-pro-v1:0', schema), null);
});
