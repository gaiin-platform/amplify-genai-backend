/**
 * Native structured-output (`outputConfig`) support on Bedrock Converse, by model family.
 *
 * Verified against live Bedrock (us.openai.gpt-5.6-luna / -sol, SDK 3.1050):
 *  - OpenAI GPT-5.6 REQUIRES `jsonSchema.name` (HTTP 400 "Missing required parameter:
 *    'text.format.name'" without it). Optional schema properties and min/max are accepted.
 *  - Claude works without a name, so its request shape is left exactly as it was.
 *  - Other families (Nova, Titan, Llama, Mistral, ...) reject outputConfig, so they get none.
 */
const OPENAI_GPT_5_6 = /^((us|global)\.)?openai\.gpt-5\.6-/i;

/** @returns {'claude'|'openai-gpt-5.6'|null} */
export function getBedrockStructuredOutputFamily(modelId) {
    const id = String(modelId || '');
    if (id.toLowerCase().includes('claude')) return 'claude';
    if (OPENAI_GPT_5_6.test(id)) return 'openai-gpt-5.6';
    return null;
}

/**
 * @param {string} modelId
 * @param {object} schema  JSON schema, already passed through addAdditionalPropertiesFalse
 * @returns {object|null}  value for chatBody.outputConfig, or null when unsupported
 */
export function buildBedrockOutputConfig(modelId, schema) {
    const family = getBedrockStructuredOutputFamily(modelId);
    if (!family) return null;
    const jsonSchema = { schema: JSON.stringify(schema) };
    if (family === 'openai-gpt-5.6') jsonSchema.name = 'data_extraction';
    return { textFormat: { type: 'json_schema', structure: { jsonSchema } } };
}
