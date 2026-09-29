import test from 'node:test';
import assert from 'node:assert/strict';
import {
    ROUTING_SCHEMA_VERSION,
    applyRoutingPolicy,
    buildRoutingContext,
    hasDownloadableFileIntent,
    resolveRoutingOptions,
    resolveSpecializedAssistantMode,
    isEligibleOrdinaryChat,
    routeOrdinaryChat,
    validateRoutingDecision
} from './ordinaryChatRouter.js';

const valid = { schemaVersion: 1, webSearch: true, artifacts: false, codeInterpreter: true };

test('validates strict versioned boolean decisions', () => {
    assert.deepEqual(validateRoutingDecision(valid), valid);
    assert.equal(validateRoutingDecision({ ...valid, schemaVersion: 2 }), null);
    assert.equal(validateRoutingDecision({ ...valid, artifacts: 'true' }), null);
    assert.equal(validateRoutingDecision({ ...valid, extra: false }), null);
    assert.equal(validateRoutingDecision({ ...valid, reasons: { artifacts: 'not-allowed' } }), null);
});

test('routing-context sanitization preserves ordinary text and removes only control characters', () => {
    const normalText = 'Case 42: punctuation, https://example.com/a?b=3&c=9!';
    const withControls = `before${String.fromCharCode(0, 8, 11, 12, 14, 31)}middle${String.fromCharCode(1, 14, 30)}after`;
    assert.deepEqual(buildRoutingContext([
        { role: 'user', content: normalText },
        { role: 'assistant', content: withControls }
    ]), [
        { role: 'user', content: normalText },
        { role: 'assistant', content: 'beforemiddleafter' }
    ]);
});

test('bounds routing context and excludes non-text/tool/system content', () => {
    const context = buildRoutingContext([
        { role: 'system', content: 'secret admin prompt' },
        { role: 'tool', content: 'secret tool result' },
        { role: 'user', content: 'a'.repeat(100) },
        { role: 'assistant', content: ['not text'] },
        { role: 'user', content: 'latest' }
    ], { maxTurns: 4, maxChars: 20 });
    assert.deepEqual(context, [{ role: 'user', content: 'a'.repeat(14) }, { role: 'user', content: 'latest' }]);
    assert.ok(context.every(turn => turn.role === 'user' || turn.role === 'assistant'));
});

test('recognizes downloadable Word, DOCX, and PDF requests', () => {
    assert.equal(hasDownloadableFileIntent([{ role: 'user', content: 'Create a Word document I can download.' }]), true);
    assert.equal(hasDownloadableFileIntent([{ role: 'user', content: 'Please export this as .docx.' }]), true);
    assert.equal(hasDownloadableFileIntent([{ role: 'user', content: 'Generate a PDF file for me.' }]), true);
    assert.equal(hasDownloadableFileIntent([{ role: 'user', content: 'Explain what DOCX means.' }]), false);
});

test('dispatches downloadable documents to code interpreter only when deployment-enabled and available', () => {
    const docxMessages = [{ role: 'user', content: 'Please create a downloadable DOCX file.' }];
    const pdfMessages = [{ role: 'user', content: 'Create a PDF report and attach it.' }];
    for (const messages of [docxMessages, pdfMessages]) {
        const decision = applyRoutingPolicy({ schemaVersion: 1, webSearch: false, artifacts: true, codeInterpreter: false },
            { artifacts: true, codeInterpreter: true }, messages);
        assert.equal(decision.artifacts, false);
        assert.equal(decision.codeInterpreter, true);
    }
    assert.equal(resolveRoutingOptions({ artifacts: true, codeInterpreter: false },
        { artifacts: true, codeInterpreter: true }, { downloadableFileIntent: true }).codeInterpreterOnly, true);
    const selectedAssistantFile = resolveRoutingOptions({ artifacts: false, codeInterpreter: false },
        { artifacts: true, codeInterpreter: true }, { downloadableFileIntent: true, explicitModes: { notEligible: true } });
    assert.equal(selectedAssistantFile.codeInterpreterOnly, true);
    const disabled = resolveRoutingOptions({ artifacts: true, codeInterpreter: true },
        { artifacts: true, codeInterpreter: false }, { downloadableFileIntent: true });
    assert.equal(disabled.codeInterpreterOnly, false);
    assert.equal(disabled.fileGenerationUnavailable, true);
    const unavailable = resolveRoutingOptions({ artifacts: true, codeInterpreter: true },
        { artifacts: true, codeInterpreter: true }, { downloadableFileIntent: true, interpreterAvailable: false });
    assert.equal(unavailable.codeInterpreterOnly, false);
    assert.equal(unavailable.fileGenerationUnavailable, true);
    assert.equal(resolveRoutingOptions({ artifacts: true, codeInterpreter: false },
        { artifacts: true, codeInterpreter: false }, {}).artifacts, true);
});

