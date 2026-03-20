/**
 * MCP Server Registry
 *
 * Manages MCP server configurations stored in DynamoDB.
 * Handles storing, retrieving, and managing active MCP connections.
 */

import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, QueryCommand, PutCommand, DeleteCommand, GetCommand } from '@aws-sdk/lib-dynamodb';
import { getLogger } from '../common/logging.js';
import { MCPClient, isMCPTool } from './mcpClient.js';

const logger = getLogger('mcpRegistry');

const client = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(client);

const APP_ID = 'amplify-mcp';
const ENTITY_TYPE = 'mcp_servers';

// IDP prefix for user identity (matches Python backend)
const IDP_PREFIX = (process.env.IDP_PREFIX || '').toLowerCase();

// Cache of active MCP client instances per user
const activeClients = new Map();

/**
 * Create hash key matching storage format
 */
function createHashKey(userId, appId) {
    const sanitizedUser = userId.replace(/[^a-zA-Z0-9@._-]/g, '-');
    const sanitizedApp = appId.replace(/[^a-zA-Z0-9-]/g, '-');
    return `${sanitizedUser}#${sanitizedApp}`;
}

/**
 * Get full user ID with IDP prefix
 */
function getFullUserId(userId) {
    return IDP_PREFIX ? `${IDP_PREFIX}_${userId}` : userId;
}

/**
 * Generate unique server ID
 */
function generateServerId() {
    return `mcp_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Get all MCP server configurations for a user
 * @param {string} userId - The user ID (email/username)
 * @returns {Array} Array of MCP server configurations
 */
export async function getUserMCPServers(userId) {
    const tableName = process.env.USER_STORAGE_TABLE;

    if (!tableName) {
        logger.warn('USER_STORAGE_TABLE not configured');
        return [];
    }

    try {
        const fullUserId = getFullUserId(userId);
        const hashKey = createHashKey(fullUserId, APP_ID);
        const pk = `${hashKey}#${ENTITY_TYPE}`;

        logger.debug(`Querying MCP servers for user: ${fullUserId}, PK: ${pk}`);

        // Also query without IDP prefix for backward compatibility with records stored
        // by the Python backend before the IDP prefix alignment fix.
        const legacyHashKey = createHashKey(userId, APP_ID);
        const legacyPk = `${legacyHashKey}#${ENTITY_TYPE}`;
        const queryLegacy = legacyPk !== pk;

        const command = new QueryCommand({
            TableName: tableName,
            KeyConditionExpression: 'PK = :pk',
            ExpressionAttributeValues: {
                ':pk': pk
            }
        });

        const response = await docClient.send(command);
        const servers = [];
        const seenIds = new Set();

        function collectItems(items) {
            for (const item of (items || [])) {
                if (seenIds.has(item.SK)) continue;
                seenIds.add(item.SK);
                const serverConfig = item.data || {};
                servers.push({
                    id: item.SK,
                    name: serverConfig.name,
                    url: serverConfig.url,
                    transport: serverConfig.transport || 'http',
                    enabled: serverConfig.enabled !== false,
                    tools: serverConfig.tools || [],
                    lastConnected: serverConfig.lastConnected,
                    status: serverConfig.status || 'disconnected',
                    createdAt: serverConfig.createdAt,
                    updatedAt: serverConfig.updatedAt,
                    headers: serverConfig.headers || {}
                });
            }
        }

        collectItems(response.Items);

        // Fallback: also query the legacy (non-prefixed) PK for records stored before
        // the IDP prefix was applied consistently (e.g. early OAuth-registered servers).
        if (queryLegacy) {
            const legacyResponse = await docClient.send(new QueryCommand({
                TableName: tableName,
                KeyConditionExpression: 'PK = :pk',
                ExpressionAttributeValues: { ':pk': legacyPk }
            }));
            if (legacyResponse.Items?.length > 0) {
                logger.debug(`Found ${legacyResponse.Items.length} legacy (non-prefixed) MCP server(s) for user`);
                collectItems(legacyResponse.Items);
            }
        }

        logger.info(`Retrieved ${servers.length} MCP servers for user`);
        return servers;

    } catch (error) {
        logger.error('Failed to get user MCP servers:', error.message);
        return [];
    }
}

/**
 * Add a new MCP server configuration for a user
 * @param {string} userId - The user ID
 * @param {Object} serverConfig - Server configuration
 * @returns {Object} Created server configuration with ID
 */
export async function addMCPServer(userId, serverConfig) {
    const tableName = process.env.USER_STORAGE_TABLE;

    if (!tableName) {
        throw new Error('USER_STORAGE_TABLE not configured');
    }

    const fullUserId = getFullUserId(userId);
    const hashKey = createHashKey(fullUserId, APP_ID);
    const pk = `${hashKey}#${ENTITY_TYPE}`;
    const serverId = generateServerId();
    const now = new Date().toISOString();

    const item = {
        PK: pk,
        SK: serverId,
        data: {
            name: serverConfig.name,
            url: serverConfig.url,
            transport: serverConfig.transport || 'http',
            enabled: serverConfig.enabled !== false,
            tools: [],
            status: 'disconnected',
            headers: (serverConfig.headers && typeof serverConfig.headers === 'object') ? serverConfig.headers : {},
            createdAt: now,
            updatedAt: now
        }
    };

    try {
        const command = new PutCommand({
            TableName: tableName,
            Item: item
        });

        await docClient.send(command);

        logger.info(`Added MCP server "${serverConfig.name}" for user ${userId}`);

        return {
            id: serverId,
            ...item.data
        };

    } catch (error) {
        logger.error('Failed to add MCP server:', error.message);
        throw error;
    }
}

