//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {chatWithDataStateless} from "./chatWithData.js";
import {newStatus} from "./status.js";
import {
    StreamResultCollector,
    sendResultToStream,
    findResult,
    endStream as terminateStream,
    sendStatusEventToStream, sendOutOfOrderModeEventToStream, sendStateEventToStream
} from "./streams.js";
import {transform} from "./chat/events/openaifn.js";
import {geminiTransform} from "./chat/events/gemini.js";
import {parseValue} from "./incrementalJsonParser.js";
import {setModel, isGeminiModel, getChatFn} from "./params.js";

// account object expected  to have user, accessToken, apiKeyId, accountId
export const getDefaultLLM = async (model, stream = null, account) => {
    const chatFn = async (body, writable, context) => {
        return await getChatFn(model, body, writable, context);
    }

    let params = {
        account,
        options: {
            model
        }
    };

    return new LLM(chatFn, params, stream);
}

export class LLM {

    constructor(chatFn, params, responseStream) {
        this.user = params.account.user;
        this.params = params;
        this.chatFn = chatFn;
        this.passThrough = false;
        this.responseStream = responseStream;
        this.defaultBody = {
            "max_tokens": params?.options?.maxTokens || 1000,
            "temperature": 1.0,
            "top_p": 1,
            "n": 1,
            "stream": true
        };
        console.log("Create LLM Instance")
    }

    clone(newChatFn=this.chatFn) {
        const llm = new LLM(
            newChatFn,
            this.params,
            this.responseStream);
        llm.passThrough = this.passThrough;
        return llm;
    }

    setModel(model) {
        setModel(this.params, model);
    }

    /**
     * Sets the source that outputs for this LLM will be sent to in the response stream.
     *
     * @param status
     */
    setSource(source) {
        this.params.options = {...this.params.options, source};
    }

    /**
     * Extracts data from a string based on a list of prefixes. Each
     * prefix should start a line in the input string. This is used to
     * parse output from the LLM of the form:
     *
     * XYZ: some value
     * QRS: another value
     * TUV: yet another value
     *
     * and would return:
     *
     * {"XYZ": "some value", "QRS": "another value", "TUV": "yet another value"}
     *
     * In this case, the prefixes would be ["XYZ", "QRS", "TUV"] (":" is added automatically)
     *
     * You can prompt the llm and instruct it to output a plan, etc. with specific prefixes
     * at the start of lines. You can then use this function to extract the data from the
     * response.
     *
     * @param inputString
     * @param prefixes
     * @returns {{}}
     */
    prefixesToData(inputString, prefixes) {
        if (!inputString) {
            console.log("Prefixes to Data is Empty");
            return {};
        }
        const lines = inputString.split('\n');
        const result = {};

        lines.forEach((line) => {
            prefixes.forEach((prefix) => {
                if (line.startsWith(prefix + ":")) {
                    result[prefix] = line.substring(prefix.length + 1).trim();
                }
            });
        });

        return result;
    }

    /**
     * Sends a status message to the underlying stream.
     *
     * @param status
     */
    sendStatus(status) {
        if (this.responseStream) {
            sendStatusEventToStream(this.responseStream, status);
        }
    }

    sendStateEventToStream(state) {
        if (this.responseStream) {
            sendStateEventToStream(this.responseStream, state);
        }
    }

    enableOutOfOrderStreaming() {
        if (this.responseStream) {
            sendOutOfOrderModeEventToStream(this.responseStream);
        }
    }

    forceFlush() {
        // hack to force flush on AWS lambda streaming
        this.sendStatus(newStatus(
            {
                inProgress: false,
                message: " ".repeat(100000)
            }));
    }

    endStream() {
        if (this.responseStream) {
            terminateStream(this.responseStream);
            try {
                if (!this.responseStream.writableEnded) {
                    this.responseStream.end();
                }
            } catch (err) {
                console.error('Error while terminating stream:', err);
            }
        }   
    }

