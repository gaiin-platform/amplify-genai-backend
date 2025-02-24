//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {chooseAssistantForRequest} from "./assistants/assistants.js";
import {getLogger} from "./common/logging.js";
import {LLM} from "./common/llm.js";
import {getChatFn, getCheapestModel, getAdvancedModel} from "./common/params.js"
import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js";
import {sendStateEventToStream, TraceStream} from "./common/streams.js";
import {resolveDataSources} from "./datasource/datasources.js";
import {saveTrace, trace} from "./common/trace.js";
import {isRateLimited} from "./rateLimit/rateLimiter.js";
import {getUserAvailableModels} from "./models/models.js";


const doTrace = process.env.TRACING_ENABLED === 'true';

const logger = getLogger("router");

function getRequestId(params) {
    return (params.body.options && params.body.options.requestId) || params.user;
}

export const routeRequest = async (params, returnResponse, responseStream) => {
    try {

        logger.debug("Extracting params from event");
        if (params && params.statusCode) {
            returnResponse(responseStream, params);
        } else if (!params || !params.body || (!params.body.messages && !params.body.killSwitch)) {
            logger.info("Invalid request body", params.body);

            returnResponse(responseStream, {
                statusCode: 400,
                body: {error: "No messages provided"}
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

        } else if (await isRateLimited(params)) {
            returnResponse(responseStream, {
                statusCode: 429,
                statusText: "Request limit reached. Please try again in a few minutes.",
                body: {error: "Too Many Requests"}
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

            params.body.options.numPrompts = params.body.messages ? Math.ceil(params.body.messages.length / 2) : 0;
            
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

            options.cheapestModel = getCheapestModel(params);
            options.advancedModel = getAdvancedModel(params);

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

                for(const ds of dataSources) {
                    console.debug("Resolved data source", ds.id, ds);
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
                },
                model,
                requestId,
                options
            };

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

            const response = await assistant.handler(
                llm,
                assistantParams,
                body,
                dataSources,
                responseStream);
            
            await deleteRequestState(params.user, requestId);

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
    }
}


