import test from 'node:test';
import assert from 'node:assert/strict';
import { detectContextOverflow, isThrottlingError } from './contextOverflow.js';

const throttle = (message, extra = {}) => Object.assign(new Error(message), { name: 'ThrottlingException', ...extra });

test('Bedrock "Too many tokens" throttling is a rate limit, not a context overflow', () => {
    // Verbatim from prod (vu-amplify-js-prod-chat_stream, gpt-5.6-luna). It matched the Gemini
    // /too many tokens/ pattern, so the error was hidden from the user and the conversation was
    // needlessly "recovered" (summarized) instead of being reported as throttling.
    const error = throttle('Too many tokens, please wait before trying again.', { $metadata: { httpStatusCode: 429 } });
    assert.deepEqual(detectContextOverflow(error), { isOverflow: false });
    assert.equal(isThrottlingError(error), true);
});

test('Bedrock "Too many requests" throttling is not an overflow', () => {
    const error = throttle('Too many requests, please wait before trying again. You have sent too many requests.  Wait before trying again.');
    assert.deepEqual(detectContextOverflow(error), { isOverflow: false });
});

test('throttling is recognised by message even when the error name is lost in wrapping', () => {
    const error = new Error('ThrottlingException: Too many tokens, please wait before trying again.');
    assert.deepEqual(detectContextOverflow(error), { isOverflow: false });
});

test('a real overflow is still detected when its message mentions tokens', () => {
    const bedrock = detectContextOverflow(new Error('prompt is too long: 208718 tokens > 200000 maximum'));
    assert.equal(bedrock.isOverflow, true);
    assert.equal(bedrock.overflow, 8718);

    const gemini = detectContextOverflow(new Error('The input token count (1200000 tokens) exceeds the maximum number of tokens allowed (1048576 tokens)'));
    assert.equal(gemini.isOverflow, true);
});

test('Gemini-style "too many tokens" overflow without throttling language is still an overflow', () => {
    assert.equal(detectContextOverflow(new Error('Request contains too many tokens for this model')).isOverflow, true);
});

test('isThrottlingError ignores ordinary errors', () => {
    assert.equal(isThrottlingError(new Error('boom')), false);
    assert.equal(isThrottlingError(null), false);
});
