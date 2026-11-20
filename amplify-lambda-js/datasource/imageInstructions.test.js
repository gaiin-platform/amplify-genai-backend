import test from 'node:test';
import assert from 'node:assert/strict';
import { additionalImageInstruction, unavailableImagesInstruction, buildImageInstruction } from './imageInstructions.js';

test('labels only the images that loaded', () => {
    const text = buildImageInstruction(['a.png', 'c.png'], []);
    assert.ok(text.includes('- Image #1: a.png'));
    assert.ok(text.includes('- Image #2: c.png'));
    assert.ok(!text.includes('Image Attachment Error'));
    assert.equal(text, additionalImageInstruction(['a.png', 'c.png']));
});

test('tells the model about images that could not be loaded instead of labelling them', () => {
    const text = buildImageInstruction(['a.png'], ['pasted-123.png']);
    assert.ok(text.includes('- Image #1: a.png'));
    assert.ok(!text.includes('Image #2'));
    assert.ok(text.includes('Image Attachment Error'));
    assert.ok(text.includes('- pasted-123.png'));
    assert.ok(/could not be loaded/i.test(text));
});

test('when no image loaded, only the error notice is produced (no empty label block)', () => {
    const text = buildImageInstruction([], ['pasted-123.png']);
    assert.equal(text, unavailableImagesInstruction(['pasted-123.png']));
    assert.ok(!text.includes('Image Reference Labels'));
});

test('returns an empty string when there is nothing to say', () => {
    assert.equal(buildImageInstruction(), '');
    assert.equal(buildImageInstruction([], []), '');
});
