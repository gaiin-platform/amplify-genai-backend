import test from 'node:test';
import assert from 'node:assert/strict';
import { ARTIFACT_TYPE_RULES } from './artifactTypeRules.js';

test('artifact type rules cover every artifact type and the summary markers', () => {
    for (const heading of ['### Type: spreadsheet', '### Type: code', '### Type: visualization', '### Type: document']) {
        assert.ok(ARTIFACT_TYPE_RULES.includes(heading), `missing ${heading}`);
    }
    assert.ok(ARTIFACT_TYPE_RULES.includes('<> I\'ve created the departments CSV'));
    assert.ok(ARTIFACT_TYPE_RULES.includes('</>'));
});

test('artifact type rules render the template placeholders, not their source text', () => {
    assert.ok(!ARTIFACT_TYPE_RULES.includes('${'));
    assert.ok(ARTIFACT_TYPE_RULES.includes('```mermaid'));
    assert.ok(ARTIFACT_TYPE_RULES.includes('```vega'));
    assert.ok(ARTIFACT_TYPE_RULES.includes("```python\n# main.py\nprint('Hello')\n```"));
});

test('the HTML-looking text the WAF objected to now lives only on the server side', () => {
    assert.ok(ARTIFACT_TYPE_RULES.includes('<svg'));
    assert.ok(ARTIFACT_TYPE_RULES.includes('<!DOCTYPE html>'));
});
