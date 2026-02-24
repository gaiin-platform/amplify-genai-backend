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

// Import context overflow handling
import { handleContextOverflow, detectContextOverflow, buildMessagesWithHistoricalContext, extractHistoricalContext, calculateBudgets, calculateHistoricalContextStructure } from './contextOverflow.js';

// Import cache for proactive overflow prevention
import { CacheManager } from '../common/cache.js';

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

    // ⚡ PROACTIVE OVERFLOW PREVENTION: Check if we know this conversation overflows
    // Cache tells us WHERE the historical cutoff is - but we ALWAYS do fresh query-aware extraction
    // because each user question needs different relevant context
    const smartMessagesFiltered = options.smartMessagesFiltered === true;
    const conversationId = options.conversationId || params.options?.conversationId;
    let finalMessages = messages;
    let proactiveExtractionNeeded = false;
    let cachedHistoricalEndIndex = null;

    if (!smartMessagesFiltered && conversationId && messages.length >= 20 && !options._isInternalCall) {
        const userId = params.account?.user || params.user || 'unknown';
        const modelId = model?.id || model?.name || 'unknown';
        const cached = await CacheManager.getCachedHistoricalContext(userId, conversationId, modelId);

        if (cached && messages.length >= cached.messageCount) {
            // Cache tells us this conversation has overflowed before
            // We know the historical cutoff point - trigger proactive extraction
            logger.info(`[PROACTIVE] Cache HIT: Conversation ${conversationId} previously overflowed at index ${cached.historicalEndIndex} (model: ${modelId})`);
            proactiveExtractionNeeded = true;
            cachedHistoricalEndIndex = cached.historicalEndIndex;
        }
    } else if (smartMessagesFiltered && conversationId) {
        logger.debug('Skipping proactive cache check (smart messages filtered - cache unsafe)');
    }

    // ⚡ PROACTIVE EXTRACTION: If cache tells us this conversation overflows, do fresh extraction NOW
    // This is QUERY-AWARE - we ALWAYS re-extract based on the current user question
    if (proactiveExtractionNeeded && cachedHistoricalEndIndex !== null) {
        logger.info(`[PROACTIVE] ========== EXTRACTION TRIGGERED ==========`);
        logger.info(`[PROACTIVE] Cache indicated overflow at index ${cachedHistoricalEndIndex}`);

        // Send status event to user so they know something is happening
        if (responseStream && !responseStream.writableEnded) {
            sendStatusEventToStream(responseStream, newStatus({
                id: 'context-extraction',
                summary: 'Analyzing conversation context...',
                message: 'Analyzing conversation context...',
                inProgress: true,
                animated: true
            }));
        }

        // Calculate budgets for extraction FIRST - we need this to determine the split
        const budgets = calculateBudgets(model, options.max_tokens);

        // IMPORTANT: Recalculate the split based on CURRENT messages, not cached index
        // The cached index tells us overflow happened, but new messages may have been added
        const structure = calculateHistoricalContextStructure(messages, budgets);

        const historicalMessages = structure.historicalMessages;
        const intactMessages = structure.intactMessages;
        const currentQuestion = messages[messages.length - 1]?.content || '';

        logger.info(`[PROACTIVE] Message split: ${historicalMessages.length} historical, ${intactMessages.length} intact (intactBudget: ${budgets.intactBudget})`);
        logger.debug(`Historical tokens: ${structure.historicalTokens}, Intact tokens: ${structure.intactTokens}`);

        const cheapestModel = params.options?.cheapestModel || options.cheapestModel || model;

        // Create internal LLM call function for extraction (non-streaming)
        // extractHistoricalContext calls: llmCallFn(params, messages, model, options)
        // We need to adapt to callUnifiedLLM(params, messages, responseStream, options)
        const internalLLMCallFn = async (p, msgs, m, opts) => {
            // m is the model passed from extraction - set it in params.options
            const paramsWithModel = {
                ...p,
                options: {
                    ...p.options,
                    model: m
                }
            };
            // Non-streaming call for extraction - pass null for responseStream
            return await callUnifiedLLM(paramsWithModel, msgs, null, {
                ...opts,
                _isInternalCall: true
            });
        };

        try {
            const extractionOptions = {
                ...options,
                totalMessageCount: messages.length,
                historicalBudget: budgets.historicalBudget,
                cheapestModel,
                conversationId,
                smartMessagesFiltered
            };

            const extractionResult = await extractHistoricalContext(
                params,
                historicalMessages,
                currentQuestion,
                cheapestModel,
                internalLLMCallFn,
                extractionOptions
            );

            if (extractionResult.extractedContext) {
                finalMessages = buildMessagesWithHistoricalContext(
                    intactMessages,
                    extractionResult.extractedContext
                );
                logger.info(`[PROACTIVE] Extraction complete: ${extractionResult.extractedContext.length} chars from historical`);
                logger.info(`[PROACTIVE] Final messages: ${finalMessages.length} total`);
                finalMessages.forEach((msg, idx) => {
                    logger.info(`[PROACTIVE]   [${idx}] ${msg.role}: ${msg.content?.length || 0} chars`);
                });
                logger.info(`[PROACTIVE] ========== PROCEEDING WITH CALL ==========`);
            } else {
                logger.info('[PROACTIVE] Extraction returned no content, using intact messages only');
                finalMessages = intactMessages;
            }
        } catch (error) {
            logger.error(`[PROACTIVE] Extraction failed: ${error.message}, falling back to original messages`);
            // Fall back to original messages - let overflow handler deal with it if needed
        } finally {
            // Clear the status event
            if (responseStream && !responseStream.writableEnded) {
                sendStatusEventToStream(responseStream, newStatus({
                    id: 'context-extraction',
                    inProgress: false
                }));
            }
        }
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
        const {
            keepStreamOpen,
            _contexts,
            _isInternalCall,
            smartMessagesFiltered,  // Internal: cache safety flag
            conversationId,         // Internal: cache key
            disableReasoning,       // Internal: controls extended thinking (Bedrock only)
            ...providerOptions
        } = options;

        const chatBody = {
            messages: finalMessages,
            model: model.id || model,
            stream: !!responseStream,
            max_tokens: providerOptions.max_tokens || 2000,
            temperature: providerOptions.temperature || 1.0,
            ...providerOptions,
            options: {
                ...params.options,
                model,
                requestId,
                user: params?.account?.user || params.user || "unknown",
                disableReasoning
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
        // Check if this is a recoverable context overflow error
        const overflowInfo = error.overflowInfo || detectContextOverflow(error);

        if (overflowInfo.isOverflow && !error.criticalErrorLogged) {
            // Attempt overflow recovery
            logger.info(`Overflow detected, attempting recovery for requestId ${requestId}`);

            try {
                // Get contexts from options if available (passed from chatWithData)
                const contexts = options._contexts || null;

                const recovery = await handleContextOverflow({
                    error,
                    params,
                    messages,  // Use ORIGINAL messages for recovery (not finalMessages)
                    contexts,
                    model,
                    responseStream,
                    cacheOptions: {},  // Cache options not available at this layer
                    llmCallFn: callUnifiedLLM,  // Recursive call for retry
                    internalLLMCallFn: async (p, msgs, mdl, opts) => {
                        // Non-streaming internal call for context extraction
                        // mdl is the model passed from extraction - set it in params.options
                        const paramsWithModel = {
                            ...p,
                            options: {
                                ...p.options,
                                model: mdl
                            }
                        };
                        return callUnifiedLLM(paramsWithModel, msgs, null, { ...opts, _isInternalCall: true });
                    },
                    llmOptions: {
                        ...options,
                        conversationId,  // Pass conversationId for cache updates
                        smartMessagesFiltered  // Pass for cache safety check
                    }
                });

                if (recovery.success) {
                    logger.info(`Context overflow recovery succeeded for requestId ${requestId}`);
                    return recovery.result;
                }
            } catch (recoveryError) {
                logger.error(`Context overflow recovery failed for requestId ${requestId}:`, recoveryError);
                // Fall through to original error handling
            }
        }

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
    // Use strong, explicit language to prevent conversational responses
    const jsonPrompt = `
CRITICAL INSTRUCTION: Ignore all previous conversational context. You MUST respond with ONLY a JSON object.

Required JSON schema:
${JSON.stringify(outputFormat, null, 2)}

RULES:
- Your response must be ONLY valid JSON matching this exact schema
- Do NOT provide explanations, commentary, or conversational text
- Do NOT respond to previous messages in the conversation
- Do NOT use markdown code blocks
- Output ONLY the raw JSON object`;

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

    let result;
    let usedStructuredOutput = false;
    let retried = false;

    // Try with structured output first (if supported by provider)
    if (Object.keys(structuredOutputOptions).length > 0) {
        try {
            usedStructuredOutput = true;
            result = await callUnifiedLLM(params, finalMessages, null, structuredOutputOptions);

            // Check if response is actually JSON (some models ignore structured output config)
            const content = result.content || '';
            const jsonMatch = content.match(/\{[\s\S]*\}/);
            const testContent = jsonMatch ? jsonMatch[0] : content;

            try {
                JSON.parse(testContent);
                // Valid JSON, continue
            } catch (jsonTestError) {
                // Structured output returned non-JSON, retry without flag
                logger.warn(`Structured output returned non-JSON for ${provider}, retrying without structured output flag. Preview: ${content.substring(0, 100)}...`);
                usedStructuredOutput = false;
                retried = true;
                result = await callUnifiedLLM(params, finalMessages, null, {});
            }
        } catch (structuredError) {
            logger.warn(`Structured output call failed for ${provider}, retrying without structured output flag:`, structuredError.message);
            usedStructuredOutput = false;
            retried = true;
            // Fallback: retry without structured output
            result = await callUnifiedLLM(params, finalMessages, null, {});
        }
    } else {
        // No structured output support, call directly
        result = await callUnifiedLLM(params, finalMessages, null, {});
    }

    // Parse JSON response
    try {
        const content = result.content || '';
        const jsonMatch = content.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
            return JSON.parse(jsonMatch[0]);
        }
        return JSON.parse(content);
    } catch (parseError) {
        logger.error('Failed to parse JSON response:', parseError);
        logger.error('Raw response content:', result.content);
        logger.error('Used structured output:', usedStructuredOutput);
        logger.error('Retried without structured output:', retried);
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