/**
 * LiteLLM Integration Client for Amplify Lambda JS
 * Manages persistent Python LiteLLM subprocess and request multiplexing
 */

import {sendDeltaToStream, sendStatusEventToStream, sendErrorMessage, sendStateEventToStream, sendToStream} from '../common/streams.js';
import {newStatus, getThinkingMessage} from '../common/status.js';
import {recordUsage} from '../common/accounting.js';
import {getSecret} from '../common/secrets.js';
import {getLogger} from '../common/logging.js';
import {trace} from '../common/trace.js';
import {spawn} from 'child_process';
import {fileURLToPath} from 'url';
import {dirname, join} from 'path';
import {PassThrough} from 'stream';
import {
    includeImageSources,
    convertToolsAndFunctions,
    convertSystemMessages,
    addWebSearchIfNeeded,
    getStatusInterval,
    isReasoningModel
} from './utils.js';

const logger = getLogger("litellmClient");

// Get current file directory for Python script path
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Global Python process management
let globalPythonProcess = null;
let processStartTime = null;
let serverReady = false;
let pendingRequests = []; // Queue for requests while server initializes
let activeRequests = new Map(); // requestId -> {responseStream, startTime, resolve, reject}
let requestCounter = 0;

/**
 * Initialize persistent Python process
 */
export function initPythonProcess() {
    if (globalPythonProcess && !globalPythonProcess.killed) {
        return globalPythonProcess;
    }

    const pythonScriptPath = join(__dirname, 'amplify_litellm.py');
    processStartTime = Date.now();
    
    console.log("[TIMING] Starting persistent Python LiteLLM server");
    
    globalPythonProcess = spawn('python3', [pythonScriptPath], {
        stdio: ['pipe', 'pipe', 'pipe']
    });
    
    const spawnDuration = Date.now() - processStartTime;
    console.log("[TIMING] Python LiteLLM server spawned", {
        spawnDuration,
        pid: globalPythonProcess.pid
    });
    
    let outputBuffer = '';
    
    // Handle stdout data with request multiplexing
    globalPythonProcess.stdout.on('data', (data) => {
        outputBuffer += data.toString();
        
        // Process complete lines
        const lines = outputBuffer.split('\n');
        outputBuffer = lines.pop(); // Keep incomplete line in buffer
        
        lines.forEach(async line => {
            if (line.trim()) {
                await routeMessage(line.trim());
            }
        });
    });
    
    // Handle stderr - output to console for debugging
    globalPythonProcess.stderr.on('data', (data) => {
        const message = data.toString();
        // Output Python debug messages to console
        console.log("[PYTHON]", message.trim());
        
        // Still log errors
        if (message.includes("ERROR") || message.includes("Exception")) {
            logger.error("Python stderr:", message);
        }
    });
    
    // Handle process exit
    globalPythonProcess.on('close', (code) => {
        console.log("[TIMING] Python LiteLLM server exited", { code });
        
        // Reject all active requests
        activeRequests.forEach((request, requestId) => {
            if (!request.responseStream.destroyed && !request.responseStream.writableEnded) {
                sendErrorMessage(request.responseStream, 500, "Python server disconnected");
                request.responseStream.end();
            }
            request.reject(new Error("Python process exited"));
        });
        
        activeRequests.clear();
        globalPythonProcess = null;
        serverReady = false;
    });
    
    globalPythonProcess.on('error', (error) => {
        logger.error("Python process error:", error);
        globalPythonProcess = null;
        serverReady = false;
    });
    
    return globalPythonProcess;
}

/**
 * Route messages from Python subprocess to appropriate stream functions with request multiplexing
 */
