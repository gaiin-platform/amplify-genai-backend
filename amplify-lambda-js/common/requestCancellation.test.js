import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createRequestCancellation } from './requestCancellation.js';

function stream() {
    const value = new EventEmitter();
    value.writableEnded = false;
    value.writableFinished = false;
    return value;
}

const waitFor = async predicate => {
    const deadline = Date.now() + 300;
    while (!predicate()) {
        if (Date.now() > deadline) throw new Error('Timed out waiting for cancellation');
        await new Promise(resolve => setTimeout(resolve, 1));
    }
};

test('killswitch abort is local to its request and polling/listeners are cleaned up', async () => {
    const killed = new Set();
    const checkKilled = async (_user, requestId) => killed.has(requestId);
    const optsA = {};
    const optsB = {};
    const streamA = stream();
    const streamB = stream();
    const a = createRequestCancellation({ user: 'u', requestId: 'a', requestOptions: optsA, responseStream: streamA, checkKilled, pollIntervalMs: 2, pollImmediately: true });
    const b = createRequestCancellation({ user: 'u', requestId: 'b', requestOptions: optsB, responseStream: streamB, checkKilled, pollIntervalMs: 2, pollImmediately: true });

    killed.add('a');
    await waitFor(() => a.signal.aborted);
    assert.equal(a.signal.reason.code, 'KILLSWITCH_CANCELLED');
    assert.equal(b.signal.aborted, false);

    a.cleanup();
    b.cleanup();
    await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(streamA.listenerCount('close'), 0);
    assert.equal(streamB.listenerCount('close'), 0);
    assert.equal('signal' in optsA, false);
    assert.equal('signal' in optsB, false);
});

test('client disconnect aborts only its request', () => {
    const aStream = stream();
    const bStream = stream();
    const a = createRequestCancellation({ user: 'u', requestId: 'a', requestOptions: {}, responseStream: aStream });
    const b = createRequestCancellation({ user: 'u', requestId: 'b', requestOptions: {}, responseStream: bStream });

    aStream.emit('close');
    assert.equal(a.signal.reason.code, 'CLIENT_DISCONNECTED');
    assert.equal(b.signal.aborted, false);
    a.cleanup();
    b.cleanup();
});

test('upstream overall timeout reason is propagated without affecting another request', () => {
    const parent = new AbortController();
    const a = createRequestCancellation({ user: 'u', requestId: 'a', requestOptions: {}, parentSignal: parent.signal });
    const b = createRequestCancellation({ user: 'u', requestId: 'b', requestOptions: {} });

    parent.abort(Object.assign(new Error('timeout'), { code: 'REQUEST_TIMEOUT' }));
    assert.equal(a.signal.reason.code, 'REQUEST_TIMEOUT');
    assert.equal(b.signal.aborted, false);
    a.cleanup();
    b.cleanup();
});
