//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {getLogger} from "./common/logging.js";
import {ModelTypes, getModelByType} from "./common/params.js"
import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js";
import {sendStateEventToStream, TraceStream} from "./common/streams.js";
import {resolveDataSources} from "./datasource/datasources.js";
import { resolveDataSourcesOptimized } from "./common/optimizedDataSources.js";
import {handleDatasourceRequest} from "./datasource/datasourceEndpoint.js";
import {saveTrace, trace} from "./common/trace.js";
import {isRateLimited, formatRateLimit, formatCurrentSpent} from "./rateLimit/rateLimiter.js";
import {getUserAvailableModels} from "./models/models.js";
import AWSXRay from "aws-xray-sdk";
import {requiredEnvVars, DynamoDBOperation, S3Operation, SecretsManagerOperation} from "./common/envVarsTracking.js";
import {CacheManager} from "./common/cache.js";
// LiteLLM integration - use litellmClient for all LLM calls
import {chooseAssistantForRequest} from "./assistants/assistants.js";
// ⚡ COMPREHENSIVE PARALLEL SETUP OPTIMIZATION
import {getDataSourcesByUse} from "./datasource/datasources.js";
// ✅ ELIMINATED: No longer need getDefaultLLM - assistants create their own InternalLLM


const doTrace = process.env.TRACING_ENABLED === 'true';

const logger = getLogger("router");


function getRequestId(params) {
    return (params.body.options && params.body.options.requestId) || params.user;
}

const routeRequestCore = async (params, returnResponse, responseStream) => {
    const segment = AWSXRay.getSegment();
    const subSegment = segment.addNewSubsegment('chat-js.router.routeRequest');

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
            // ⚡ COMPREHENSIVE PARALLEL SETUP OPTIMIZATION - All router operations in parallel!
            console.log("🚀 Starting comprehensive parallel router setup...");
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
                    if (dataSources.length === 0) return [];
                    
                    const dataSourceIds = dataSources.map(ds => ds.id || ds);
                    let cached = await CacheManager.getCachedDataSources(params.user, dataSourceIds, params.body.options);
                    if (!cached) {
                        logger.debug("Cache miss for data sources, resolving with optimization");
                        cached = await resolveDataSourcesOptimized(params, dataSources);
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
                Promise.resolve(null)
            ]);
            
            console.log(`⚡ Parallel setup completed in ${Date.now() - parallelStartTime}ms`);
            
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
            logger.info("Request options.", options);
            logger.info("Request data sources", dataSources);

            delete body.dataSources;

            // ⚡ PHASE 2: Now run getDataSourcesByUse on the resolved data sources in parallel
            console.log("🚀 Starting data source categorization...");
            const categoryStartTime = Date.now();
            
            const [
                categorizedDataSources,
                requestStateResult
            ] = await Promise.all([
                // 5. Categorize data sources (includes translate hashes)
                getDataSourcesByUse(params, body, dataSources),
                
                // 6. Create request state 
                createRequestState(params.user, requestId)
            ]);
            
            console.log(`⚡ Data source categorization completed in ${Date.now() - categoryStartTime}ms`);

            // Use categorized data sources for assistant logic
            const {dataSources: finalDataSources, ragDataSources, conversationDataSources} = categorizedDataSources;

            for (const ds of [...finalDataSources, ...(body.imageSources ?? [])]) {
                console.debug("Resolved data source: ", ds.id, "\n", ds);
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

            const initSegment = segment.addNewSubsegment('chat-js.router.init');

            // ✅ ALWAYS USE LITELLM: Feature flags removed, migration complete
            const requestStartTime = Date.now();
            let processingError = false;

            try {
                // ✅ OPTIMIZED PATH: LiteLLM Integration with all optimizations
                logger.info(`Using LiteLLM optimized path for user ${params.user}`);
                
                sendStateEventToStream(responseStream, {
                    assistant: "LiteLLM", 
                    routingTime: 5, // Fast routing with no LLM-based assistant selection
                    optimizationFlags: {
                        litellm: true,
                        caching: true,
                        parallel: true
                    }
                });
                initSegment.close();

                const chatSegment = segment.addNewSubsegment('chat-js.router.litellm');
                
                // 🚀 BREAKTHROUGH: Direct assistant execution without LLM dependency
                // Assistants now create their own InternalLLM internally for massive performance gains
                const selectedAssistant = await chooseAssistantForRequest(assistantParams.account, model, body, finalDataSources, responseStream);
                await selectedAssistant.handler(assistantParams, body, finalDataSources, responseStream);
                
                chatSegment.close();

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

            if (response) {
                logger.debug("Returning a json response that wasn't streamed from chatWithDataStateless");
                logger.debug("Response", response);
                returnResponse(responseStream, response);
            } 

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

// Environment variables tracking wrapper for router
export const routeRequest = requiredEnvVars({
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
    "SECRETS_ARN_NAME": [SecretsManagerOperation.GET_SECRET_VALUE]
})(routeRequestCore);


