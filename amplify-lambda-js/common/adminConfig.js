import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { NodeHttpHandler } from '@smithy/node-http-handler';
import { DynamoDBDocumentClient, GetCommand } from '@aws-sdk/lib-dynamodb';
import { getLogger } from './logging.js';

const logger = getLogger('adminConfig');
const PROMPT_KEYS = [
    'ordinaryChat.base',
    'webSearch.use',
    'artifacts.generate',
    'codeInterpreter.use',
    'amplifyHelper.base'
];
const AVAILABILITY_KEYS = ['promptHighlighter', 'artifacts', 'webSearch', 'codeInterpreter', 'memory'];
const DEFAULT_AVAILABILITY = Object.freeze({
    promptHighlighter: false,
    artifacts: true,
    webSearch: true,
    codeInterpreter: true,
    memory: false
});
const MAX_PROMPT_BYTES = 16 * 1024;
const DEFAULT_CACHE_TTL_MS = 30_000;
const DEFAULT_READ_TIMEOUT_MS = 2_500;
const MAX_MODEL_ID_LENGTH = 200;
const ROUTING_MODEL_CACHE_TTL_MS = 30_000;
const ROUTING_MODEL_MISS_TTL_MS = 10_000;

const client = DynamoDBDocumentClient.from(new DynamoDBClient({
    maxAttempts: 1,
    requestHandler: new NodeHttpHandler({ connectionTimeout: 1_000, socketTimeout: 2_000 })
}), {
    marshallOptions: { removeUndefinedValues: true }
});

export function normalizeDeploymentConfig(promptRecord, featureRecord, { featureFlagsRecord = null, featureReadFailed = false } = {}) {
    const prompts = {};
    const inputPrompts = promptRecord?.schemaVersion === 1 && promptRecord.prompts && typeof promptRecord.prompts === 'object'
        ? promptRecord.prompts
        : {};
    for (const key of PROMPT_KEYS) {
        const candidate = inputPrompts[key];
        const text = candidate && Number.isInteger(candidate.version) && typeof candidate.text === 'string'
            ? candidate.text
            : '';
        prompts[key] = Buffer.byteLength(text, 'utf8') <= MAX_PROMPT_BYTES ? text : '';
    }

    const sourceAvailability = featureRecord?.schemaVersion === 1 &&
        featureRecord.availability && typeof featureRecord.availability === 'object'
        ? featureRecord.availability
        : {};
    const availability = Object.fromEntries(AVAILABILITY_KEYS.map(key => [
        key,
        typeof sourceAvailability[key] === 'boolean'
            ? sourceAvailability[key]
            : (featureReadFailed ? false : DEFAULT_AVAILABILITY[key])
    ]));

    const helperFlag = featureFlagsRecord?.amplifyHelper;
    return {
        prompts,
        availability,
        // Feature flag for the built-in Amplify Helper, evaluated per user by isAmplifyHelperAllowed.
        amplifyHelperFlag: {
            enabled: helperFlag?.enabled === true,
            userExceptions: Array.isArray(helperFlag?.userExceptions) ? helperFlag.userExceptions.filter(u => typeof u === 'string') : [],
            hasGroupExceptions: Array.isArray(helperFlag?.amplifyGroupExceptions) && helperFlag.amplifyGroupExceptions.length > 0
        },
        allowClassicUiSwitch: typeof featureRecord?.allowClassicUiSwitch === 'boolean'
            ? featureRecord.allowClassicUiSwitch
            : true,
        // Routing is opt-in so installations that predate this record retain
        // ordinary-chat behavior until an administrator enables it explicitly.
        routingEnabled: typeof featureRecord?.routingEnabled === 'boolean'
            ? featureRecord.routingEnabled
            : false,
        // Admin-chosen classifier model id from the model catalog. '' = use the built-in default.
        routingModelId: typeof featureRecord?.routingModelId === 'string' &&
            featureRecord.routingModelId.trim().length <= MAX_MODEL_ID_LENGTH
            ? featureRecord.routingModelId.trim()
            : '',
        status: featureReadFailed ? 'unavailable' : 'loaded'
    };
}

/**
 * Mirrors the admin feature-flag rule: enabled flips for users listed as exceptions.
 * Group exceptions cannot be resolved here, so a flag with group exceptions is treated
 * leniently when disabled; the helper only adds guidance text, never access.
 */
export function isAmplifyHelperAllowed(deploymentConfig, user) {
    const flag = deploymentConfig?.amplifyHelperFlag;
    if (!flag) return false;
    if (user && flag.userExceptions?.includes(user)) return !flag.enabled;
    return flag.enabled || flag.hasGroupExceptions === true;
}

/**
 * Shape a model-catalog row (MODEL_RATE_TABLE) like the model objects the chat path uses.
 * `systemPrompt` is deliberately blank: the catalog's additional prompt is chat guidance
 * and must not leak into an internal classification call.
 */
