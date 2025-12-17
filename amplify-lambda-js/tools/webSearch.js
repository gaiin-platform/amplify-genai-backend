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
 * Get the admin-configured web search API key from SSM and DynamoDB
 * @returns {Object|null} { provider, api_key } or null if not configured
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
        const getItemCommand = new GetItemCommand({
            TableName: adminTableName,
            Key: {
                config_id: { S: 'web_search_config' }
            }
        });

        const response = await dynamoClient.send(getItemCommand);

        if (!response.Item || !response.Item.data?.M?.provider?.S) {
            logger.debug('No admin web search config found');
            return null;
        }

        const provider = response.Item.data.M.provider.S;
        const paramName = `/tools/web_search/${provider}/${stage}`;

        // Get API key from SSM
        const ssmCommand = new GetParameterCommand({
            Name: paramName,
            WithDecryption: true
        });

        const ssmResponse = await ssmClient.send(ssmCommand);
        const apiKey = ssmResponse.Parameter?.Value;

        if (!apiKey) {
            logger.warn(`Admin web search key not found in SSM: ${paramName}`);
            return null;
        }

        logger.info(`Using admin web search key for provider: ${provider}`);
        return {
            provider,
            api_key: apiKey
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
 * @returns {Object} Search results
 */
export async function executeWebSearch(query, apiKeys = {}, skipAdminKey = false) {
    logger.info(`Executing web search for query: ${query}`);

    // Try providers in order of preference
    const providers = [
        { name: 'brave_search', execute: executeBraveSearch },
        { name: 'tavily', execute: executeTavilySearch },
        { name: 'serper', execute: executeSerperSearch },
        { name: 'serpapi', execute: executeSerpApiSearch }
    ];

    // First, check for admin-configured API key if not skipped
    if (!skipAdminKey) {
        try {
            const adminKey = await getAdminWebSearchApiKey();
            if (adminKey && adminKey.provider && adminKey.api_key) {
                logger.info(`Found admin-configured web search key for provider: ${adminKey.provider}`);
                // Merge admin key with user keys, giving priority to admin key's provider
                apiKeys = {
                    ...apiKeys,
                    [adminKey.provider]: adminKey.api_key
                };
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
        if (apiKey) {
            try {
                logger.info(`Using ${provider.name} for web search`);
                const result = await provider.execute(query, apiKey);
                logger.info(`Web search completed with ${result.resultCount} results`);
                return result;
            } catch (error) {
                logger.error(`${provider.name} search failed:`, error.message);
                // Try next provider
            }
        }
    }

    throw new Error('No configured search provider available or all providers failed');
}

/**
 * Format search results for LLM consumption
 */
export function formatSearchResultsForLLM(searchResult) {
    let formatted = `## Web Search Results for: "${searchResult.query}"\n\n`;

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
    } else {
        formatted += `No results found.\n`;
    }

    return formatted;
}

/**
 * Execute a tool call
 * @param {Object} toolCall - The tool call from LLM
 * @param {Object} apiKeys - User's API keys
 * @returns {Object} Tool result
 */
export async function executeToolCall(toolCall, apiKeys) {
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
            const searchResult = await executeWebSearch(args.query, apiKeys);
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
