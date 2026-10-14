import test from 'node:test';
import assert from 'node:assert/strict';
import { usesAdaptiveThinking } from './thinkingModels.js';

test('Opus 4.6+/5+ and Sonnet 5+ use adaptive thinking', () => {
    for (const id of [
        'us.anthropic.claude-opus-4-6',
        'us.anthropic.claude-opus-5-5',
        'global.anthropic.claude-opus-5',
        'us.anthropic.claude-sonnet-5',
        'us.anthropic.claude-sonnet-5-5',
    ]) {
        assert.equal(usesAdaptiveThinking(id), true, id);
    }
});

test('Haiku 5+ uses adaptive thinking', () => {
    for (const id of [
        'us.anthropic.claude-haiku-5-5',
        'global.anthropic.claude-haiku-5-5',
        'anthropic.claude-haiku-5-v1:0',
        'us.anthropic.claude-haiku-6',
        'us.anthropic.claude-haiku-10',
    ]) {
        assert.equal(usesAdaptiveThinking(id), true, id);
    }
});

test('older Sonnet/Opus/Haiku keep the legacy enabled+budget_tokens request', () => {
    for (const id of [
        'us.anthropic.claude-haiku-4-5-20251001-v1:0',
        'anthropic.claude-3-haiku-20240307-v1:0',
        'anthropic.claude-3-5-haiku-20241022-v1:0',
        'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'us.anthropic.claude-opus-4-1-20250805-v1:0',
    ]) {
        assert.equal(usesAdaptiveThinking(id), false, id);
    }
});

test('non-Claude and missing model IDs are not adaptive', () => {
    for (const id of ['us.openai.gpt-5.6-sol', 'amazon.nova-pro-v1:0', '', undefined, null]) {
        assert.equal(usesAdaptiveThinking(id), false, String(id));
    }
});
