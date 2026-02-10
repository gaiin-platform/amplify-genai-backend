//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {getLogger} from "./common/logging.js";
import {ModelTypes, getModelByType} from "./common/params.js"
import {createRequestState, deleteRequestState, updateKillswitch, localKill} from "./requests/requestState.js";
import {sendStateEventToStream, TraceStream, sendStatusEventToStream, forceFlush} from "./common/streams.js";
import {resolveDataSources, getDataSourcesByUse} from "./datasource/datasources.js";
import {handleDatasourceRequest} from "./datasource/datasourceEndpoint.js";
import {saveTrace, trace} from "./common/trace.js";
import {isRateLimited, formatRateLimit, formatCurrentSpent, recordErrorViolation} from "./rateLimit/rateLimiter.js";
import {getUserAvailableModels} from "./models/models.js";
// Removed AWS X-Ray for performance optimization
import {requiredEnvVars, DynamoDBOperation, S3Operation, SecretsManagerOperation, SQSOperation} from "./common/envVarsTracking.js";
import {logCriticalError} from "./common/criticalLogger.js";
import {CacheManager} from "./common/cache.js";
// Native LLM integration - use UnifiedLLMClient for all LLM calls
import {chooseAssistantForRequest} from "./assistants/assistants.js";
import {processSmartMessages} from "./common/conversations.js";
import {newStatus} from "./common/status.js";
// ⚡ COMPREHENSIVE PARALLEL SETUP OPTIMIZATION

// 🛡️ DEFENSIVE ROUTING
import { 
    validateModelConfiguration, 
    validateRequestBody,
    withCostMonitoring
} from "./common/defensiveRouting.js";


const doTrace = false; // Enable or disable tracing

const logger = getLogger("router");


function getRequestId(params) {
    return (params.body.options && params.body.options.requestId) || params.user;
}

