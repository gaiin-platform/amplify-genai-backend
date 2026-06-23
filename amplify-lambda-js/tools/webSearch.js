/**
 * Web Search Tool Service
 *
 * Executes web search using admin-configured or user-provided API keys.
 * Supports multiple search providers: Brave Search, Tavily, Serper, SerpAPI.
 */

import { getLogger } from '../common/logging.js';
import { SSMClient, GetParameterCommand } from '@aws-sdk/client-ssm';
import { DynamoDBClient, GetItemCommand } from '@aws-sdk/client-dynamodb';

const logger = getLogger('webSearch');
const ssmClient = new SSMClient({});
const dynamoClient = new DynamoDBClient({});

// MCP protocol version used when talking to the Bedrock AgentCore Gateway.
// Matches the version used by the app's existing MCP client (mcp/mcpClient.js).
const AGENTCORE_MCP_PROTOCOL_VERSION = '2024-11-05';
const AGENTCORE_DEFAULT_TIMEOUT_MS = 30000;
const AGENTCORE_DEFAULT_MAX_RESULTS = 5; // matches the 5-result convention used by the other providers

// In-memory cache for OAuth client-credentials access tokens, keyed by tokenUrl|clientId|scope.
// Lives for the lifetime of the warm Lambda container so we don't mint a token per search.
const agentCoreTokenCache = new Map();

let agentCoreRpcCounter = 0;
function nextAgentCoreRpcId() {
    agentCoreRpcCounter += 1;
    return `acrpc_${Date.now()}_${agentCoreRpcCounter}`;
}

function safeJsonParse(text) {
    try {
        return JSON.parse(text);
    } catch (e) {
        return null;
    }
}

// Tool definition for LLM
export const WEB_SEARCH_TOOL_DEFINITION = {
    type: 'function',
    function: {
        name: 'web_search',
        description: 'Search the web for current information. Use this when you need to find up-to-date information, recent news, current events, or facts that may have changed since your knowledge cutoff.',
        parameters: {
            type: 'object',
            properties: {
                query: {
                    type: 'string',
                    description: 'The search query to look up on the web'
                }
            },
            required: ['query']
        }
    }
};

/**
 * Execute a Brave Search query
 */
async function executeBraveSearch(query, apiKey) {
    const url = `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=5`;

    const response = await fetch(url, {
        method: 'GET',
        headers: {
            'Accept': 'application/json',
            'X-Subscription-Token': apiKey
        }
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Brave Search API error: ${response.status} - ${errorText}`);
    }

    const data = await response.json();

    // Format results
    const results = (data.web?.results || []).slice(0, 5).map(result => ({
        title: result.title,
        url: result.url,
        description: result.description
    }));

    return {
        provider: 'brave_search',
        query,
        results,
        resultCount: results.length
    };
}

/**
 * Execute a Tavily Search query
 */
async function executeTavilySearch(query, apiKey) {
    const url = 'https://api.tavily.com/search';

    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            api_key: apiKey,
            query: query,
            search_depth: 'basic',
            max_results: 5,
            include_answer: true
        })
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Tavily API error: ${response.status} - ${errorText}`);
    }

    const data = await response.json();

    // Format results
    const results = (data.results || []).slice(0, 5).map(result => ({
        title: result.title,
        url: result.url,
        description: result.content
    }));

    return {
        provider: 'tavily',
        query,
        answer: data.answer,
        results,
        resultCount: results.length
    };
}

/**
 * Execute a Serper Search query
 */