async function routeMessage(message) {
    try {
        const parsed = JSON.parse(message);
        const requestId = parsed.requestId;
        
        // Handle server ready signal
        if (parsed.type === 'ready') {
            const readyTime = Date.now();
            const startupDuration = readyTime - processStartTime;
            serverReady = true;
            
            console.log("[TIMING] Python LiteLLM server ready", {
                startupDuration,
                memoryUsage: parsed.data?.memoryUsage,
                queuedRequests: pendingRequests.length
            });
            
            // Process any queued requests
            const queuedRequests = [...pendingRequests];
            pendingRequests = [];
            
            queuedRequests.forEach(queuedRequest => {
                console.log("[TIMING] Processing queued request", { 
                    requestId: queuedRequest.requestId,
                    queueTime: readyTime - queuedRequest.queueTime 
                });
                sendRequestToPython(queuedRequest);
            });
            
            return;
        }
        
        if (!requestId || !activeRequests.has(requestId)) {
            logger.warn("Message for unknown/completed request:", requestId, parsed.type);
            return;
        }
        
        const request = activeRequests.get(requestId);
        const responseStream = request.responseStream;
        
        // Check if stream is still writable
        if (responseStream.destroyed || responseStream.writableEnded) {
            logger.debug("Stream already ended, ignoring message:", parsed.type);
            activeRequests.delete(requestId);
            return;
        }
        
        switch (parsed.type) {
            case 'content':
                // Track first content token timing
                if (!request.firstTokenTime) {
                    request.firstTokenTime = Date.now();
                    const timeToFirstToken = request.firstTokenTime - (request.pythonProcessingStartTime || request.startTime);
                    console.log("[TIMING] First content token from LiteLLM", {
                        requestId,
                        litellmTimeToFirstToken: timeToFirstToken,
                        totalTimeToFirstToken: request.firstTokenTime - request.startTime
                    });
                    
                    // Clear status timer on first real content
                    if (request.statusTimer) {
                        clearTimeout(request.statusTimer);
                        request.statusTimer = null;
                        // Clear any sticky status message
                        sendStatusEventToStream(request.responseStream, newStatus({
                            animated: false,
                            inProgress: false,
                            sticky: false,
                            message: ""
                        }));
                    }
                }
                request.tokenCount++;
                // Accumulate complete response for return value
                request.completeResponse += parsed.data;
                // Only stream to user if requested (for behind-the-scenes calls, don't stream)
                if (request.streamToUser) {
                    sendDeltaToStream(responseStream, 0, parsed.data);
                }
                break;
                
            case 'status':
                // Track when actual LiteLLM processing starts
                if (parsed.data?.summary === 'Calling LLM...' && !request.pythonProcessingStartTime) {
                    request.pythonProcessingStartTime = Date.now();
                    console.log("[TIMING] Python LiteLLM processing started", {
                        requestId,
                        pythonOverheadTime: request.pythonProcessingStartTime - request.startTime
                    });
                }
                // Only send status to user if requested (for behind-the-scenes calls, don't stream)
                if (request.streamToUser) {
                    sendStatusEventToStream(responseStream, newStatus(parsed.data));
                }
                break;
                
            case 'state':
                // Only send state to user if requested (for behind-the-scenes calls, don't stream)
                if (request.streamToUser) {
                    sendStateEventToStream(responseStream, parsed.data);
                }
                break;
                
            case 'usage':
                // Record usage data for billing/accounting
                try {
                    const usageData = parsed.data;
                    const details = {
                        reasoning_tokens: usageData.reasoning_tokens || 0,
                        prompt_tokens_details: usageData.prompt_tokens_details || {},
                        completion_tokens_details: usageData.completion_tokens_details || {}
                    };
                    
                    // Call recordUsage with the same signature as original implementation
                    recordUsage(
                        request.account,           // account object
                        request.requestId,        // request ID  
                        request.model,            // model object
                        usageData.prompt_tokens || 0,      // input tokens
                        usageData.completion_tokens || 0,  // output tokens
                        usageData.cached_tokens || 0,      // cached tokens
                        details                   // additional details
                    ).catch(error => {
                        logger.error("Failed to record usage data:", error);
                    });
                    
                    console.log("[USAGE] Recorded usage data", {
                        requestId,
                        promptTokens: usageData.prompt_tokens,
                        completionTokens: usageData.completion_tokens,
                        cachedTokens: usageData.cached_tokens,
                        reasoningTokens: usageData.reasoning_tokens
                    });
                } catch (error) {
                    logger.error("Failed to record usage data:", error);
                }
                break;
                
            case 'error':
                // Clear status timer on error
                if (request.statusTimer) {
                    clearTimeout(request.statusTimer);
                    request.statusTimer = null;
                }
                sendErrorMessage(responseStream, parsed.data.statusCode, parsed.data.message);
                activeRequests.delete(requestId);
                request.reject(new Error(parsed.data.message));
                break;
                
            case 'end':
                // Clear status timer when request ends
                if (request.statusTimer) {
                    clearTimeout(request.statusTimer);
                    request.statusTimer = null;
                }
                
                // Send end signal to frontend before closing stream
                if (request.streamToUser && !responseStream.destroyed && !responseStream.writableEnded) {
                    sendToStream(responseStream, 0, {type: 'end'});
                }
                
                // Only end if not already ended  
                if (!responseStream.destroyed && !responseStream.writableEnded) {
                    responseStream.end();
                }
                
                // Clean up request and resolve
                const endTime = Date.now();
                const duration = endTime - request.startTime;
                const finalMemory = process.memoryUsage();
                
                // Track when LiteLLM processing ends
                request.pythonProcessingEndTime = endTime;
                
                // Calculate detailed timing breakdown
                const timingBreakdown = {
                    // High-level timings
                    totalRequestTime: duration,
                    
                    // Python processing breakdown
                    pythonOverheadTime: request.pythonProcessingStartTime ? 
                        (request.pythonProcessingStartTime - request.startTime) : null,
                    
                    // Pure LiteLLM performance
                    pureLitellmTime: request.pythonProcessingEndTime && request.pythonProcessingStartTime ? 
                        (request.pythonProcessingEndTime - request.pythonProcessingStartTime) : null,
                    timeToFirstToken: request.firstTokenTime && request.pythonProcessingStartTime ? 
                        (request.firstTokenTime - request.pythonProcessingStartTime) : null,
                    
                    // Token metrics
                    tokenCount: request.tokenCount,
                    avgTokenTime: request.firstTokenTime && request.tokenCount > 1 ? 
                        (request.pythonProcessingEndTime - request.firstTokenTime) / (request.tokenCount - 1) : null,
                        
                    // Efficiency metrics - now showing persistent server efficiency
                    pythonOverheadPercentage: request.pythonProcessingStartTime ? 
                        Math.round(((request.pythonProcessingStartTime - request.startTime) / duration) * 100) : null,
                    litellmPercentage: request.pythonProcessingEndTime && request.pythonProcessingStartTime ? 
                        Math.round(((request.pythonProcessingEndTime - request.pythonProcessingStartTime) / duration) * 100) : null,
                        
                    // Persistent server context
                    serverAge: `${Math.round((endTime - processStartTime) / 1000)}s`,
                    serverSpawnOverheadEliminated: true
                };
                
                console.log("[TIMING] === LITELLM REQUEST COMPLETED ===", {
                    requestId,
                    ...timingBreakdown
                });
                
                console.log("[TIMING] LiteLLM processing completed", {
                    requestId,
                    pureLitellmProcessingTime: timingBreakdown.pureLitellmTime,
                    totalTokens: request.tokenCount
                });
                
                console.log("[MEMORY] Memory usage summary", {
                    requestId,
                    initialRss: `${Math.round(request.initialMemory.rss / 1024 / 1024)}MB`,
                    finalRss: `${Math.round(finalMemory.rss / 1024 / 1024)}MB`,
                    peakIncrease: `${Math.round((finalMemory.rss - request.initialMemory.rss) / 1024 / 1024)}MB`,
                    initialHeap: `${Math.round(request.initialMemory.heapUsed / 1024 / 1024)}MB`,
                    finalHeap: `${Math.round(finalMemory.heapUsed / 1024 / 1024)}MB`,
                    heapChange: `${Math.round((finalMemory.heapUsed - request.initialMemory.heapUsed) / 1024 / 1024)}MB`,
                    serverAge: `${Math.round((endTime - processStartTime) / 1000)}s`,
                    activeRequestCount: activeRequests.size - 1 // -1 because we haven't deleted this one yet
                });
                
                // Add tracing integration
                trace(requestId, ["LLM Response"], {data: request.completeResponse});
                
                // ✅ CONVERSATION TRACKING: Moved from sequentialChat
                // Check if conversation tracking is enabled and this is a direct response to user
                if (request.streamToUser && request.chatRequest?.options?.trackConversations && request.completeResponse) {
                    const { queueConversationAnalysisWithFallback } = await import("../groupassistants/conversationAnalysis.js");
                    const performCategoryAnalysis = !!request.chatRequest.options?.analysisCategories;
                    
                    logger.debug(`Conversation tracking enabled for: ${request.chatRequest.options.conversationId}`);
                    logger.debug(`Category analysis ${performCategoryAnalysis ? 'enabled' : 'disabled'}`);
                    
                    // Queue analysis asynchronously (doesn't block user response)
                    queueConversationAnalysisWithFallback(
                        request.chatRequest,
                        request.completeResponse,
                        request.account,
                        performCategoryAnalysis
                    ).catch(error => {
                        logger.debug('Error queuing conversation analysis:', error);
                    });
                }
                
                activeRequests.delete(requestId);
                request.resolve(request.completeResponse); // Return the complete response
                break;
                
            default:
                logger.warn("Unknown message type from Python:", parsed.type);
        }
    } catch (e) {
        logger.error("Error routing message from Python:", e, "Message:", message);
    }
}

