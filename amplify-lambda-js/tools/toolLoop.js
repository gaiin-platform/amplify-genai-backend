/**
 * Tool Execution Loop Service
 *
 * Handles the LLM → tool call → execute → LLM cycle for function calling.
 * Supports web search, MCP tools, and can be extended for other tools.
 */

import { getLogger } from '../common/logging.js';
import { sendStatusEventToStream, sendStateEventToStream, sendDeltaToStream, endStream, forceFlush } from '../common/streams.js';
import { newStatus } from '../common/status.js';
import { callUnifiedLLM } from '../llm/UnifiedLLMClient.js';
import { getUserToolApiKeys } from './userToolKeys.js';
import { WEB_SEARCH_TOOL_DEFINITION, executeToolCall, getAdminWebSearchApiKey } from './webSearch.js';
import { getMCPToolDefinitions, executeMCPTool, isMCPTool } from '../mcp/mcpRegistry.js';
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { extractKey } from '../datasource/datasources.js';

const s3Client = new S3Client({});
const PRESIGNED_URL_TTL = 3600; // 1 hour

/**
 * Generate a pre-signed S3 URL for a data source so remote MCP servers can fetch it.
 */
async function presignDataSource(ds, bucket) {
    try {
        const key = extractKey(ds.id);
        const command = new GetObjectCommand({ Bucket: bucket, Key: key });
        const url = await getSignedUrl(s3Client, command, { expiresIn: PRESIGNED_URL_TTL });
        return { id: ds.id, type: ds.type, name: ds.name || ds.id, url };
    } catch (err) {
        logger.warn(`Failed to presign attachment ${ds.id}: ${err.message}`);
        // Fall back to sending just metadata — MCP server may still find it useful
        return { id: ds.id, type: ds.type, name: ds.name || ds.id };
    }
}

const logger = getLogger('toolLoop');

const MAX_TOOL_ITERATIONS = 5;

/**
 * Extract tool calls from LLM result
 */
function extractToolCalls(result) {
    if (result.tool_calls && result.tool_calls.length > 0) {
        return result.tool_calls;
    }
    if (result.content && typeof result.content === 'string') {
        const match = result.content.match(/\{"tool_calls":\s*\[([\s\S]*?)\]\}/);
        if (match) {
            try {
                const parsed = JSON.parse(match[0]);
                return parsed.tool_calls || [];
            } catch (e) {
                return [];
            }
        }
    }
    return [];
}

/**
 * Execute the tool loop
 * @param {Object} params - Request parameters including account info
 * @param {Array} messages - Chat messages
 * @param {Object} model - Model configuration
 * @param {Object} responseStream - Response stream
 * @param {Object} options - Additional options
 * @param {boolean} options.mcpClientSide - If true, MCP tools should be executed client-side
 * @returns {Object} Final result
 */