export function routingModelFromRecord(item) {
    if (!item || typeof item.ModelID !== 'string' || !item.ModelID) return null;
    const num = (v) => (v === undefined || v === null || Number.isNaN(Number(v)) ? undefined : Number(v));
    return {
        id: item.ModelID,
        name: typeof item.ModelName === 'string' && item.ModelName ? item.ModelName : item.ModelID,
        provider: typeof item.Provider === 'string' ? item.Provider : '',
        inputContextWindow: num(item.InputContextWindow),
        outputTokenLimit: num(item.OutputTokenLimit),
        inputTokenCost: num(item.InputCostPerThousandTokens),
        outputTokenCost: num(item.OutputCostPerThousandTokens),
        inputCachedTokenCost: num(item.InputCachedCostPerThousandTokens),
        inputWriteCachedTokenCost: num(item.InputWriteCachedCostPerThousandTokens),
        supportsImages: item.SupportsImages === true,
        supportsImageGeneration: item.SupportsImageGeneration === true,
        supportsReasoning: item.SupportsReasoning === true,
        supportsSystemPrompts: item.SupportsSystemPrompts === true,
        systemPrompt: ''
    };
}

/**
 * Cached lookup of one catalog model by id. Misses (unknown id, read failure) resolve to
 * `null` and are cached briefly so a bad setting cannot add a Dynamo read to every request.
 */
export function createRoutingModelLoader({
    getModelItem,
    ttlMs = ROUTING_MODEL_CACHE_TTL_MS,
    missTtlMs = ROUTING_MODEL_MISS_TTL_MS,
    now = () => Date.now()
}) {
    const cache = new Map();
    return async function loadRoutingModelById(modelId) {
        if (typeof modelId !== 'string' || !modelId) return null;
        const hit = cache.get(modelId);
        if (hit && now() < hit.expiresAt) return hit.model;
        let model = null;
        let ttl = missTtlMs;
        try {
            model = routingModelFromRecord(await getModelItem(modelId));
            if (model) ttl = ttlMs;
        } catch (error) {
            logger.warn('Routing model lookup failed; default routing model will be used', {
                errorName: error?.name || 'Error'
            });
        }
        cache.set(modelId, { model, expiresAt: now() + ttl });
        return model;
    };
}

export function createDeploymentConfigLoader({
    getConfigItem,
    ttlMs = DEFAULT_CACHE_TTL_MS,
    timeoutMs = DEFAULT_READ_TIMEOUT_MS,
    now = () => Date.now()
}) {
    let cached;
    let expiresAt = 0;
    let inFlight;
    let lastKnownGood;

    return async function loadDeploymentConfig() {
        if (cached && now() < expiresAt) return cached;
        if (inFlight) return inFlight;

        inFlight = (async () => {
            try {
                const [promptRecord, featureRecord, featureFlagsRecord] = await Promise.all([
                    getConfigItem('systemPrompts', timeoutMs),
                    getConfigItem('deploymentFeatures', timeoutMs),
                    // Optional: a failed read must only disable the helper, never other config.
                    Promise.resolve().then(() => getConfigItem('featureFlags', timeoutMs)).catch(() => null)
                ]);
                const normalized = normalizeDeploymentConfig(promptRecord, featureRecord, { featureFlagsRecord });
                lastKnownGood = normalized;
                cached = normalized;
                expiresAt = now() + ttlMs;
                return normalized;
            } catch (error) {
                logger.warn('Deployment prompt configuration unavailable; using safe fallback policy', {
                    errorName: error?.name || 'Error'
                });
                if (lastKnownGood) return lastKnownGood;
                const fallback = normalizeDeploymentConfig(null, null, { featureFlagsRecord: null, featureReadFailed: true });
                cached = fallback;
                expiresAt = now() + Math.min(ttlMs, 5_000);
                return fallback;
            } finally {
                inFlight = undefined;
            }
        })();
        return inFlight;
    };
}

const loadDeploymentConfig = createDeploymentConfigLoader({
    getConfigItem: async (configId, timeoutMs) => {
        const tableName = process.env.AMPLIFY_ADMIN_DYNAMODB_TABLE;
        if (!tableName) throw new Error('Admin config table is not configured');
        const result = await client.send(new GetCommand({
            TableName: tableName,
            Key: { config_id: configId },
            ProjectionExpression: '#data',
            ExpressionAttributeNames: { '#data': 'data' }
        }), { abortSignal: AbortSignal.timeout(timeoutMs) });
        return result.Item?.data ?? null;
    }
});

const loadRoutingModelById = createRoutingModelLoader({
    getModelItem: async (modelId) => {
        const tableName = process.env.MODEL_RATE_TABLE;
        if (!tableName) throw new Error('Model table is not configured');
        const result = await client.send(new GetCommand({
            TableName: tableName,
            Key: { ModelID: modelId }
        }), { abortSignal: AbortSignal.timeout(DEFAULT_READ_TIMEOUT_MS) });
        return result.Item ?? null;
    }
});

export { loadDeploymentConfig, loadRoutingModelById, PROMPT_KEYS, AVAILABILITY_KEYS, DEFAULT_AVAILABILITY, MAX_PROMPT_BYTES };
