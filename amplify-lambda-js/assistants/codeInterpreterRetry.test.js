import test from 'node:test';
import assert from 'node:assert/strict';
import {
    MAX_CODE_RETRIES,
    isRetryableCodeError,
    parseToolCode,
    buildToolErrorTurn,
    truncateErrorForModel,
    isFileRelatedError,
    RETRY_FILE_HINT
} from './codeInterpreterRetry.js';

test('only Python script errors are retryable', () => {
    assert.equal(isRetryableCodeError('Code execution error: ModuleNotFoundError: No module named x'), true);
    for (const m of ['session_expired', 'Code execution timed out after 240 seconds.', 'Request was cancelled by user',
        'Failed to invoke code interpreter: boom', '', undefined]) {
        assert.equal(isRetryableCodeError(m), false, String(m));
    }
    assert.ok(MAX_CODE_RETRIES >= 1);
});

test('parseToolCode returns the code for valid arguments', () => {
    const r = parseToolCode({ function: { arguments: JSON.stringify({ code: 'print(1)' }) } });
    assert.deepEqual(r, { code: 'print(1)', error: null });
});

test('parseToolCode flags truncated JSON, missing and empty code instead of returning empty code', () => {
    assert.match(parseToolCode({ function: { arguments: '{"code": "from pptx import Pres' } }).error, /cut off/);
    assert.ok(parseToolCode({ function: {} }).error);
    assert.ok(parseToolCode({ function: { arguments: '{}' } }).error);
    assert.ok(parseToolCode({ function: { arguments: '{"code": "   "}' } }).error);
    assert.equal(parseToolCode({ function: { arguments: '{"code": "x"}' } }).code, 'x');
});

test('buildToolErrorTurn pairs the tool call with an error result and can replace bad arguments', () => {
    const toolCall = { id: 't1', type: 'function', function: { name: 'execute_code', arguments: '{"code": "bro' } };
    const [assistant, tool] = buildToolErrorTurn({
        assistantContent: null, toolCall, errorMessage: 'bad', safeArguments: '{}'
    });
    assert.equal(assistant.role, 'assistant');
    assert.equal(assistant.tool_calls[0].function.arguments, '{}');
    assert.equal(assistant.tool_calls[0].function.name, 'execute_code');
    assert.equal(tool.tool_call_id, 't1');
    assert.deepEqual(JSON.parse(tool.content), { error: 'bad' });

    const [keep] = buildToolErrorTurn({ toolCall: { ...toolCall, function: { ...toolCall.function, arguments: '{"code":"x"}' } }, errorMessage: 'e' });
    assert.equal(keep.tool_calls[0].function.arguments, '{"code":"x"}');
});

test('long tracebacks keep their final lines', () => {
    const long = 'x'.repeat(10000) + '\nAttributeError: final line';
    const out = truncateErrorForModel(long);
    assert.ok(out.length < 4100);
    assert.ok(out.endsWith('AttributeError: final line'));
});

test('python tracebacks are not mistaken for missing-file errors', () => {
    const traceback = 'Code execution error: Traceback (most recent call last):\n  File "run.py", line 12, in <module>\nAttributeError: x';
    assert.equal(isFileRelatedError(traceback), false);
    assert.equal(isFileRelatedError('Code execution error: FileNotFoundError: [Errno 2] No such file or directory: a.csv'), true);
    assert.equal(isFileRelatedError('You are not authorized to access the referenced files'), true);
    assert.equal(isFileRelatedError('Failed to load file into sandbox'), true);
    assert.equal(isFileRelatedError('Code execution timed out after 240 seconds.'), false);
});

test('retry hint tells the model to save under a new filename', () => {
    assert.match(RETRY_FILE_HINT, /different filename/);
});
