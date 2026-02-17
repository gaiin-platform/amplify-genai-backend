//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { v4 as uuidv4 } from 'uuid';
import { Writable } from 'stream';
import { getLogger } from '../common/logging.js';
import { 
    sendDeltaToStream, 
    sendStatusEventToStream, 
    sendErrorMessage,
    endStream 
} from '../common/streams.js';
import { newStatus, getThinkingMessage } from '../common/status.js';
import { getAccountId } from '../common/params.js';
import { recordUsage } from '../common/accounting.js';

// Import native provider implementations
import { chat as openaiChat } from '../azure/openai.js';
import { chatBedrock } from '../bedrock/bedrock.js';
import { chat as geminiChat } from '../gemini/gemini.js';

// Import event transformers
import { openAiTransform, openaiUsageTransform } from '../common/chat/events/openai.js';
import { bedrockConverseTransform, bedrockTokenUsageTransform } from '../common/chat/events/bedrock.js';
import { geminiTransform, geminiUsageTransform } from '../common/chat/events/gemini.js';
import {ARTIFACTS_PROMPT} from "../common/conversations.js";

// Import secrets management
import { getLLMConfig } from '../common/secrets.js';

const logger = getLogger('UnifiedLLMClient');

// Active request tracking
const activeRequests = new Map();


/**
 * Get the appropriate chat function and transformer for a model
 * Uses model.provider instead of model ID patterns since providers like Bedrock can serve OpenAI models
 */
const getProviderConfig = (model) => {
    const provider = model?.provider;
    const modelId = model?.id || model;
    
    if (!provider) {
        throw new Error(`Model provider not specified for model: ${modelId}`);
    }
    
    // OpenAI and Azure use the same configuration (OpenAI-compatible API)
    const openAIConfig = () => ({
        chatFn: openaiChat,
        needsEndpointProvider: true, // OpenAI needs getLLMConfig
        transform: openAiTransform,
        usageTransform: openaiUsageTransform
    });
    
    const providerConfigs = {
        'OpenAI': openAIConfig,
        'Azure': openAIConfig,  // Same as OpenAI (OpenAI-compatible)
        'Gemini': () => ({
            chatFn: geminiChat,
            needsEndpointProvider: false,
            transform: geminiTransform,
            usageTransform: geminiUsageTransform
        }),
        'Bedrock': () => ({
            chatFn: chatBedrock,
            needsEndpointProvider: false,
            transform: bedrockConverseTransform,
            usageTransform: bedrockTokenUsageTransform
        })
    };
    
    // Get config by provider name, with case-insensitive fallback
    const config = providerConfigs[provider] || 
                  providerConfigs[Object.keys(providerConfigs).find(key => 
                      key.toLowerCase() === provider.toLowerCase())];
    
    if (!config) {
        throw new Error(`Unsupported provider: ${provider} for model: ${modelId}`);
    }
    
    return config();
};

/**
 * Queue conversation analysis with fallback
 */
async function queueConversationAnalysisWithFallback(params, messages, result) {
    try {
        const { queueConversationAnalysisWithFallback: queueConversationAnalysisImpl } = await import('../groupassistants/conversationAnalysis.js');
        
        if (params.options?.trackConversations) {
            // Construct proper chatRequest object
            const chatRequest = {
                messages: messages,
                options: params.options
            };
            
            // Construct llmResponse object
            const llmResponse = result?.content || "";
            
            // Use account from params
            const account = params.account;
            
            // Always perform analysis for system rating, categories are handled separately in analysis function
            const performCategoryAnalysis = true; // Always analyze for rating, categories determined by analysisCategories presence
            
            await queueConversationAnalysisImpl(
                chatRequest,
                llmResponse,
                account,
                performCategoryAnalysis
            );
        }
    } catch (error) {
        logger.error('Failed to queue conversation analysis:', error);
    }
}

/**
 * Create a stream interceptor to capture usage and handle transformations
 */
