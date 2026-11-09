import test from 'node:test';
import assert from 'node:assert/strict';
import { openaiUsageTransform } from './openai.js';
import { geminiUsageTransform } from './gemini.js';

test('chat completions: cached tokens are not billed again as plain input', () => {
    // Real prod row: input=6,777 cached=6,656
    const usage = openaiUsageTransform({ usage: { prompt_tokens: 6777, completion_tokens: 40, prompt_tokens_details: { cached_tokens: 6656 } } });
    assert.equal(usage.prompt_tokens, 121);
    assert.equal(usage.inputCachedTokens, 6656);
});

test('responses API: cache writes are split out, reasoning is not added to output again', () => {
    const usage = openaiUsageTransform({ response: { usage: {
        input_tokens: 1502, output_tokens: 500,
        input_tokens_details: { cached_tokens: 1000, cache_write_tokens: 400 },
        output_tokens_details: { reasoning_tokens: 450 },
    } } });
    assert.equal(usage.prompt_tokens, 102);
    assert.equal(usage.inputWriteCachedTokens, 400);
    assert.equal(usage.completion_tokens, 500);
    assert.equal(usage.reasoning_tokens, 450);
});

test('no caching leaves input unchanged and cached larger than input never goes negative', () => {
    assert.equal(openaiUsageTransform({ usage: { prompt_tokens: 1234, completion_tokens: 5 } }).prompt_tokens, 1234);
    assert.equal(openaiUsageTransform({ usage: { prompt_tokens: 10, completion_tokens: 5, prompt_tokens_details: { cached_tokens: 50 } } }).prompt_tokens, 0);
});

test('gemini: cached tokens are not billed again as plain input', () => {
    const usage = geminiUsageTransform({ usage: { prompt_tokens: 5000, completion_tokens: 20, prompt_tokens_details: { cached_tokens: 4000 } } });
    assert.equal(usage.prompt_tokens, 1000);
    assert.equal(usage.inputCachedTokens, 4000);
});
