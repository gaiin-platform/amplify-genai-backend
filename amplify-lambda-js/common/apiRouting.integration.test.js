import test from 'node:test';
import assert from 'node:assert/strict';
import { routeOrdinaryChat, resolveRoutingOptions, resolveRequestRoutingPolicy, isEligibleForAssistantFeatures, isEligibleOrdinaryChat } from './ordinaryChatRouter.js';

const availability = { webSearch: true, artifacts: true, codeInterpreter: true };
const apiForwardedRequest = (extraOptions = { enableRouting: true }) => ({
    messages: [
        { role: 'system', content: 'API deployment base prompt' },
        { role: 'user', content: 'Please search current information and make a reusable summary.' }
    ],
    options: {
        api_accessed: true,
        ...extraOptions,
        model: { id: 'model-allowed-by-facade', provider: 'OpenAI' },
        assistantId: undefined
    }
});

async function classifyApiRequest(body, candidate, policy = availability) {
    let calls = 0;
    const result = await routeOrdinaryChat(
        { options: { cheapestModel: { id: 'cheapest-allowed-model' }, ...body.options } },
        body,
        { routingEnabled: true, availability: policy },
        { classify: async () => { calls += 1; return candidate; } }
    );
    return { result, calls };
}

test('API-forwarded ordinary chat routes web search, artifacts, and Code Interpreter once', async () => {
    const featureCases = [
        [{ webSearch: true, artifacts: false, codeInterpreter: false }, { enableWebSearch: true, artifacts: false, codeInterpreterOnly: false }],
        [{ webSearch: false, artifacts: true, codeInterpreter: false }, { enableWebSearch: false, artifacts: true, codeInterpreterOnly: false }],
        [{ webSearch: false, artifacts: false, codeInterpreter: true }, { enableWebSearch: false, artifacts: false, codeInterpreterOnly: true }]
    ];
    for (const [decisionBits, expected] of featureCases) {
        const body = apiForwardedRequest();
        const { result, calls } = await classifyApiRequest(body, { schemaVersion: 1, ...decisionBits });
        assert.equal(calls, 1);
        assert.equal(result.metadata.eligible, true);
        assert.equal(body.options.api_accessed, true);
        const resolved = resolveRoutingOptions(result.decision, availability, { explicitModes: { apiAccessed: true } });
        assert.equal(resolved.enableWebSearch, expected.enableWebSearch);
        assert.equal(resolved.artifacts, expected.artifacts);
        assert.equal(resolved.codeInterpreterOnly, expected.codeInterpreterOnly);
    }
});

test('API-forwarded disabled features remain off regardless of classifier output', async () => {
    const policy = { webSearch: false, artifacts: false, codeInterpreter: false };
    const { result, calls } = await classifyApiRequest(apiForwardedRequest(), {
        schemaVersion: 1, webSearch: true, artifacts: true, codeInterpreter: true
    }, policy);
    assert.equal(calls, 1);
    assert.deepEqual(resolveRoutingOptions(result.decision, policy), {
        enableWebSearch: false, artifacts: false, artifactsMode: false, codeInterpreterOnly: false,
        downloadableFileIntent: false, fileGenerationUnavailable: false, notEligible: false
    });
});

test('API classifier failure safely disables optional modes without blocking facade forwarding', async () => {
    let forwarded = 0;
    const body = apiForwardedRequest();
    const { result, calls } = await classifyApiRequest(body, null);
    forwarded += 1;
    assert.equal(forwarded, 1);
    assert.equal(calls, 1);
    assert.equal(result.decision.webSearch, false);
    assert.equal(result.decision.artifacts, false);
    assert.equal(result.decision.codeInterpreter, false);
    assert.equal(body.messages[0].content, 'API deployment base prompt');
});

const deployment = { routingEnabled: true, availability };

test('API requests are routing-off by default: classifier never runs and nothing is inferred', async () => {
    const body = apiForwardedRequest({});
    const { result, calls } = await classifyApiRequest(body, { schemaVersion: 1, webSearch: true, artifacts: true, codeInterpreter: true });
    assert.equal(calls, 0);
    assert.equal(result.metadata.eligible, true); // still ordinary chat for prompt composition
    assert.deepEqual([result.decision.webSearch, result.decision.artifacts, result.decision.codeInterpreter], [false, false, false]);
    const policy = resolveRequestRoutingPolicy(body.options, body, deployment);
    assert.equal(policy.automaticRouting, false);
    assert.equal(policy.inferredRoutingAllowed, false);
});

test('API enableRouting opts in, but only when the deployment has routing enabled', async () => {
    const on = await classifyApiRequest(apiForwardedRequest({ enableRouting: true }), { schemaVersion: 1, webSearch: true, artifacts: false, codeInterpreter: false });
    assert.equal(on.calls, 1);
    assert.equal(on.result.decision.webSearch, true);
    const body = apiForwardedRequest({ enableRouting: true });
    const policyOff = resolveRequestRoutingPolicy(body.options, body, { routingEnabled: false, availability });
    assert.equal(policyOff.automaticRouting, false);
    assert.equal(policyOff.inferredRoutingAllowed, false);
    assert.equal(resolveRequestRoutingPolicy(body.options, body, deployment).inferredRoutingAllowed, true);
});

