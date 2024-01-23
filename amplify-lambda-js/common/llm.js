import {chatWithDataStateless} from "./chatWithData.js";
import {newStatus} from "./status.js";
import {
    StreamResultCollector,
    sendResultToStream,
    findResult,
    sendToStream,
    sendStatusEventToStream, sendOutOfOrderModeEventToStream
} from "./streams.js";
import {chat} from "../azure/openai.js";
import {transform} from "./chat/events/openaifn.js";
import {parseValue} from "./incrementalJsonParser.js";

export class LLM {

    constructor(chatFn, params, responseStream) {
        this.user = params.account.user;
        this.params = params;
        this.chatFn = chatFn;
        this.passThrough = false;
        this.responseStream = responseStream;
        this.defaultBody = {
            "max_tokens": 1000,
            "temperature": 1.0,
            "top_p": 1,
            "n": 1,
            "stream": true
        };
    }

    /**
     * Sets the source that outputs for this LLM will be sent to in the response stream.
     *
     * @param status
     */
    setSource(source){
        this.params.options = {...this.params.options, source};
    }

    /**
     * Sends a status message to the underlying stream.
     *
     * @param status
     */
    sendStatus(status){
        if(this.responseStream){
            sendStatusEventToStream(this.responseStream, status);
        }
    }

    enableOutOfOrderStreaming(){
        if(this.responseStream){
            sendOutOfOrderModeEventToStream(this.responseStream);
        }
    }

    forceFlush(){
        // hack to force flush on AWS lambda streaming
        this.sendStatus(newStatus(
                {inProgress: false,
                    message: " ".repeat(100000)}));
    }

    /**
     * Determines if promptForXYZ functions should pass the result through to the response stream
     * in addition to returning the result.
     */
    enablePassThrough(){
        this.passThrough = true;
    }

    /**
     * Determines if promptForXYZ functions should pass the result through to the response stream
     * in addition to returning the result.
     *
     * If you need to disable passthrough for the prompt(...) function, you should pass in a
     * StreamResultCollector as the targetStream rather than a stream that goes straight to the
     * client.
     */
    disablePassThrough(){
        this.passThrough = false;
    }


    async prompt(body, dataSources = [], targetStream = this.responseStream) {

        const updatedParams = {
            ...this.params,
            model: (body.options && body.options.model) || (this.params.options && this.params.options.model),
            options: {
                ...this.params.options,
                ...body.options}};

        return chatWithDataStateless(
            updatedParams,
            this.chatFn,
            {...this.defaultBody, ...body},
            dataSources,
            targetStream);
    }

    async promptForFunctionCallStreaming(body, functions, function_call, dataSources = [], targetStream = this.responseStream) {
        const updatedChatBody = {
            ...this.defaultBody,
            ...body,
            options: {
                ...(this.params.options || {}),
                ...(body.options || {}),
                functions: functions,
                ...(function_call ? {function_call: function_call} : {})
            }
        };

        const resultCollector = new StreamResultCollector();
        resultCollector.addTransformer(transform);
        resultCollector.addOutputStream(targetStream);

        await this.prompt(updatedChatBody, dataSources, resultCollector);

        const result = findResult(resultCollector.result);
        const {value} = parseValue(result);

        return value;
    }

    async promptForJsonStreaming(body, targetSchema, dataSources = [], targetStream = this.responseStream) {
        const functions = [
            {
                name: 'answer',
                description: 'Answer the question',
                parameters: targetSchema,
            }
        ];

        const resultCollector = new StreamResultCollector();
        resultCollector.addTransformer((chunk)=>{
            try {
                chunk = JSON.parse(chunk);
                if (chunk.tool_calls && chunk.tool_calls.length > 0 && chunk.tool_calls[0].arguments) {
                    return chunk.tool_calls[0].arguments;
                }
                else if (chunk.tool_calls &&
                    chunk.tool_calls.length > 0 &&
                    chunk.tool_calls[0].function &&
                    chunk.tool_calls[0].function.arguments
                ) {
                    return chunk.tool_calls[0].function.arguments;
                }
            }catch(e){
                console.log(e);
            }
            return "";
        });
        resultCollector.addOutputStream(targetStream);

        const function_call = "answer";
        const result = await this.promptForFunctionCallStreaming(body, functions, function_call, dataSources, resultCollector);

        return result['arguments'];
    }

    // functions: [{name:'', description:'', parameters: schema}]
    // function_call: 'some_function_name'
    async promptForFunctionCall(body, functions, function_call, dataSources = [], targetStream = this.responseStream) {
        const updatedChatBody = {
            ...this.defaultBody,
            ...body,
            options: {
                ...(this.params.options || {}),
                ...(body.options || {}),
                functions: functions,
                ...(function_call ? {function_call: function_call} : {})
            }
        };

        const resultCollector = new StreamResultCollector();
        resultCollector.addTransformer(transform);

        await this.prompt(updatedChatBody, dataSources, resultCollector);

        const result = findResult(resultCollector.result);
        const {value} = parseValue(result);

        if(targetStream && this.passThrough){
            sendResultToStream(targetStream, resultCollector.result);
        }

        return value;
    }

    async promptForJson(body, targetSchema, dataSources = [], targetStream = this.responseStream) {
        const functions = [
            {
                name: 'answer',
                description: 'Answer the question',
                parameters: targetSchema,
            }
        ];

        const function_call = "answer";
        const result = await this.promptForFunctionCall(body, functions, function_call, dataSources, null);

        if(targetStream && this.passThrough){
            sendResultToStream(targetStream, result['arguments']);
        }

        return result['arguments'];
    }

    async promptForBoolean(body, dataSources = [], targetStream = this.responseStream) {
        const schema = {
            "type": "object",
            "properties": {
                "value": {
                    "type": "boolean",
                }
            },
            "required": ["value"],
            "additionalProperties": false
        };

        const result = await this.promptForJson(body, schema, dataSources, null);

        if(targetStream && this.passThrough){
            sendResultToStream(targetStream, result.value);
        }

        return result.value;
    }

    async promptForChoice(body, choices, dataSources = [], targetStream = this.responseStream) {

        const schema = {
            "type": "object",
            "properties": {
                "thought" : {
                    "type": "string",
                    "description": "How did you come up with this choice?",
                },
                "bestChoiceBasedOnThought": {
                    "type": "string",
                    "enum": choices,
                    "description": "The best choice based on your thought.",
                },
            },
            "required": ["thought","bestChoiceBasedOnThought"],
            "additionalProperties": false
        };

        const result = await this.promptForJson(body, schema, dataSources, null);

        if(targetStream  && this.passThrough){
            sendResultToStream(targetStream, result.bestChoiceBasedOnThought);
        }

        return result.bestChoiceBasedOnThought;
    }

}