async function executeSerperSearch(query, apiKey) {
    const url = 'https://google.serper.dev/search';

    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-API-KEY': apiKey
        },
        body: JSON.stringify({
            q: query,
            num: 5
        })
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Serper API error: ${response.status} - ${errorText}`);
    }

    const data = await response.json();

    // Format results
    const results = (data.organic || []).slice(0, 5).map(result => ({
        title: result.title,
        url: result.link,
        description: result.snippet
    }));

    return {
        provider: 'serper',
        query,
        results,
        resultCount: results.length
    };
}

/**
 * Execute a SerpAPI Search query
 */
async function executeSerpApiSearch(query, apiKey) {
    const params = new URLSearchParams({
        q: query,
        api_key: apiKey,
        engine: 'google',
        num: '5'
    });

    const url = `https://serpapi.com/search?${params.toString()}`;

    const response = await fetch(url, {
        method: 'GET',
        headers: {
            'Accept': 'application/json'
        }
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`SerpAPI error: ${response.status} - ${errorText}`);
    }

    const data = await response.json();

    // Format results
    const results = (data.organic_results || []).slice(0, 5).map(result => ({
        title: result.title,
        url: result.link,
        description: result.snippet
    }));

    return {
        provider: 'serpapi',
        query,
        results,
        resultCount: results.length
    };
}

/**
 * Parse a Server-Sent Events (text/event-stream) body and return the last
 * JSON-RPC message it contains. The Bedrock AgentCore Gateway uses the MCP
 * "Streamable HTTP" transport, which may answer a request with either a plain
 * JSON body or a single SSE event, depending on content negotiation.
 */
function parseSSEJsonRpc(text) {
    const dataPayloads = [];
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.replace(/\s+$/, '');
        if (line.startsWith('data:')) {
            dataPayloads.push(line.slice(5).trim());
        }
    }

    let lastMessage = null;
    for (const payload of dataPayloads) {
        if (!payload) continue;
        const obj = safeJsonParse(payload);
        if (obj && (obj.result !== undefined || obj.error !== undefined || obj.jsonrpc)) {
            lastMessage = obj;
        }
    }

    if (lastMessage) return lastMessage;
    // Fall back to parsing the whole body as JSON (some gateways send raw JSON in SSE).
    return safeJsonParse(text);
}

/**
 * Make a single JSON-RPC call to an MCP endpoint (the AgentCore Gateway URL).
 * Handles both JSON and SSE responses and surfaces the MCP session id header so
 * the caller can thread it through subsequent requests.
 *
 * @returns {Object} { sessionId, body }
 */
async function agentCoreMcpRpc(url, headers, message, timeoutMs) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers,
            body: JSON.stringify(message),
            signal: controller.signal
        });

        const sessionId = response.headers.get('mcp-session-id');

        if (!response.ok) {
            const errorText = await response.text().catch(() => '');
            throw new Error(`AgentCore gateway HTTP ${response.status} ${response.statusText}${errorText ? ` - ${errorText}` : ''}`);
        }

        const contentType = (response.headers.get('content-type') || '').toLowerCase();
        let body = null;

        if (contentType.includes('text/event-stream')) {
            body = parseSSEJsonRpc(await response.text());
        } else if (contentType.includes('application/json')) {
            body = await response.json();
        } else {
            // Notifications (e.g. notifications/initialized) typically return 202 with an empty body.
            const text = await response.text();
            body = text ? safeJsonParse(text) : null;
        }

        return { sessionId, body };
    } catch (error) {
        if (error.name === 'AbortError') {
            throw new Error('AgentCore gateway request timed out');
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
    }
}

/**
 * Determine which auth mode to use for the AgentCore gateway.
 *   - explicit config.authMode wins
 *   - tokenUrl + clientId -> OAuth2 client-credentials
 *   - a secret present -> static bearer token
 *   - otherwise -> user_token (forward the caller's Cognito access token)
 */
function resolveAgentCoreAuthMode(config = {}, secret) {
    if (config.authMode) return config.authMode;
    if (config.tokenUrl && config.clientId) return 'oauth';
    if (secret) return 'bearer';
    return 'user_token';
}

