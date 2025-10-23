//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { promptLiteLLMForData } from "../litellm/litellmClient.js";
import { newStatus } from "./status.js";
import {
    sendStatusEventToStream,
    sendOutOfOrderModeEventToStream,
    sendStateEventToStream,
    endStream
} from "./streams.js";
import { parseValue } from "./incrementalJsonParser.js";
import { getLogger } from "./logging.js";

const logger = getLogger("internalLLM");

/**
 * FastLLM - High-performance LLM class that provides the same interface as the original LLM class
 * but bypasses the expensive chatWithDataStateless pipeline for simple prompting operations.
 * 
 * This dramatically improves performance for StateBasedAssistants that do lots of internal
 * prompting without needing RAG or complex data source processing.
 */
export class InternalLLM {
    
    constructor(model, account, responseStream) {
        this.model = model;
        this.account = account;
        this.responseStream = responseStream;
        this.passThrough = false;
        this.params = {
            account,
            options: { model }
        };
        this.defaultBody = {
            "max_tokens": 1000,
            "temperature": 1.0,
            "top_p": 1,
            "n": 1,
            "stream": false // Most internal calls don't need streaming
        };
        logger.info("Created InternalLLM Instance for model:", model?.id || model);
    }

    clone() {
        const llm = new InternalLLM(this.model, this.account, this.responseStream);
        llm.passThrough = this.passThrough;
        return llm;
    }

    setModel(model) {
        this.model = model;
        this.params.options.model = model;
    }

    setSource(source) {
        this.params.options = { ...this.params.options, source };
    }