/**
 * Send request data to Python server
 */
function sendRequestToPython(requestData) {
    if (!globalPythonProcess || globalPythonProcess.killed) {
        throw new Error("Python process not available");
    }
    
    const inputLine = JSON.stringify(requestData) + '\n';
    globalPythonProcess.stdin.write(inputLine);
    
    console.log("[TIMING] Request sent to Python server", {
        requestId: requestData.requestId,
        inputDataSize: inputLine.length,
        serverReady,
        activeRequestCount: activeRequests.size
    });
}

/**
 * Call persistent Python LiteLLM server with request multiplexing
 * 
 * @param {Object} chatRequest - The chat request object containing messages and options
 * @param {Object} model - Model configuration object 
 * @param {Object} account - Account object for usage tracking
 * @param {Object} responseStream - Stream to write responses to
 * @param {Array} dataSources - Optional array of data sources to include
 * @returns {Promise} Promise that resolves when request completes
 */
export async function callLiteLLM(chatRequest, model, account, responseStream, dataSources = [], streamToUser = false) {
    return new Promise(async (resolve, reject) => {
        const startTime = Date.now();
        const initialMemory = process.memoryUsage();
        
        // Generate unique request ID
        const requestId = `req_${++requestCounter}_${Date.now()}`;
        
        console.log("=== LITELLM REQUEST STARTED ===", {
            requestId,
            model: model.id,
            provider: model.provider || 'unknown',
            messageCount: chatRequest.messages?.length || 0,
            dataSourceCount: dataSources ? dataSources.length : 0,
            persistentServer: !!globalPythonProcess
        });
        
        console.log("[MEMORY] Node.js memory at request start", {
            requestId,
            rss: `${Math.round(initialMemory.rss / 1024 / 1024)}MB`,
            heapUsed: `${Math.round(initialMemory.heapUsed / 1024 / 1024)}MB`,
            heapTotal: `${Math.round(initialMemory.heapTotal / 1024 / 1024)}MB`,
            external: `${Math.round(initialMemory.external / 1024 / 1024)}MB`
        });
        
        try {
            // Ensure Python process is running
            const pythonProcess = initPythonProcess();
            if (!pythonProcess || pythonProcess.killed) {
                throw new Error("Failed to initialize Python process");
            }
            
            // Resolve secrets based on model provider (NOT CACHED for security)
            const secrets = {};
            
            // Get OpenAI/Azure secrets
            try {
                const secretArn = process.env.SECRETS_ARN_NAME;
                if (secretArn) {
                    const secret = await getSecret(secretArn);
                    const apiKeys = JSON.parse(secret);
                    secrets.openai_key = apiKeys["OPENAI_API_KEY"];
                    secrets.gemini_key = apiKeys["GEMINI_API_KEY"];
                }
            } catch (e) {
                logger.debug("Error fetching API keys:", e);
            }
            
            // Get Azure config if needed
            try {
                if (model.provider && model.provider.includes("Azure")) {
                    const secretName = process.env.LLM_ENDPOINTS_SECRETS_NAME;
                    if (secretName) {
                        const secretData = await getSecret(secretName);
                        const parsedSecret = JSON.parse(secretData);
                        
                        // Map model names
                        let mappedModelName = model.id;
                        if (model.id === "gpt-4-1106-Preview" || model.id === "gpt-4-1106-preview") {
                            mappedModelName = "gpt-4-turbo";
                        } else if (model.id === "gpt-35-1106") {
                            mappedModelName = "gpt-35-turbo";
                        }
                        
                        const endpointData = parsedSecret.models.find(m => m.hasOwnProperty(mappedModelName));
                        if (endpointData) {
                            const endpoints = endpointData[mappedModelName].endpoints;
                            const endpointInfo = endpoints[Math.floor(Math.random() * endpoints.length)];
                            secrets.azure_config = {
                                url: endpointInfo.url,
                                key: endpointInfo.key
                            };
                        }
                    }
                }
            } catch (e) {
                logger.debug("No Azure config available:", e);
            }
            
            // Process chat request with restored functionality
            let processedMessages = [...chatRequest.messages];
            const options = chatRequest.options || {};
            
            // 1. Handle image sources
            if (chatRequest.imageSources && !options.dataSourceOptions?.disableDataSources) {
                processedMessages = await includeImageSources(
                    chatRequest.imageSources,
                    processedMessages,
                    model,
                    responseStream
                );
            }
            
            // 2. Convert system messages for models that don't support them
            processedMessages = convertSystemMessages(processedMessages, model);
            
            // 3. Add system prompt if model has one
            if (model.systemPrompt && processedMessages.length > 0) {
                if (processedMessages[0].role === 'system') {
                    processedMessages[0].content += `\n${model.systemPrompt}`;
                } else {
                    processedMessages.unshift({
                        role: 'system',
                        content: model.systemPrompt
                    });
                }
            }
            
            // 4. Convert tools and functions to modern format
            const toolConversions = convertToolsAndFunctions(options);
            
            // 5. Add web search tool if URL detected (OpenAI only)
            const tools = addWebSearchIfNeeded(
                processedMessages,
                model,
                toolConversions.tools || []
            );
            
            // 6. Set up status message timer for long-running requests
            let statusTimer = null;
            const statusInterval = getStatusInterval(model);
            
            const sendStatusMessage = () => {
                const statusInfo = newStatus({
                    animated: true,
                    inProgress: true,
                    sticky: true,
                    message: getThinkingMessage ? getThinkingMessage() : "Thinking..."
                });
                // Only send status if stream is still writable
                if (responseStream && !responseStream.destroyed && responseStream.writable) {
                    sendStatusEventToStream(responseStream, statusInfo);
                } else {
                    // Stream is closed, clear the timer
                    if (statusTimer) {
                        clearTimeout(statusTimer);
                        statusTimer = null;
                    }
                    return;
                }
                
                // Schedule next status message
                statusTimer = setTimeout(sendStatusMessage, statusInterval);
            };
            
            // Start status timer
            statusTimer = setTimeout(sendStatusMessage, statusInterval);
            
            // Prepare enhanced chat request
            const enhancedChatRequest = {
                ...chatRequest,
                messages: processedMessages,
                tools: tools.length > 0 ? tools : undefined,
                tool_choice: toolConversions.tool_choice,
                // Handle o-models and reasoning models
                ...(isReasoningModel(model.id) && {
                    max_completion_tokens: model.outputTokenLimit,
                    reasoning_effort: options.reasoningLevel || 'low'
                })
            };
            
            // Clean up undefined fields
            Object.keys(enhancedChatRequest).forEach(key => {
                if (enhancedChatRequest[key] === undefined) {
                    delete enhancedChatRequest[key];
                }
            });
            
            // Prepare input data for Python
            const inputData = {
                requestId,
                chatRequest: enhancedChatRequest,
                model, 
                account,
                secrets,
                dataSources
                // statusTimer removed - only tracked locally, not sent to Python
            };
            
            // Register this request
            activeRequests.set(requestId, {
                responseStream,
                startTime,
                initialMemory,
                resolve,
                reject,
                firstTokenTime: null,
                tokenCount: 0,
                pythonProcessingStartTime: null,
                pythonProcessingEndTime: null,
                completeResponse: '', // Track complete response for return value
                streamToUser, // Control whether to stream to user or just accumulate
                chatRequest: enhancedChatRequest, // Store enhanced request for conversation tracking
                // Store context for usage recording
                account,
                model,
                requestId,
                statusTimer // Track timer for cleanup
            });
            
            // Send request to persistent Python server or queue if not ready
            if (serverReady) {
                sendRequestToPython(inputData);
            } else {
                console.log("[TIMING] Server not ready, queuing request", {
                    requestId,
                    serverAge: Date.now() - processStartTime,
                    queuedRequests: pendingRequests.length
                });
                
                pendingRequests.push({
                    ...inputData,
                    queueTime: Date.now()
                });
            }

        } catch (error) {
            const errorDuration = Date.now() - startTime;
            console.log("=== LITELLM REQUEST SETUP FAILED ===", {
                requestId,
                model: model.id,
                setupDuration: errorDuration,
                error: error.message
            });
            
            // Clean up if registered
            activeRequests.delete(requestId);
            sendErrorMessage(responseStream, 500, `Setup error: ${error.message}`);
            reject(error);
        }
    });
}

