import test from 'node:test';
import assert from 'node:assert/strict';
import { bedrockTokenUsageTransform } from './bedrock.js';

test('metadata.usage: camelCase Converse cache fields are mapped', () => {
    const u = bedrockTokenUsageTransform({
        metadata: { usage: { inputTokens: 10, outputTokens: 20, totalTokens: 1030, cacheReadInputTokens: 800, cacheWriteInputTokens: 200 } },
    });
    assert.equal(u.prompt_tokens, 10); // inputTokens already EXCLUDES cached tokens
    assert.equal(u.completion_tokens, 20);
    assert.equal(u.inputCachedTokens, 800);
    assert.equal(u.inputWriteCachedTokens, 200);
});

test('metadata.usage: snake_case fallback still works', () => {
    const u = bedrockTokenUsageTransform({
        metadata: { usage: { input_tokens: 5, output_tokens: 6, cache_read_input_tokens: 7, cache_creation_input_tokens: 8 } },
    });
    assert.equal(u.prompt_tokens, 5);
    assert.equal(u.completion_tokens, 6);
    assert.equal(u.inputCachedTokens, 7);
    assert.equal(u.inputWriteCachedTokens, 8);
});

test('metadata.usage: camelCase wins over snake_case', () => {
    const u = bedrockTokenUsageTransform({
        metadata: { usage: { inputTokens: 1, cacheReadInputTokens: 3, cache_read_input_tokens: 99 } },
    });
    assert.equal(u.inputCachedTokens, 3);
});

test('metadata.usage: missing cache fields -> 0', () => {
    const u = bedrockTokenUsageTransform({ metadata: { usage: { inputTokens: 4, outputTokens: 2 } } });
    assert.equal(u.inputCachedTokens, 0);
    assert.equal(u.inputWriteCachedTokens, 0);
    assert.equal(u.prompt_tokens, 4);
    assert.equal(u.completion_tokens, 2);
});

test('metadata.usage: does not mutate the event', () => {
    const usage = { inputTokens: 4, outputTokens: 2, cacheReadInputTokens: 1 };
    bedrockTokenUsageTransform({ metadata: { usage } });
    assert.deepEqual(usage, { inputTokens: 4, outputTokens: 2, cacheReadInputTokens: 1 });
});

test('legacy event.d.usage shape', () => {
    const u = bedrockTokenUsageTransform({ d: { usage: { inputTokens: 11, outputTokens: 12 } } });
    assert.equal(u.prompt_tokens, 11);
    assert.equal(u.completion_tokens, 12);
});

test('direct event.usage shape (camelCase and snake_case cache)', () => {
    const a = bedrockTokenUsageTransform({ usage: { inputTokens: 1, outputTokens: 2, cacheReadInputTokens: 3, cacheWriteInputTokens: 4 } });
    assert.deepEqual([a.prompt_tokens, a.completion_tokens, a.inputCachedTokens, a.inputWriteCachedTokens], [1, 2, 3, 4]);
    const b = bedrockTokenUsageTransform({ usage: { input_tokens: 1, output_tokens: 2, cache_read_input_tokens: 5, cache_creation_input_tokens: 6 } });
    assert.deepEqual([b.prompt_tokens, b.completion_tokens, b.inputCachedTokens, b.inputWriteCachedTokens], [1, 2, 5, 6]);
});

test('no usage -> null', () => {
    assert.equal(bedrockTokenUsageTransform({}), null);
    assert.equal(bedrockTokenUsageTransform(null), null);
});
