/**
 * Utility functions for LiteLLM integration
 * Restores functionality from deleted provider-specific files
 */

import { getLogger } from '../common/logging.js';
import { sendStateEventToStream } from '../common/streams.js';
import { getImageBase64Content, extractKey, doesNotSupportImagesInstructions } from '../datasource/datasources.js';

const logger = getLogger("litellm-utils");

/**
 * Detect if messages contain URL queries for web search
 */
export function containsUrlQuery(messages) {
    if (!Array.isArray(messages)) return false;
    
    const isLikelyUrl = (text) => {
        if (typeof text !== 'string' || text.length === 0) return false;
        if (/^data:/i.test(text)) return false;
        const urlPattern = /(?:https?:\/\/|www\.)[^\s<>"'()]+|(?:\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b(?:\/[^^\s<>"'()]*)?)/i;
        return urlPattern.test(text);
    };
    
    return messages.some((message) => {
        if (typeof message.content === 'string') {
            return isLikelyUrl(message.content);
        }
        return false;
    });
}

/**
 * Include image sources in messages (from deleted provider files)
 */
export async function includeImageSources(dataSources, messages, model, responseStream) {
    if (!dataSources || dataSources.length === 0) return messages;
    
    const msgLen = messages.length - 1;
    
    // Model doesn't support images
    if (!model.supportsImages) {
        messages[msgLen]['content'] += doesNotSupportImagesInstructions(model.name);
        return messages;
    }
    
    // Send image metadata to stream
    sendStateEventToStream(responseStream, {
        sources: {
            images: {
                sources: dataSources.map(ds => ({
                    ...ds,
                    contentKey: extractKey(ds.id)
                }))
            }
        }
    });
    
    const retrievedImages = [];
    const imageMessageContent = [];
    
    // Process each image source
    for (const ds of dataSources) {
        const encoded_image = await getImageBase64Content(ds);
        if (encoded_image) {
            retrievedImages.push({...ds, contentKey: extractKey(ds.id)});
            imageMessageContent.push({
                type: "image_url",
                image_url: {
                    url: `data:${ds.type};base64,${encoded_image}`,
                    detail: "high"
                }
            });
        }
    }
    
    // Add images to the last user message
    if (imageMessageContent.length > 0) {
        const lastMessage = messages[msgLen];
        
        // Convert string content to array format if needed
        if (typeof lastMessage.content === 'string') {
            lastMessage.content = [
                { type: "text", text: lastMessage.content },
                ...imageMessageContent
            ];
        } else if (Array.isArray(lastMessage.content)) {
            lastMessage.content.push(...imageMessageContent);
        }
    }
    
    return messages;
}

/**
 * Convert legacy function/tool formats to modern format
 */
export function convertToolsAndFunctions(options) {
    const converted = {};
    
    // Convert functions to tools
    if (options.functions && !options.tools) {
        converted.tools = options.functions.map(fn => ({
            type: 'function',
            function: fn
        }));
        logger.debug("Converted functions to tools format");
    } else if (options.tools) {
        converted.tools = options.tools;
    }
    
    // Convert function_call to tool_choice
    if (options.function_call && !options.tool_choice) {
        if (options.function_call === 'auto' || options.function_call === 'none') {
            converted.tool_choice = options.function_call;
        } else if (typeof options.function_call === 'string') {
            converted.tool_choice = {
                type: 'function',
                function: { name: options.function_call }
            };
        } else if (typeof options.function_call === 'object') {
            converted.tool_choice = {
                type: 'function',
                function: options.function_call
            };
        }
        logger.debug("Converted function_call to tool_choice format");
    } else if (options.tool_choice) {
        converted.tool_choice = options.tool_choice;
    }
    
    return converted;
}

/**
 * Detect if this is an O-model or reasoning model
 */
export function isReasoningModel(modelId) {
    return /^o\d/.test(modelId) || /^gpt-5/.test(modelId) || modelId.includes('o1');
}

/**
 * Get status message interval based on model type
 */
export function getStatusInterval(model) {
    if (model.supportsReasoning || isReasoningModel(model.id)) {
        return 15000; // 15 seconds for reasoning models
    }
    return 8000; // 8 seconds for regular models
}

/**
 * Prepare messages for models that don't support system prompts
 */
export function convertSystemMessages(messages, model) {
    if (!model.supportsSystemPrompts) {
        return messages.map(m => {
            if (m.role === 'system') {
                return { ...m, role: 'user' };
            }
            return m;
        });
    }
    return messages;
}

/**
 * Add web search tool if URL is detected (OpenAI specific)
 */
export function addWebSearchIfNeeded(messages, model, existingTools = []) {
    // Only for OpenAI models
    if (!model.provider || !model.provider.includes('OpenAI')) {
        return existingTools;
    }
    
    if (containsUrlQuery(messages)) {
        logger.debug("URL detected in messages, adding web_search_preview tool");
        
        // Check if web search already exists
        const hasWebSearch = existingTools.some(tool => 
            tool.type === 'web_search_preview' || 
            (tool.type === 'function' && tool.function?.name === 'web_search')
        );
        
        if (!hasWebSearch) {
            return [...existingTools, { type: "web_search_preview" }];
        }
    }
    
    return existingTools;
}