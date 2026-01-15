//Copyright (c) 2024 Vanderbilt University  
//Authors: Nahuel Pereira (FortyAU)
import { sendStatusEventToStream } from "../../streams.js";
import { newStatus } from "../../status.js";
import { getLogger } from "../../logging.js";

const logger = getLogger("gemini-events");

/**
 * Transforms Gemini streaming events into plain text output or side-effect updates.
 *
 * @param {object} event - The raw Gemini streaming event, which may be in OpenAI compatibility
 *   format (with `choices`) or native Gemini format (with `candidates`).
 * @param {object|null} [_responseStream=null] - Optional stream or channel used to send status
 *   updates (for example, reasoning/thinking content) via {@link sendStatusEventToStream}.
 * @param {object|null} [capturedContent=null] - Optional mutable accumulator object used to
 *   collect tool invocation data across multiple chunks. When present, tool calls are
 *   accumulated into `capturedContent.toolCalls` array with each tool call containing
 *   `id`, `type`, and `function` (with `name` and `arguments` fields).
 * @returns {string|null} The text content from the event, or null if the event was handled
 *   as a side effect (e.g., tool call accumulation, thinking content).
 */
export const geminiTransform = (event, _responseStream = null, capturedContent = null) => {
    try {
        // Handle Gemini OpenAI compatibility format directly
        if (event && event.choices && event.choices.length > 0) {
            const choice = event.choices[0];

            // Handle thinking/reasoning content from LLM
            if (choice && choice.delta?.content  && choice.delta?.extra_content?.google?.thought && _responseStream) {
                const thought = choice.delta?.content.replace("<thought>", "").replace("</thought>", "");
                sendStatusEventToStream(_responseStream, newStatus({
                    id: "reasoning",
                    summary: "Thinking Details:",
                    message: thought,
                    icon: "bolt",
                    inProgress: true,
                    animated: true
                }));
                return null; // Don't return thought as regular content
            }

            // Handle regular streaming delta content
            if (choice.delta && choice.delta.content) {
                return choice.delta.content; // Return raw text, sendDeltaToStream will wrap it
            }

            // Handle complete message format
            if (choice.message && choice.message.content) {
                return choice.message.content; // Return raw text
            }

            // Handle tool calls (OpenAI compatibility format)
            if (choice.delta && choice.delta.tool_calls) {
                // Capture tool calls for the tool loop
                if (capturedContent) {
                    for (const tc of choice.delta.tool_calls) {
                        if (tc.index !== undefined) {
                            // Streaming tool call delta
                            if (!capturedContent.toolCalls) capturedContent.toolCalls = [];
                            if (!capturedContent.toolCalls[tc.index]) {
                                capturedContent.toolCalls[tc.index] = {
                                    id: tc.id || `gemini-tool-${Date.now()}-${tc.index}`,
                                    type: 'function',
                                    function: { name: '', arguments: '' }
                                };
                            }
                            if (tc.function?.name) {
                                capturedContent.toolCalls[tc.index].function.name = tc.function.name;
                            }
                            if (tc.function?.arguments) {
                                capturedContent.toolCalls[tc.index].function.arguments += tc.function.arguments;
                            }
                        }
                    }
                }
                return null;
            }
        }

        // For Gemini API native format (non-OpenAI compatibility)
        if (event && event.candidates && event.candidates.length > 0) {
            if (event.candidates[0].content && event.candidates[0].content.parts) {
                const parts = event.candidates[0].content.parts;
                // Get text content and tool calls from parts
                for (const part of parts) {
                    if (part.text) {
                        return part.text; // Return raw text
                    }
                    // Handle Gemini native functionCall format
                    if (part.functionCall) {
                        logger.debug(`Gemini functionCall detected: ${part.functionCall.name}`);
                        if (capturedContent) {
                            if (!capturedContent.toolCalls) capturedContent.toolCalls = [];
                            capturedContent.toolCalls.push({
                                id: `gemini-${Date.now()}-${capturedContent.toolCalls.length}`,
                                type: 'function',
                                function: {
                                    name: part.functionCall.name,
                                    arguments: JSON.stringify(part.functionCall.args || {})
                                }
                            });
                            logger.debug(`Captured tool call: ${JSON.stringify(capturedContent.toolCalls)}`);
                        }
                        return null;
                    }
                }
            }
        }

        // Unrecognized Gemini event format

    } catch (err) {
        logger.error("Error in geminiTransform:", err);
    }

    return null;
}

export const geminiUsageTransform = (event) => {
    // Handle Gemini usage format directly
    if (event && event.usage) {
        const usage = {
            prompt_tokens: event.usage.prompt_tokens || 0,
            completion_tokens: event.usage.completion_tokens || 0,
            total_tokens: event.usage.total_tokens || 0
        };
        
        // Extract Gemini cached tokens (OpenAI compatibility format)
        // Use inputCachedTokens if already present, otherwise extract from prompt_tokens_details.cached_tokens
        usage.inputCachedTokens = event.usage.inputCachedTokens ?? 
                                 event.usage.prompt_tokens_details?.cached_tokens ?? 0;
        return usage;
    }
    
    return null;
} 