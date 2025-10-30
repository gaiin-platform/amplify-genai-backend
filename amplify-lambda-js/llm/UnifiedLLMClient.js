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

// Import secrets management
import { getLLMConfig } from '../common/secrets.js';

const logger = getLogger('UnifiedLLMClient');

// Active request tracking
const activeRequests = new Map();

// Model type detection utilities
const isOpenAIModel = (modelId) => {
    return modelId && (modelId.includes("gpt") || /^o\d/.test(modelId));
};

const isGeminiModel = (modelId) => {
    return modelId && modelId.includes("gemini");
};

const isBedrockModel = (modelId) => {
    return modelId && (modelId.includes("claude") || modelId.includes("mistral") || modelId.includes("amazon"));
};

/**
 * Get the appropriate chat function and transformer for a model
 */
const getProviderConfig = (model) => {
    const modelId = model?.id || model;
    
    if (isOpenAIModel(modelId)) {
        return {
            chatFn: openaiChat,
            needsEndpointProvider: true, // OpenAI needs getLLMConfig
            transform: openAiTransform,
            usageTransform: openaiUsageTransform
        };
    } else if (isGeminiModel(modelId)) {
        return {
            chatFn: geminiChat,
            needsEndpointProvider: false,
            transform: geminiTransform,
            usageTransform: geminiUsageTransform
        };
    } else if (isBedrockModel(modelId)) {
        return {
            chatFn: chatBedrock,
            needsEndpointProvider: false,
            transform: bedrockConverseTransform,
            usageTransform: bedrockTokenUsageTransform
        };
    } else {
        throw new Error(`Unsupported model: ${modelId}`);
    }
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
            
            // Check if category analysis should be performed
            const performCategoryAnalysis = !!(params.options?.analysisCategories?.length);
            
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
function createStreamInterceptor(responseStream, transform, usageTransform, requestState) {
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
                            
                            // Transform event
                            const transformed = transform(event, responseStream);
                            if (transformed) {
                                sendDeltaToStream(responseStream, 'answer', transformed);
                            }
                            
                            // Extract usage
                            const usage = usageTransform(event);
                            if (usage) {
                                requestState.totalUsage = { ...requestState.totalUsage, ...usage };
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
                        
                        // Transform event
                        const transformed = transform(event, responseStream);
                        if (transformed) {
                            sendDeltaToStream(responseStream, 'answer', transformed);
                        }
                        
                        // Extract usage
                        const usage = usageTransform(event);
                        if (usage) {
                            requestState.totalUsage = { ...requestState.totalUsage, ...usage };
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
            reasoning_tokens: 0
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
        const chatBody = {
            messages,
            model: model.id || model,
            stream: !!responseStream,
            max_tokens: options.max_tokens || 2000,
            temperature: options.temperature || 1.0,
            ...options,
            options: {
                ...params.options,
                model,
                requestId
            }
        };

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

        let result;

        if (responseStream) {
            // Streaming mode - create interceptor stream
            const interceptor = createStreamInterceptor(
                responseStream,
                providerConfig.transform,
                providerConfig.usageTransform,
                requestState
            );

            // Call provider with appropriate arguments
            if (providerConfig.needsEndpointProvider) {
                // OpenAI needs getLLMConfig as first argument
                result = await providerConfig.chatFn(getLLMConfig, chatBody, interceptor);
            } else {
                // Bedrock and Gemini just need chatBody and stream
                result = await providerConfig.chatFn(chatBody, interceptor);
            }

            // Ensure stream is properly ended
            if (!responseStream.writableEnded) {
                endStream(responseStream);
                responseStream.end();
            }

        } else {
            // Non-streaming mode - create a buffer stream to capture response
            let responseBuffer = '';
            let fullContent = '';
            const bufferStream = new Writable({
                write(chunk, _encoding, callback) {
                    const text = chunk.toString();
                    responseBuffer += text;
                    
                    // Parse SSE format to extract content
                    const lines = text.split('\n');
                    for (const line of lines) {
                        if (line.startsWith('data: ') && !line.includes('[DONE]')) {
                            try {
                                const data = line.slice(6);
                                const event = JSON.parse(data);
                                
                                // Apply transform to get content
                                const transformed = providerConfig.transform(event);
                                if (transformed) {
                                    fullContent += typeof transformed === 'string' ? transformed : (transformed.d || '');
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

            // Use the extracted content
            result = {
                content: fullContent || responseBuffer,  // Fallback to raw buffer if no SSE parsing worked
                usage: requestState.totalUsage
            };
        }

        // Record usage if we have it
        if (requestState.totalUsage.prompt_tokens > 0 || requestState.totalUsage.completion_tokens > 0) {
            await recordUsage(
                params,
                messages,
                model,
                requestState.totalUsage.prompt_tokens,
                requestState.totalUsage.completion_tokens,
                requestState.totalUsage.cached_tokens || 0,
                requestState.totalUsage.reasoning_tokens || 0
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

    // Convert output format to function schema
    const functionSchema = {
        name: 'extract_data',
        description: 'Extract structured data from the response',
        parameters: outputFormat
    };

    // Determine if model supports function calling
    const supportsFunctions = isOpenAIModel(model.id) || isGeminiModel(model.id);
    
    if (supportsFunctions) {
        // Use function calling
        const result = await callUnifiedLLM(
            params,
            messages,
            null, // No streaming for structured data
            {
                tools: [{ type: 'function', function: functionSchema }],
                tool_choice: { type: 'function', function: { name: 'extract_data' } }
            }
        );

        // Parse function call response
        if (result.content) {
            try {
                // Check if response contains tool calls
                const response = typeof result.content === 'string' 
                    ? JSON.parse(result.content) 
                    : result.content;
                    
                if (response.tool_calls?.[0]?.function?.arguments) {
                    return JSON.parse(response.tool_calls[0].function.arguments);
                }
                
                // Sometimes the response is directly in the content
                if (typeof response === 'object' && !response.choices) {
                    return response;
                }
            } catch (e) {
                // Failed to parse function response, trying fallback
            }
        }
    }

    // Fallback: Use JSON schema in prompt
    const jsonPrompt = `
Please respond with a JSON object that matches this schema:
${JSON.stringify(outputFormat, null, 2)}

Respond ONLY with valid JSON, no explanation or markdown.`;

    const enhancedMessages = [
        ...messages,
        { role: 'system', content: jsonPrompt }
    ];

    const result = await callUnifiedLLM(params, enhancedMessages, null);
    
    try {
        const content = result.content || '';
        // Try to extract JSON from response
        const jsonMatch = content.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
            return JSON.parse(jsonMatch[0]);
        }
        return JSON.parse(content);
    } catch (e) {
        logger.error('Failed to parse JSON response:', e);
        // Response was unparseable
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