function createStreamInterceptor(responseStream, transform, usageTransform, requestState, capturedContent = null) {
    let buffer = ''; // Buffer for incomplete SSE data
    
    const interceptor = new Writable({
        write(chunk, _encoding, callback) {
            try {
                // Append chunk to buffer
                buffer += chunk.toString();
                
                // Process complete lines
                const lines = buffer.split('\n');
                
                // Keep the last incomplete line in the buffer
                buffer = lines.pop() || '';
                
                for (const line of lines) {
                    if (line.trim() && line.startsWith('data: ')) {
                        const data = line.slice(6).trim();
                        if (data === '[DONE]') continue;
                        
                        try {
                            const event = JSON.parse(data);
                            
                            // ✅ SMART ROUTING: Detect our events vs LLM events
                            if (event.s === "meta") {
                                // This is our internal event (status/state/mode) - send directly, bypass transformer
                                responseStream.write(line + '\n\n');
                            } else {
                                // This is LLM response data - apply provider transformer
                                const transformed = transform(event, responseStream, capturedContent);
                                if (transformed) {
                                    sendDeltaToStream(responseStream, 'answer', transformed);
                                    // Capture content for conversation analysis if requested
                                    if (capturedContent) {
                                        capturedContent.fullResponse += typeof transformed === 'string' ? transformed : (transformed.d || '');
                                    }
                                }
                                
                                // Extract usage from LLM events only
                                const usage = usageTransform(event);
                                if (usage) {
                                    requestState.totalUsage = { ...requestState.totalUsage, ...usage };
                                }
                            }
                        } catch (e) {
                            // Only log if it's not an empty line or whitespace
                            if (data.trim()) {
                                logger.error(`Failed to parse streaming event: ${e.message}`);
                                // Raw event failed to parse
                            }
                        }
                    }
                }
                callback();
            } catch (error) {
                callback(error);
            }
        },
        final(callback) {
            // Process any remaining buffered data
            if (buffer.trim() && buffer.startsWith('data: ')) {
                const data = buffer.slice(6).trim();
                if (data !== '[DONE]') {
                    try {
                        const event = JSON.parse(data);
                        
                        // ✅ SMART ROUTING: Detect our events vs LLM events (buffered data)
                        if (event.s === "meta") {
                            // This is our internal event (status/state/mode) - send directly, bypass transformer
                            responseStream.write(buffer + '\n\n');
                        } else {
                            // This is LLM response data - apply provider transformer
                            const transformed = transform(event, responseStream, capturedContent);
                            if (transformed) {
                                sendDeltaToStream(responseStream, 'answer', transformed);
                                // Capture content for conversation analysis if requested
                                if (capturedContent) {
                                    capturedContent.fullResponse += typeof transformed === 'string' ? transformed : (transformed.d || '');
                                }
                            }
                            
                            // Extract usage from LLM events only
                            const usage = usageTransform(event);
                            if (usage) {
                                requestState.totalUsage = { ...requestState.totalUsage, ...usage };
                            }
                        }
                    } catch (e) {
                        // Buffer had incomplete data at end
                    }
                }
            }
            
            // Clear thinking timer when stream ends
            if (requestState.statusTimer) {
                clearTimeout(requestState.statusTimer);
                requestState.statusTimer = null;
                
                // Send thinking complete status
                sendStatusEventToStream(responseStream, newStatus({
                    id: "thinking",
                    inProgress: false
                }));
            }
            callback();
        }
    });
    
    return interceptor;
}

/**
 * Main unified LLM call function
 */