export async function executeToolLoop(params, messages, model, responseStream, options = {}) {
    // Use username for tool API key lookup (matches how Python backend stores keys)
    // Falls back to user (sub) if username not available
    const userId = params.account?.username || params.account?.user;

    // Use Cognito sub (user) for MCP server lookups - this matches how Python backend stores MCP servers
    // The Python mcp_servers.py uses current_user which is the Cognito sub UUID
    const mcpUserId = params.account?.user || params.account?.username;

    logger.info('Starting tool execution loop');
    logger.debug(`Tool loop using userId for API key lookup: ${userId}`);
    logger.debug(`Tool loop using mcpUserId for MCP server lookup: ${mcpUserId}`);

    // Get user's tool API keys
    let apiKeys = await getUserToolApiKeys(userId);
     // Also check for admin-configured web search (auto-enable ONLY if frontend didn't explicitly set a preference)
    let adminKey = null;

    if (options.webSearchEnabled) {
        try {
            adminKey = await getAdminWebSearchApiKey();
            if (adminKey && adminKey.provider && adminKey.api_key) {
                logger.info(`Admin web search available (${adminKey.provider}), auto-enabling tool loop`);
                apiKeys = {
                ...apiKeys,
                    [adminKey.provider]: adminKey.api_key
                };
            } else {
                logger.warn("→ Web search enabled for this request but no admin API key configured");
                options.webSearchEnabled = false;
            }
        } catch (error) {
            logger.debug('Failed to check admin web search config:', error.message);
        }
    } 

    logger.info("🔍 Tool loop check:", {
        adminWebSearchAvailable: !!adminKey,
        webSearchEnabled: options.webSearchEnabled,
        mcpEnabled :options?.mcpEnabled,
        toolsCount: (options?.tools)?.length || 0
    });


    logger.info(`Tool API keys available: ${Object.keys(apiKeys).join(', ') || 'none'}`);

    // Collect all available tools
    const allTools = [];

    // Add web search tool if API keys are available
    if (Object.keys(apiKeys).length > 0) {
        allTools.push(WEB_SEARCH_TOOL_DEFINITION);
    }

    // Handle MCP tools based on execution mode
    // IMPORTANT: MCP tools don't need API keys - they use the user's local MCP servers
    const mcpClientSide = options.mcpClientSide === true;

    if (mcpClientSide && options.tools && options.tools.length > 0) {
        // Use MCP tools passed from frontend (for client-side execution)
        // Filter to only include MCP tools from frontend-provided tools
        const frontendMCPTools = options.tools.filter(t => {
            const toolName = t.function?.name || t.name;
            return isMCPTool(toolName);
        });

        if (frontendMCPTools.length > 0) {
            logger.info(`Using ${frontendMCPTools.length} MCP tools from frontend (client-side execution mode)`);
            allTools.push(...frontendMCPTools);
        }
    } else if (!mcpClientSide) {
        // Get MCP tools from backend registry (for server-side execution)
        // Use mcpUserId (Cognito sub) to match Python storage format
        let mcpTools = [];
        try {
            mcpTools = await getMCPToolDefinitions(mcpUserId);
            if (mcpTools.length > 0) {
                logger.info(`Found ${mcpTools.length} MCP tools for user from registry`);
                allTools.push(...mcpTools);
            }
        } catch (error) {
            logger.warn('Failed to get MCP tools:', error.message);
        }
    }

    // If no tools available at all, just run without tools
    if (allTools.length === 0) {
        logger.warn('No tools available (no API keys and no MCP servers)');
        // Remove tools from options to prevent confusion
        const { tools, tool_choice, ...cleanOptions } = options;
        return await callUnifiedLLM(
            { ...params, tools:[], options: { ...params.options, model, enableWebSearch: false, tools: [] } },
            messages,
            responseStream,
            cleanOptions
        );
    }

    // Add tool definitions to options
    const toolOptions = {
        ...options,
        tools: allTools,
        tool_choice: 'auto'
    };

    // Send initial status to let user know tools are available
    if (responseStream && !responseStream.writableEnded) {
        const hasWebSearch = allTools.some(t => (t.function?.name || t.name) === 'web_search');
        const hasMCP = allTools.some(t => isMCPTool(t.function?.name || t.name));

        if (hasWebSearch) {
            sendStatusEventToStream(responseStream, newStatus({
                id: 'web-search-init',
                summary: 'Web search enabled',
                inProgress: true,
                animated: true,
                icon: 'search'
            }));
        }

        if (hasMCP) {
            sendStatusEventToStream(responseStream, newStatus({
                id: 'mcp-init',
                summary: `MCP tools enabled (${mcpClientSide ? 'client-side' : 'server-side'})`,
                inProgress: true,
                animated: true,
                icon: 'tool'
            }));
        }
    }

    let currentMessages = [...messages];
    let iteration = 0;
    let allWebSearchSources = []; // Track sources across iterations
    const maxIterations = options.maxIterations || MAX_TOOL_ITERATIONS;

    // Build attachment context once — forwarded to every MCP tool call so servers
    // can access images and document data sources that were attached to the conversation.
    // Pre-signed S3 URLs are generated so remote MCP servers can actually fetch the content.
    const attachmentContext = {};
    const imageBucket = process.env.S3_IMAGE_INPUT_BUCKET_NAME;
    const docBucket = process.env.S3_RAG_INPUT_BUCKET_NAME;

    if (options.imageSources && options.imageSources.length > 0 && imageBucket) {
        attachmentContext.images = await Promise.all(
            options.imageSources.map(img => presignDataSource(img, imageBucket))
        );
    }
    if (options.dataSources && options.dataSources.length > 0 && docBucket) {
        attachmentContext.documents = await Promise.all(
            options.dataSources.map(ds => presignDataSource(ds, docBucket))
        );
    }
    const hasAttachments = Object.keys(attachmentContext).length > 0;

    while (iteration < maxIterations) {
        iteration++;
        logger.info(`Tool loop iteration ${iteration}/${maxIterations}`);

        const result = await callUnifiedLLM(
            { ...params, options: { ...params.options, model } },
            currentMessages,
            responseStream,
            { ...toolOptions, keepStreamOpen: true } // Keep stream open during first iteration for tool execution
        );
        if (responseStream && !responseStream.writableEnded && result.content) {
                sendDeltaToStream(responseStream, 'answer', `\n\n`);
        }
        // Check for tool calls
        const toolCalls = extractToolCalls(result);

        if (!toolCalls || toolCalls.length === 0) {
            logger.info('No tool calls in response, completing loop');

            // Clear the init status
            if (responseStream && !responseStream.writableEnded) {
                sendStatusEventToStream(responseStream, newStatus({
                    id: 'web-search-init',
                    summary: 'Web search enabled',
                    inProgress: false
                }));
            }

            // Send collected sources if any
            if (responseStream && !responseStream.writableEnded && allWebSearchSources.length > 0) {
                logger.info(`Sending ${allWebSearchSources.length} web search sources to frontend`);
                sendStateEventToStream(responseStream, {
                    sources: {
                        webSearch: {
                            sources: allWebSearchSources
                        }
                    }
                });
            }

            
            return result;
        }

        logger.info(`Found ${toolCalls.length} tool calls`);

        // Clear the init status now that we're actually searching
        if (responseStream && !responseStream.writableEnded) {
            sendStatusEventToStream(responseStream, newStatus({
                id: 'web-search-init',
                summary: 'Web search enabled',
                inProgress: false
            }));
        }

        // Execute tool calls and collect all web search sources
        const toolResults = [];

        for (const toolCall of toolCalls) {
            const toolName = toolCall.function?.name || toolCall.name;
            let args = {};
            try {
                args = toolCall.function?.arguments ? JSON.parse(toolCall.function.arguments) : {};
            } catch (e) { /* ignore */ }

            // Check if this is an MCP tool
            const isToolMCP = isMCPTool(toolName);

            // Check if MCP tools should be executed client-side
            const mcpClientSide = options.mcpClientSide === true;

            // If this is an MCP tool and client-side execution is requested,
            // first try server-side execution (OAuth servers are stored in DynamoDB with
            // token refresh support). Only fall back to client-side if the server is not
            // in the backend registry.
            if (isToolMCP && mcpClientSide) {
                try {
                    logger.info(`MCP tool ${toolName} - trying server-side execution first`);
                    if (responseStream && !responseStream.writableEnded) {
                        sendStatusEventToStream(responseStream, newStatus({
                            id: `mcp-tool-${toolName}`,
                            summary: `Executing MCP tool: ${toolName}...`,
                            inProgress: true,
                            animated: true,
                            icon: 'tool'
                        }));
                    }
                    const serverSideResult = await executeMCPTool(mcpUserId, toolName, args,
                        hasAttachments ? attachmentContext : undefined,
                        params.account?.accessToken);
                    if (responseStream && !responseStream.writableEnded) {
                        sendStatusEventToStream(responseStream, newStatus({
                            id: `mcp-tool-${toolName}`,
                            summary: `MCP tool ${toolName} completed`,
                            inProgress: false
                        }));
                    }
                    toolResults.push(serverSideResult);
                    continue; // processed server-side, move to next tool call
                } catch (serverSideError) {
                    if (!serverSideError.message?.includes('not found')) {
                        // Server found but execution failed — surface as tool error
                        logger.error(`MCP tool ${toolName} server-side execution failed: ${serverSideError.message}`);
                        toolResults.push({
                            callId: toolCall.id,
                            toolName,
                            content: `Error executing tool: ${serverSideError.message}`,
                            isError: true
                        });
                        continue;
                    }
                    // Server not in backend registry — fall through to client-side
                    logger.info(`MCP tool ${toolName} not in server registry, routing to client-side`);
                }

                // Client-side fallback: send tool call to frontend
                logger.info(`MCP tool ${toolName} requires client-side execution`);
                if (responseStream && !responseStream.writableEnded) {
                    sendStateEventToStream(responseStream, {
                        mcpToolCalls: [{
                            id: toolCall.id,
                            type: 'function',
                            function: {
                                name: toolName,
                                arguments: JSON.stringify(args)
                            },
                            // Include attachment context so the client can pass files to the MCP server
                            attachments: hasAttachments ? attachmentContext : undefined
                        }]
                    });
                }

                // Return partial result - frontend will continue the conversation
                return {
                    content: result.content || '',
                    tool_calls: toolCalls,
                    pendingMCPToolCalls: true
                };
            }

            // Send status update BEFORE executing the tool
            if (responseStream && !responseStream.writableEnded) {
                if (isToolMCP) {
                    // MCP tool status
                    sendStatusEventToStream(responseStream, newStatus({
                        id: `mcp-tool-${toolName}`,
                        summary: `Executing MCP tool: ${toolName}...`,
                        inProgress: true,
                        animated: true,
                        icon: 'tool'
                    }));
                } else {
                    // Web search status
                    const query = args.query || 'query';
                    sendStatusEventToStream(responseStream, newStatus({
                        id: 'web-search',
                        summary: `Searching the web for "${query}"...`,
                        inProgress: true,
                        animated: true,
                        icon: 'search'
                    }));
                }
            }

            try {
                logger.info(`Executing tool: ${toolName} (MCP: ${isToolMCP})`);
                let toolResult;

                if (isToolMCP) {
                    // Execute MCP tool server-side (for publicly accessible MCP servers)
                    // Use mcpUserId (Cognito sub) to match Python storage format
                    toolResult = await executeMCPTool(mcpUserId, toolName, args,
                        hasAttachments ? attachmentContext : undefined,
                        params.account?.accessToken);

                    // Clear MCP tool status
                    if (responseStream && !responseStream.writableEnded) {
                        sendStatusEventToStream(responseStream, newStatus({
                            id: `mcp-tool-${toolName}`,
                            summary: `MCP tool ${toolName} completed`,
                            inProgress: false
                        }));
                    }
                } else {
                    // Execute web search or other built-in tools
                    toolResult = await executeToolCall(toolCall, apiKeys);

                    // Collect web search sources for display
                    if (toolName === 'web_search' && toolResult.rawResult && toolResult.rawResult.results) {
                        const sources = toolResult.rawResult.results.map(r => ({
                            name: r.title,
                            url: r.url,
                            content: r.description
                        }));
                        allWebSearchSources.push(...sources);
                    }
                }

                toolResults.push(toolResult);

            } catch (error) {
                logger.error('Tool execution failed:', error.message);
                toolResults.push({
                    callId: toolCall.id,
                    toolName: toolName,
                    content: `Error executing tool: ${error.message}`,
                    isError: true
                });

                // Clear status on error
                if (responseStream && !responseStream.writableEnded) {
                    const statusId = isToolMCP ? `mcp-tool-${toolName}` : 'web-search';
                    sendStatusEventToStream(responseStream, newStatus({
                        id: statusId,
                        summary: `Tool ${toolName} failed`,
                        inProgress: false
                    }));
                }
            }
        }

        // Send web search sources to frontend
        if (responseStream && !responseStream.writableEnded && allWebSearchSources.length > 0) {
            sendStateEventToStream(responseStream, {
                sources: {
                    webSearch: {
                        sources: allWebSearchSources
                    }
                }
            });
        }

        // Send status complete
        if (responseStream && !responseStream.writableEnded) {
            sendStatusEventToStream(responseStream, newStatus({
                id: 'web-search',
                summary: `Found ${allWebSearchSources.length} web results`,
                inProgress: false
            }));
        }

        // Build messages with tool results
        // Add assistant message with tool_calls
        // Ensure each tool_call has the required 'type' field for OpenAI API
        const normalizedToolCalls = toolCalls.map(tc => ({
            id: tc.id,
            type: tc.type || 'function',
            function: tc.function
        }));

        currentMessages.push({
            role: 'assistant',
            content: result.content || '',
            tool_calls: normalizedToolCalls
        });

        // Add tool results
        for (let i = 0; i < toolCalls.length; i++) {
            const toolCall = toolCalls[i];
            const toolResult = toolResults[i];

            currentMessages.push({
                role: 'tool',
                tool_call_id: toolCall.id,
                content: toolResult.content
            });
        }

        // Remove tools for follow-up call (let LLM generate final response)
        if (iteration === maxIterations - 1) {
            delete toolOptions.tools;
            delete toolOptions.tool_choice;
        }
    }

    logger.warn('Max tool iterations reached');
    return { content: 'Maximum tool iterations reached. Please try rephrasing your request.' };
}

/**
 * Check if web search should be enabled for this request
 */
export function shouldEnableWebSearch(body) {
    const result = body?.enableWebSearch === true ||
           body?.options?.enableWebSearch === true ||
           body?.options?.options?.webSearch === true;

    logger.info(`🔍 shouldEnableWebSearch check:`, {
        'body.enableWebSearch': body?.enableWebSearch,
        'body.options?.enableWebSearch': body?.options?.enableWebSearch,
        'body.options?.options?.webSearch': body?.options?.options?.webSearch,
        result
    });

    return result;
}

export default {
    executeToolLoop,
    shouldEnableWebSearch,
    WEB_SEARCH_TOOL_DEFINITION
};