    /**
     * Extracts data from a string based on a list of prefixes - same as original LLM
     */
    prefixesToData(inputString, prefixes) {
        if (!inputString) {
            logger.debug("Prefixes to Data is Empty");
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

    // Status and stream management methods - same as original
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
        this.sendStatus(newStatus({
            inProgress: false,
            message: " ".repeat(100000)
        }));
    }

    endStream() {
        if (this.responseStream) {
            endStream(this.responseStream);
            try {
                if (!this.responseStream.writableEnded) {
                    this.responseStream.end();
                }
            } catch (err) {
                logger.error('Error while terminating stream:', err);
            }
        }
    }

    enablePassThrough() {
        this.passThrough = true;
    }

    disablePassThrough() {
        this.passThrough = false;
    }

    /**
     * 🚀 BREAKTHROUGH: Direct LiteLLM prompting bypassing chatWithDataStateless
     * This is where the massive performance gains come from!
     */
    async promptForString(body, dataSources = [], prompt, targetStream = null, retries = 3, streamToUser = false) {
        // If we have data sources, we might need RAG - could fall back to original LLM
        // For now, let's bypass for all internal calls
        
        const messages = prompt ? [
            ...(body.messages || []),
            {
                role: "user",
                content: prompt
            }
        ] : body.messages;

        const requestId = this.params?.options?.requestId || `litellm-${Date.now()}`;
        
        try {
            if (streamToUser && this.responseStream) {
                // Stream to user - use callLiteLLM directly for real-time streaming
                const { callLiteLLM } = await import("../litellm/litellmClient.js");
                
                const chatRequest = {
                    messages,
                    max_tokens: this.defaultBody.max_tokens,
                    temperature: this.defaultBody.temperature,
                    options: {
                        model: this.model
                    }
                };
                
                const result = await callLiteLLM(
                    chatRequest,
                    this.model,
                    this.account,
                    this.responseStream,
                    dataSources,
                    true // streamToUser=true for real-time streaming
                );
                
                return result || "";
            } else {
                // Behind-the-scenes call - use promptLiteLLMForData
                const result = await promptLiteLLMForData(
                    messages,
                    this.model,
                    prompt || "Continue the conversation",
                    null, // No structured output for string prompts
                    this.account,
                    requestId,
                    { 
                        maxTokens: this.defaultBody.max_tokens,
                        temperature: this.defaultBody.temperature
                    }
                );

                return result || "";
            }
        } catch (error) {
            logger.error("InternalLLM promptForString error:", error);
            if (retries > 0) {
                return await this.promptForString(body, dataSources, prompt, targetStream, retries - 1, streamToUser);
            }
            return "";
        }
    }

    /**
     * 🚀 BREAKTHROUGH: Direct structured data extraction via LiteLLM
     */
    async promptForData(body, dataSources = [], prompt, dataItems, targetStream = null, checker = (result) => true, retries = 3, includeThoughts = true) {
        const messages = [
            ...(body.messages || []),
            {
                role: "user", 
                content: prompt
            }
        ];

        // Convert dataItems to schema format
        const schema = {};
        Object.entries(dataItems).forEach(([key, description]) => {
            schema[key] = description;
        });

        if (includeThoughts) {
            schema.thought = "explain your reasoning";
        }

        const requestId = this.params?.options?.requestId || `litellm-${Date.now()}`;

        for (let i = 0; i < retries; i++) {
            try {
                const result = await promptLiteLLMForData(
                    messages,
                    this.model,
                    prompt,
                    schema,
                    this.account,
                    requestId,
                    { 
                        maxTokens: this.defaultBody.max_tokens,
                        temperature: 0.1, // Lower temperature for structured data
                        streamToUser: false
                    }
                );

                if (result && checker(result)) {
                    return result;
                }
            } catch (error) {
                logger.error(`InternalLLM promptForData attempt ${i + 1} error:`, error);
            }
        }

        return {};
    }

    /**
     * 🚀 BREAKTHROUGH: Direct prefix data extraction
     */
    async promptForPrefixData(body, prefixes, dataSources = [], targetStream = null, checker = (result) => true, retries = 3) {
        for (let i = 0; i < retries; i++) {
            try {
                const result = await this.promptForString(body, dataSources, null, targetStream, 1);
                const data = this.prefixesToData(result, prefixes);
                
                if (!checker || checker(data)) {
                    return data;
                }
            } catch (error) {
                logger.error(`InternalLLM promptForPrefixData attempt ${i + 1} error:`, error);
            }
        }

        return {};
    }

    /**
     * 🚀 BREAKTHROUGH: Direct JSON extraction via LiteLLM
     */
    async promptForJson(body, targetSchema, dataSources = [], targetStream = null) {
        const messages = [
            ...(body.messages || [])
        ];

        const requestId = this.params?.options?.requestId || `litellm-${Date.now()}`;

        try {
            const result = await promptLiteLLMForData(
                messages,
                this.model,
                "Provide a JSON response matching the required schema",
                targetSchema,
                this.account,
                requestId,
                { 
                    maxTokens: this.defaultBody.max_tokens,
                    temperature: 0.1,
                    streamToUser: false
                }
            );

            return result;
        } catch (error) {
            logger.error("InternalLLM promptForJson error:", error);
            return {};
        }
    }

    /**
     * 🚀 BREAKTHROUGH: Direct boolean extraction  
     */
    async promptForBoolean(body, dataSources = [], targetStream = null) {
        const schema = {
            "type": "object",
            "properties": {
                "value": {
                    "type": "boolean"
                }
            },
            "required": ["value"],
            "additionalProperties": false
        };

        const result = await this.promptForJson(body, schema, dataSources, targetStream);
        return result.value || false;
    }

    /**
     * 🚀 BREAKTHROUGH: Direct choice extraction
     */
    async promptForChoice(body, choices, dataSources = [], targetStream = null) {
        const schema = {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "How did you come up with this choice?"
                },
                "bestChoiceBasedOnThought": {
                    "type": "string", 
                    "enum": choices,
                    "description": "The best choice based on your thought."
                }
            },
            "required": ["thought", "bestChoiceBasedOnThought"],
            "additionalProperties": false
        };

        const result = await this.promptForJson(body, schema, dataSources, targetStream);
        return result.bestChoiceBasedOnThought;
    }

    // Compatibility methods that aren't needed for StateBasedAssistants but maintain interface
    async prompt(body, dataSources = [], targetStream = null) {
        logger.warn("InternalLLM.prompt() called - this should not happen for StateBasedAssistants");
        return await this.promptForString(body, dataSources, null, targetStream);
    }

    async promptForFunctionCall(body, functions, function_call, dataSources = [], targetStream = null) {
        logger.warn("InternalLLM.promptForFunctionCall() called - not optimized yet");
        // Could implement if needed, but StateBasedAssistants don't use this
        return {};
    }

    async promptForFunctionCallStreaming(body, functions, function_call, dataSources = [], targetStream = null) {
        logger.warn("InternalLLM.promptForFunctionCallStreaming() called - not optimized yet");
        return {};
    }

    async promptForJsonStreaming(body, targetSchema, dataSources = [], targetStream = null) {
        logger.warn("InternalLLM.promptForJsonStreaming() called - not optimized yet");
        return {};
    }
}

/**
 * Factory function to create InternalLLM instances for high-performance internal operations
 */
export const getInternalLLM = (model, account, responseStream = null) => {
    return new InternalLLM(model, account, responseStream);
};