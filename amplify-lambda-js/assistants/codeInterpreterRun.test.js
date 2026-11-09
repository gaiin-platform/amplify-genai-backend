import test from 'node:test';
import assert from 'node:assert/strict';
import {
    RUN_LIMITS,
    buildRunRecord,
    generatedFileName,
    isCodeFailure,
    stripCodeFailurePrefix,
} from './codeInterpreterRun.js';

test('a successful run keeps code, output, description and the files it made', () => {
    const run = buildRunRecord({
        id: 'call_1',
        ok: true,
        args: { code: 'print(1+1)', description: '  Added two numbers.  ' },
        stdout: '2\n',
        files: [{ type: 'text/csv', values: { file_key: 'me@x.edu/abc123-FN-fib.csv' } }],
    });
    assert.deepEqual(run, {
        id: 'call_1',
        ok: true,
        code: 'print(1+1)',
        description: 'Added two numbers',
        stdout: '2\n',
        files: [{ name: 'fib.csv', type: 'text/csv' }],
    });
});

test('empty fields are omitted, and a missing description is simply absent', () => {
    const run = buildRunRecord({ id: 'c', ok: true, args: { code: 'x = 1' }, stdout: '  \n', files: [] });
    assert.deepEqual(run, { id: 'c', ok: true, code: 'x = 1' });
});

test('a failed run carries the error and is not ok', () => {
    const run = buildRunRecord({ id: 'c', ok: false, args: { code: '1/0' }, error: 'ZeroDivisionError: division by zero' });
    assert.equal(run.ok, false);
    assert.equal(run.error, 'ZeroDivisionError: division by zero');
    assert.equal('stdout' in run, false);
});

test('ok is only ever true when explicitly true', () => {
    assert.equal(buildRunRecord({ id: 'c', args: { code: 'x' } }).ok, false);
    assert.equal(buildRunRecord({ id: 'c', ok: 'yes', args: { code: 'x' } }).ok, false);
});

test('long code, output and errors are truncated and flagged', () => {
    const run = buildRunRecord({
        id: 'c',
        ok: false,
        args: { code: 'a'.repeat(RUN_LIMITS.code + 50) },
        stdout: 'b'.repeat(RUN_LIMITS.stdout + 50),
        error: 'c'.repeat(RUN_LIMITS.error + 50),
    });
    assert.equal(run.code.length, RUN_LIMITS.code);
    assert.equal(run.stdout.length, RUN_LIMITS.stdout);
    assert.equal(run.error.length, RUN_LIMITS.error);
    assert.deepEqual(run.truncated, { code: true, stdout: true, error: true });
});

test('nothing is flagged truncated when everything fits', () => {
    const run = buildRunRecord({ id: 'c', ok: true, args: { code: 'x' }, stdout: 'y' });
    assert.equal('truncated' in run, false);
});

test('a description that is not a string, or is enormous, is handled', () => {
    assert.equal('description' in buildRunRecord({ id: 'c', ok: true, args: { code: 'x', description: 42 } }), false);
    const long = buildRunRecord({ id: 'c', ok: true, args: { code: 'x', description: 'w '.repeat(200) } });
    assert.ok(long.description.length <= RUN_LIMITS.description);
});

test('malformed args or files never throw', () => {
    assert.doesNotThrow(() => buildRunRecord({ id: 'c', ok: true, args: {}, files: null }));
    assert.doesNotThrow(() => buildRunRecord({ id: 'c', ok: true, args: { code: null }, files: [null, {}] }));
});

test('the record is JSON-safe and stable, so repeated events de-duplicate', () => {
    const make = () => buildRunRecord({ id: 'c', ok: true, args: { code: 'x', description: 'Did x' }, stdout: 'out' });
    assert.equal(JSON.stringify(make()), JSON.stringify(make()));
});

test('generatedFileName reads the -FN- name, else the basename, else a default', () => {
    assert.equal(generatedFileName({ values: { file_key: 'u/id-FN-report final.xlsx' } }), 'report final.xlsx');
    assert.equal(generatedFileName({ values: { file_key: 'u/plain.png' } }), 'plain.png');
    assert.equal(generatedFileName({}), 'generated_file');
});

test('only the user\'s own code failing is recorded as a run', () => {
    assert.equal(isCodeFailure('Code execution error: Traceback...'), true);
    assert.equal(isCodeFailure('Code execution timed out after 240 seconds.'), true);
    assert.equal(isCodeFailure('session_expired'), false);
    assert.equal(isCodeFailure('Request was cancelled by user during execution'), false);
    assert.equal(isCodeFailure('You are not authorized to access the referenced files'), false);
    assert.equal(isCodeFailure(undefined), false);
    assert.equal(stripCodeFailurePrefix('Code execution error: NameError: x'), 'NameError: x');
    assert.equal(stripCodeFailurePrefix('Code execution timed out after 240 seconds.'), 'Code execution timed out after 240 seconds.');
});