test('non-API requests keep deployment-governed routing (enableRouting not required)', () => {
    const body = { messages: [{ role: 'user', content: 'hi' }], options: {} };
    const policy = resolveRequestRoutingPolicy(body.options, body, deployment);
    assert.equal(policy.automaticRouting, true);
    assert.equal(policy.inferredRoutingAllowed, true);
});

test('a selected assistant is always respected: no inference, and API explicit modes cannot replace it', () => {
    for (const enableRouting of [false, true]) {
        const api = { messages: [{ role: 'user', content: 'x' }], options: { api_accessed: true, assistantId: 'astp/a', enableRouting, codeInterpreterOnly: true, artifactsMode: true } };
        const p = resolveRequestRoutingPolicy(api.options, api, deployment);
        assert.equal(p.inferredRoutingAllowed, false);
        assert.equal(p.specializedModeEligible, false);
    }
    // browser behavior with an explicit mode is unchanged
    const ui = { messages: [{ role: 'user', content: 'x' }], options: { assistantId: 'astp/a', codeInterpreterOnly: true } };
    assert.equal(resolveRequestRoutingPolicy(ui.options, ui, deployment).specializedModeEligible, true);
    assert.equal(resolveRequestRoutingPolicy(ui.options, ui, deployment).inferredRoutingAllowed, false);
});

test('explicit enables work individually with routing off', () => {
    const off = { webSearch: false, artifacts: false, codeInterpreter: false };
    const only = (explicitModes, extra = {}) => resolveRoutingOptions(off, availability, { explicitModes, ...extra });
    const ws = only({}, { explicitWebSearch: true });
    assert.deepEqual([ws.enableWebSearch, ws.artifacts, ws.codeInterpreterOnly], [true, false, false]);
    const art = only({ artifactsMode: true });
    assert.deepEqual([art.enableWebSearch, art.artifacts, art.codeInterpreterOnly], [false, true, false]);
    const ci = only({ codeInterpreterOnly: true });
    assert.deepEqual([ci.enableWebSearch, ci.artifacts, ci.codeInterpreterOnly], [false, false, true]);
    const none = only({});
    assert.deepEqual([none.enableWebSearch, none.artifacts, none.codeInterpreterOnly], [false, false, false]);
});

test('combination rules: disables beat enables and routing; availability beats everything; artifacts wins over CI, never both on', () => {
    const all = { webSearch: true, artifacts: true, codeInterpreter: true };
    const r = (decision, policy, opts) => resolveRoutingOptions(decision, policy, opts);
    // explicit disable beats explicit enable and classifier
    assert.equal(r(all, availability, { explicitWebSearch: true, userDisabledWebSearch: true }).enableWebSearch, false);
    assert.equal(r(all, availability, { explicitModes: { artifactsMode: true }, userDisabledArtifacts: true }).artifacts, false);
    assert.equal(r(all, availability, { explicitModes: { codeInterpreterOnly: true }, userDisabledCodeInterpreter: true }).codeInterpreterOnly, false);
    // routing on with one feature vetoed leaves the others routable
    const partial = r({ webSearch: true, artifacts: false, codeInterpreter: true }, availability, { userDisabledCodeInterpreter: true });
    assert.deepEqual([partial.enableWebSearch, partial.codeInterpreterOnly], [true, false]);
    // deployment availability is authoritative over explicit enables
    const locked = { webSearch: false, artifacts: false, codeInterpreter: false };
    const denied = r(all, locked, { explicitModes: { artifactsMode: true, codeInterpreterOnly: true }, explicitWebSearch: true });
    assert.deepEqual([denied.enableWebSearch, denied.artifacts, denied.codeInterpreterOnly], [false, false, false]);
    // artifacts + CI together resolve to CI only
    const both = r(all, availability, { explicitModes: { artifactsMode: true, codeInterpreterOnly: true } });
    assert.deepEqual([both.codeInterpreterOnly, both.artifacts, both.artifactsMode], [false, true, true]);
    const routedBoth = r({ webSearch: false, artifacts: true, codeInterpreter: true }, availability);
    assert.deepEqual([routedBoth.codeInterpreterOnly, routedBoth.artifacts], [false, true]);
    // explicit artifacts also beats an inferred file request; classifier artifacts does not
    const explicitVsFile = r(all, availability, { explicitModes: { artifactsMode: true }, downloadableFileIntent: true });
    assert.deepEqual([explicitVsFile.codeInterpreterOnly, explicitVsFile.artifacts], [false, true]);
    assert.equal(r(all, availability, { downloadableFileIntent: true }).codeInterpreterOnly, true);
    // artifacts vetoed -> code interpreter still available
    assert.equal(r(all, availability, { explicitModes: { artifactsMode: true, codeInterpreterOnly: true }, userDisabledArtifacts: true }).codeInterpreterOnly, true);
});