/**
 * Obtain a bearer token for the AgentCore Gateway.
 *
 * AgentCore Gateways enforce inbound OAuth/JWT authorization. Three modes are
 * supported:
 *   1. user_token: forward the caller's own Cognito access token. The gateway's
 *      JWT authorizer is configured to trust the app's Cognito user pool/client,
 *      so no extra secret needs to be provisioned. This is the default for the
 *      automated deployment path.
 *   2. oauth: OAuth2 client-credentials flow (config.tokenUrl + config.clientId);
 *      `secret` is the client secret. Tokens are cached until shortly before expiry.
 *   3. bearer: `secret` is used directly as a static bearer token.
 *
 * @param {Object} config - { authMode, tokenUrl, clientId, scope }
 * @param {string} secret - client secret (OAuth) or static bearer token
 * @param {string} accessToken - caller's access token (user_token mode)
 * @returns {string} bearer token
 */
async function getAgentCoreAccessToken(config = {}, secret, accessToken) {
    const authMode = resolveAgentCoreAuthMode(config, secret);

    if (authMode === 'user_token') {
        if (!accessToken) {
            throw new Error('Bedrock AgentCore user_token auth requires the caller access token');
        }
        return accessToken;
    }

    if (authMode === 'bearer') {
        if (!secret) {
            throw new Error('Bedrock AgentCore bearer auth requires a token');
        }
        return secret;
    }

    // OAuth2 client-credentials flow
    const { tokenUrl, clientId, scope } = config;
    if (!tokenUrl || !clientId) {
        throw new Error('Bedrock AgentCore oauth auth requires tokenUrl and clientId');
    }
    if (!secret) {
        throw new Error('Bedrock AgentCore OAuth client-credentials flow requires a client secret');
    }

    const cacheKey = `${tokenUrl}|${clientId}|${scope || ''}`;
    const now = Date.now();
    const cached = agentCoreTokenCache.get(cacheKey);
    // Reuse the cached token if it has more than 60s of life left.
    if (cached && cached.expiresAt > now + 60000) {
        return cached.token;
    }

    const form = new URLSearchParams({
        grant_type: 'client_credentials',
        client_id: clientId,
        client_secret: secret
    });
    if (scope) {
        form.set('scope', scope);
    }

    const response = await fetch(tokenUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form.toString()
    });

    if (!response.ok) {
        const errorText = await response.text().catch(() => '');
        throw new Error(`AgentCore OAuth token request failed: ${response.status}${errorText ? ` - ${errorText}` : ''}`);
    }

    const data = await response.json();
    const token = data.access_token;
    if (!token) {
        throw new Error('AgentCore OAuth token response did not include an access_token');
    }

    const expiresInMs = (typeof data.expires_in === 'number' ? data.expires_in : 3600) * 1000;
    agentCoreTokenCache.set(cacheKey, { token, expiresAt: now + expiresInMs });
    return token;
}

/**
 * Pick the web search tool name from the gateway's tool list.
 *
 * AgentCore Gateways namespace target tool names (e.g. "web-search-tool___WebSearch"),
 * so we discover the actual name rather than hardcoding it. An explicit
 * config.toolName always wins.
 */
function resolveAgentCoreWebSearchToolName(tools, configuredName) {
    if (configuredName) return configuredName;

    if (!Array.isArray(tools) || tools.length === 0) {
        throw new Error('AgentCore gateway exposed no tools (check the gateway target configuration)');
    }
    if (tools.length === 1) {
        return tools[0].name;
    }

    const lower = (name) => (name || '').toLowerCase();
    const byWebSearch = tools.find(t => lower(t.name).includes('websearch'));
    if (byWebSearch) return byWebSearch.name;

    const bySearch = tools.find(t => /search/i.test(t.name));
    if (bySearch) return bySearch.name;

    return tools[0].name;
}

/**
 * Normalize the AgentCore Web Search tool result into the standard
 * { title, url, description } shape used across the app.
 */
