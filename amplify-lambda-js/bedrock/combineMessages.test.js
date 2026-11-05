import test from 'node:test';
import assert from 'node:assert/strict';
import { combineMessages } from './combineMessages.js';

const DELIM = "\n_________________________\n";
const FAILSAFE = 'blank';

const U = (content) => ({ role: 'user', content });
const A = (content) => ({ role: 'assistant', content });

test('rendered prefix is identical across turns (history growth does not rewrite earlier messages)', () => {
    const turn2 = combineMessages([U('u1'), A('a1'), U('u2')], FAILSAFE);
    const turn3 = combineMessages([U('u1'), A('a1'), U('u2'), A('a2'), U('u3')], FAILSAFE);
    assert.equal(turn2.length, 3);
    assert.deepEqual(turn3.slice(0, 3), turn2);
});

test('a lone final user message has no leading delimiter', () => {
    const out = combineMessages([U('hello')], FAILSAFE);
    assert.deepEqual(out, [{ role: 'user', content: 'hello' }]);
    assert.ok(!out[0].content.includes(DELIM));
});

test('consecutive same-role messages are merged with the delimiter between them', () => {
    const out = combineMessages([U('ctx'), U('question')], FAILSAFE);
    assert.equal(out.length, 1);
    assert.equal(out[0].content, `ctx${DELIM}question`);
    // no doubled delimiter
    assert.ok(!out[0].content.includes(DELIM + DELIM));
});

test('trailing whitespace is trimmed on string content', () => {
    const out = combineMessages([U('hi  \n'), A('yo \n')], FAILSAFE);
    assert.deepEqual(out.map(m => m.content), ['hi', 'yo']);
});

test('tool and assistant-with-tool_calls messages are preserved untouched', () => {
    const assistantTc = { role: 'assistant', content: '', tool_calls: [{ id: 't1', function: { name: 'f', arguments: '{}' } }] };
    const toolMsg = { role: 'tool', tool_call_id: 't1', content: 'result' };
    const out = combineMessages([U('q'), assistantTc, toolMsg], FAILSAFE);
    assert.equal(out.length, 3);
    assert.deepEqual(out[0], { role: 'user', content: 'q' });
    assert.deepEqual(out[1], assistantTc);
    assert.deepEqual(out[2], toolMsg);
});

test('tool-loop iteration 1 renders the same first user message as iteration 2+', () => {
    const assistantTc = { role: 'assistant', content: '', tool_calls: [{ id: 't1', function: { name: 'f', arguments: '{}' } }] };
    const toolMsg = { role: 'tool', tool_call_id: 't1', content: 'result' };
    const first = combineMessages([U('q')], FAILSAFE);
    const later = combineMessages([U('q'), assistantTc, toolMsg], FAILSAFE);
    assert.deepEqual(later[0], first[0]);
});

test('first message must be user: failsafe is prepended', () => {
    const out = combineMessages([A('a1'), U('u1')], FAILSAFE);
    assert.deepEqual(out[0], { role: 'user', content: FAILSAFE });
    assert.equal(out.length, 3);
});

test('empty content gets the placeholder; empty/undefined input is returned as-is', () => {
    const out = combineMessages([U('')], FAILSAFE);
    assert.match(out[0].content, /NA Intentionally left empty/);
    assert.deepEqual(combineMessages([], FAILSAFE), []);
    assert.equal(combineMessages(undefined, FAILSAFE), undefined);
});
