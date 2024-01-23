import {chat} from "./azure/openai.js";
import {canReadDatasource} from "./common/permissions.js";
import {Models} from "./models/models.js";
import {chooseAssistantForRequest} from "./assistants/assistants.js";
import {getLogger} from "./common/logging.js";
import {getSecret} from "./common/secrets.js";
import {LLM} from "./common/llm.js";
import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js";

const logger = getLogger("router");

const secretData = await getSecret(process.env.SECRETS_NAME);
const apiKey = JSON.parse(secretData).OPENAI_API_KEY;


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

            const chatFn = async (body, writable, context) => {
                return await chat(apiKey, body, writable, context);
            }

            if (!params.body.dataSources) {
                params.body.dataSources = [];
            }

            //if (params.body.dataSources) {
            logger.debug("Checking access on data sources");
            const dataSources = [...params.body.dataSources];
            let body = {...params.body};

            logger.info("Request options.", options);

            delete body.dataSources;
            //delete body.options;

            try {
                if (dataSources.some((ds) => !canReadDatasource(params.user, ds.id))) {
                    returnResponse(responseStream, {
                        statusCode: 401,
                        body: {error: "Unauthorized data source access."}
                    });
                }
            } catch (e) {
                logger.error("Error checking access on data sources: " + e);
                returnResponse(responseStream, {
                    statusCode: 401,
                    body: {error: "Unauthorized data source access."}
                });
            }

            const modelId = (options.model && options.model.id) || "gpt-4-1106-Preview";
            const model = Models[modelId];

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

            await createRequestState(params.user, requestId);

            const llm = new LLM(
                chatFn,
                assistantParams,
                responseStream);

            const assistant = await chooseAssistantForRequest(llm, model, body, dataSources);

            const response = await assistant.handler(
                llm,
                assistantParams,
                body,
                dataSources,
                responseStream);

            await deleteRequestState(params.user, requestId);

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