test('connector actions block inferred file-intent routing; workflows block explicit modes', () => {
    const body = { messages: [{ role: 'user', content: 'x', configuredTools: [{ id: 'read_email' }] }], options: { api_accessed: true, enableRouting: true } };
    const p = resolveRequestRoutingPolicy(body.options, body, deployment);
    assert.equal(p.connectorActions, true);
    assert.equal(p.inferredRoutingAllowed, false);
    const wf = { messages: [{ role: 'user', content: 'x' }], options: { api_accessed: true, workflowId: 'w1', codeInterpreterOnly: true } };
    assert.equal(resolveRequestRoutingPolicy(wf.options, wf, deployment).specializedModeEligible, false);
});

const assistantBody = (options = {}) => ({
    messages: [{ role: 'user', content: 'Search the web and build me a reusable dashboard' }],
    options: { assistantId: 'astp/strict', ...options }
});

test('selected assistants can be augmented with web search and artifacts, never Code Interpreter', async () => {
    const body = assistantBody();
    assert.equal(isEligibleOrdinaryChat({}, body), false); // still not ordinary chat: assistant prompt flow unchanged
    assert.equal(isEligibleForAssistantFeatures({}, body), true);
    let calls = 0;
    const result = await routeOrdinaryChat({ options: body.options }, body, { routingEnabled: true, availability },
        { classify: async () => { calls += 1; return { schemaVersion: 1, webSearch: true, artifacts: true, codeInterpreter: true }; } });
    assert.equal(calls, 1);
    assert.deepEqual([result.decision.webSearch, result.decision.artifacts, result.decision.codeInterpreter], [true, true, false]);
    assert.equal(result.metadata.eligible, false);
    assert.equal(result.metadata.assistantFeatures, true);
    assert.equal(result.metadata.notEligible, true);
    // resolution: classifier artifacts honored for the assistant, Code Interpreter still off
    const r = resolveRoutingOptions(result.decision, availability, { explicitModes: { notEligible: true }, assistantFeatures: true });
    assert.deepEqual([r.enableWebSearch, r.artifacts, r.artifactsMode, r.codeInterpreterOnly], [true, true, false, false]);
    // without the assistant flag the old behavior (classifier ignored for artifacts) is kept
    assert.equal(resolveRoutingOptions(result.decision, availability, { explicitModes: { notEligible: true } }).artifacts, false);
});

test('assistant augmentation follows routing switches and excludes connector/workflow requests', async () => {
    const classify = async () => assert.fail('classifier must not run');
    // API + assistant: routing off by default
    const api = assistantBody({ api_accessed: true });
    const off = await routeOrdinaryChat({ options: api.options }, api, { routingEnabled: true, availability }, { classify });
    assert.deepEqual([off.decision.webSearch, off.decision.artifacts], [false, false]);
    // deployment routing off
    const dep = assistantBody();
    await routeOrdinaryChat({ options: dep.options }, dep, { routingEnabled: false, availability }, { classify });
    // connector actions / workflows / explicit specialized modes are not augmented
    const withTools = { messages: [{ role: 'user', content: 'x', configuredTools: [{ id: 'read_email' }] }], options: { assistantId: 'astp/a' } };
    assert.equal(isEligibleForAssistantFeatures({}, withTools), false);
    assert.equal(isEligibleForAssistantFeatures({}, assistantBody({ workflowId: 'w' })), false);
    assert.equal(isEligibleForAssistantFeatures({}, assistantBody({ codeInterpreterOnly: true })), false);
    assert.equal(isEligibleForAssistantFeatures({}, { messages: [{ role: 'user', content: 'x' }], options: {} }), false);
    // API + assistant + enableRouting does classify
    const optIn = assistantBody({ api_accessed: true, enableRouting: true });
    let calls = 0;
    await routeOrdinaryChat({ options: optIn.options }, optIn, { routingEnabled: true, availability },
        { classify: async () => { calls += 1; return { schemaVersion: 1, webSearch: false, artifacts: false, codeInterpreter: false }; } });
    assert.equal(calls, 1);
    assert.equal(resolveRequestRoutingPolicy(optIn.options, optIn, { routingEnabled: true }).assistantFeatures, true);
});

test('admin availability and user vetoes still bound assistant features', () => {
    const decision = { webSearch: true, artifacts: true, codeInterpreter: false };
    const locked = resolveRoutingOptions(decision, { webSearch: false, artifacts: false, codeInterpreter: false }, { explicitModes: { notEligible: true }, assistantFeatures: true });
    assert.deepEqual([locked.enableWebSearch, locked.artifacts], [false, false]);
    const vetoed = resolveRoutingOptions(decision, availability, { explicitModes: { notEligible: true }, assistantFeatures: true, userDisabledWebSearch: true, userDisabledArtifacts: true });
    assert.deepEqual([vetoed.enableWebSearch, vetoed.artifacts], [false, false]);
});