test('ordinary routing preserves an artifact auto-route when Code Interpreter is disabled', () => {
    const result = applyRoutingPolicy({ schemaVersion: 1, webSearch: false, artifacts: true, codeInterpreter: true },
        { artifacts: true, codeInterpreter: false }, [{ role: 'user', content: 'Build a reusable planning tool.' }]);
    assert.equal(result.artifacts, true);
    assert.equal(result.codeInterpreter, false);
});

test('preserves explicit specialized modes, gates disabled features, and keeps fallback automatic routing off', () => {
    const availability = { webSearch: true, artifacts: true, codeInterpreter: true };
    const explicitInterpreter = resolveRoutingOptions({ webSearch: false, artifacts: false, codeInterpreter: false }, availability,
        { explicitModes: { codeInterpreterOnly: true } });
    assert.equal(explicitInterpreter.codeInterpreterOnly, true);
    assert.equal(resolveSpecializedAssistantMode({ codeInterpreterOnly: true, deploymentFeatures: availability, codeInterpreterAvailable: true }), 'codeInterpreter');
    assert.equal(resolveSpecializedAssistantMode({ artifactsMode: true, deploymentFeatures: availability }), 'artifacts');
    assert.equal(resolveSpecializedAssistantMode({ codeInterpreterOnly: true, deploymentFeatures: { ...availability, codeInterpreter: false } }), null);
    assert.equal(resolveSpecializedAssistantMode({ codeInterpreterOnly: true, deploymentFeatures: availability, codeInterpreterAvailable: false }), null);
    assert.equal(resolveSpecializedAssistantMode({ artifactsMode: true, deploymentFeatures: { ...availability, artifacts: false } }), null);
    assert.equal(resolveSpecializedAssistantMode({ artifactsMode: true, api_accessed: true, deploymentFeatures: availability }), 'artifacts');
    const explicitArtifacts = resolveRoutingOptions({ webSearch: false, artifacts: false, codeInterpreter: false }, availability,
        { explicitModes: { artifactsMode: true, notEligible: true } });
    assert.equal(explicitArtifacts.artifactsMode, true);
    assert.equal(resolveRoutingOptions({ webSearch: true, artifacts: true, codeInterpreter: true },
        { webSearch: false, artifacts: false, codeInterpreter: false },
        { explicitModes: { artifactsMode: true, codeInterpreterOnly: true } }).artifactsMode, false);
    assert.equal(resolveRoutingOptions({ webSearch: true, artifacts: true, codeInterpreter: true },
        { webSearch: false, artifacts: false, codeInterpreter: false },
        { explicitModes: { artifactsMode: true, codeInterpreterOnly: true } }).codeInterpreterOnly, false);
    const ordinary = resolveRoutingOptions({ webSearch: true, artifacts: true, codeInterpreter: false }, availability);
    assert.deepEqual(ordinary, { enableWebSearch: true, artifacts: true, artifactsMode: false, codeInterpreterOnly: false, downloadableFileIntent: false, fileGenerationUnavailable: false, notEligible: false });
    const fallback = resolveRoutingOptions({ webSearch: false, artifacts: false, codeInterpreter: false }, availability);
    assert.equal(fallback.artifacts, false);
    assert.equal(fallback.codeInterpreterOnly, false);
});

test('admin availability always wins over classifier output', () => {
    const decision = applyRoutingPolicy(valid, { webSearch: false, artifacts: false, codeInterpreter: false }, []);
    assert.deepEqual(decision, { schemaVersion: ROUTING_SCHEMA_VERSION, webSearch: false, artifacts: false, codeInterpreter: false });
});

const params = { options: { cheapestModel: { id: 'cheap', provider: 'OpenAI' } } };
const body = { messages: [{ role: 'user', content: 'hello' }] };

