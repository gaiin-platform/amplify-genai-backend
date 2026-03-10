//Copyright (c) 2024 Vanderbilt University
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { sendStatusEventToStream, sendStateEventToStream } from "../../streams.js";
import { newStatus } from "../../status.js";
import { getLogger } from "../../logging.js";

const logger = getLogger("openai-events");

/**
 * Transforms OpenAI streaming events into plain text output or side-effect updates.
 *
 * @param {object} event - The raw OpenAI streaming event, which may contain
 *   `choices` with `delta` or `message` fields, or new `/responses` endpoint format.
 * @param {object|null} [responseStream=null] - Optional stream or channel used to send status
 *   updates (for example, reasoning content) via {@link sendStatusEventToStream}.
 * @param {object|null} [capturedContent=null] - Optional mutable accumulator object used to
 *   collect tool invocation data across multiple chunks. When present, tool call deltas
 *   are accumulated into `capturedContent.toolCalls` array with each tool call containing
 *   `id`, `type`, and `function` (with `name` and `arguments` fields).
 * @returns {string|object|null} The text content from the event, tool_calls object for streaming,
 *   or null if the event was handled as a side effect.
 */
export const openAiTransform = (event, responseStream = null, capturedContent = null) => {
    // Handle new /responses endpoint format
    if (event && event.type) {
        // Handle streaming reasoning text deltas
        if ((event.type === "response.reasoning_summary_text.delta" && event.delta)) {
            if (responseStream) {
                // Send reasoning text as it streams (frontend should accumulate)
                sendStatusEventToStream(responseStream, newStatus({
                    id: "reasoning",
                    summary: "Thinking Details:",
                    message: event.delta,
                    icon: "bolt",
                    inProgress: true,
                    animated: true,
                }));
            }
            return null; // Don't send this as content
        }

        // Handle web search events (OpenAI built-in web search)
        if (event.type === "response.web_search_call.searching") {
            if (responseStream) {
                sendStatusEventToStream(responseStream, newStatus({
                    id: "openai-web-search",
                    summary: "Searching the web...",
                    icon: "search",
                    inProgress: true,
                    animated: true,
                }));
            }
            return null;
        }

        if (event.type === "response.web_search_call.completed") {
            if (responseStream) {
                sendStatusEventToStream(responseStream, newStatus({
                    id: "openai-web-search",
                    summary: "Web search completed",
                    icon: "search",
                    inProgress: false,
                }));
            }
            return null;
        }

        // Capture web search sources from output_item.done
        if (event.type === "response.output_item.done" && event.item?.type === "web_search_call") {
            if (capturedContent && event.item.action?.url) {
                // Initialize web search sources array if needed
                if (!capturedContent.webSearchSources) {
                    capturedContent.webSearchSources = [];
                }
                capturedContent.webSearchSources.push({
                    url: event.item.action.url,
                    // Title will be added from annotations if available
                });
                logger.debug(`Captured web search URL: ${event.item.action.url}`);
            }
            return null;
        }

        // Capture URL citations (contains title and URL)
        if (event.type === "response.output_text.annotation.added" && event.annotation?.type === "url_citation") {
            if (capturedContent) {
                // Initialize annotations array if needed
                if (!capturedContent.urlCitations) {
                    capturedContent.urlCitations = [];
                }
                capturedContent.urlCitations.push({
                    url: event.annotation.url,
                    title: event.annotation.title,
                    start_index: event.annotation.start_index,
                    end_index: event.annotation.end_index,
                });
                logger.debug(`Captured URL citation: ${event.annotation.title} - ${event.annotation.url}`);
            }
            return null;
        }

        // When response completes, send collected web search sources to frontend
        if (event.type === "response.done" || event.type === "response.completed") {
            if (responseStream && capturedContent?.urlCitations && capturedContent.urlCitations.length > 0) {
                // Format sources for frontend (similar to our web search tool format)
                const sources = capturedContent.urlCitations.map(citation => ({
                    name: citation.title,
                    url: citation.url,
                    content: citation.title  // Use title as content since we don't have snippets
                }));

                logger.info(`Sending ${sources.length} OpenAI web search sources to frontend`);
                sendStateEventToStream(responseStream, {
                    sources: {
                        webSearch: {
                            sources: sources
                        }
                    }
                });
            }
            return null;
        }

        // Handle text delta from assistant response
        if (event.type === "response.output_text.delta" && event.delta) {
            return event.delta;  // Return raw text, sendDeltaToStream will wrap it
        }

    }

    // Handle legacy completions endpoint format
    if (event && event.choices && event.choices.length > 0) {
        const choice = event.choices[0];

        // Handle tool calls - accumulate in capturedContent
        if (choice.delta && choice.delta.tool_calls) {
            const toolCallDeltas = choice.delta.tool_calls;

            if (capturedContent) {
                if (!capturedContent.toolCalls) capturedContent.toolCalls = [];

                for (const delta of toolCallDeltas) {
                    const idx = delta.index;

                    // Initialize tool call on first delta (has id and function.name)
                    if (delta.id && delta.function?.name) {
                        logger.debug(`OpenAI tool call start: index=${idx}, id=${delta.id}, name=${delta.function.name}`);
                        // Ensure array is large enough
                        while (capturedContent.toolCalls.length <= idx) {
                            capturedContent.toolCalls.push(null);
                        }
                        capturedContent.toolCalls[idx] = {
                            id: delta.id,
                            type: 'function',
                            function: {
                                name: delta.function.name,
                                arguments: delta.function.arguments || ''
                            }
                        };
                    } else if (delta.function?.arguments && capturedContent.toolCalls[idx]) {
                        // Accumulate arguments
                        capturedContent.toolCalls[idx].function.arguments += delta.function.arguments;
                    }
                }
            }

            // Still return the tool calls for streaming to frontend
            return {tool_calls: toolCallDeltas};
        }

        // Check for finish_reason to finalize tool calls
        if (choice.finish_reason === 'tool_calls' && capturedContent) {
            logger.debug(`OpenAI finish_reason=tool_calls, captured ${capturedContent.toolCalls?.length || 0} tool calls`);
            // Filter out any null entries
            if (capturedContent.toolCalls) {
                capturedContent.toolCalls = capturedContent.toolCalls.filter(tc => tc !== null);
                for (const tc of capturedContent.toolCalls) {
                    logger.debug(`Finalized tool call: ${JSON.stringify(tc)}`);
                }
            }
        }

        // Handle text content
        if (choice.delta && choice.delta.content) {
            return choice.delta.content;  // Return raw text, sendDeltaToStream will wrap it
        } else if (choice.message && choice.message.content) {
            return choice.message.content;  // Return raw text, sendDeltaToStream will wrap it
        }
    } else if (event && event.d && event.d.delta && event.d.delta.text) { // for error message
        return event.d.delta.text;  // Return raw text, sendDeltaToStream will wrap it
    }

    return null;

}