/**
 * Update an existing MCP server configuration
 * @param {string} userId - The user ID
 * @param {string} serverId - The server ID
 * @param {Object} updates - Fields to update
 * @returns {Object} Updated server configuration
 */
export async function updateMCPServer(userId, serverId, updates) {
    const tableName = process.env.USER_STORAGE_TABLE;

    if (!tableName) {
        throw new Error('USER_STORAGE_TABLE not configured');
    }

    const fullUserId = getFullUserId(userId);
    const hashKey = createHashKey(fullUserId, APP_ID);
    const pk = `${hashKey}#${ENTITY_TYPE}`;

    try {
        // First get the existing item
        const getCommand = new GetCommand({
            TableName: tableName,
            Key: { PK: pk, SK: serverId }
        });

        const existing = await docClient.send(getCommand);

        if (!existing.Item) {
            throw new Error(`MCP server ${serverId} not found`);
        }

        const updatedData = {
            ...existing.Item.data,
            ...updates,
            updatedAt: new Date().toISOString()
        };

        const putCommand = new PutCommand({
            TableName: tableName,
            Item: {
                PK: pk,
                SK: serverId,
                data: updatedData
            }
        });

        await docClient.send(putCommand);

        logger.info(`Updated MCP server ${serverId} for user ${userId}`);

        return {
            id: serverId,
            ...updatedData
        };

    } catch (error) {
        logger.error('Failed to update MCP server:', error.message);
        throw error;
    }
}

/**
 * Delete an MCP server configuration
 * @param {string} userId - The user ID
 * @param {string} serverId - The server ID to delete
 * @returns {boolean} True if deleted successfully
 */
export async function deleteMCPServer(userId, serverId) {
    const tableName = process.env.USER_STORAGE_TABLE;

    if (!tableName) {
        throw new Error('USER_STORAGE_TABLE not configured');
    }

    const fullUserId = getFullUserId(userId);
    const hashKey = createHashKey(fullUserId, APP_ID);
    const pk = `${hashKey}#${ENTITY_TYPE}`;

    try {
        // Disconnect if connected
        disconnectMCPServer(userId, serverId);

        const command = new DeleteCommand({
            TableName: tableName,
            Key: { PK: pk, SK: serverId }
        });

        await docClient.send(command);

        logger.info(`Deleted MCP server ${serverId} for user ${userId}`);
        return true;

    } catch (error) {
        logger.error('Failed to delete MCP server:', error.message);
        throw error;
    }
}

/**
 * Test connection to an MCP server
 * @param {string} userId - The user ID
 * @param {Object} serverConfig - Server configuration to test
 * @returns {Object} Connection result with discovered tools
 */
export async function testMCPConnection(userId, serverConfig) {
    logger.info(`Testing MCP connection: ${serverConfig.name} at ${serverConfig.url}`);

    const testClient = new MCPClient({
        id: 'test',
        name: serverConfig.name,
        url: serverConfig.url,
        transport: serverConfig.transport || 'http',
        headers: serverConfig.headers || {},
        timeout: 10000 // Shorter timeout for testing
    });

    try {
        const result = await testClient.connect();

        return {
            success: true,
            serverInfo: result.serverInfo,
            tools: result.tools
        };

    } catch (error) {
        logger.error(`MCP connection test failed: ${error.message}`);
        return {
            success: false,
            error: error.message
        };
    } finally {
        await testClient.disconnect();
    }
}

/**
 * Connect to an MCP server and cache the client
 * @param {string} userId - The user ID
 * @param {string} serverId - The server ID
 * @returns {MCPClient} Connected MCP client
 */
export async function connectMCPServer(userId, serverId) {
    const servers = await getUserMCPServers(userId);
    const serverConfig = servers.find(s => s.id === serverId);

    if (!serverConfig) {
        throw new Error(`MCP server ${serverId} not found`);
    }

    if (!serverConfig.enabled) {
        throw new Error(`MCP server ${serverId} is disabled`);
    }

    const cacheKey = `${userId}:${serverId}`;

    // Check if already connected
    if (activeClients.has(cacheKey)) {
        const existingClient = activeClients.get(cacheKey);
        if (existingClient.connected) {
            // Sync headers from DynamoDB so OAuth token changes (disconnect/refresh)
            // take effect immediately without restarting the server process.
            existingClient.headers = serverConfig.headers || {};
            return existingClient;
        }
        // Remove stale client
        activeClients.delete(cacheKey);
    }

    // Create new client and connect
    const mcpClient = new MCPClient({
        id: serverId,
        name: serverConfig.name,
        url: serverConfig.url,
        transport: serverConfig.transport,
        headers: serverConfig.headers || {}
    });

    try {
        const result = await mcpClient.connect();

        // Update server with discovered tools
        await updateMCPServer(userId, serverId, {
            tools: result.tools,
            lastConnected: new Date().toISOString(),
            status: 'connected'
        });

        // Cache the client
        activeClients.set(cacheKey, mcpClient);

        logger.info(`Connected to MCP server ${serverConfig.name}`);
        return mcpClient;

    } catch (error) {
        // Update status to error
        await updateMCPServer(userId, serverId, {
            status: 'error',
            lastError: error.message
        });
        throw error;
    }
}

