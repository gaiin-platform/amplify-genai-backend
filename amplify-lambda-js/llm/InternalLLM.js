//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { callUnifiedLLM, promptUnifiedLLMForData } from "./UnifiedLLMClient.js";
import { getLogger } from "../common/logging.js";

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

    enablePassThrough() {
        this.passThrough = true;
    }

    /**
     * 🚀 BREAKTHROUGH: Direct native provider prompting bypassing chatWithDataStateless
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

        const requestId = this.params?.options?.requestId || `unified-${Date.now()}`;
        
        try {
            if (streamToUser && this.responseStream) {
                // Stream to user - use callUnifiedLLM directly for real-time streaming
                const result = await callUnifiedLLM(
                    this.params,
                    messages,
                    this.responseStream,
                    {
                        max_tokens: this.defaultBody.max_tokens,
                        temperature: this.defaultBody.temperature
                    }
                );
                
                return result?.content || "";
            } else {
                // Behind-the-scenes call - non-streaming
                const result = await callUnifiedLLM(
                    this.params,
                    messages,
                    null, // No streaming
                    {
                        max_tokens: this.defaultBody.max_tokens,
                        temperature: this.defaultBody.temperature
                    }
                );

                return result?.content || "";
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
     * 🚀 BREAKTHROUGH: Direct structured data extraction via native providers
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

        const requestId = this.params?.options?.requestId || `unified-${Date.now()}`;

        for (let i = 0; i < retries; i++) {
            try {
                // Build proper schema format for function calling
                const outputFormat = {
                    type: "object",
                    properties: {},
                    required: Object.keys(schema).filter(k => k !== 'thought')
                };
                
                Object.entries(schema).forEach(([key, description]) => {
                    outputFormat.properties[key] = {
                        type: "string",
                        description
                    };
                });
                
                const result = await promptUnifiedLLMForData(
                    this.params,
                    messages,
                    outputFormat,
                    null // No streaming for structured data
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
     * 🚀 BREAKTHROUGH: Direct JSON extraction via native providers
     */
    async promptForJson(body, targetSchema, dataSources = [], targetStream = null) {
        const messages = [
            ...(body.messages || [])
        ];

        const requestId = this.params?.options?.requestId || `unified-${Date.now()}`;

        try {
            const result = await promptUnifiedLLMForData(
                this.params,
                messages,
                targetSchema,
                null // No streaming for structured data
            );

            return result;
        } catch (error) {
            logger.error("InternalLLM promptForJson error:", error);
            return {};
        }
    }
}

/**
 * Factory function to create InternalLLM instances for high-performance internal operations
 */
export const getInternalLLM = (model, account, responseStream = null) => {
    return new InternalLLM(model, account, responseStream);
};