const routeRequestCore = async (params, returnResponse, responseStream) => {
    // 🚀 NATIVE JS PROVIDERS: No Python process needed - direct JS execution

    // Check if running locally
    const isLocal = process.env.LOCAL_DEVELOPMENT === 'true';

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
                if (value) localKill(params.user, requestId);
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
        } else {
            // 🛡️ DEFENSIVE VALIDATION
            try {
                validateRequestBody(params.body);
            } catch (error) {
                logger.error("Request validation failed:", error.message);
                return returnResponse(responseStream, {
                    statusCode: error.statusCode || 400,
                    body: { error: error.message }
                });
            }
            
            // ⚡ COMPREHENSIVE PARALLEL SETUP OPTIMIZATION - All router operations in parallel!
            logger.info("🚀 Starting comprehensive parallel router setup...");
            const parallelStartTime = Date.now();
            
            const [
                rateLimitResult,
                userModelData,
                resolvedDataSources,
                requestId,
                preResolvedDataSourcesByUse
            ] = await Promise.all([
                // 1. Rate limiting check
                isRateLimited(params),
                
                // 2. Get user available models (with caching)
                (async () => {
                    let cached = await CacheManager.getCachedUserModels(params.user, params.accessToken);
                    if (!cached) {
                        logger.debug("Cache miss for user models, fetching from API");
                        cached = await getUserAvailableModels(params.accessToken);
                        CacheManager.setCachedUserModels(params.user, params.accessToken, cached);
                    } else {
                        logger.debug("Cache hit for user models");
                    }
                    return cached;
                })(),
                
                // 3. Resolve data sources (with caching and translate hashes)
                (async () => {
                    const dataSources = [...(params.body.dataSources || [])];
                    
                    
                    if (dataSources.length === 0) return [];
                    
                    const dataSourceIds = dataSources.map(ds => ds.id || ds);
                    let cached = await CacheManager.getCachedDataSources(params.user, dataSourceIds, params.body.options);
                    if (!cached) {
                        logger.debug("Cache miss for data sources, resolving with optimization");
                        cached = await resolveDataSources(params, params.body, dataSources);
                        if (cached.length > 0) {
                            CacheManager.setCachedDataSources(params.user, dataSourceIds, cached, params.body.options);
                        }
                    } else {
                        logger.debug("Cache hit for data sources resolution");
                    }
                    return cached;
                })(),
                
                // 4. Generate request ID
                Promise.resolve(getRequestId(params)),
                
                // 5. 🚀 PERFORMANCE: Pre-resolve data sources for smart routing
                (async () => {
                    try {
                        // Only resolve if we have potential data sources or conversation context
                        if ((params.body.dataSources && params.body.dataSources.length > 0) || 
                            params.body.options?.conversationId) {
                            logger.debug("🔍 ROUTER: Pre-resolving data sources for smart routing");
                            // First resolve data sources (including image extraction)
                            const resolvedSources = await resolveDataSources(params, params.body, params.body.dataSources || []);
                            // Then categorize them by use  
                            const resolved = await getDataSourcesByUse(params, params.body, resolvedSources);
                            return resolved;
                        }
                        return null;
                    } catch (error) {
                        // 🚨 CRITICAL: Permission errors should kill the entire request
                        if (error.message?.includes("Unauthorized") || error.message?.includes("permission") || error.message?.includes("access")) {
                            logger.error("🚨 ROUTER: Permission/access error - terminating request:", error.message);
                            throw error; // Re-throw permission errors to kill the request
                        }
                        
                        logger.warn("⚠️ ROUTER: Data source pre-resolution failed, will fallback:", error.message);
                        return null;
                    }
                })(),
                
            ]);
            
            logger.info(`⚡ Parallel setup completed in ${Date.now() - parallelStartTime}ms`);
            
            // 🚀 NATIVE JS PROVIDERS: Direct execution without Python subprocess
            logger.info(`✅ Using native JS providers for optimal performance`);
            
            // Check rate limit result first (early exit if rate limited)
            if (rateLimitResult) {
                const rateLimitInfo = params.body.options.rateLimit;
                let errorMessage = "Request limit reached."
                if (rateLimitInfo) {
                    const currentRate = rateLimitInfo.currentSpent ? `Current Spent: ${formatCurrentSpent(rateLimitInfo)}` : "";
                    const rateLimitStr = `${rateLimitInfo.adminSet ? "Amplify " : ""}Set Rate limit: ${formatRateLimit(rateLimitInfo)}`;
                    errorMessage = `${errorMessage} ${currentRate} ${rateLimitStr}`;
                }
                logger.warn(`🚫 Rate limit exceeded for user ${params.user}: ${errorMessage}`);
                return returnResponse(responseStream, {
                    statusCode: 429,
                    statusText: "Request limit reached. Please try again in a few minutes.",
                    body: {error: errorMessage}
                });
            }
            
            const user_model_data = userModelData;
            let dataSources = resolvedDataSources;
            
            
            const models = user_model_data.models;
            if (!models) {
                    return returnResponse(responseStream, {
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

            // 🛡️ STRICT VALIDATION: Bad data = immediate rejection, no fallbacks
            let model;
            try {
                if (!modelId) {
                    const error = new Error("No model ID provided in request");
                    error.statusCode = 400;
                    error.code = "MISSING_MODEL_ID";
                    throw error;
                }
                
                // Use defensive validation instead of direct access
                model = validateModelConfiguration(models, modelId, options.model, params.user);
                logger.info(`✅ Model validation passed: ${modelId}`);
                
            } catch (error) {
                logger.error(`❌ Model validation failed for ${modelId} - REJECTING REQUEST:`, error.message);
                
                // 🚫 NO FALLBACKS: Bad data gets kicked out immediately
                return returnResponse(responseStream, {
                    statusCode: error.statusCode || 400,
                    body: { 
                        error: `Invalid model: ${error.message}`,
                        code: error.code || "INVALID_MODEL",
                        requestedModel: modelId,
                        availableModels: Object.keys(models).slice(0, 10) // Help debugging
                    }
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
            logger.info("Request options.", options);
            logger.info("Request data sources", dataSources);

            delete body.dataSources;

            
            // ⚡ Create request state 
            await createRequestState(params.user, requestId);
            
            
            for (const ds of [...dataSources, ...(body.imageSources ?? [])]) {
                logger.debug("Resolved data source: ", ds.id, "\n", ds);
            }

            if (doTrace) {
                responseStream = new TraceStream({}, responseStream);
            }

            logger.debug("Calling chat with data");

            const assistantParams = {
                account: {
                    user: params.user,
                    username: params.username,  // Clean username for services like tool API key lookup
                    accessToken: params.accessToken,
                    accountId: options.accountId,
                    apiKeyId: params.apiKeyId
                },
                model,
                requestId,
                options,
                preResolvedDataSourcesByUse,  // Pass pre-resolved data sources for performance optimization
                body: params.body  // ✅ INCLUDE MODIFIED BODY: Contains imageSources from resolveDataSources()
            };


            const requestStartTime = Date.now();
            let processingError = false;

            try {
                // 🚀 ASYNC OPTIMIZATION: Start smart messages processing while assistant loads
                let smartMessagesPromise = null;

                // Log what we're checking

                if (options.options?.smartMessages || options.options?.artifacts) {

                    sendStatusEventToStream(responseStream, newStatus({
                        summary: "Analyzing conversation context...",
                        inProgress: true,
                        type: "info",
                        id: "smart-messages-processing"
                    }));


                    smartMessagesPromise = processSmartMessages({
                        messages: body.messages || [],
                        options: options,
                        account: assistantParams.account,
                        requestId: assistantParams.requestId,
                        params
                    }).catch(error => {
                        logger.error("❌ [Smart Messages] ROUTER - processSmartMessages failed:", error.message, {
                            stack: error.stack,
                            source: "ROUTER"
                        });
                        logger.warn("Smart messages processing failed, continuing with original messages:", error.message);
                        return {
                            filteredMessages: body.messages || [],
                            metadata: {
                                processed: false,
                                reason: "processing_error",
                                error: error.message
                            }
                        };
                    });
                }

                // 🚀 BREAKTHROUGH: Direct assistant execution without LLM dependency
                // Assistants now create their own InternalLLM internally for massive performance gains
                const selectedAssistant = await chooseAssistantForRequest(assistantParams.account, model, body, dataSources, responseStream);

                // 🚀 ASYNC OPTIMIZATION: Wait for smart messages if it was started
                if (smartMessagesPromise) {
                    const smartMessagesResult = await smartMessagesPromise;


                    // ALWAYS send metadata to frontend (even if disabled/failed)

                    sendStateEventToStream(responseStream, {
                        smartMessages: smartMessagesResult.metadata
                    });


                    // Update body with filtered messages if processing succeeded
                    if (smartMessagesResult.filteredMessages && smartMessagesResult.metadata.processed) {
                        body.messages = smartMessagesResult.filteredMessages;
                        // CRITICAL: Also update assistantParams.body so the assistant gets filtered messages!
                        assistantParams.body.messages = smartMessagesResult.filteredMessages;
                        logger.info(`✅ [Processing] Complete:`, smartMessagesResult._internal || {});

                        options.options.artifacts = smartMessagesResult.includeArtifactInstructions;

                        // Update status to show completion
                        sendStatusEventToStream(responseStream, newStatus({
                            summary: "Context analysis complete",
                            inProgress: true,
                            type: "success",
                            id: "smart-messages-processing"
                        }));
                        forceFlush(responseStream);
                    } else {
                        logger.debug(`⏭️ Smart messages complete: ${smartMessagesResult.filteredMessages?.length || body.messages.length} messages (unfiltered)`,
                                   smartMessagesResult._internal || {});
                    }
                }

                // Send "Assistant is responding" status RIGHT BEFORE prompting
                // (moved from assistants.js so it appears after smart messages processing)
                logger.info("🤖 [Router] About to call assistant handler - sending 'Assistant is responding' status");
                sendStatusEventToStream(responseStream, newStatus({
                    inProgress: false,
                    message: `The "${selectedAssistant.displayName || selectedAssistant.name} Assistant" is responding.`,
                    icon: "assistant",
                    sticky: true
                }));

                // All assistants now use the same handler signature

                await selectedAssistant.handler(assistantParams, body, dataSources, responseStream);
                
                
                // Removed X-Ray tracing for performance

            } catch (error) {
                processingError = true;
                logger.error(`Request processing failed for user ${params.user}:`, error);

                // CRITICAL: Assistant handler failure = user cannot get LLM response
                // Skip critical error logging in local development
                if (!isLocal) {
                    await logCriticalError({
                        functionName: 'routeRequest_assistantHandler',
                        errorType: 'AssistantHandlerFailure',
                        errorMessage: `Assistant handler failed: ${error.message || "Unknown error"}`,
                        currentUser: params.user,
                        severity: 'HIGH',
                        stackTrace: error.stack || '',
                        context: {
                            requestId: requestId || 'unknown',
                            modelId: model?.id || 'unknown',
                            conversationId: options?.conversationId || 'N/A',
                            hasDataSources: dataSources?.length > 0,
                            requestedAssistantId: options?.assistantId || 'none',
                            codeInterpreterOnly: options?.codeInterpreterOnly || false,
                            artifactsMode: options?.artifactsMode || false,
                            api_accessed: options?.api_accessed || false
                        }
                    });
                }
                
                // ❌ DON'T RE-THROW - Handle error gracefully to prevent Lambda hang
                // Return error response instead of throwing
                return returnResponse(responseStream, {
                    statusCode: 500,
                    body: { error: error.message || "Internal server error" }
                });
            } finally {
                // Simple request completion logging
                const processingTime = Date.now() - requestStartTime;
                
                logger.info("Request completed", {
                    userId: params.user,
                    requestId,
                    processingTime,
                    error: processingError,
                });
                
                // 🛡️ DEFENSIVE CLEANUP: Ensure stream is closed in all cases
                ensureStreamClosed(responseStream, "finally-block");
            }
            

            if (doTrace) {
                try {
                    trace(requestId, ["response"], {stream: responseStream.trace})
                    await saveTrace(params.user, requestId);
                } catch (traceError) {
                    logger.error("Error in tracing:", traceError);
                }
            }

            // Response is streamed directly by the assistant handler
            // ✅ Assistant handlers manage their own stream closure
            return;

        }
    } catch (e) {
        // 🚨 Record error violation for user (if we have user info)
        if (params && params.user) {
            const errorViolation = recordErrorViolation(params.user);
            logger.debug(`Recorded error violation for ${params.user}: ${errorViolation.count} errors`);
        }
        // Check if this is a critical secrets/endpoints error that should terminate the Lambda
        const isLambdaTermination = e.isLambdaTermination || 
                                  (e.message && (
                                      e.message.includes("LAMBDA_TERMINATION_REQUIRED") ||
                                      e.message.includes("Critical error") ));
        
        if (isLambdaTermination) {
            logger.error("[LAMBDA_TERMINATION] 💀 Forcing Lambda termination due to critical failure");
            
            // Strategy 1: Force stream closure using returnResponse (handles both local and AWS)
            returnResponse(responseStream, { statusCode: 500, body: { error: "Lambda terminated" } });
            
            // Strategy 2: Defensive cleanup as backup
            ensureStreamClosed(responseStream, "lambda-termination");
            
            // Strategy 3: Re-throw the critical error to propagate up
            throw new Error(`LAMBDA_TERMINATION_REQUIRED: ${e.message}`);
        }

        logger.error("Error processing request:", e.message);
        logger.error("Full error:", e);

        // CRITICAL: Router failure = user cannot access chat/LLM functionality
        // Skip critical error logging in local development
        if (!isLocal) {
            await logCriticalError({
                functionName: 'routeRequest_mainRouter',
                errorType: 'RouterFailure',
                errorMessage: `Main router failed: ${e.message || "Unknown error"}`,
                currentUser: params?.user || 'unknown',
                severity: 'HIGH',
                stackTrace: e.stack || '',
                context: {
                    hasUser: !!params?.user,
                    hasBody: !!params?.body,
                    bodyKeys: params?.body ? Object.keys(params.body).join(',') : 'N/A',
                    errorName: e.name || 'Error'
                }
            });
        }

        returnResponse(responseStream, {
            statusCode: 400,
            body: {error: e.message}
        });
        
        // 🛡️ DEFENSIVE CLEANUP: Backup stream closure for error cases
        ensureStreamClosed(responseStream, "error-handling");
        
        // ✅ EXPLICIT RETURN to ensure Lambda completion after error handling
        return;
    } 
}

// Environment variables tracking wrapper for router
const routeRequestWrapper = requiredEnvVars({
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.UPDATE_ITEM],
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.QUERY],
    "COST_CALCULATIONS_DYNAMO_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.UPDATE_ITEM],
    "HISTORY_COST_CALCULATIONS_DYNAMO_TABLE": [DynamoDBOperation.SCAN, DynamoDBOperation.QUERY],
    "MODEL_RATE_TABLE": [DynamoDBOperation.QUERY],
    "CHAT_USAGE_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
    "REQUEST_STATE_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM, DynamoDBOperation.DELETE_ITEM],
    "ASSISTANTS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.SCAN],
    "ASSISTANTS_ALIASES_DYNAMODB_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.SCAN],
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "DATASOURCE_REGISTRY_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "HASH_FILES_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "S3_FILE_TEXT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_IMAGE_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_RAG_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "TRACE_BUCKET_NAME": [S3Operation.PUT_OBJECT],
    "S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME": [S3Operation.GET_OBJECT, S3Operation.PUT_OBJECT], //Marked for future deletion
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.GET_OBJECT, S3Operation.PUT_OBJECT],
    "LLM_ENDPOINTS_SECRETS_NAME_ARN": [SecretsManagerOperation.GET_SECRET_VALUE],
    "ENV_VARS_TRACKING_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "LLM_ENDPOINTS_SECRETS_NAME": [SecretsManagerOperation.GET_SECRET_VALUE],
    "SECRETS_ARN_NAME": [SecretsManagerOperation.GET_SECRET_VALUE],
    "CONVERSATION_ANALYSIS_QUEUE_URL": [SQSOperation.SEND_MESSAGE],
    "CRITICAL_ERRORS_SQS_QUEUE_NAME": [SQSOperation.SEND_MESSAGE]
})(routeRequestCore);


// Main export
export const routeRequest = (params, returnResponse, responseStream) => {
    return routeRequestWrapper(params, returnResponse, responseStream);
};


// 🛡️ DEFENSIVE STREAM CLEANUP: Handles both local and AWS environments
function ensureStreamClosed(responseStream, context = "cleanup") {
    try {
        // Check if this is local development environment
        const isLocal = process.env.LOCAL_DEVELOPMENT === 'true';
        
        if (isLocal) {
            // Local SSEWrapper - check if already ended
            if (responseStream && typeof responseStream.end === 'function') {
                if (!responseStream.res?.writableEnded) {
                    logger.debug(`🔒 ${context}: Local stream cleanup - ending SSEWrapper`);
                    responseStream.end();
                } else {
                    logger.debug(`🔒 ${context}: Local stream already ended`);
                }
            }
        } else {
            // AWS Lambda - manually force stream closure
            if (responseStream && typeof responseStream.end === 'function') {
                try {
                    logger.debug(`🔒 ${context}: AWS stream cleanup - manually ending stream`);
                    responseStream.end();
                } catch (error) {
                    logger.debug(`🔒 ${context}: AWS stream end error (safe to ignore):`, error.message);
                }
            } else {
                logger.debug(`🔒 ${context}: AWS stream - no .end() method available`);
            }
        }
    } catch (error) {
        logger.debug(`🔒 ${context}: Stream cleanup error (safe to ignore):`, error.message);
        // Errors here are safe to ignore - stream might already be closed
    }
}
