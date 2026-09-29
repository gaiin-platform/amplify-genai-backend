import test from 'node:test';
import assert from 'node:assert/strict';
import { routeOrdinaryChat, resolveRoutingOptions } from './ordinaryChatRouter.js';

const availability = { webSearch: true, artifacts: true, codeInterpreter: true };
const apiForwardedRequest = () => ({
    messages: [
        { role: 'system', content: 'API deployment base prompt' },
        { role: 'user', content: 'Please search current information and make a reusable summary.' }
    ],
    options: {
        api_accessed: true,
        model: { id: 'model-allowed-by-facade', provider: 'OpenAI' },
        assistantId: undefined
    }
});

async function classifyApiRequest(body, candidate, policy = availability) {
    let calls = 0;
    const result = await routeOrdinaryChat(
        { options: { cheapestModel: { id: 'cheapest-allowed-model' } } },
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
