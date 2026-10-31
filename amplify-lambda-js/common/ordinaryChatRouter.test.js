import test from 'node:test';
import assert from 'node:assert/strict';

// Required before the default classifier's dynamic import of UnifiedLLMClient.js (and its
// transitive secrets/provider modules) runs for the first time in this test process, or
// module-load-time secret fetching crashes the whole test file instead of rejecting normally.
process.env.LOCAL_DEVELOPMENT = 'true';
process.env.LOCAL_SECRET_ = '{"models":[]}';
process.env.API_BASE_URL = 'https://test.invalid';

import {
    ROUTING_SCHEMA_VERSION,
    ROUTING_TIMEOUT_MS,
    applyRoutingPolicy,
    resolveRoutingModel,
    buildRoutingContext,
    defaultClassifier,
    hasDownloadableFileIntent,
    hasSelectedAssistant,
    hasConfiguredToolsOnMessages,
    resolveRoutingOptions,
    resolveSpecializedAssistantMode,
    isEligibleOrdinaryChat,
    routeOrdinaryChat,
    resolveConfiguredRoutingModel,
    validateRoutingDecision
} from './ordinaryChatRouter.js';

const valid = { schemaVersion: 1, webSearch: true, artifacts: false, codeInterpreter: true };

test('deployment config defaults memory and prompt highlighter off and other features on', async () => {
    const { normalizeDeploymentConfig } = await import('./adminConfig.js');
    const normalized = normalizeDeploymentConfig(null, null);
    assert.deepEqual(normalized.availability, {
        promptHighlighter: false,
        artifacts: true,
        webSearch: true,
        codeInterpreter: true,
        memory: false
    });
});

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

test('does not treat file names or extensions inside pasted data as a download request', () => {
    const requisition = 'Requisition: R27017824\nAmount: $500.00 USD\nAttachment: Colloquium F26 Chair COA Approval.pdf\nFunds status: Not reserved';
    assert.equal(hasDownloadableFileIntent([{ role: 'user', content: requisition }]), false);
    assert.equal(hasDownloadableFileIntent([{ role: 'user', content: 'Summarize budget.xlsx and notes.docx' }]), false);
    assert.equal(hasDownloadableFileIntent([{ role: 'user', content: 'Save the output as report.docx' }]), true);
});

