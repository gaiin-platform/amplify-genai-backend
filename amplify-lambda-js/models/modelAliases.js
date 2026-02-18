/**
 * Model Alias Resolution Module
 *
 * Provides functionality to resolve user-friendly model aliases (e.g., "opus-latest")
 * to actual AWS Bedrock model IDs (e.g., "us.anthropic.claude-opus-4-6-v1:0").
 *
 * Benefits:
 * - User-friendly model names
 * - Automatic updates via deployment (controlled, testable)
 * - Backward compatible (exact IDs still work)
 * - Prevents silent model degradation
 */

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { getLogger } from '../common/logging.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const logger = getLogger('modelAliases');

// Alias mapping cache (loaded once at module initialization)
let aliasMapping = null;
let loadError = null;

/**
 * Load alias mapping from JSON file
 * Called once at module initialization
 */
function loadAliasMapping() {
    try {
        // Path to model_aliases.json in chat-billing/model_rates/
        const aliasFilePath = join(__dirname, '../../chat-billing/model_rates/model_aliases.json');

        logger.info(`Loading model aliases from: ${aliasFilePath}`);
        const fileContent = readFileSync(aliasFilePath, 'utf8');
        const data = JSON.parse(fileContent);

        if (!data.aliases || typeof data.aliases !== 'object') {
            throw new Error('Invalid model_aliases.json format: missing or invalid "aliases" object');
        }

        logger.info(`Model aliases loaded successfully (version: ${data.version}, ${Object.keys(data.aliases).length} aliases)`);
        return data.aliases;

    } catch (error) {
        logger.warn(`Failed to load model aliases: ${error.message}`);
        logger.warn('Model alias resolution will be disabled. All model IDs will pass through unchanged.');
        loadError = error;
        return {};
    }
}

// Initialize alias mapping at module load
aliasMapping = loadAliasMapping();

/**
 * Check if a string is a known alias
 * @param {string} modelId - The model ID or alias to check
 * @returns {boolean} True if this is a known alias
 */
export function isAlias(modelId) {
    if (!modelId || typeof modelId !== 'string') {
        return false;
    }
    return aliasMapping && aliasMapping.hasOwnProperty(modelId);
}

/**
 * Resolve a model ID or alias to the actual model ID
 * @param {string} modelIdOrAlias - The model ID or alias
 * @returns {Object} Resolution result with properties:
 *   - resolvedId: The actual model ID to use
 *   - wasAlias: Boolean indicating if resolution occurred
 *   - aliasInfo: Metadata about the alias (if wasAlias is true)
 */
export function resolveModelAlias(modelIdOrAlias) {
    // Handle null/undefined/empty
    if (!modelIdOrAlias) {
        return {
            resolvedId: modelIdOrAlias,
            wasAlias: false,
            aliasInfo: null
        };
    }

    // Check if this is a known alias
    if (isAlias(modelIdOrAlias)) {
        const aliasConfig = aliasMapping[modelIdOrAlias];
        const resolvedId = aliasConfig.resolves_to;

        logger.info(`Model alias resolved: '${modelIdOrAlias}' -> '${resolvedId}'`, {
            alias: modelIdOrAlias,
            resolvedTo: resolvedId,
            category: aliasConfig.category,
            tier: aliasConfig.tier,
            description: aliasConfig.description
        });

        return {
            resolvedId: resolvedId,
            wasAlias: true,
            aliasInfo: {
                alias: modelIdOrAlias,
                category: aliasConfig.category,
                tier: aliasConfig.tier,
                description: aliasConfig.description
            }
        };
    }

    // Not an alias - pass through unchanged
    return {
        resolvedId: modelIdOrAlias,
        wasAlias: false,
        aliasInfo: null
    };
}

/**
 * Get all available aliases
 * @returns {Object} Map of alias -> config
 */
export function getAllAliases() {
    if (loadError) {
        return {
            error: true,
            message: loadError.message,
            aliases: {}
        };
    }

    return {
        error: false,
        aliases: aliasMapping || {}
    };
}

/**
 * Get reverse mapping: model ID -> aliases that point to it
 * @returns {Object} Map of model ID -> array of aliases
 */
export function getReverseMapping() {
    const reverseMap = {};

    if (!aliasMapping) {
        return reverseMap;
    }

    for (const [alias, config] of Object.entries(aliasMapping)) {
        const modelId = config.resolves_to;
        if (!reverseMap[modelId]) {
            reverseMap[modelId] = [];
        }
        reverseMap[modelId].push({
            alias: alias,
            description: config.description,
            category: config.category,
            tier: config.tier
        });
    }

    return reverseMap;
}

export default {
    resolveModelAlias,
    isAlias,
    getAllAliases,
    getReverseMapping
};
