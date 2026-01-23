/**
 * User Tool Keys Service
 *
 * Retrieves user's tool API keys from the user-data storage table.
 * Keys are stored by the frontend in obfuscated form.
 */

import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, QueryCommand } from '@aws-sdk/lib-dynamodb';
import { getLogger } from '../common/logging.js';

const logger = getLogger('userToolKeys');

const client = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(client);

const APP_ID = 'amplify-tools';
const ENTITY_TYPE = 'api_keys';

// IDP prefix for user identity (matches Python backend)
const IDP_PREFIX = (process.env.IDP_PREFIX || '').toLowerCase();

/**
 * De-obfuscate a key stored by the frontend
 * Frontend uses: base64 encode then reverse
 */
function deobfuscateKey(obfuscated) {
    try {
        const base64 = obfuscated.split('').reverse().join('');
        return Buffer.from(base64, 'base64').toString('utf8');
    } catch (e) {
        logger.error('Failed to deobfuscate key:', e.message);
        return null;
    }
}

/**
 * Create hash key matching frontend format
 */
function createHashKey(userId, appId) {
    // Sanitize user and app similar to frontend
    const sanitizedUser = userId.replace(/[^a-zA-Z0-9@._-]/g, '-');
    const sanitizedApp = appId.replace(/[^a-zA-Z0-9-]/g, '-');
    return `${sanitizedUser}#${sanitizedApp}`;
}

/**
 * Get all configured tool API keys for a user
 * @param {string} userId - The user ID (email)
 * @returns {Object} Object with provider names as keys and API keys as values
 */
export async function getUserToolApiKeys(userId) {
    const tableName = process.env.USER_STORAGE_TABLE;

    logger.info(`🔑 getUserToolApiKeys called with userId: "${userId}"`);
    logger.info(`🔑 USER_STORAGE_TABLE: "${tableName}"`);
    logger.info(`🔑 IDP_PREFIX: "${IDP_PREFIX}"`);

    if (!tableName) {
        logger.warn('USER_STORAGE_TABLE not configured');
        return {};
    }

    try {
        const hashKey = createHashKey(userId, APP_ID);
        // PK format in Python: {hash_key}#{entity_type} where hash_key = {user}#{app}
        // So PK = {user}#{app}#{entity_type}
        const pk = `${hashKey}#${ENTITY_TYPE}`;

        logger.info(`🔑 Generated hashKey: "${hashKey}"`);
        logger.info(`🔑 Full PK for query: "${pk}"`);
        logger.debug(`Querying tool API keys for user: ${userId}`);

        // Python stores with SK = item_id (provider name), not entity_type#provider
        const command = new QueryCommand({
            TableName: tableName,
            KeyConditionExpression: 'PK = :pk',
            ExpressionAttributeValues: {
                ':pk': pk
            }
        });

        const response = await docClient.send(command);

        logger.info(`🔑 DynamoDB query returned ${response.Items?.length || 0} items`);
        if (response.Items && response.Items.length > 0) {
            logger.info(`🔑 First item PK: "${response.Items[0].PK}", SK: "${response.Items[0].SK}"`);
        }

        const apiKeys = {};

        if (response.Items && response.Items.length > 0) {
            for (const item of response.Items) {
                // SK = item_id = provider name (e.g., "brave_search")
                // Handle both "provider_name" and "provider_name#range_key" formats
                const skParts = item.SK.split('#');
                const provider = skParts[0]; // Provider is the first part (or only part)

                logger.info(`🔑 Processing item - SK: "${item.SK}", provider: "${provider}"`);

                // Get the obfuscated key from the data field
                const obfuscatedKey = item.data?.key;

                if (obfuscatedKey) {
                    const apiKey = deobfuscateKey(obfuscatedKey);
                    if (apiKey) {
                        apiKeys[provider] = apiKey;
                        logger.info(`🔑 Found API key for provider: ${provider}`);
                    }
                } else {
                    logger.warn(`🔑 No obfuscated key found in item.data for provider: ${provider}`);
                    logger.debug(`🔑 Item data: ${JSON.stringify(item.data)}`);
                }
            }
        }

        logger.info(`Retrieved ${Object.keys(apiKeys).length} tool API keys for user`);
        return apiKeys;

    } catch (error) {
        logger.error('Failed to get user tool API keys:', error.message);
        return {};
    }
}

/**
 * Check if user has any search tool configured
 */
export async function hasUserSearchTool(userId) {
    const apiKeys = await getUserToolApiKeys(userId);
    return Object.keys(apiKeys).length > 0;
}

export default {
    getUserToolApiKeys,
    hasUserSearchTool
};