function parseAgentCoreResults(toolResult, maxResults) {
    if (!toolResult) return [];

    // Collect every candidate payload: structuredContent (if present) and any
    // JSON parsed from text content items. The gateway may return results in
    // either place, so we pick whichever actually carries a results array
    // rather than assuming one source.
    const candidates = [];
    if (toolResult.structuredContent && typeof toolResult.structuredContent === 'object') {
        candidates.push(toolResult.structuredContent);
    }
    if (Array.isArray(toolResult.content)) {
        for (const item of toolResult.content) {
            if (item?.type === 'text' && item.text) {
                const parsed = safeJsonParse(item.text);
                if (parsed) candidates.push(parsed);
            }
        }
    }

    let items = [];
    for (const candidate of candidates) {
        if (candidate && Array.isArray(candidate.results)) {
            items = candidate.results;
            break;
        }
        if (Array.isArray(candidate)) {
            items = candidate;
            break;
        }
    }

    return items.slice(0, maxResults).map(result => ({
        title: result.title || result.url || 'Untitled',
        url: result.url || '',
        description: result.text || result.snippet || result.description || ''
    }));
}

/**
 * Extract a human-readable error message from an MCP tool result.
 */
function extractAgentCoreText(toolResult) {
    if (toolResult && Array.isArray(toolResult.content)) {
        const textItem = toolResult.content.find(item => item.type === 'text' && item.text);
        if (textItem) return textItem.text;
    }
    return 'Unknown error';
}

/**
 * Execute a web search via the Amazon Bedrock AgentCore Gateway Web Search tool.
 *
 * Unlike the REST providers, this talks to an MCP endpoint (the gateway URL):
 *   initialize -> notifications/initialized -> tools/list (if needed) -> tools/call
 *
 * @param {string} query - search query
 * @param {string} secret - OAuth client secret or static bearer token (from SSM)
 * @param {Object} config - { gatewayUrl, authMode, tokenUrl, clientId, scope, toolName, region, maxResults, timeoutMs }
 * @param {string} accessToken - caller's Cognito access token (used in user_token auth mode)
 */
async function executeBedrockAgentCoreSearch(query, secret, config = {}, accessToken) {
    const gatewayUrl = config.gatewayUrl;
    if (!gatewayUrl) {
        throw new Error('Bedrock AgentCore web search requires a configured gateway URL (bedrockAgentCoreGatewayUrl)');
    }

    const token = await getAgentCoreAccessToken(config, secret, accessToken);
    if (!token) {
        throw new Error('Bedrock AgentCore web search requires a bearer token, OAuth client credentials, or a caller access token');
    }

    const timeoutMs = config.timeoutMs || AGENTCORE_DEFAULT_TIMEOUT_MS;
    const baseHeaders = {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Authorization': `Bearer ${token}`
    };

    // 1. initialize the MCP session
    const initResponse = await agentCoreMcpRpc(gatewayUrl, baseHeaders, {
        jsonrpc: '2.0',
        id: nextAgentCoreRpcId(),
        method: 'initialize',
        params: {
            protocolVersion: AGENTCORE_MCP_PROTOCOL_VERSION,
            capabilities: { tools: {} },
            clientInfo: { name: 'amplify-genai', version: '1.0.0' }
        }
    }, timeoutMs);

    if (initResponse.body?.error) {
        throw new Error(`AgentCore gateway initialize failed: ${initResponse.body.error.message}`);
    }

    // Thread the MCP session id (if the gateway issued one) through later requests.
    const sessionHeaders = initResponse.sessionId
        ? { ...baseHeaders, 'Mcp-Session-Id': initResponse.sessionId }
        : baseHeaders;

    // 2. tell the server initialization is complete (best-effort notification)
    try {
        await agentCoreMcpRpc(gatewayUrl, sessionHeaders, {
            jsonrpc: '2.0',
            method: 'notifications/initialized',
            params: {}
        }, timeoutMs);
    } catch (notifyError) {
        logger.debug(`AgentCore initialized notification skipped: ${notifyError.message}`);
    }

    // 3. resolve the tool name (discover via tools/list unless explicitly configured)
    let toolName = config.toolName;
    if (!toolName) {
        const listResponse = await agentCoreMcpRpc(gatewayUrl, sessionHeaders, {
            jsonrpc: '2.0',
            id: nextAgentCoreRpcId(),
            method: 'tools/list',
            params: {}
        }, timeoutMs);

        if (listResponse.body?.error) {
            throw new Error(`AgentCore gateway tools/list failed: ${listResponse.body.error.message}`);
        }
        toolName = resolveAgentCoreWebSearchToolName(listResponse.body?.result?.tools, config.toolName);
    }

    // 4. call the web search tool
    const maxResults = Math.min(Math.max(parseInt(config.maxResults, 10) || AGENTCORE_DEFAULT_MAX_RESULTS, 1), 25);
    const callResponse = await agentCoreMcpRpc(gatewayUrl, sessionHeaders, {
        jsonrpc: '2.0',
        id: nextAgentCoreRpcId(),
        method: 'tools/call',
        params: {
            name: toolName,
            arguments: { query, maxResults }
        }
    }, timeoutMs);

    if (callResponse.body?.error) {
        throw new Error(`AgentCore web search failed: ${callResponse.body.error.message}`);
    }

    const toolResult = callResponse.body?.result;
    if (toolResult?.isError) {
        throw new Error(`AgentCore web search returned an error: ${extractAgentCoreText(toolResult)}`);
    }

    const results = parseAgentCoreResults(toolResult, maxResults);

    return {
        provider: 'bedrock_agentcore',
        query,
        results,
        resultCount: results.length
    };
}

