//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {chatWithDataStateless} from "../common/chatWithData.js";

import {getLogger} from "../common/logging.js";
import {
    StreamResultCollector,
    sendResultToStream,
    sendStatusEventToStream,
    findResultKey,
    endStream,
    forceFlush, StatusOutputStream
} from "../common/streams.js";
import {newStatus} from "../common/status.js";
import {isKilled} from "../requests/requestState.js";


const logger = getLogger("workflow");


export const workflowSchema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "resultKeys": {
            "type": "array",
            "items":{"type":"string"}
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statusMessage": {
                        "type": "string"
                    },
                    "input": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "prompt": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ]
                    },
                    "reduce": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ]
                    },
                    "map": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ]
                    },
                    "outputTo": {
                        "type": "string"
                    }
                },
                "required": [
                    "statusMessage",
                    "input",
                    "outputTo"
                ],
                "additionalProperties": false,
                "oneOf": [
                    { "required": ["prompt"] },
                    { "required": ["reduce"] },
                    { "required": ["map"] }
                ]
            }
        }
    },
    "required": [
        "resultKey",
        "steps"
    ],
    "additionalProperties": false
};


function buildChatBody(step, body) {

    if(step.prompt && step.prompt === "__use_body__") {
        return {...body};
    }

    const customInstructions = step.customInstructions ? step.customInstructions : [];

    const history = body.messages ? body.messages : [];

    const updatedBody = {
        ...body, messages: [
            ...history,
            ...customInstructions,
            {role: "user", content: step.prompt || step.reduce || step.map },
        ]
    };

    return updatedBody;
}



const doPrompt = async ({
                            step,
                            body,
                            chatFn,
                            responseStream,
                            dataSources,
                            params
                        }) => {

    const updatedBody = buildChatBody(step, body);

    if(await isKilled(params.account.user, responseStream, body)){
        return;
    }

    return chatWithDataStateless(
        {...params, options:{...params.options, skipRag:true}},
        chatFn,
        updatedBody,
        dataSources,
        responseStream);
}

const doMap = async ({
                         statusUpdater,
                         step,
                         dataSources,
                         body,
                         chatFn,
                         responseStream,
                         params
                     }) => {

    const updatedBody = buildChatBody(step, body);

    if(await isKilled(params.account.user, responseStream, body)){
        return;
    }

    return chatWithDataStateless(
        {...params, options:{...params.options, skipRag:true}},
        chatFn,
        updatedBody,
        dataSources,
        responseStream);
}

const doReduce = async ({
                            step,
                            dataSources,
                            body,
                            chatFn,
                            responseStream,
                            params
                        }) => {

    const updatedBody = buildChatBody(step, body);

    const resultStream = new StreamResultCollector();

    if(responseStream.statusStreams) {
        for (const sst of responseStream.statusStreams) {
            resultStream.addStatusStream(sst);
        }
    }
    if(responseStream.outputStreams){
        for (const os of responseStream.outputStreams) {
            resultStream.addOutputStream(os);
        }
    }

    if(await isKilled(params.account.user, responseStream, body)){
        return;
    }

    const response = await chatWithDataStateless(
        {...params, options:{...params.options, skipRag:true}},
        chatFn,
        updatedBody,
        dataSources,
        resultStream);

    if (response) {
        return response;
    } else {
        const result = resultStream.result;
        const total = Object.keys(result).length;
        if (total > 0 && (total / 2) > 1) {
            const updatedStep = {...step, input: ["__lastResult"]};
            const updatedDataSources = resolveDataSources(
                updatedStep,
                {"__lastResult": result},
                []);

            if(await isKilled(params.account.user, responseStream, body)){
                return;
            }

            await doReduce({
                step:updatedStep,
                dataSources:updatedDataSources,
                body,
                chatFn,
                responseStream,
                params
            });
        } else {
            const resultKey = findResultKey(result);
            if(resultKey){
                sendResultToStream(responseStream, result[resultKey]);
            }
            endStream(responseStream);
        }
    }


}


