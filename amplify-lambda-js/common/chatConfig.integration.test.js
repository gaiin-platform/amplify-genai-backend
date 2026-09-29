import test from 'node:test';
import assert from 'node:assert/strict';
import { composeOrdinaryChatMessages, normalizeSystemMessagesForProvider } from './systemPrompts.js';

test('UI and API-shaped request messages receive identical canonical ordinary chat base', () => {
    const config = { prompts: { 'ordinaryChat.base': 'admin base' } };
    const uiMessages = composeOrdinaryChatMessages([{ role: 'user', content: 'hello' }], { promptSettings: config });
    const apiMessages = composeOrdinaryChatMessages([
        { role: 'system', content: 'request system' },
        { role: 'user', content: 'hello' }
    ], { promptSettings: config, requestPrompt: 'request system' });
    assert.equal(uiMessages[0].content, 'admin base');
    assert.equal(apiMessages[0].content, 'admin base');
    assert.equal(apiMessages.filter(message => message.content === 'request system').length, 1);
});

test('provider adaptations preserve the assembled prompt for unsupported system roles', () => {
    for (const provider of ['OpenAI', 'Azure', 'Gemini', 'Bedrock']) {
        const normalized = normalizeSystemMessagesForProvider([
            { role: 'system', content: 'base\nmodel\nfeature' },
            { role: 'user', content: 'request' }
        ], false);
        assert.equal(normalized[0].role, 'user', provider);
        assert.match(normalized[0].content, /base\nmodel\nfeature/, provider);
    }
});
