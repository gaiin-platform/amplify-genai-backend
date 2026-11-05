#!/usr/bin/env node
/**
 * Dev-only smoke test for Bedrock Converse prompt caching. NOT part of CI and NOT run automatically:
 * it makes live Bedrock calls with your AWS credentials and costs a few cents.
 *
 *   cd amplify-lambda-js
 *   AWS_PROFILE=<dev-profile> DEP_REGION=us-east-1 \
 *     node local/bedrockCacheSmoke.mjs <modelId> [mode] [--nonce=<string>]
 *
 *   modelId  e.g. us.anthropic.claude-sonnet-4-6 | us.anthropic.claude-haiku-4-5-20251001-v1:0
 *   mode     basic       (default) same >=5,000-token prompt twice, cachePoint on the system block
 *            below-min   tiny prompt + cachePoint: does Bedrock ignore it or reject it?
 *            toolresult  cachePoint AFTER a toolResult block in a user message (the "tail" checkpoint)
 *            all         run the three modes in order
 *
 * Expected results (what we want to confirm):
 *   basic       call 1: cacheWriteInputTokens > 0, cacheReadInputTokens = 0
 *               call 2: cacheReadInputTokens  > 0, cacheWriteInputTokens = 0 (or small)
 *   below-min   per the AWS doc the request SUCCEEDS with cacheRead = cacheWrite = 0 (silently not cached).
 *               If you see a ValidationException instead, the planner's minimum-token gating is essential.
 *   toolresult  call 1: write > 0 and call 2: read > 0  => a cachePoint after toolResult is accepted AND works.
 *               A ValidationException => do not use the `tail` point (set BEDROCK_PROMPT_CACHING_POINTS=tools,system,history).
 *
 * Each run prefixes the prompt with a random nonce so it never reads a cache entry left by a previous
 * run (caches live ~5 minutes). Pass --nonce=<same> across runs only if you WANT to hit an old entry.
 * Never sends a `ttl` field.
 */
import { BedrockRuntimeClient, ConverseStreamCommand } from '@aws-sdk/client-bedrock-runtime';

const args = process.argv.slice(2);
const flags = Object.fromEntries(args.filter(a => a.startsWith('--')).map(a => a.replace(/^--/, '').split('=')));
const [modelId, mode = 'basic'] = args.filter(a => !a.startsWith('--'));

if (!modelId) {
    console.error('usage: node local/bedrockCacheSmoke.mjs <modelId> [basic|below-min|toolresult|all] [--nonce=x]');
    process.exit(2);
}

const region = process.env.DEP_REGION ?? process.env.AWS_REGION ?? 'us-east-1';
const client = new BedrockRuntimeClient({ region });
const nonce = flags.nonce ?? Math.random().toString(36).slice(2, 10);
const CACHE_POINT = { cachePoint: { type: 'default' } };

// Deterministic filler with enough variety that it is ~5-6k tokens regardless of tokenizer.
const filler = (label, sentences) =>
    Array.from({ length: sentences }, (_, i) => `${label} fact ${i + 1}: the quick brown fox number ${i * 7 + 3} jumps over lazy dog ${i * 13 + 5}.`).join('\n');

async function converse(label, input) {
    process.stdout.write(`  ${label}: `);
    try {
        const res = await client.send(new ConverseStreamCommand(input));
        let usage = null;
        let text = '';
        for await (const ev of res.stream) {
            if (ev.contentBlockDelta?.delta?.text) text += ev.contentBlockDelta.delta.text;
            if (ev.metadata?.usage) usage = ev.metadata.usage;
        }
        const u = usage ?? {};
        console.log(
            `inputTokens=${u.inputTokens} outputTokens=${u.outputTokens} ` +
            `cacheReadInputTokens=${u.cacheReadInputTokens ?? 0} cacheWriteInputTokens=${u.cacheWriteInputTokens ?? 0}` +
            (u.cacheDetails ? ` cacheDetails=${JSON.stringify(u.cacheDetails)}` : '') +
            `  reply=${JSON.stringify(text.slice(0, 40))}`
        );
        return u;
    } catch (e) {
        console.log(`REJECTED -> ${e.name}: ${e.message}`);
        return null;
    }
}

const base = { modelId, inferenceConfig: { maxTokens: 20, temperature: 0 } };

async function modeBasic() {
    console.log(`\n[basic] model=${modelId} nonce=${nonce}  (expect call1 write>0, call2 read>0)`);
    const input = {
        ...base,
        system: [{ text: `Run ${nonce}. You are a test assistant.\n${filler('system', 450)}` }, CACHE_POINT],
        messages: [{ role: 'user', content: [{ text: 'Reply with the single word: ok' }] }],
    };
    await converse('call 1', input);
    await new Promise(r => setTimeout(r, 2000)); // the cache entry must exist before the second request starts
    await converse('call 2', input);
}

async function modeBelowMin() {
    console.log(`\n[below-min] model=${modelId}  (expect success with read=write=0, i.e. silently not cached)`);
    await converse('tiny prompt + cachePoint', {
        ...base,
        system: [{ text: `Run ${nonce}. Be brief.` }, CACHE_POINT],
        messages: [{ role: 'user', content: [{ text: 'Reply with the single word: ok' }] }],
    });
}

async function modeToolResult() {
    console.log(`\n[toolresult] model=${modelId} nonce=${nonce}  (cachePoint after a toolResult block; expect call1 write>0, call2 read>0)`);
    const input = {
        ...base,
        // No cachePoint on system: the ONLY checkpoint is the one after the toolResult, so any write/read proves it works.
        system: [{ text: `Run ${nonce}. You are a test assistant. Use the lookup tool, then answer.` }],
        toolConfig: {
            tools: [{
                toolSpec: {
                    name: 'lookup',
                    description: 'Look up a document by id.',
                    inputSchema: { json: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] } },
                },
            }],
        },
        messages: [
            { role: 'user', content: [{ text: 'Look up document 1 and tell me one word about it.' }] },
            { role: 'assistant', content: [{ toolUse: { toolUseId: 'tooluse_smoke_1', name: 'lookup', input: { id: '1' } } }] },
            {
                role: 'user',
                content: [
                    { toolResult: { toolUseId: 'tooluse_smoke_1', content: [{ text: filler('document', 450) }] } },
                    CACHE_POINT,
                ],
            },
        ],
    };
    await converse('call 1', input);
    await new Promise(r => setTimeout(r, 2000));
    await converse('call 2', input);
}

const modes = { basic: modeBasic, 'below-min': modeBelowMin, toolresult: modeToolResult };
const toRun = mode === 'all' ? Object.values(modes) : [modes[mode]];
if (toRun.some(f => !f)) {
    console.error(`unknown mode "${mode}"`);
    process.exit(2);
}
for (const run of toRun) await run();
console.log('\nDone. Also check: does a guardrail (BEDROCK_GUARDRAIL_ID) change these results? Re-run with a guardrailConfig if you use one.');