const getExecutor = (step) => {
    // Check if the prompt key is present
    if (step.prompt) {
        return doPrompt;
    } else if (step.map) {
        return doMap;
    } else if (step.reduce) {
        return doReduce;
    }
}

const resolveDataSources = (step, workflowOutputs, externalDataSources) => {
    const dataSources = [];

    if (step.input) {
        for (const [index, inputName] of step.input.entries()) {
            if (inputName.startsWith("s3://")) {
                const dataSource = externalDataSources.find((ds) => ds.id === inputName);
                if (!dataSource) {
                    throw new Error("Data source not found: " + inputName);
                }
                dataSources.push(dataSource);
            }
            else if(inputName.split("://").length === 2){
                const dataSource = externalDataSources.find((ds) => ds.id === inputName);
                if (!dataSource) {
                    throw new Error("Data source not found: " + inputName);
                }
                dataSources.push(dataSource);
            }
            else if(workflowOutputs[inputName]) {
                const dataSource = workflowOutputs[inputName];
                if (!dataSource) {
                    throw new Error("Data source not found: " + inputName);
                }
                dataSources.push({id: "obj://" + inputName, content: dataSource});
            }
        }
    }

    return dataSources;
}

export const executeWorkflow = async (
    {
        workflow,
        body,
        chatFn,
        dataSources,
        responseStream,
        params,
        initialState
    }) => {

    logger.debug("Starting workflow...");

    if (!workflow || !workflow.steps) {
        return {
            statusCode: 400,
            body: {error: "Bad request, invalid workflow."}
        };
    }

    const outputs = {...(initialState || {})};

    const status = newStatus({summary: "", inProgress:true});

    for (const [index, step] of workflow.steps.entries()) {

        if(await isKilled(params.account.user, responseStream, body)){
            responseStream.end();
            return;
        }

        logger.debug("Executing workflow step", {index, step});

        const executor = getExecutor(step);

        logger.debug("Building results collector...");
        const resultStream = new StreamResultCollector();
        resultStream.addStatusStream(responseStream);

        const workStatus = newStatus({summary: "Details...", message:"", inProgress:true});
        resultStream.addOutputStream(new StatusOutputStream({}, responseStream, workStatus));

        if(step.statusMessage){
            status.summary = step.statusMessage;
            sendStatusEventToStream(responseStream, status);
            forceFlush(responseStream);
        }

        const statusUpdater = (summary, message) => {
            status.summary = summary;
            status.message = message;
            sendStatusEventToStream(responseStream, status);
            forceFlush(responseStream);
        };

        const resolvedDataSources = resolveDataSources(step, outputs, dataSources);

        const response = await executor({
            statusUpdater,
            step,
            params,
            chatFn,
            body,
            dataSources: resolvedDataSources,
            responseStream: resultStream
        });

        workStatus.inProgress = false;
        sendStatusEventToStream(responseStream, workStatus);
        forceFlush(responseStream);

        if(await isKilled(params.account.user, responseStream, body)){
            responseStream.end();
            return;
        }

        logger.debug("Binding output of step to ", step.outputTo);
        logger.debug("Result", resultStream.result);

        outputs[step.outputTo] = resultStream.result;

        if (response) {
            // Error returned
            return {
                statusCode: 500,
                body: {error: "Error executing workflow at step:" + index}
            };
        }
    }

    const result = (workflow.resultKey) ? outputs[workflow.resultKey] : outputs;

    if(typeof result === 'object' && result !== null && !Array.isArray(result)){
        if(Object.keys(result).length === 2){
            const resultKey = findResultKey(result);
            if(resultKey){
                sendResultToStream(responseStream, result[resultKey]);
            }
        }
    }
    else {
        sendResultToStream(responseStream, result);
    }
    endStream(responseStream);
}