test('hasSelectedAssistant detects user/group assistants but not the built-in helper', () => {
    assert.equal(hasSelectedAssistant({ assistantId: 'astp/abc' }, {}), true);
    assert.equal(hasSelectedAssistant({ groupId: 'g1' }, {}), true);
    assert.equal(hasSelectedAssistant({}, { assistantId: 'astp/abc' }), true);
    assert.equal(hasSelectedAssistant({}, {}), false);
    assert.equal(hasSelectedAssistant({ assistantId: 'amplify-helper', amplifyHelper: true }, {}), false);
    assert.equal(hasSelectedAssistant({ assistantId: 'amplify-helper', amplifyHelper: false }, {}), true);
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

test('routing model uses supported configured cheapest model and falls back from provider-less records', () => {
    const requestModel = { id: 'request-model', provider: 'Bedrock' };
    assert.equal(resolveRoutingModel({ options: { cheapestModel: { id: 'cheap', provider: 'OpenAI' }, model: requestModel } }).id, 'cheap');
    assert.equal(resolveRoutingModel({ options: { cheapestModel: { id: 'cheap', provider: '' }, model: requestModel } }), requestModel);
    assert.equal(resolveRoutingModel({ cheapestModel: { id: 'cheap' }, model: requestModel }), requestModel);
    assert.equal(resolveRoutingModel({ options: { cheapestModel: { id: 'unknown', provider: 'unsupported' } } }), null);
});

test('classifier always uses the internal Haiku 4.5 model instead of user-selected models', async () => {
    let classifierParams;
    const requestModel = { id: 'user-selected-model', provider: 'Bedrock' };
    const userCheapestModel = { id: 'user-cheapest-model', provider: 'OpenAI' };
    const result = await routeOrdinaryChat({
        model: requestModel,
        options: { cheapestModel: userCheapestModel, model: requestModel }
    }, body, { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } }, {
        classify: async (params) => {
            classifierParams = params;
            return { schemaVersion: 1, webSearch: true, artifacts: true, codeInterpreter: false };
        }
    });
    assert.equal(classifierParams.model.id, 'us.anthropic.claude-haiku-4-5-20251001-v1:0');
    assert.equal(classifierParams.model.name, 'Claude Haiku 4.5');
    assert.equal(classifierParams.model.provider, 'Bedrock');
    assert.equal(classifierParams.model.outputTokenLimit, 64_000);
    assert.equal(classifierParams.options.model, classifierParams.model);
    assert.equal(classifierParams.cheapestModel, classifierParams.model);
    assert.equal(classifierParams.options.cheapestModel, classifierParams.model);
    assert.notEqual(classifierParams.options.model.id, requestModel.id);
    assert.notEqual(classifierParams.options.cheapestModel.id, userCheapestModel.id);
    assert.equal(requestModel.id, 'user-selected-model');
    assert.equal(userCheapestModel.id, 'user-cheapest-model');
    assert.equal(result.decision.webSearch, true);
    assert.equal(result.metadata.fallback, null);
});

const ALL_ON = { webSearch: true, artifacts: true, codeInterpreter: true };
const HAIKU_ID = 'us.anthropic.claude-haiku-4-5-20251001-v1:0';

async function routedModelFor(deploymentConfig, loadModel) {
    let classifierParams;
    const result = await routeOrdinaryChat(params, body,
        { routingEnabled: true, availability: ALL_ON, ...deploymentConfig },
        {
            loadModel,
            classify: async (p) => { classifierParams = p; return { schemaVersion: 1, webSearch: false, artifacts: false, codeInterpreter: false }; }
        });
    return { model: classifierParams.model, result, classifierParams };
}

test('an admin-configured routing model replaces the default classifier model', async () => {
    const configured = { id: 'gpt-4o-mini', name: 'GPT-4o mini', provider: 'OpenAI' };
    let asked;
    const { model, result, classifierParams } = await routedModelFor({ routingModelId: 'gpt-4o-mini' }, async (id) => { asked = id; return configured; });
    assert.equal(asked, 'gpt-4o-mini');
    assert.equal(model, configured);
    assert.equal(classifierParams.options.model, configured);
    assert.equal(classifierParams.options.cheapestModel, configured);
    assert.equal(classifierParams.options.disableReasoning, true);
    assert.equal(result.metadata.modelId, 'gpt-4o-mini');
});

test('routing falls back to the default model when no model is configured or the configured one cannot be used', async () => {
    const never = async () => { throw new Error('must not look up'); };
    assert.equal((await routedModelFor({}, never)).model.id, HAIKU_ID);
    assert.equal((await routedModelFor({ routingModelId: '' }, never)).model.id, HAIKU_ID);
    assert.equal((await routedModelFor({ routingModelId: HAIKU_ID }, never)).model.id, HAIKU_ID);

    const cases = {
        'not in catalog': async () => null,
        'lookup throws': async () => { throw new Error('dynamo down'); },
        'unsupported provider': async () => ({ id: 'x', provider: 'Mystery' }),
        'embedding model': async () => ({ id: 'amazon.titan-embed-text-v2:0', provider: 'Bedrock' }),
        'image generation model': async () => ({ id: 'img', provider: 'OpenAI', supportsImageGeneration: true })
    };
    for (const [label, load] of Object.entries(cases)) {
        const { model, result } = await routedModelFor({ routingModelId: 'configured' }, load);
        assert.equal(model.id, HAIKU_ID, label);
        assert.equal(result.metadata.fallback, null, label);
    }
});

test('the model lookup is skipped when routing will not classify', async () => {
    const never = async () => { throw new Error('must not look up'); };
    const off = await routeOrdinaryChat(params, body, { routingEnabled: false, availability: ALL_ON, routingModelId: 'configured' }, { loadModel: never });
    assert.equal(off.decision.webSearch, false);
    assert.equal(off.metadata.modelId, undefined);
});

test('resolveConfiguredRoutingModel returns the default for a missing config', async () => {
    assert.equal((await resolveConfiguredRoutingModel(null)).id, HAIKU_ID);
    assert.equal((await resolveConfiguredRoutingModel({ routingModelId: 42 })).id, HAIKU_ID);
});

test('classifier call disables extended reasoning and allows up to five seconds for classification', async () => {
    // Regression test: a reasoning-capable routing model with reasoning left enabled
    // took multiple seconds per classification call, always exceeding the old
    // 1500ms timeout and forcing every request to the safe all-off fallback.
    // disableReasoning must reach the classifier options, and the slightly larger
    // timeout leaves room for model warmup or temporary provider latency after thinking
    // has been disabled.
    let classifierParams;
    await routeOrdinaryChat(params, body,
        { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } },
        { classify: async (p) => { classifierParams = p; return { schemaVersion: 1, webSearch: false, artifacts: false, codeInterpreter: false }; } }
    );
    assert.equal(classifierParams.options.disableReasoning, true);
    assert.equal(ROUTING_TIMEOUT_MS, 5_000);
});