test('routing fallback is safe on disabled and malformed classifier calls', async () => {
    const disabled = await routeOrdinaryChat(params, body, { routingEnabled: false, availability: { webSearch: true, artifacts: true, codeInterpreter: true } });
    assert.equal(disabled.decision.webSearch, false);
    const malformed = await routeOrdinaryChat(params, body, { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } }, { classify: async () => ({ schemaVersion: 1, webSearch: 'yes', artifacts: false, codeInterpreter: false }) });
    assert.equal(malformed.metadata.fallback, 'invalid_schema');
});

test('timeout aborts classifier work while returning the safe fallback for ordinary chat', async () => {
    const body = { messages: [{ role: 'user', content: 'hello' }] };
    const disabled = await routeOrdinaryChat(params, body, { routingEnabled: false, availability: { webSearch: true, artifacts: true, codeInterpreter: true } });
    assert.equal(disabled.decision.webSearch, false);
    const malformed = await routeOrdinaryChat(params, body, { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } }, { classify: async () => ({ schemaVersion: 1, webSearch: 'yes', artifacts: false, codeInterpreter: false }) });
    assert.equal(malformed.metadata.fallback, 'invalid_schema');
    let classifierSignal;
    let didAbort = false;
    const timedOut = await routeOrdinaryChat(params, body, { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } }, {
        timeoutMs: 2,
        classify: (_params, _messages, _schema, _stream, { signal }) => {
            classifierSignal = signal;
            signal.addEventListener('abort', () => { didAbort = true; });
            return new Promise(() => {});
        }
    });
    assert.equal(timedOut.metadata.fallback, 'router_timeout');
    assert.equal(classifierSignal.aborted, true);
    assert.equal(didAbort, true);
    const fallbackOptions = resolveRoutingOptions(timedOut.decision, { webSearch: true, artifacts: true, codeInterpreter: true });
    assert.equal(fallbackOptions.enableWebSearch, false);
    assert.equal(fallbackOptions.artifacts, false);
    assert.equal(fallbackOptions.codeInterpreterOnly, false);
});

test('request cancellation aborts routing instead of being treated as a safe classifier failure', async () => {
    const controller = new AbortController();
    controller.abort(Object.assign(new Error('killed'), { code: 'KILLSWITCH_CANCELLED' }));
    await assert.rejects(
        routeOrdinaryChat({ ...params, signal: controller.signal }, body,
            { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } },
            { classify: async () => assert.fail('classifier must not start') }),
        { code: 'KILLSWITCH_CANCELLED' }
    );
});

test('in-flight cancellation aborts only its classifier and rejects without fallback', async () => {
    const aController = new AbortController();
    const bController = new AbortController();
    let aSignal;
    let bSignal;
    const config = { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } };
    const classify = (_params, _messages, _schema, _stream, { signal, requestId }) => {
        if (requestId === 'request-a') aSignal = signal;
        if (requestId === 'request-b') bSignal = signal;
        return new Promise(() => {});
    };
    const pendingA = routeOrdinaryChat({ ...params, requestId: 'request-a', signal: aController.signal }, body, config, { classify });
    const pendingB = routeOrdinaryChat({ ...params, requestId: 'request-b', signal: bController.signal }, body, config, { classify });
    await new Promise(resolve => setImmediate(resolve));
    aController.abort(Object.assign(new Error('disconnect'), { code: 'CLIENT_DISCONNECTED' }));
    await assert.rejects(pendingA, { code: 'CLIENT_DISCONNECTED' });
    assert.equal(aSignal.aborted, true);
    assert.equal(bSignal.aborted, false);
    bController.abort(Object.assign(new Error('killswitch'), { code: 'KILLSWITCH_CANCELLED' }));
    await assert.rejects(pendingB, { code: 'KILLSWITCH_CANCELLED' });
    assert.equal(bSignal.aborted, true);
});

test('does not route specialized requests', async () => {
    assert.equal(isEligibleOrdinaryChat({}, { messages: [{ role: 'user', content: 'x' }], options: { assistantId: 'astp/x' } }), false);
    const result = await routeOrdinaryChat({ options: { cheapestModel: { id: 'cheap' } } }, { messages: [{ role: 'user', content: 'x' }], options: { configuredTools: [] } }, { routingEnabled: true, availability: {} }, { classify: async () => { throw new Error('must not classify'); } });
    assert.equal(result.metadata.eligible, false);
});
