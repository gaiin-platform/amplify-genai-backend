//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {chooseAssistantForRequest} from "./assistants/assistants.js";
import {getLogger} from "./common/logging.js";
import {LLM} from "./common/llm.js";
import {getChatFn, ModelTypes, getModelByType} from "./common/params.js"
import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js";
import {sendStateEventToStream, TraceStream} from "./common/streams.js";
import {resolveDataSources} from "./datasource/datasources.js";
import {handleDatasourceRequest} from "./datasource/datasourceEndpoint.js";
import {saveTrace, trace} from "./common/trace.js";
import {isRateLimited, formatRateLimit, formatCurrentSpent} from "./rateLimit/rateLimiter.js";
import {getUserAvailableModels} from "./models/models.js";
import AWSXRay from "aws-xray-sdk";
import {requiredEnvVars, DynamoDBOperation, S3Operation, SQSOperation} from "./common/envVarsTracking.js";


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

            const llm = new LLM(
                chatFn,
                assistantParams,
                responseStream);

            const now = new Date();
            const assistant = await chooseAssistantForRequest(llm, model, body, dataSources);
            const assistantSelectionTime = new Date() - now;
            sendStateEventToStream(responseStream, {routingTime: assistantSelectionTime});
            sendStateEventToStream(responseStream, {assistant: assistant.name});
            initSegment.close();


            const chatSegment = segment.addNewSubsegment('chat-js.router.assistantHandler');
            const response = await assistant.handler(
                llm,
                assistantParams,
                body,
                dataSources,
                responseStream);
            chatSegment.close();
            

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
    "ASSISTANT_LOGS_BUCKET_NAME": [S3Operation.PUT_OBJECT],
    "DATASOURCE_REGISTRY_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "HASH_FILES_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "S3_FILE_TEXT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_IMAGE_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_RAG_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "TRACE_BUCKET_NAME": [S3Operation.PUT_OBJECT],
    "ENV_VARS_TRACKING_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM]
})(routeRequestCore);


