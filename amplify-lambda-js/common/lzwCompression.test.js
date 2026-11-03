import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { lzwCompress, lzwUncompress } from './lzwCompression.js';

// Shared with pycommon (tests/fixtures/lzw_vectors.json) and the frontend: all three must produce/read the same codes.
const vectors = JSON.parse(readFileSync(new URL('./fixtures/lzw_vectors.json', import.meta.url), 'utf8'));
const roundTrip = (text) => lzwUncompress(lzwCompress(text));

test('shared vectors: compress gives the same codes as pycommon and the frontend', () => {
    for (const v of vectors) assert.deepEqual(lzwCompress(v.text), v.codes, JSON.stringify(v.text));
});

test('shared vectors: uncompress reads the codes written by pycommon and the frontend', () => {
    for (const v of vectors) assert.equal(lzwUncompress(v.codes), v.text, JSON.stringify(v.text));
});

test('round trip for scripts that used to come back as raw tags', () => {
    for (const text of ['Привет мир', 'zażółć gęślą', 'Γειά σου', 'שלום', 'مرحبا', 'สวัสดี', 'hi 😀 ok', 'type U+0041 here',
        'u+0041 stays', 'ends with U+', 'ā' + 'abc' + 'ł' + 'deadbeef', 'café ñ', '你好世界']) {
        assert.equal(roundTrip(text), text);
    }
});

test('reads artifacts written by the old encoder (unpadded 3 digit tags)', () => {
    const legacy = Array.from('U+41fU+440U+438 ok').map((c) => c.charCodeAt(0));
    assert.equal(lzwUncompress(legacy), 'При ok'.replace('и', 'и'));
    assert.equal(lzwUncompress(Array.from('U+430 ').map((c) => c.charCodeAt(0))), 'а ');
});

test('empty input', () => {
    assert.deepEqual(lzwCompress(''), []);
    assert.equal(lzwUncompress([]), '');
});