test('classification completing between old and new timeouts keeps enabled feature decisions', async () => {
    const result = await routeOrdinaryChat(params, body,
        { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } },
        { classify: async () => {
            await new Promise(resolve => setTimeout(resolve, 2_000));
            return { schemaVersion: 1, webSearch: true, artifacts: false, codeInterpreter: false };
        } }
    );
    assert.equal(result.metadata.fallback, null);
    assert.equal(result.decision.webSearch, true);
});

test('the exported default classifier is a real delegate, not an unbound reference', async () => {
    // Deterministic, no-network failure mode (promptUnifiedLLMForData throws before any
    // provider call when no model is supplied), which proves the dynamic import and
    // delegation wiring actually run rather than silently resolving to `undefined`.
    await assert.rejects(
        defaultClassifier({ options: {} }, [{ role: 'user', content: 'x' }], { type: 'object' }),
        /Model not specified/
    );
});

test('routeOrdinaryChat resolves a real classifier when no override is passed, matching the production call shape', async () => {
    // Regression test for the exact production bug: router.js calls
    // routeOrdinaryChat(params, body, deploymentConfig) with no 4th argument at all, so
    // `classify` must default to a real function. Previously it silently defaulted to
    // `undefined` while an unused local variable held the real implementation, so every
    // call threw "classify is not a function" synchronously — a single microtask that
    // Date.now() always measures as latencyMs: 0 — before ever reaching a provider.
    // A real classifier attempt crosses multiple awaited operations (dynamic import,
    // secrets/provider lookup) and always measures a non-zero latency, even when it
    // ultimately fails fast in this test environment (no configured secrets/network).
    const result = await routeOrdinaryChat(
        { options: { cheapestModel: { id: 'cheap', provider: 'OpenAI' } } },
        { messages: [{ role: 'user', content: 'hello' }] },
        { routingEnabled: true, availability: { webSearch: true, artifacts: true, codeInterpreter: true } }
    );
    assert.equal(result.metadata.fallback, 'router_error');
    assert.ok(result.metadata.latencyMs > 0, `expected non-zero latency proving the classifier ran, got ${result.metadata.latencyMs}`);
});

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

test('user web-search opt-out vetoes routing but never enables it', () => {
    const wants = { webSearch: true, artifacts: false, codeInterpreter: false };
    const avail = { webSearch: true, artifacts: true, codeInterpreter: false };
    assert.equal(resolveRoutingOptions(wants, avail, {}).enableWebSearch, true);
    assert.equal(resolveRoutingOptions(wants, avail, { userDisabledWebSearch: true }).enableWebSearch, false);
    assert.equal(resolveRoutingOptions(wants, avail, { userDisabledWebSearch: false }).enableWebSearch, true);
    assert.equal(resolveRoutingOptions({ ...wants, webSearch: false }, avail, { userDisabledWebSearch: false }).enableWebSearch, false);
});

test('connector actions only suppress inferred Code Interpreter; other routing is unchanged', () => {
    const withTools = [{ role: 'user', content: 'Read my latest email and create a report', configuredTools: [{ id: 'read_email' }] }];
    assert.equal(hasConfiguredToolsOnMessages(withTools), true);
    assert.equal(hasConfiguredToolsOnMessages([{ role: 'user', content: 'hi', configuredTools: [] }]), false);
    assert.equal(hasConfiguredToolsOnMessages([{ role: 'user', content: 'hi' }]), false);
    // still ordinary chat (base prompt + web search / artifacts classification)
    assert.equal(isEligibleOrdinaryChat({}, { messages: withTools, options: {} }), true);
    const all = { webSearch: true, artifacts: true, codeInterpreter: true };
    const avail = { webSearch: true, artifacts: true, codeInterpreter: true };
    const r = resolveRoutingOptions(all, avail, { connectorActions: true });
    assert.deepEqual([r.codeInterpreterOnly, r.enableWebSearch, r.artifacts], [false, true, true]);
    // explicit CI toggle still honored when artifacts isn't also requested
    assert.equal(resolveRoutingOptions({ webSearch: false, artifacts: false, codeInterpreter: false }, avail, { connectorActions: true, explicitModes: { codeInterpreterOnly: true } }).codeInterpreterOnly, true);
});

test('explicit enableWebSearch is honored but still bound by availability and user opt-out', () => {
    const off = { webSearch: false, artifacts: false, codeInterpreter: false };
    assert.equal(resolveRoutingOptions(off, { webSearch: true }, { explicitWebSearch: true }).enableWebSearch, true);
    assert.equal(resolveRoutingOptions(off, { webSearch: true }, {}).enableWebSearch, false);
    assert.equal(resolveRoutingOptions(off, { webSearch: false }, { explicitWebSearch: true }).enableWebSearch, false);
    assert.equal(resolveRoutingOptions(off, { webSearch: true }, { explicitWebSearch: true, userDisabledWebSearch: true }).enableWebSearch, false);
});