/**
 * Get status of the LiteLLM server
 */
export function getLiteLLMServerStatus() {
    return {
        ready: serverReady,
        processRunning: !!(globalPythonProcess && !globalPythonProcess.killed),
        activeRequests: activeRequests.size,
        pendingRequests: pendingRequests.length,
        serverAge: processStartTime ? Date.now() - processStartTime : 0,
        processId: globalPythonProcess?.pid || null
    };
}

/**
 * Shutdown the LiteLLM server gracefully
 */
export function shutdownLiteLLMServer() {
    if (globalPythonProcess && !globalPythonProcess.killed) {
        console.log("[TIMING] Shutting down LiteLLM server gracefully");
        globalPythonProcess.stdin.end();
        globalPythonProcess.kill('SIGTERM');
        
        // Force kill after timeout
        setTimeout(() => {
            if (globalPythonProcess && !globalPythonProcess.killed) {
                console.log("[TIMING] Force killing LiteLLM server");
                globalPythonProcess.kill('SIGKILL');
            }
        }, 5000);
    }
}

/**
 * Prompt LiteLLM for structured data using function calling
 * Used for behind-the-scenes LLM calls that need structured output without streaming to user
 * @param {Array} messages - Array of chat messages  
 * @param {Object} model - Model configuration object
 * @param {string} prompt - Additional prompt text
 * @param {Object} schema - Expected response schema for structured output
 * @param {Object} account - Account object for usage tracking
 * @param {string} requestId - Request ID for tracking
 * @param {Object} options - Additional options (maxTokens, temperature, etc.)
 * @returns {Promise<Object>} Promise that resolves to structured response data
 */