export async function callUnifiedLLM(params, messages, responseStream = null, options = {}) {
    const requestId = params.requestId || `unified-${uuidv4()}`;
    const model = params.options?.model || params.model;

    if (!model) {
        throw new Error('Model not specified');
    }

    // Get provider configuration
    const providerConfig = getProviderConfig(model);

    // Track request
    const requestState = {
        requestId,
        cancelled: false,
        startTime: Date.now(),
        responseStream,
        statusTimer: null,
        totalUsage: {
            prompt_tokens: 0,
            completion_tokens: 0,
            cached_tokens: 0,
            reasoning_tokens: 0,
            inputCachedTokens: 0,
            inputWriteCachedTokens: 0
        }
    };
    activeRequests.set(requestId, requestState);

    try {
        // Starting UnifiedLLM call
        
        // // Start thinking status timer if streaming (reduced to 1 second for faster UX)
        // if (responseStream) {
        //     requestState.statusTimer = setTimeout(() => {
        //         if (!requestState.cancelled && responseStream) {
        //             sendStatusEventToStream(responseStream, newStatus({
        //                 id: "thinking",
        //                 summary: getThinkingMessage(),
        //                 inProgress: true,
        //                 animated: true
        //             }));
        //         }
        //     }, 8000);
        // }

        // Prepare request body
        // Extract internal options that should NOT be passed to providers
        const { keepStreamOpen, ...providerOptions } = options;

        const chatBody = {
            messages,
            model: model.id || model,
            stream: !!responseStream,
            max_tokens: providerOptions.max_tokens || 2000,
            temperature: providerOptions.temperature || 1.0,
            ...providerOptions,
            options: {
                ...params.options,
                model,
                requestId,
                user: params?.account?.user || params.user || "unknown"// Pass user for critical error logging
            }
        };

        // ✅ FIX: Pass imageSources from options to chatBody for provider compatibility
        if (options.imageSources) {
            chatBody.imageSources = options.imageSources;
        }

        // Handle tools/functions
        if (options.tools) {
            chatBody.tools = options.tools;
        }
        if (options.tool_choice) {
            chatBody.tool_choice = options.tool_choice;
        }
        if (options.functions) {
            chatBody.functions = options.functions;
        }
        if (options.function_call) {
            chatBody.function_call = options.function_call;
        }

        // Handle artifact instructions - add as system message at the front
        if (params.options?.options?.artifacts) {
            // Insert system message at the beginning (after any existing system messages)
            const firstNonSystemIndex = chatBody.messages.findIndex(m => m.role !== 'system');
            const insertIndex = firstNonSystemIndex === -1 ? chatBody.messages.length : firstNonSystemIndex;

            chatBody.messages = [
                ...chatBody.messages.slice(0, insertIndex),
                {
                    role: 'system',
                    content: ARTIFACTS_PROMPT
                },
                ...chatBody.messages.slice(insertIndex)
            ];

            logger.info("✅ [Artifacts] Instructions added as system message");
        }

        let result;

        if (responseStream) {
            // Streaming mode - create interceptor stream that captures content and tool calls
            const capturedContent = { fullResponse: '', toolCalls: [], currentToolCall: null };
            const interceptor = createStreamInterceptor(
                responseStream,
                providerConfig.transform,
                providerConfig.usageTransform,
                requestState,
                capturedContent
            );

            // Call provider with appropriate arguments
            if (providerConfig.needsEndpointProvider) {
                // OpenAI needs getLLMConfig as first argument
                result = await providerConfig.chatFn(getLLMConfig, chatBody, interceptor);
            } else {
                // Bedrock and Gemini just need chatBody and stream
                result = await providerConfig.chatFn(chatBody, interceptor);
            }

            // Ensure stream is properly ended (unless keepStreamOpen is set for tool loops)
            if (!responseStream.writableEnded && !keepStreamOpen) {
                endStream(responseStream);
                responseStream.end();
            }
            logger.debug('Streaming LLM call completed: ', requestId);
            logger.debug('LLM contenet: ', capturedContent.fullResponse);
            // Create result object with captured content for conversation analysis
            result = {
                content: capturedContent.fullResponse,
                usage: requestState.totalUsage,
                tool_calls: capturedContent.toolCalls || []
            };

        } else {
            // Non-streaming mode - create a buffer stream to capture response
            let fullContent = '';
            // Create capturedContent object to accumulate tool calls (same as streaming mode)
            const nonStreamCapturedContent = { fullResponse: '', toolCalls: [] };

            const bufferStream = new Writable({
                write(chunk, _encoding, callback) {
                    const text = chunk.toString();

                    // Parse SSE format to extract content
                    const lines = text.split('\n');
                    for (const line of lines) {
                        if (line.startsWith('data: ') && !line.includes('[DONE]')) {
                            try {
                                const data = line.slice(6);
                                const event = JSON.parse(data);

                                // Apply transform to get content - pass capturedContent for tool call accumulation
                                const transformed = providerConfig.transform(event, null, nonStreamCapturedContent);
                                if (transformed) {
                                    if (typeof transformed === 'string') {
                                        fullContent += transformed;
                                        nonStreamCapturedContent.fullResponse += transformed;
                                    } else if (transformed.d) {
                                        fullContent += transformed.d;
                                        nonStreamCapturedContent.fullResponse += transformed.d;
                                    }
                                    // tool_calls are accumulated in nonStreamCapturedContent by transform
                                }

                                // Extract usage
                                const usage = providerConfig.usageTransform(event);
                                if (usage) {
                                    requestState.totalUsage = { ...requestState.totalUsage, ...usage };
                                }
                            } catch (err) {
                                // Not JSON, skip
                            }
                        }
                    }
                    callback();
                }
            });

            // Call provider
            if (providerConfig.needsEndpointProvider) {
                result = await providerConfig.chatFn(getLLMConfig, chatBody, bufferStream);
            } else {
                result = await providerConfig.chatFn(chatBody, bufferStream);
            }

            // Use the extracted content and tool_calls
            logger.debug(`Non-streaming result: fullContent="${fullContent.substring(0, 100)}...", toolCalls=${JSON.stringify(nonStreamCapturedContent.toolCalls)}`);
            result = {
                content: fullContent,
                usage: requestState.totalUsage,
                tool_calls: nonStreamCapturedContent.toolCalls || []
            };
        }

        // Record usage if we have ANY tokens (regular OR cached)
        const promptTokens = requestState.totalUsage.prompt_tokens || 0;
        const completionTokens = requestState.totalUsage.completion_tokens || 0;
        const inputCachedTokens = requestState.totalUsage?.inputCachedTokens || 0;
        const inputWriteCachedTokens = requestState.totalUsage?.inputWriteCachedTokens || 0;
        
        if (promptTokens > 0 || completionTokens > 0 || inputCachedTokens > 0 || inputWriteCachedTokens > 0) {
            await recordUsage(
                params.account,
                requestId,
                model,
                promptTokens,
                completionTokens,
                inputCachedTokens,
                inputWriteCachedTokens,
                { reasoning_tokens: requestState.totalUsage.reasoning_tokens || 0 }
            );
        }

        // Queue conversation analysis
        await queueConversationAnalysisWithFallback(params, messages, result);

        return result || { usage: requestState.totalUsage };

    } catch (error) {
        logger.error(`UnifiedLLM call failed for requestId ${requestId}:`, error);
        logger.error('Error stack:', error.stack);
        
        if (responseStream && !responseStream.writableEnded) {
            try {
                sendErrorMessage(responseStream, error.message || 'LLM call failed');
                endStream(responseStream);
            } catch (streamError) {
                logger.error('Failed to send error to stream:', streamError);
            }
        }
        
        throw error;
    } finally {
        // Cleanup
        if (requestState.statusTimer) {
            clearTimeout(requestState.statusTimer);
        }
        activeRequests.delete(requestId);
    }
}