export const openaiUsageTransform = (event) => {
    // Handle response.completed event format
    const usage = event.usage || (event.response && event.response.usage);
    
    if (usage) {
        // Handle new /responses endpoint format
        if (usage.output_tokens !== undefined && usage.input_tokens !== undefined) {
            // Convert to legacy format for compatibility
            usage.prompt_tokens = usage.input_tokens;
            usage.completion_tokens = usage.output_tokens;
            // Add reasoning tokens if present
            usage.completion_tokens += usage.output_tokens_details?.reasoning_tokens ?? 0;
            // Extract reasoning tokens for separate tracking
            usage.reasoning_tokens = usage.output_tokens_details?.reasoning_tokens ?? 0;
        } else {
            // Handle legacy completions endpoint format
            const reasoningTokens = usage.reasoning_tokens ?? 0;
            usage.completion_tokens += reasoningTokens;
            // Extract reasoning tokens for separate tracking
            usage.reasoning_tokens = reasoningTokens;
        }
        
        // Extract cached tokens from OpenAI format
        // Input cached tokens: from prompt_tokens_details.cached_tokens OR input_tokens_details.cached_tokens
        usage.inputCachedTokens = (usage.inputCachedTokens ?? usage.input_tokens_details?.cached_tokens ?? usage.prompt_tokens_details?.cached_tokens) ?? 0;
        
        return usage;
    } else {
        return null;
    }
}