/**
 * Get the admin-configured web search API key from SSM and DynamoDB
 * @returns {Object|null} { provider, api_key, config } or null if not configured
 */
export async function getAdminWebSearchApiKey() {
    const adminTableName = process.env.AMPLIFY_ADMIN_DYNAMODB_TABLE;
    const stage = process.env.INTEGRATION_STAGE || process.env.STAGE || 'dev';

    if (!adminTableName) {
        logger.debug('AMPLIFY_ADMIN_DYNAMODB_TABLE not set, skipping admin key check');
        return null;
    }

    try {
        // Get config from DynamoDB admin table
        // Uses 'webSearchConfig' to match AdminConfigTypes convention in Python backend
        const getItemCommand = new GetItemCommand({
            TableName: adminTableName,
            Key: {
                config_id: { S: 'webSearchConfig' }
            }
        });

        const response = await dynamoClient.send(getItemCommand);

        if (!response.Item || !response.Item.data?.M?.provider?.S) {
            logger.debug('No admin web search config found');
            return null;
        }

        // Check if web search is enabled at admin level
        const isEnabled = response.Item.data.M.isEnabled?.BOOL;
        logger.info(`Admin web search config: provider=${response.Item.data.M.provider.S}, isEnabled=${isEnabled}`);

        if (isEnabled === false) {
            logger.info('Web search is disabled by admin configuration');
            return null;
        }

        const data = response.Item.data.M;
        const provider = data.provider.S;
        const paramName = `/tools/web_search/${provider}/${stage}`;

        // For the Bedrock AgentCore provider, pull the extra (non-secret) gateway
        // configuration that lives alongside the provider in the admin config item.
        // Falls back to environment variables published by the deployment-time
        // provisioner so the gateway is usable even before an admin edits the config.
        let config;
        if (provider === 'bedrock_agentcore') {
            config = {
                gatewayUrl: data.bedrockAgentCoreGatewayUrl?.S || process.env.WEB_SEARCH_AGENTCORE_GATEWAY_URL || undefined,
                region: data.bedrockAgentCoreRegion?.S || process.env.WEB_SEARCH_AGENTCORE_REGION || undefined,
                authMode: data.bedrockAgentCoreAuthMode?.S || process.env.WEB_SEARCH_AGENTCORE_AUTH_MODE || undefined,
                tokenUrl: data.bedrockAgentCoreTokenUrl?.S,
                clientId: data.bedrockAgentCoreClientId?.S,
                scope: data.bedrockAgentCoreScope?.S,
                toolName: data.bedrockAgentCoreToolName?.S
            };
        }

        // Get API key from SSM. The AgentCore "user_token" auth mode forwards the
        // caller's own access token to the gateway, so no stored secret is required.
        let apiKey;
        try {
            const ssmResponse = await ssmClient.send(new GetParameterCommand({
                Name: paramName,
                WithDecryption: true
            }));
            apiKey = ssmResponse.Parameter?.Value;
        } catch (ssmError) {
            if (ssmError.name !== 'ParameterNotFound') {
                throw ssmError;
            }
        }

        if (!apiKey) {
            const usesUserToken = provider === 'bedrock_agentcore'
                && (config?.authMode === 'user_token'
                    || (!config?.authMode && !config?.tokenUrl && !config?.clientId));
            if (!(usesUserToken && config?.gatewayUrl)) {
                logger.warn(`Admin web search key not found in SSM: ${paramName}`);
                return null;
            }
            logger.info('AgentCore user_token auth mode: proceeding without a stored secret');
        }

        logger.info(`Using admin web search config for provider: ${provider}`);
        return {
            provider,
            api_key: apiKey,
            config
        };

    } catch (error) {
        if (error.name === 'ParameterNotFound') {
            logger.debug('Admin web search SSM parameter not found');
            return null;
        }
        logger.error('Error getting admin web search API key:', error.message);
        return null;
    }
}

