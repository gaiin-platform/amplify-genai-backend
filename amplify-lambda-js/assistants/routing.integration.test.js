import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveRoutingOptions, resolveSpecializedAssistantMode, hasDownloadableFileIntent } from '../common/ordinaryChatRouter.js';

process.env.LOCAL_DEVELOPMENT = 'true';
process.env.LOCAL_SECRET_ = '{"models":[]}';
process.env.API_BASE_URL = 'https://test.invalid';
const { chooseAssistantForRequest } = await import('./assistants.js');
const defaultAssistantForRouting = { name: 'Selected Assistant', id: 'astp/selected' };
const availability = { webSearch: true, artifacts: true, codeInterpreter: true };

async function routeToAssistant({ clientOptions = {}, decision = {}, policy = availability, interpreterAvailable = true }) {
    const requestText = clientOptions.requestText || (clientOptions.downloadableFileIntent ? 'Create a downloadable PDF file' : 'please help');
    const downloadableFileIntent = clientOptions.downloadableFileIntent === true ||
        /\b(download|export|save|deliver|attach|generate|create|make|produce)\b[\s\S]{0,80}\b(file|document|report|word|docx|doc|spreadsheet|xlsx|pdf)\b|\.(docx?|xlsx?|pdf)\b/i.test(requestText);
    const routing = resolveRoutingOptions(decision, policy, {
        downloadableFileIntent,
        explicitModes: {
            notEligible: Object.keys(clientOptions).some(key => !['downloadableFileIntent', 'assistantId'].includes(key)),
            artifactsMode: clientOptions.artifactsMode === true,
            codeInterpreterOnly: clientOptions.codeInterpreterOnly === true,
            apiAccessed: clientOptions.api_accessed === true
        },
        interpreterAvailable
    });
    const body = {
        messages: [{ role: 'user', content: clientOptions.requestText ||
            (clientOptions.downloadableFileIntent === true ? 'Create a downloadable PDF file' : 'please help') }],
        options: {
            ...clientOptions,
            ...routing,
            codeInterpreterAvailable: interpreterAvailable,
            isOrdinaryChatRequest: Object.keys(clientOptions).every(key => ['downloadableFileIntent', 'assistantId'].includes(key)) ||
                (clientOptions.artifactsMode !== true && clientOptions.codeInterpreterOnly !== true),
            deploymentFeatures: policy,
            routingDecision: decision
        }
    };
    return chooseAssistantForRequest({ user: 'test-user', accessToken: 'test-token' }, {}, body, [], { writableEnded: false, write() {} });
}

test('router-applied explicit specialized modes reach their assistants even with selected assistants', async () => {
    assert.equal((await routeToAssistant({ clientOptions: { codeInterpreterOnly: true, assistantId: 'astp/selected' } })).name, 'Code Interpreter Assistant');
    assert.equal((await routeToAssistant({ clientOptions: { artifactsMode: true, assistantId: 'astp/selected' } })).name, 'Artifacts Assistant');
});

test('assistant selection precedence preserves ordinary assistant but resolves explicit modes first', () => {
    const selected = { ...defaultAssistantForRouting, name: 'Selected Assistant', id: 'astp/selected' };
    const ordinary = {
        ...selected,
        options: { assistantId: 'astp/selected', deploymentFeatures: availability }
    };
    assert.equal(resolveSpecializedAssistantMode(ordinary), null);
    assert.equal(ordinary.options.assistantId, 'astp/selected');
    assert.equal(resolveSpecializedAssistantMode({ assistantId: 'astp/selected', artifactsMode: true, deploymentFeatures: availability }), 'artifacts');
    assert.equal(resolveSpecializedAssistantMode({ assistantId: 'astp/selected', downloadableFileIntent: true, deploymentFeatures: availability, codeInterpreterAvailable: true }), 'codeInterpreter');
});

test('a selected assistant is never treated as ordinary chat, and never gets an inferred Code Interpreter', async () => {
    const body = { messages: [{ role: 'user', content: 'ordinary question' }], options: { assistantId: 'astp/selected' } };
    let classified = 0;
    const routing = await import('../common/ordinaryChatRouter.js').then(({ routeOrdinaryChat }) =>
        routeOrdinaryChat({ options: { cheapestModel: { id: 'cheap' } } }, body,
            { routingEnabled: true, availability },
            { classify: async () => { classified += 1; return { schemaVersion: 1, webSearch: true, artifacts: false, codeInterpreter: true }; } }));
    assert.equal(classified, 1); // web search / artifacts may be layered onto the assistant
    assert.equal(routing.metadata.eligible, false);
    assert.equal(routing.metadata.notEligible, true);
    assert.equal(routing.decision.webSearch, true);
    assert.equal(routing.decision.codeInterpreter, false);
    assert.equal(body.options.assistantId, 'astp/selected');
});

test('explicit modes remain disabled by deployment policy and unavailable interpreter configuration', async () => {
    assert.equal((await routeToAssistant({ clientOptions: { codeInterpreterOnly: true }, policy: { ...availability, codeInterpreter: false } })).name, 'default');
    assert.equal((await routeToAssistant({ clientOptions: { artifactsMode: true }, policy: { ...availability, artifacts: false } })).name, 'default');
    assert.equal((await routeToAssistant({ clientOptions: { codeInterpreterOnly: true }, interpreterAvailable: false })).name, 'default');
});

test('automatic artifact decisions select the artifact-mode assistant', async () => {
    assert.equal((await routeToAssistant({ decision: { artifacts: true, codeInterpreter: false } })).name, 'default');
    assert.equal((await routeToAssistant({ decision: { artifacts: false, codeInterpreter: true }, policy: { ...availability, codeInterpreter: false } })).name, 'default');
    assert.equal((await routeToAssistant({ decision: { artifacts: true, codeInterpreter: false }, policy: { ...availability, artifacts: false } })).name, 'default');
});

test('downloadable DOCX and PDF requests with selected assistants reach the interpreter', async () => {
    for (const file of ['DOCX', 'PDF']) {
        const body = {
            messages: [{ role: 'user', content: `Create a downloadable ${file} file` }],
            options: {
                assistantId: 'astp/selected',
                requestText: `Create a downloadable ${file} file`,
                ...resolveRoutingOptions({ artifacts: true, codeInterpreter: false }, availability,
                    { downloadableFileIntent: true, explicitModes: { notEligible: true } }),
                deploymentFeatures: availability,
                codeInterpreterAvailable: true
            }
        };
        assert.equal((await chooseAssistantForRequest({ user: 'test-user', accessToken: 'test-token' }, {}, body, [], { writableEnded: false, write() {} })).name, 'Code Interpreter Assistant');
    }
});

test('selected assistant with attachment file names in its input is not replaced by the interpreter', async () => {
    // Regression: pasted data such as "Attachment: Approval.pdf" used to be read as a file request,
    // which swapped the assistant (and its strict JSON-only instructions) for Code Interpreter.
    const messages = [{ role: 'user', content: 'Requisition: R27017824\nAttachment: Colloquium F26 Chair COA Approval.pdf' }];
    const intent = hasDownloadableFileIntent(messages);
    assert.equal(intent, false);
    const body = {
        messages,
        options: {
            assistantId: 'astp/selected',
            api_accessed: true,
            ...resolveRoutingOptions({ artifacts: false, codeInterpreter: false }, availability,
                { downloadableFileIntent: intent, explicitModes: { notEligible: true } }),
            deploymentFeatures: availability,
            codeInterpreterAvailable: true
        }
    };
    assert.equal(body.options.codeInterpreterOnly, false);
    assert.equal(resolveSpecializedAssistantMode(body.options), null);
});
