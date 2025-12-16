/**
 * Tool Execution Loop Service
 *
 * Handles the LLM → tool call → execute → LLM cycle for function calling.
 * Supports web search and can be extended for other tools.
 */

import { getLogger } from '../common/logging.js';
import { sendStatusEventToStream, sendStateEventToStream, sendDeltaToStream, endStream } from '../common/streams.js';
import { newStatus } from '../common/status.js';
import { callUnifiedLLM } from '../llm/UnifiedLLMClient.js';
import { getUserToolApiKeys } from './userToolKeys.js';
import { WEB_SEARCH_TOOL_DEFINITION, executeToolCall } from './webSearch.js';

const logger = getLogger('toolLoop');

const MAX_TOOL_ITERATIONS = 5;

/**
 * Check if the LLM response contains tool calls
 */
function hasToolCalls(result) {
    // Check different possible locations for tool calls
    if (result.tool_calls && result.tool_calls.length > 0) {
        return true;
    }
    if (result.content && typeof result.content === 'string') {
        // Try to parse tool_calls from streamed content
        const match = result.content.match(/\{"tool_calls":\s*\[([\s\S]*?)\]\}/);
        if (match) {
            try {
                const parsed = JSON.parse(match[0]);
                return parsed.tool_calls && parsed.tool_calls.length > 0;
            } catch (e) {
                // Not valid JSON
            }
        }
    }
    return false;
}

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
 * @returns {Object} Final result
 */
export async function executeToolLoop(params, messages, model, responseStream, options = {}) {
    // Use username for tool API key lookup (matches how Python backend stores keys)
    // Falls back to user (sub) if username not available
    const userId = params.account?.username || params.account?.user;
    const maxIterations = options.maxIterations || MAX_TOOL_ITERATIONS;

    logger.info('Starting tool execution loop');
    logger.debug(`Tool loop using userId for API key lookup: ${userId}`);

    // Get user's tool API keys
    const apiKeys = await getUserToolApiKeys(userId);

    if (Object.keys(apiKeys).length === 0) {
        logger.warn('No tool API keys configured for user, proceeding without tools');
        // Continue without tools
        return await callUnifiedLLM(
            { ...params, options: { ...params.options, model } },
            messages,
            responseStream,
            options
        );
    }

    // Add tool definitions to options
    const toolOptions = {
        ...options,
        tools: [WEB_SEARCH_TOOL_DEFINITION],
        tool_choice: 'auto'
    };

    // Send initial status to let user know web search is available
    if (responseStream && !responseStream.writableEnded) {
        sendStatusEventToStream(responseStream, newStatus({
            id: 'web-search-init',
            summary: 'Web search enabled',
            inProgress: true,
            animated: true,
            icon: 'search'
        }));
    }

    let currentMessages = [...messages];
    let iteration = 0;
    let allWebSearchSources = []; // Track sources across iterations

    while (iteration < maxIterations) {
        iteration++;
        logger.info(`Tool loop iteration ${iteration}/${maxIterations}`);

        // Call LLM
        // First iteration: stream to user but keep stream open for status updates
        // Subsequent iterations: don't stream (tool results go to messages)
        const result = await callUnifiedLLM(
            { ...params, options: { ...params.options, model } },
            currentMessages,
            iteration === 1 ? responseStream : null,
            { ...toolOptions, keepStreamOpen: iteration === 1 } // Keep stream open during first iteration for tool execution
        );

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

            // For iterations > 1, we need to stream the final response to the user
            // (iteration 1 already streams, but subsequent iterations pass null responseStream)
            if (iteration > 1 && responseStream && !responseStream.writableEnded && result.content) {
                logger.info('Streaming final response from tool loop');
                sendDeltaToStream(responseStream, 'answer', result.content);
                // Send end marker so frontend knows response is complete
                endStream(responseStream);
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
            let query = 'query';
            try {
                query = toolCall.function?.arguments ? JSON.parse(toolCall.function.arguments).query : 'query';
            } catch (e) { /* ignore */ }

            // Send status update BEFORE executing the tool
            if (responseStream && !responseStream.writableEnded) {
                sendStatusEventToStream(responseStream, newStatus({
                    id: 'web-search',
                    summary: `Searching the web for "${query}"...`,
                    inProgress: true,
                    animated: true,
                    icon: 'search'
                }));
            }

            try {
                logger.info(`Executing tool: ${toolName}`);
                const toolResult = await executeToolCall(toolCall, apiKeys);
                toolResults.push(toolResult);

                // Collect web search sources for display
                if (toolName === 'web_search' && toolResult.rawResult && toolResult.rawResult.results) {
                    const sources = toolResult.rawResult.results.map(r => ({
                        name: r.title,
                        url: r.url,
                        content: r.description
                    }));
                    allWebSearchSources.push(...sources);
                }

            } catch (error) {
                logger.error('Tool execution failed:', error.message);
                toolResults.push({
                    callId: toolCall.id,
                    toolName: toolName,
                    content: `Error executing tool: ${error.message}`,
                    isError: true
                });
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
