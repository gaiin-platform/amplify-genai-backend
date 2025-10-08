//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {chooseAssistantForRequest} from "./assistants/assistants.js";
import {getLogger} from "./common/logging.js";
import {LLM} from "./common/llm.js";
import {getChatFn, ModelTypes, getModelByType} from "./common/params.js"
import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js";
import {sendStateEventToStream, sendDeltaToStream, sendStatusEventToStream, sendErrorMessage, TraceStream} from "./common/streams.js";
import {resolveDataSources} from "./datasource/datasources.js";
import {handleDatasourceRequest} from "./datasource/datasourceEndpoint.js";
import {saveTrace, trace} from "./common/trace.js";
import {recordUsage} from "./common/accounting.js";
import {isRateLimited, formatRateLimit, formatCurrentSpent} from "./rateLimit/rateLimiter.js";
import {getUserAvailableModels} from "./models/models.js";
import {getLLMConfig, getSecretApiKey} from "./common/secrets.js";
import {newStatus} from "./common/status.js";
import {spawn} from 'child_process';
import {fileURLToPath} from 'url';
import {dirname, join} from 'path';
import AWSXRay from "aws-xray-sdk";


const doTrace = process.env.TRACING_ENABLED === 'true';

const logger = getLogger("router");

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
function initPythonProcess() {
    if (globalPythonProcess && !globalPythonProcess.killed) {
        return globalPythonProcess;
    }

    const pythonScriptPath = join(__dirname, 'common', 'amplify_litellm.py');
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
        
        lines.forEach(line => {
            if (line.trim()) {
                routeMessage(line.trim());
            }
        });
    });
    
    // Handle stderr
    globalPythonProcess.stderr.on('data', (data) => {
        logger.error("Python stderr:", data.toString());
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
function routeMessage(message) {
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
                }
                request.tokenCount++;
                sendDeltaToStream(responseStream, "answer", parsed.data);
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
                sendStatusEventToStream(responseStream, newStatus(parsed.data));
                break;
                
            case 'state':
                sendStateEventToStream(responseStream, parsed.data);
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
                sendErrorMessage(responseStream, parsed.data.statusCode, parsed.data.message);
                break;
                
            case 'end':
                // Only end if not already ended
                if (!responseStream.destroyed && !responseStream.writableEnded) {
                    responseStream.write('data: {"s":"result","type":"end"}\n\n');
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
                
                activeRequests.delete(requestId);
                request.resolve();
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
 */
async function callLiteLLM(chatRequest, model, account, responseStream, dataSources = []) {
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
            
            // Resolve secrets based on model provider
            const secrets = {};
            
            // Get OpenAI/Azure secrets
            try {
                secrets.openai_key = await getSecretApiKey("OPENAI_API_KEY");
            } catch (e) {
                logger.debug("No OpenAI key available");
            }
            
            // Get Azure config if needed
            try {
                if (model.provider && model.provider.includes("Azure")) {
                    secrets.azure_config = await getLLMConfig(model.id, model.provider);
                }
            } catch (e) {
                logger.debug("No Azure config available");
            }
            
            // Get Gemini key
            try {
                secrets.gemini_key = await getSecretApiKey("GEMINI_API_KEY");
            } catch (e) {
                logger.debug("No Gemini key available");
            }
            
            // Prepare input data for Python
            const inputData = {
                requestId,
                chatRequest,
                model, 
                account,
                secrets,
                dataSources
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
                // Store context for usage recording
                account,
                model,
                requestId
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

function getRequestId(params) {
    return (params.body.options && params.body.options.requestId) || params.user;
}

export const routeRequest = async (params, returnResponse, responseStream) => {
    const segment = AWSXRay.getSegment();
    const subSegment = segment.addNewSubsegment('chat-js.router.routeRequest');
    
    const routeRequestId = `route_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    console.log("[DEBUG] routeRequest called", {
        routeRequestId,
        timestamp: new Date().toISOString(),
        hasParams: !!params,
        hasBody: !!(params?.body),
        hasMessages: !!(params?.body?.messages),
        userAgent: params?.headers?.['user-agent'] || 'unknown',
        requestId: params?.body?.options?.requestId || 'none'
    });

    try {

        logger.debug("Extracting params from event");
        if (params && params.statusCode) {
            returnResponse(responseStream, params);
        } else if (!params || !params.body || (!params.body.messages && !params.body.killSwitch && !params.body.datasourceRequest)) {
            logger.info("Invalid request body", params.body);

            returnResponse(responseStream, {
                statusCode: 400,
                body: {error: "Invalid request body"}
            });
        } else if (params && !params.user) {
            logger.info("No user found, returning 401");
            returnResponse(responseStream, {
                statusCode: 401,
                body: {error: "Unauthorized"}
            });
        } else if(params.body.killSwitch) {
            try {
                const {requestId, value} = params.body.killSwitch;

                if (!requestId) {
                    return returnResponse(responseStream, {
                        statusCode: 400,
                        body: {error: "No requestId provided for killswitch request"}
                    });
                }

                await updateKillswitch(params.user, requestId, value);
                returnResponse(responseStream, {
                    statusCode: 200,
                    body: {status: "OK"}
                });
            } catch (e) {
                return returnResponse(responseStream, {
                    statusCode: 400,
                    body: {error: "Invalid killswitch request"}
                });
            }
        } else if(params.body.datasourceRequest) {
            // Handle datasource request
            logger.info("Processing datasource request");
            const response = await handleDatasourceRequest(params, params.body.datasourceRequest);
            returnResponse(responseStream, response);
        } else if (await isRateLimited(params)) {
            const rateLimitInfo = params.body.options.rateLimit;
            let errorMessage = "Request limit reached."
            if (rateLimitInfo) {
                const currentRate = rateLimitInfo.currentSpent ? `Current Spent: ${formatCurrentSpent(rateLimitInfo)}` : "";
                const rateLimitStr = `${rateLimitInfo.adminSet ? "Amplify " : ""}Set Rate limit: ${formatRateLimit(rateLimitInfo)}`;
                errorMessage = `${errorMessage} ${currentRate} ${rateLimitStr}`;
            }
            returnResponse(responseStream, {
                statusCode: 429,
                statusText: "Request limit reached. Please try again in a few minutes.",
                body: {error: errorMessage}
            });

        } else {
            const user_model_data = await getUserAvailableModels(params.accessToken);
            const models = user_model_data.models;
            if (!models) {
                    returnResponse(responseStream, {
                    statusCode: 400,
                    body: {error: "No user models."}
                });
            }


            logger.debug("Processing request");
                                                                        
            let options = params.body.options ? {...params.body.options} : {};

            // Calculate numberPrompts and set it in both places
            const calculatedPrompts = params.body.messages ? Math.ceil(params.body.messages.length / 2) : 0;
            params.body.options.numberPrompts = calculatedPrompts;
            options.numberPrompts = calculatedPrompts; // Set it in the new options object too
            
            const modelId = (options.model && options.model.id);

            const model = models[modelId];

            if (!model) {
                returnResponse(responseStream, {
                    statusCode: 400,
                    body: {error: "Invalid model."}
                });
            }

            // override model in params/options so its from our backend end 
            params.model = model;
            options.model = model;

            //default to user model in case there is no defined cheapest or advanced models 
            params.cheapestModel = user_model_data.cheapest ?? model;
            params.advancedModel = user_model_data.advanced ?? model;
            params.documentCachingModel = user_model_data.documentCaching ?? model;

            options.cheapestModel = getModelByType(params, ModelTypes.CHEAPEST);
            options.advancedModel = getModelByType(params, ModelTypes.ADVANCED);
            options.documentCachingModel = getModelByType(params, ModelTypes.DOCUMENT_CACHING);

            // ensure the model id in the body and options is consitent with the changes 
            let body = {...params.body, options: options, model: model.id}; 
            logger.debug("Checking access on data sources");
            let dataSources = [...params.body.dataSources];
            logger.info("Request options.", options);

            delete body.dataSources;

            logger.debug("Determining chatFn");
            const chatFn = async (body, writable, context) => {
                return await getChatFn(model, body, writable, context);
            }

            if (!params.body.dataSources) {
                params.body.dataSources = [];
            }

            try {
                logger.info("Request data sources", dataSources);
                dataSources = await resolveDataSources(params, body, dataSources);

                for (const ds of [...dataSources, ...(body.imageSources ?? [])]) {
                    console.debug("Resolved data source: ", ds.id, "\n". ds);
                }

            } catch (e) {
                logger.error("Unauthorized access on data sources: " + e);
                return returnResponse(responseStream, {
                    statusCode: 401,
                    body: {error: "Unauthorized data source access."}
                });
            }

            if (doTrace) {
                responseStream = new TraceStream({}, responseStream);
            }

            logger.debug("Calling chat with data");

            const requestId = getRequestId(params);

            const assistantParams = {
                account: {
                    user: params.user,
                    accessToken: params.accessToken,
                    accountId: options.accountId,
                    apiKeyId: params.apiKeyId
                },
                model,
                requestId,
                options
            };


            const initSegment = segment.addNewSubsegment('chat-js.router.init');
            await createRequestState(params.user, requestId);

            // Replace LLM/Assistant system with Python LiteLLM
            sendStateEventToStream(responseStream, {assistant: "LiteLLM"});
            initSegment.close();

            const chatSegment = segment.addNewSubsegment('chat-js.router.litellm');
            
            console.log("[DEBUG] About to call callLiteLLM", {
                routeRequestId,
                timestamp: new Date().toISOString(),
                modelId: model?.id,
                bodyMessageCount: body?.messages?.length,
                dataSourceCount: dataSources?.length,
                messages: body?.messages?.map(msg => ({
                    role: msg.role,
                    content: typeof msg.content === 'string' ? 
                        msg.content.substring(0, 200) + (msg.content.length > 200 ? '...' : '') :
                        '[complex_content]'
                })) || [],
                stackTrace: new Error().stack?.split('\n').slice(0, 5)
            });
            
            // Call Python LiteLLM subprocess with resolved dataSources
            await callLiteLLM(body, model, assistantParams.account, responseStream, dataSources);
            
            console.log("[DEBUG] callLiteLLM completed", {
                routeRequestId,
                timestamp: new Date().toISOString()
            });
            
            chatSegment.close();
            
            // No response object returned from streaming
            

            if(doTrace) {
                trace(requestId, ["response"], {stream: responseStream.trace})
                await saveTrace(params.user, requestId);
            }

            // Response is handled via streaming from Python subprocess 

        }
    } catch (e) {
        console.error("Error processing request: " + e);
        console.error(e);

        returnResponse(responseStream, {
            statusCode: 400,
            body: {error: e.message}
        });
    } finally {
        subSegment.close();
    }
}