    /**
     * Determines if promptForXYZ functions should pass the result through to the response stream
     * in addition to returning the result.
     */
    enablePassThrough() {
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
    disablePassThrough() {
        this.passThrough = false;
    }


    async prompt(body, dataSources = [], targetStream = this.responseStream) {

        const updatedParams = {
            ...this.params,
            model: (body.options && body.options.model) || (this.params.options && this.params.options.model),
            options: {
                ...this.params.options,
                ...body.options
            }
        };

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
        
        // Select appropriate transformer based on model type
        const model = this.params.options.model;
        if (model && isGeminiModel(model.id)) {
            resultCollector.addTransformer(geminiTransform);
        } else {
            resultCollector.addTransformer(transform);
        }
        
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
        resultCollector.addTransformer((chunk) => {
            try {
                chunk = JSON.parse(chunk);
                // Handle OpenAI format
                if (chunk.tool_calls && chunk.tool_calls.length > 0 && chunk.tool_calls[0].arguments) {
                    return chunk.tool_calls[0].arguments;
                } else if (chunk.tool_calls &&
                    chunk.tool_calls.length > 0 &&
                    chunk.tool_calls[0].function &&
                    chunk.tool_calls[0].function.arguments
                ) {
                    return chunk.tool_calls[0].function.arguments;
                }
                // Handle Gemini format
                else if (chunk.functionCall && chunk.functionCall.arguments) {
                    return chunk.functionCall.arguments;
                }
            } catch (e) {
                console.log(e);
            }
            return "";
        });
        resultCollector.addOutputStream(targetStream);

        const function_call = "answer";
        const result = await this.promptForFunctionCallStreaming(body, functions, function_call, dataSources, resultCollector);

        return result['arguments'];
    }

    async promptForData(body, dataSources = [], prompt, dataItems, targetStream = this.responseStream, checker = (result) => true, retries = 3, includeThoughts = true) {
        const dataDescs = Object.entries(dataItems).map(([name, schema]) => {
            return name + ": " + schema;
        }).join("\n");

        const systemPrompt = `
Analyze the task or question and output the requested data.

Your output with the data should be in the format:
\`\`\`data
thought: <INSERT THOUGHT>
${dataDescs}
\`\`\`

You MUST provide the requested data:

You ALWAYS output a \`\`\`data code block.
`
        const updatedChatBody = {
            ...this.defaultBody,
            ...body,
            messages: [
                ...(body.messages|| []),
                {
                    role: "system",
                    content: systemPrompt
                },
                {
                    role: "assistant",
                    content: prompt + "\n```data\n"
                }
            ],
            options: {
                ...(this.params.options || {}),
                ...(body.options || {}),
                systemPrompt: systemPrompt,
                prompt: prompt
            }
        };

        const prefixes = Object.keys(dataItems);

        if(includeThoughts) {
            prefixes.push("thought");
        }

        return await this.promptForPrefixData(updatedChatBody, prefixes, dataSources, targetStream, checker, retries);
    }

    async promptForString(body, dataSources = [], prompt, targetStream = this.responseStream, retries = 3) {

        const messages = prompt ? [
            ...(body.messages || []),
            {
                role: "user",
                content: prompt
            }
        ] : body.messages;

        const updatedChatBody = {
            ...this.defaultBody,
            ...body,
            messages,
            options: {
                ...(this.params.options || {}),
                ...(body.options || {}),
            }
        };

        const doPrompt = async () => {

            const resultCollector = new StreamResultCollector();
            if(this.passThrough && targetStream) {
                resultCollector.addOutputStream(targetStream);
            }

            await this.prompt(updatedChatBody, dataSources, resultCollector);

            const result = findResult(resultCollector.result);
            return result;
        }

        let result = null;
        for (let i = 0; i < retries; i++) {
            result = await doPrompt();
            if (result && result.length > 0) {
                break;
            }
        }

        return result || "";
    }


    async promptForPrefixData(body, prefixes, dataSources = [], targetStream = this.responseStream, checker = (result) => true, retries = 3) {
        const updatedChatBody = {
            ...this.defaultBody,
            ...body,
            options: {
                ...(this.params.options || {}),
                ...(body.options || {}),
            }
        };

        const doPrompt = async () => {

            const resultCollector = new StreamResultCollector();
            if(this.passThrough && targetStream) {
                resultCollector.addOutputStream(targetStream);
            }

            await this.prompt(updatedChatBody, dataSources, resultCollector);

            const result = findResult(resultCollector.result);
            const data = this.prefixesToData(result, prefixes);
            return data;
        }

        for (let i = 0; i < retries; i++) {
            const data = await doPrompt();
            if (!checker || checker(data)) {
                return data;
            }
        }

        return {};
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
        
        // Select appropriate transformer based on model type, just like in promptForFunctionCallStreaming
        const model = this.params.options.model;
        if (model && isGeminiModel(model.id)) {
            resultCollector.addTransformer(geminiTransform);
        } else {
            resultCollector.addTransformer(transform);
        }

        await this.prompt(updatedChatBody, dataSources, resultCollector);

        const result = findResult(resultCollector.result);
        const {value} = parseValue(result);

        if (targetStream && this.passThrough) {
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

        if (targetStream && this.passThrough) {
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

        if (targetStream && this.passThrough) {
            sendResultToStream(targetStream, result.value);
        }

        return result.value;
    }

    async promptForChoice(body, choices, dataSources = [], targetStream = this.responseStream) {

        const schema = {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "How did you come up with this choice?",
                },
                "bestChoiceBasedOnThought": {
                    "type": "string",
                    "enum": choices,
                    "description": "The best choice based on your thought.",
                },
            },
            "required": ["thought", "bestChoiceBasedOnThought"],
            "additionalProperties": false
        };

        const result = await this.promptForJson(body, schema, dataSources, null);

        if (targetStream && this.passThrough) {
            sendResultToStream(targetStream, result.bestChoiceBasedOnThought);
        }

        return result.bestChoiceBasedOnThought;
    }

}