/**
 * Execute web search using available provider
 * @param {string} query - The search query
 * @param {Object} apiKeys - Object with provider keys { brave_search: 'key', tavily: 'key', serper: 'key', serpapi: 'key' }
 * @param {boolean} skipAdminKey - If true, skip checking for admin key (used when user keys are explicitly provided)
 * @param {Object} context - Optional runtime context, e.g. { accessToken } used by the AgentCore user_token auth mode
 * @returns {Object} Search results
 */
export async function executeWebSearch(query, apiKeys = {}, skipAdminKey = false, context = {}) {
    // Validate query parameter
    if (!query || typeof query !== 'string') {
        throw new Error('Search query is required and must be a string');
    }

    const trimmedQuery = query.trim();
    if (trimmedQuery.length === 0) {
        throw new Error('Search query cannot be empty');
    }

    // Validate query length (most search APIs have limits around 2000-4000 characters)
    const MAX_QUERY_LENGTH = 2000;
    if (trimmedQuery.length > MAX_QUERY_LENGTH) {
        throw new Error(`Search query exceeds maximum length of ${MAX_QUERY_LENGTH} characters`);
    }

    logger.info(`Executing web search for query: ${trimmedQuery}`);

    // Try providers in order of preference
    const providers = [
        { name: 'brave_search', execute: executeBraveSearch },
        { name: 'tavily', execute: executeTavilySearch },
        { name: 'serper', execute: executeSerperSearch },
        { name: 'serpapi', execute: executeSerpApiSearch },
        { name: 'bedrock_agentcore', execute: executeBedrockAgentCoreSearch }
    ];

    // Per-provider non-secret configuration (e.g. AgentCore gateway URL / OAuth settings).
    // The REST providers ignore this; only bedrock_agentcore consumes it.
    const providerConfigs = {};

    // First, check for admin-configured API key if not skipped
    if (!skipAdminKey) {
        try {
            const adminKey = await getAdminWebSearchApiKey();
            if (adminKey && adminKey.provider) {
                logger.info(`Found admin-configured web search provider: ${adminKey.provider}`);
                // The key may be absent for AgentCore user_token mode (no stored secret).
                if (adminKey.api_key) {
                    apiKeys = {
                        ...apiKeys,
                        [adminKey.provider]: adminKey.api_key
                    };
                }
                if (adminKey.config) {
                    providerConfigs[adminKey.provider] = adminKey.config;
                }
            }
        } catch (error) {
            logger.warn('Failed to get admin web search key, falling back to user keys:', error.message);
        }
    }

    // Log available keys for debugging
    const availableProviders = Object.keys(apiKeys).filter(k => apiKeys[k]);
    logger.info(`Available web search providers: ${availableProviders.join(', ') || 'none'}`);

    for (const provider of providers) {
        const apiKey = apiKeys[provider.name];
        const config = providerConfigs[provider.name];

        // AgentCore can run without a stored key (user_token / IAM-authorized gateway)
        // as long as it has a gateway URL to talk to.
        const isAgentCore = provider.name === 'bedrock_agentcore';
        const runnable = apiKey || (isAgentCore && config?.gatewayUrl);
        if (!runnable) {
            continue;
        }

        try {
            logger.info(`Using ${provider.name} for web search`);
            const result = await provider.execute(trimmedQuery, apiKey, config, context.accessToken);
            logger.info(`Web search completed with ${result.resultCount} results`);
            return result;
        } catch (error) {
            logger.error(`${provider.name} search failed:`, error.message);
            // Try next provider
        }
    }

    throw new Error('No configured search provider available or all providers failed');
}