export async function promptLiteLLMForData(messages, model, prompt, schema, account, requestId, options = {}) {
    try {
        // Create a dummy stream for non-streaming calls
        const dummyStream = new PassThrough();
        
        // Build the complete message array with the additional prompt
        const fullMessages = [
            ...messages,
            {
                role: "user",
                content: prompt
            }
        ];
        
        // Create chat request with optional function calling for structured output
        const chatRequest = {
            messages: fullMessages,
            max_tokens: options.maxTokens || 1000,
            temperature: options.temperature || 0.1,
            options: {
                model
            }
        };

        // Only add function calling if schema is provided
        if (schema) {
            chatRequest.options.functions = [{
                name: 'structured_response',
                description: 'Provide a structured response based on the schema',
                parameters: {
                    type: 'object',
                    properties: schema,
                    required: Object.keys(schema)
                }
            }];
            chatRequest.options.function_call = { name: 'structured_response' };
        }
        
        // Call LiteLLM with streamToUser=false for behind-the-scenes processing
        const completeResponse = await callLiteLLM(
            chatRequest, 
            model, 
            account, 
            dummyStream, 
            [], // no dataSources for direct calls
            false // streamToUser=false (behind the scenes)
        );
        
        // Handle response based on whether schema was provided
        
        try {
            if (schema) {
                // Parse the function call response to extract structured data
                const functionCallMatch = completeResponse.match(/```json\n(.*?)\n```/s) || 
                                        completeResponse.match(/"arguments":\s*"([^"]*)"/) ||
                                        completeResponse.match(/\{.*\}/s);
                
                if (functionCallMatch) {
                    let jsonStr = functionCallMatch[1] || functionCallMatch[0];
                    // Handle escaped JSON strings
                    if (typeof jsonStr === 'string' && jsonStr.includes('\\"')) {
                        jsonStr = jsonStr.replace(/\\"/g, '"');
                    }
                    const parsedData = JSON.parse(jsonStr);
                    return parsedData;
                } else {
                    // Fallback: try to parse the entire response as JSON
                    const parsedData = JSON.parse(completeResponse);
                    return parsedData;
                }
            } else {
                // No schema provided - return response as string or try to parse as JSON
                try {
                    // Try to parse as JSON first in case it's structured anyway
                    return JSON.parse(completeResponse);
                } catch {
                    // Return as plain string if not valid JSON
                    return completeResponse;
                }
            }
        } catch (parseError) {
            logger.warn("Failed to parse structured response, returning raw text", { 
                parseError: parseError.message,
                response: completeResponse.substring(0, 200) 
            });
            
            // Fallback: create a response object with the raw text
            const fallbackResponse = {};
            const schemaKeys = Object.keys(schema);
            if (schemaKeys.length > 0) {
                fallbackResponse[schemaKeys[0]] = completeResponse;
                // Add thought field if it doesn't exist
                if (!fallbackResponse.thought && schemaKeys.includes('thought')) {
                    fallbackResponse.thought = completeResponse;
                }
            }
            return fallbackResponse;
        }
        
    } catch (error) {
        logger.error("Error in promptLiteLLMForData", error);
        throw error;
    }
}