/**
 * Prompt for structured data using function calling or JSON schema
 */
export async function promptUnifiedLLMForData(
    params,
    messages,
    outputFormat,
    _responseStream = null // Currently unused, kept for API compatibility
) {
    const model = params.options?.model || params.model;

    if (!model) {
        throw new Error('Model not specified');
    }

    // Always include JSON instructions in messages (works with all models/endpoints)
    const jsonPrompt = `
Please respond with a JSON object that matches this schema:
${JSON.stringify(outputFormat, null, 2)}

Respond ONLY with valid JSON, no explanation or markdown.`;

    const finalMessages = [
        ...messages,
        { role: 'system', content: jsonPrompt }
    ];

    // Prepare structured output options based on provider (additional enforcement if supported)
    const provider = model?.provider;
    let structuredOutputOptions = {};

    if (provider === 'Bedrock') {
        structuredOutputOptions.outputConfig = {
            textFormat: {
                type: "json_schema",
                structure: {
                    jsonSchema: {
                        schema: JSON.stringify(outputFormat)
                    }
                }
            }
        };
    } else if (provider === 'Azure' || provider === 'OpenAI' || provider === 'Gemini') {
        structuredOutputOptions.response_format = {
            type: "json_schema",
            json_schema: {
                name: "data_extraction",
                strict: true,
                schema: { ...outputFormat, additionalProperties: false }
            }
        };
    }

    const result = await callUnifiedLLM(params, finalMessages, null, structuredOutputOptions);

    try {
        const content = result.content || '';
        const jsonMatch = content.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
            return JSON.parse(jsonMatch[0]);
        }
        return JSON.parse(content);
    } catch (e) {
        logger.error('Failed to parse JSON response:', e);
        throw new Error('Failed to extract structured data from response');
    }

}

/**
 * Check if request is cancelled (killswitch support)
 */
export function isRequestCancelled(requestId) {
    const state = activeRequests.get(requestId);
    return state?.cancelled || false;
}

/**
 * Cancel a request
 */
export function cancelRequest(requestId) {
    const state = activeRequests.get(requestId);
    if (state) {
        state.cancelled = true;
        if (state.statusTimer) {
            clearTimeout(state.statusTimer);
        }
        if (state.responseStream && !state.responseStream.writableEnded) {
            sendErrorMessage(state.responseStream, 'Request cancelled');
            endStream(state.responseStream);
        }
    }
}

// Export with cleaner naming (no LiteLLM reference)
export const callLLM = callUnifiedLLM;
export const promptLLMForData = promptUnifiedLLMForData;