//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {chat} from "./azure/openai.js";
import {chatAnthropic} from "./bedrock/anthropic.js";
import {chatMistral} from "./bedrock/mistral.js";
import {Models} from "./models/models.js";
import {chooseAssistantForRequest} from "./assistants/assistants.js";
import {getLogger} from "./common/logging.js";
import {getLLMConfig} from "./common/secrets.js";
import {LLM} from "./common/llm.js";
import {createRequestState, deleteRequestState, updateKillswitch} from "./requests/requestState.js";
import {sendStateEventToStream, TraceStream} from "./common/streams.js";
import {DynamoDBClient, QueryCommand} from "@aws-sdk/client-dynamodb";

import {unmarshall} from "@aws-sdk/util-dynamodb";
import {resolveDataSources} from "./datasource/datasources.js";
import {saveTrace, trace} from "./common/trace.js";

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
                body: {error: "Too Many Requests",
                       rateLimitInfo: rateLimit,
                }
            });

        } else {
            logger.debug("Processing request");

            let options = params.body.options ? {...params.body.options} : {};

            params.body.options.numPrompts = params.body.messages ? Math.ceil(params.body.messages.length / 2) : 0;
            
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



async function isRateLimited(params) {
    const rateLimit = params.body.options.rateLimit;
    if (!rateLimit || rateLimit.period === 'Unlimited') return false;
    const costCalcTable = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
    const dynamodbClient = new DynamoDBClient();
    

    if (!costCalcTable) {
        console.log("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("COST_CALCULATIONS_DYNAMO_TABLE is not provided in the environment variables.");
    }
    try {

        const command = new QueryCommand({
            TableName: costCalcTable,
            KeyConditionExpression: '#id = :userid',
            ExpressionAttributeNames: {
                '#id': 'id'  // Using an expression attribute name to avoid any potential keyword conflicts
            },
            ExpressionAttributeValues: {
                ':userid': { S: params.user} // Assuming this is the id you are querying by
            }
        });
        
        logger.debug("Calling billing table.");
        const response = await dynamodbClient.send(command);
        
        const item = response.Items[0];

        if (!item) {
            logger.error("Table entry does not exist. Can not verify if rate limited");
            return false;
        }
        const rateData = unmarshall(item);

        //periods include Monthly, Daily, Hourly 
        const period = rateLimit.period
        const colName = `${period.toLowerCase()}Cost`
        let spent = rateData[colName];
        if (period === 'Hourly') spent = spent[new Date().getHours()]// Get the current hour as a number from 0 to 23
        return spent >= rateLimit.rate;
        
    } catch (error) {
        console.error("Error during rate limit DynamoDB operation:", error);
        // let it slide for now
        return false;
    }

}