/**
 * Disconnect from an MCP server
 * @param {string} userId - The user ID
 * @param {string} serverId - The server ID
 */
export function disconnectMCPServer(userId, serverId) {
    const cacheKey = `${userId}:${serverId}`;

    if (activeClients.has(cacheKey)) {
        const client = activeClients.get(cacheKey);
        client.disconnect();
        activeClients.delete(cacheKey);
        logger.info(`Disconnected from MCP server ${serverId}`);
    }
}

/**
 * Get active MCP client for a server
 * @param {string} userId - The user ID
 * @param {string} serverId - The server ID
 * @returns {MCPClient|null} Active client or null
 */
export function getActiveMCPClient(userId, serverId) {
    const cacheKey = `${userId}:${serverId}`;
    return activeClients.get(cacheKey) || null;
}

/**
 * Get all enabled MCP servers for a user with active connections
 * @param {string} userId - The user ID
 * @returns {Array} Array of { serverId, client } objects
 */
export async function getEnabledMCPClients(userId) {
    const servers = await getUserMCPServers(userId);
    const enabledServers = servers.filter(s => s.enabled);
    const clients = [];

    for (const server of enabledServers) {
        try {
            const client = await connectMCPServer(userId, server.id);
            clients.push({ serverId: server.id, serverName: server.name, client });
        } catch (error) {
            logger.warn(`Failed to connect to MCP server ${server.name}: ${error.message}`);
        }
    }

    return clients;
}

/**
 * Get tool definitions from all enabled MCP servers for a user
 * @param {string} userId - The user ID
 * @returns {Array} Array of tool definitions in OpenAI function format
 */
export async function getMCPToolDefinitions(userId) {
    const clients = await getEnabledMCPClients(userId);
    const allTools = [];

    for (const { client } of clients) {
        const tools = client.getToolDefinitions();
        allTools.push(...tools);
    }

    logger.info(`Retrieved ${allTools.length} MCP tool definitions for user`);
    return allTools;
}

/**
 * Execute an MCP tool
 * @param {string} userId - The user ID
 * @param {string} toolName - Full tool name (mcp_{serverId}_{toolName})
 * @param {Object} args - Tool arguments
 * @param {Object} [attachmentContext] - Optional attachment context (images, documents) to forward
 * @param {string} [userToken] - Optional JWT access token for OAuth refresh on 401
 * @returns {Object} Tool execution result
 */
export async function executeMCPTool(userId, toolName, args, attachmentContext, userToken) {
    const { serverId, originalName } = await import('./mcpClient.js').then(m => m.parseMCPToolName(toolName));

    if (!serverId || !originalName) {
        throw new Error(`Invalid MCP tool name: ${toolName}`);
    }

    let client = await connectMCPServer(userId, serverId);
    let result = await client.executeTool(originalName, args, attachmentContext);

    // Retry once on 401 (expired OAuth token) if we have a token to call the refresh endpoint
    if (result.isError && result.content?.includes('401') && userToken) {
        logger.info(`MCP tool 401 detected for server ${serverId}, attempting OAuth token refresh`);
        try {
            const apiBase = process.env.API_BASE_URL;
            const refreshResp = await fetch(`${apiBase}/integrations/mcp/server/oauth/refresh`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${userToken}`
                },
                body: JSON.stringify({ data: { serverId } })
            });
            const refreshData = await refreshResp.json();
            if (refreshData.success) {
                // Evict stale cached client so connectMCPServer re-reads new token from DynamoDB
                const cacheKey = `${userId}:${serverId}`;
                activeClients.delete(cacheKey);
                client = await connectMCPServer(userId, serverId);
                result = await client.executeTool(originalName, args, attachmentContext);
            } else {
                logger.warn(`MCP OAuth refresh failed for server ${serverId}: ${refreshData.message}`);
            }
        } catch (refreshErr) {
            logger.error(`MCP OAuth refresh request failed for server ${serverId}: ${refreshErr.message}`);
        }
    }

    return result;
}

// Re-export the isMCPTool function for convenience
export { isMCPTool };

export default {
    getUserMCPServers,
    addMCPServer,
    updateMCPServer,
    deleteMCPServer,
    testMCPConnection,
    connectMCPServer,
    disconnectMCPServer,
    getActiveMCPClient,
    getEnabledMCPClients,
    getMCPToolDefinitions,
    executeMCPTool,
    isMCPTool
};
