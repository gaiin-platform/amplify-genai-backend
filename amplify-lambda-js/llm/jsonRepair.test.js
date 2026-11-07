import test from 'node:test';
import assert from 'node:assert/strict';
import { repairMalformedJson, parseJsonWithRepair } from './jsonRepair.js';

test('repairs the doubled closing quote gpt-5.6-luna emitted in the FAQ step', () => {
    // Verbatim from the dev incident (request ptnf49).
    const raw = '{"firstQuestion":"Which StarTech.com cables in the audio descriptor file mention gold plating?","secondQuestion":"What StarTech.com audio cables are described as having gold-plated connectors or contacts?","thirdQuestion":"Which cable product descriptions in the audio descriptor file specify gold plating?""}';
    assert.throws(() => JSON.parse(raw));
    const { parsed, repaired } = parseJsonWithRepair(raw);
    assert.equal(repaired, true);
    assert.equal(parsed.thirdQuestion, 'Which cable product descriptions in the audio descriptor file specify gold plating?');
    assert.equal(parsed.firstQuestion.endsWith('gold plating?'), true);
});

test('repairs a doubled quote mid-object and inside arrays', () => {
    assert.deepEqual(parseJsonWithRepair('{"a":"x"","b":"y"}').parsed, { a: 'x', b: 'y' });
    assert.deepEqual(parseJsonWithRepair('{"a":["x"",  "y""]}').parsed, { a: ['x', 'y'] });
});

test('does not touch legitimate empty strings', () => {
    for (const text of ['{"a":""}', '{"a":"x","b":""}', '["",""]', '{"a":"" }']) {
        assert.equal(repairMalformedJson(text), text);
        assert.equal(parseJsonWithRepair(text).repaired, false);
    }
});

test('does not damage a legitimate empty string when another defect forces a repair', () => {
    const { parsed } = parseJsonWithRepair('{"a":"","b":[1,,2]}');
    assert.deepEqual(parsed, { a: '', b: [1, 2] });
});

test('existing comma repairs still work', () => {
    assert.deepEqual(parseJsonWithRepair('[0,1,,3]').parsed, [0, 1, 3]);
    assert.deepEqual(parseJsonWithRepair('[0,1,]').parsed, [0, 1]);
    assert.deepEqual(parseJsonWithRepair('[,0,1]').parsed, [0, 1]);
});

test('genuinely broken JSON still throws', () => {
    assert.throws(() => parseJsonWithRepair('{"a": '));
    assert.throws(() => parseJsonWithRepair('not json'));
});
