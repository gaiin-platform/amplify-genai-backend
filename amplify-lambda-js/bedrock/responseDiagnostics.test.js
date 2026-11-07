import test from 'node:test';
import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import { extractBedrockResponseDiagnostics } from './responseDiagnostics.js';

// The SDK attaches the raw response as a hidden, non-enumerable `$response` (see
// schemaDeserializationMiddleware) - mirror that here.
const withHiddenResponse = (err, response) => Object.defineProperty(err, '$response', { value: response, enumerable: false });

test('no $response -> N/A placeholders', () => {
    assert.deepEqual(extractBedrockResponseDiagnostics(new Error('x')), { hasResponse: false, statusCode: 'N/A', headers: 'N/A', bodyPreview: 'N/A' });
});

test('surfaces status code and headers from the hidden $response on a deserialization error', () => {
    const err = withHiddenResponse(new Error('Bedrock is unable to process your request.\n  Deserialization error'), {
        statusCode: 200,
        headers: { 'x-amzn-requestid': 'abc', 'content-type': 'application/vnd.amazon.eventstream', 'x-amzn-errortype': 'InternalServerException' },
        body: Readable.from(['frame']),
    });
    assert.equal(JSON.stringify(err).includes('x-amzn'), false, 'precondition: default serialization hides it');
    const d = extractBedrockResponseDiagnostics(err);
    assert.equal(d.hasResponse, true);
    assert.equal(d.statusCode, 200);
    assert.equal(d.headers['x-amzn-errortype'], 'InternalServerException');
    assert.equal(d.bodyPreview, '[stream - not consumed]');
});

test('previews string and byte bodies, truncated to 500 chars', () => {
    assert.equal(extractBedrockResponseDiagnostics(withHiddenResponse(new Error(), { statusCode: 502, body: '<html>Bad Gateway</html>' })).bodyPreview, '<html>Bad Gateway</html>');
    assert.equal(extractBedrockResponseDiagnostics(withHiddenResponse(new Error(), { statusCode: 502, body: new TextEncoder().encode('oops') })).bodyPreview, 'oops');
    assert.equal(extractBedrockResponseDiagnostics(withHiddenResponse(new Error(), { statusCode: 502, body: 'a'.repeat(900) })).bodyPreview.length, 500);
});

// ---- middleware: survives the SDK's retry-layer rewrap -----------------------------------------
import { captureRawResponseMiddleware, captureRawResponseMiddlewareOptions } from './responseDiagnostics.js';
import { createRequire } from 'node:module';

test('middleware copies status/headers so they survive asSdkError\'s Object.assign(new Error(), thrown)', async () => {
    const thrown = { message: 'Bedrock is unable to process your request.' };            // stream exceptions are plain objects
    Object.defineProperty(thrown, '$response', { value: { statusCode: 200, headers: { 'x-amzn-requestid': 'r1' } }, enumerable: false });
    await assert.rejects(captureRawResponseMiddleware(async () => { throw thrown; })({}), (e) => e === thrown);
    const rewrapped = Object.assign(new Error(), thrown);                                 // exactly what the SDK retry layer does
    assert.equal(rewrapped.$response, undefined, 'precondition: $response is lost by the rewrap');
    const d = extractBedrockResponseDiagnostics(rewrapped);
    assert.equal(d.hasResponse, true);
    assert.equal(d.statusCode, 200);
    assert.equal(d.headers['x-amzn-requestid'], 'r1');
});

test('middleware is transparent for successes and for errors with no $response', async () => {
    assert.deepEqual(await captureRawResponseMiddleware(async (a) => ({ output: a }))({ x: 1 }), { output: { x: 1 } });
    const plain = new Error('boom');
    await assert.rejects(captureRawResponseMiddleware(async () => { throw plain; })({}), (e) => e === plain && e.rawResponseStatus === undefined);
    await assert.rejects(captureRawResponseMiddleware(async () => { throw 'a string'; })({}), (e) => e === 'a string'); // non-object throws untouched
});

test('in the REAL SDK stack the middleware sees the deserializer\'s $response and it survives to the caller', async () => {
    const req = createRequire(new URL('../package.json', import.meta.url));
    const { BedrockRuntimeClient, ConverseCommand } = req('@aws-sdk/client-bedrock-runtime');
    const { HttpResponse } = req('@smithy/protocol-http');
    // 200 with an un-parseable body -> the SDK's own deserializer throws and attaches hidden $response.
    const requestHandler = { handle: async () => ({ response: new HttpResponse({ statusCode: 200, headers: { 'x-amzn-requestid': 'real-1', 'content-type': 'application/json' }, body: Buffer.from('<<not json>>') }) }), destroy() {} };
    const client = new BedrockRuntimeClient({ region: 'us-east-1', credentials: { accessKeyId: 'x', secretAccessKey: 'y' }, requestHandler, maxAttempts: 1 });
    client.middlewareStack.add(captureRawResponseMiddleware, captureRawResponseMiddlewareOptions);
    const err = await client.send(new ConverseCommand({ modelId: 'm', messages: [{ role: 'user', content: [{ text: 'hi' }] }] })).then(() => null, (e) => e);
    assert.ok(err, 'expected a deserialization failure');
    const d = extractBedrockResponseDiagnostics(err);
    assert.equal(d.statusCode, 200);
    assert.equal(d.headers['x-amzn-requestid'], 'real-1');
});