/**
 * Format search results for LLM consumption
 */
export function formatSearchResultsForLLM(searchResult) {
    const currentDate = new Date().toISOString().split('T')[0];

    let formatted = `## Web Search Results for: "${searchResult.query}"\n`;
    formatted += `**Search Date:** ${currentDate} (today)\n\n`;

    // Add strong instruction to prioritize search results
    formatted += `**CRITICAL INSTRUCTION:** These are REAL, LIVE web search results retrieved just now from the internet. Today's date is ${currentDate}. These results are from legitimate news sources (Reuters, BBC, CNN, NYT, etc.) reporting on ACTUAL current events.\n\n`;
    formatted += `**DO NOT** dismiss these results as "fictional", "hypothetical", or "future scenarios". Your training data has a knowledge cutoff and CANNOT know about recent events. These search results represent what is ACTUALLY happening in the world RIGHT NOW.\n\n`;
    formatted += `If these results contradict your training data, the search results are correct because they are more recent. Trust the search results.\n\n`;

    if (searchResult.answer) {
        formatted += `**Quick Answer:** ${searchResult.answer}\n\n`;
    }

    formatted += `**Search Provider:** ${searchResult.provider}\n\n`;

    if (searchResult.results && searchResult.results.length > 0) {
        formatted += `**Results:**\n\n`;
        searchResult.results.forEach((result, index) => {
            formatted += `${index + 1}. **${result.title}**\n`;
            formatted += `   URL: ${result.url}\n`;
            formatted += `   ${result.description}\n\n`;
        });

        formatted += `\n**RESPONSE INSTRUCTIONS:** You MUST base your response on the above search results. These are real news articles from today. Cite the sources. Do not claim these events haven't happened or are fictional - they are real and current.\n`;
    } else {
        formatted += `No results found.\n`;
    }

    return formatted;
}

/**
 * Execute a tool call
 * @param {Object} toolCall - The tool call from LLM
 * @param {Object} apiKeys - User's API keys
 * @param {Object} context - Optional runtime context, e.g. { accessToken } for AgentCore user_token auth
 * @returns {Object} Tool result
 */
export async function executeToolCall(toolCall, apiKeys, context = {}) {
    const toolName = toolCall.function?.name || toolCall.name;
    let args = {};

    try {
        args = typeof toolCall.function?.arguments === 'string'
            ? JSON.parse(toolCall.function.arguments)
            : (toolCall.function?.arguments || toolCall.arguments || {});
    } catch (e) {
        logger.error('Failed to parse tool arguments:', e);
    }

    logger.info(`Executing tool: ${toolName}`, { args });

    switch (toolName) {
        case 'web_search':
            const searchResult = await executeWebSearch(args.query, apiKeys, false, context);
            return {
                callId: toolCall.id,
                toolName,
                content: formatSearchResultsForLLM(searchResult),
                rawResult: searchResult,
                isError: false
            };

        default:
            return {
                callId: toolCall.id,
                toolName,
                content: `Unknown tool: ${toolName}`,
                isError: true
            };
    }
}

export default {
    WEB_SEARCH_TOOL_DEFINITION,
    executeWebSearch,
    executeToolCall,
    formatSearchResultsForLLM,
    getAdminWebSearchApiKey
};
