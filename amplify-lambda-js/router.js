//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {getLogger} from "./common/logging.js";
import {ModelTypes, getModelByType} from "./common/params.js"
import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js";
import {sendStateEventToStream, TraceStream} from "./common/streams.js";
import {resolveDataSources} from "./datasource/datasources.js";
import {handleDatasourceRequest} from "./datasource/datasourceEndpoint.js";
import {saveTrace, trace} from "./common/trace.js";
import {isRateLimited, formatRateLimit, formatCurrentSpent, recordErrorViolation} from "./rateLimit/rateLimiter.js";
import {getUserAvailableModels} from "./models/models.js";
// Removed AWS X-Ray for performance optimization
import {requiredEnvVars, DynamoDBOperation, S3Operation, SecretsManagerOperation, SQSOperation} from "./common/envVarsTracking.js";
import {CacheManager} from "./common/cache.js";
// LiteLLM integration - use litellmClient for all LLM calls
import {chooseAssistantForRequest} from "./assistants/assistants.js";
import {StateBasedAssistant} from "./assistants/statemachine/states.js";
import {initPythonProcess} from "./litellm/litellmClient.js";
// ⚡ COMPREHENSIVE PARALLEL SETUP OPTIMIZATION

// 🛡️ DEFENSIVE ROUTING
import { 
    validateModelConfiguration, 
    validateRequestBody,
    withCostMonitoring
} from "./common/defensiveRouting.js";


const doTrace = process.env.TRACING_ENABLED === 'true';

const logger = getLogger("router");


function getRequestId(params) {
    return (params.body.options && params.body.options.requestId) || params.user;
}

const routeRequestCore = async (params, returnResponse, responseStream, pythonProcessPromise = null) => {
    // 🚀 ULTIMATE OPTIMIZATION: Python process already started in index.js before authentication!
    // If not provided, fallback to starting it here (shouldn't happen in normal flow)
    const actualPythonProcessPromise = pythonProcessPromise || initPythonProcess();

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
                preloadedSecrets
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
                    
                    // 🔍 DEBUG: Log initial dataSources from request
                    logger.debug("🔍 ROUTER DEBUG - Initial dataSources from params.body:", {
                        dataSources_raw: params.body.dataSources,
                        dataSources_length: dataSources.length,
                        body_keys: Object.keys(params.body),
                        hasMessages: !!params.body.messages,
                        messagesLength: params.body.messages?.length || 0
                    });
                    
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
                
                // 5. Placeholder for future optimizations
                Promise.resolve(null),
                
            ]);
            
            logger.info(`⚡ Parallel setup completed in ${Date.now() - parallelStartTime}ms`);
            
            // 🚀 ULTIMATE PYTHON OPTIMIZATION: Ensure Python process is ready (started in index.js!)
            await actualPythonProcessPromise;
            logger.info(`✅ Python LiteLLM server pre-spawned during parallel setup`);
            
            // Check rate limit result first (early exit if rate limited)
            if (rateLimitResult) {
                const rateLimitInfo = params.body.options.rateLimit;
                let errorMessage = "Request limit reached."
                if (rateLimitInfo) {
                    const currentRate = rateLimitInfo.currentSpent ? `Current Spent: ${formatCurrentSpent(rateLimitInfo)}` : "";
                    const rateLimitStr = `${rateLimitInfo.adminSet ? "Amplify " : ""}Set Rate limit: ${formatRateLimit(rateLimitInfo)}`;
                    errorMessage = `${errorMessage} ${currentRate} ${rateLimitStr}`;
                }
                return returnResponse(responseStream, {
                    statusCode: 429,
                    statusText: "Request limit reached. Please try again in a few minutes.",
                    body: {error: errorMessage}
                });
            }
            
            const user_model_data = userModelData;
            let dataSources = resolvedDataSources;
            
            // 🔍 DEBUG: Log final resolved dataSources
            logger.debug("🔍 ROUTER DEBUG - Final resolved dataSources:", {
                resolvedDataSources_length: resolvedDataSources.length,
                dataSources_length: dataSources.length,
                dataSources_preview: dataSources.slice(0, 2).map(ds => ({
                    id: ds?.id || "NO_ID",
                    type: ds?.type || "NO_TYPE",
                    hasContent: !!ds?.content
                }))
            });
            
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
            
            logger.debug("🎯 Router: Passing raw datasources to assistant:", {
                dataSources_length: dataSources.length,
                dataSources_ids: dataSources.map(ds => ds.id?.substring(0, 50))
            });
            
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
                    accessToken: params.accessToken,
                    accountId: options.accountId,
                    apiKeyId: params.apiKeyId
                },
                model,
                requestId,
                options,
                preloadedSecrets  // Pass prefetched secrets to avoid duplicate fetching
            };

            // Removed X-Ray tracing for performance

            // ✅ ALWAYS USE LITELLM: Feature flags removed, migration complete
            const requestStartTime = Date.now();
            let processingError = false;

            try {
                // ✅ OPTIMIZED PATH: LiteLLM Integration with all optimizations
                logger.info(`Using LiteLLM optimized path for user ${params.user}`);
                
               
                // 🚀 BREAKTHROUGH: Direct assistant execution without LLM dependency
                // Assistants now create their own InternalLLM internally for massive performance gains
                const selectedAssistant = await chooseAssistantForRequest(assistantParams.account, model, body, dataSources, responseStream);
                
                // Different assistant types have different handler signatures
                if (selectedAssistant instanceof StateBasedAssistant) {
                    // StateBasedAssistant expects: (originalLLM, params, body, dataSources, responseStream)
                    await selectedAssistant.handler(model, assistantParams, body, dataSources, responseStream);
                } else {
                    // Regular assistant expects: (params, body, dataSources, responseStream)
                    await selectedAssistant.handler(assistantParams, body, dataSources, responseStream);
                }
                
                // Removed X-Ray tracing for performance

            } catch (error) {
                processingError = true;
                logger.error(`Request processing failed for user ${params.user}:`, error);
                throw error;
            } finally {
                // Simple request completion logging
                const processingTime = Date.now() - requestStartTime;
                
                logger.info("Request completed", {
                    userId: params.user,
                    requestId,
                    processingTime,
                    error: processingError,
                    litellm: true
                });
            }
            

            if(doTrace) {
                trace(requestId, ["response"], {stream: responseStream.trace})
                await saveTrace(params.user, requestId);
            }

            // Response is streamed directly by the assistant handler
            // No additional response handling needed 

        }
    } catch (e) {
        logger.error("Error processing request:", e.message);
        logger.error("Full error:", e);

        // 🚨 Record error violation for user (if we have user info)
        if (params && params.user) {
            const errorViolation = recordErrorViolation(params.user);
            logger.debug(`Recorded error violation for ${params.user}: ${errorViolation.count} errors`);
        }

        returnResponse(responseStream, {
            statusCode: 400,
            body: {error: e.message}
        });
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
    "CONVERSATION_ANALYSIS_QUEUE_URL": [SQSOperation.SEND_MESSAGE] 
})(routeRequestCore);

// Main export that accepts pythonProcessPromise from index.js
export const routeRequest = (params, returnResponse, responseStream, pythonProcessPromise = null) => {
    return routeRequestWrapper(params, returnResponse, responseStream, pythonProcessPromise);
};


