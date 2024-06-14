import {chat} from "./azure/openai.js";
import {chatAnthropic} from "./bedrock/anthropic.js";
import {chatMistral} from "./bedrock/mistral.js";
import {canReadDatasource, canReadDataSources} from "./common/permissions.js";
import {Models} from "./models/models.js";
import {chooseAssistantForRequest} from "./assistants/assistants.js";
import {getLogger} from "./common/logging.js";
import {getLLMConfig} from "./common/secrets.js";
import {LLM} from "./common/llm.js";
import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js";
import {sendStateEventToStream, TraceStream} from "./common/streams.js";
import {
    extractKey,
    getDataSourcesInConversation, resolveDataSources,
    translateUserDataSourcesToHashDataSources
} from "./datasource/datasources.js";
import {saveTrace, trace} from "./common/trace.js";
import { codeInterpreterAssistant } from "./assistants/codeInterpreter.js";

const doTrace = process.env.TRACING_ENABLED;
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

        } else {
            logger.debug("Processing request");

            let options = params.body.options ? {...params.body.options} : {};
            
            const modelId = (options.model && options.model.id);//|| "gpt-4-1106-Preview";
            const model = Models[modelId];


            if (!model) {
                returnResponse(responseStream, {
                    statusCode: 400,
                    body: {error: "Invalid model."}
                });
            }
            
            logger.debug("Determining chatFn");
            
            const chatFn = async (body, writable, context) => {
                if (model.id.includes("gpt")) {
                    return await chat(getLLMConfig, body, writable, context);

                } else if (model.id.includes("anthropic")) { //claude models
                    return await chatAnthropic(body, writable, context);

                } else if (model.id.includes("mistral")) { // mistral 7b and mixtral 7x8b
                    return await chatMistral(body, writable, context);
                }
            }


            if (!params.body.dataSources) {
                params.body.dataSources = [];
            }

            //if (params.body.dataSources) {
            logger.debug("Checking access on data sources");
            let dataSources = [...params.body.dataSources];
            let body = {...params.body};

            logger.info("Request options.", options);

            delete body.dataSources;
            //delete body.options;

            try {

                dataSources = await resolveDataSources(params, body, dataSources);

            } catch (e) {
                logger.error("Unauthorized access on data sources: " + e);
                return returnResponse(responseStream, {
                    statusCode: 401,
                    body: {error: "Unauthorized data source access."}
                });
            }

            if(doTrace) {
                responseStream = new TraceStream({}, responseStream);
            }

            if (!model) {
                returnResponse(responseStream, {
                    statusCode: 400,
                    body: {error: "Invalid model."}
                });
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
            console.log(
                "Enter router: ", body.messages
            )

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
