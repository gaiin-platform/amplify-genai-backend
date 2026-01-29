//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import axios from 'axios';
import {getLogger} from "../common/logging.js";
import {logCriticalError} from "../common/criticalLogger.js";
import {trace} from "../common/trace.js";
import {doesNotSupportImagesInstructions, additionalImageInstruction, getImageBase64Content} from "../datasource/datasources.js";
import {sendErrorMessage, sendStateEventToStream, sendStatusEventToStream} from "../common/streams.js";
import {getSecretApiKey} from "../common/secrets.js";
import {newStatus, getThinkingMessage} from "../common/status.js";
import { getBudgetTokens } from "../common/params.js";

const logger = getLogger("gemini");

// Always fetch API key fresh for security - no caching of secrets
const getGeminiApiKey = async () => {
    return await getSecretApiKey("GEMINI_API_KEY");
};

const constructGeminiUrl = () => {
    return `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`;
}

export const chat = async (chatBody, writable) => {
    try {
        let body = {...chatBody};
        const options = {...body.options};
        delete body.options;
        const model = options.model;
        const modelId = (model && model.id) || "gemini-1.5-pro";
        const maxTokens = body.max_tokens || 2000;

        // Check for tools in both body (from UnifiedLLMClient) and options (legacy)
        let tools = body.tools || options.tools;
        if(!tools && options.functions){
            tools = options.functions.map((fn)=>{return {type: 'function', function: fn}});
            // Removed debug logging for performance
        }

        // Check for tool_choice in both body (from UnifiedLLMClient) and options (legacy)
        let tool_choice = body.tool_choice || options.tool_choice;
        if(!tool_choice && options.function_call){
            if(options.function_call === 'auto' || options.function_call === 'none'){
                tool_choice = options.function_call;
            }
            else {
                tool_choice = {type: 'function', function: {name: options.function_call}};
            }
            // Removed debug logging for performance
        }

        // Removed debug logging for performance

        let data = {
            ...body,
            "model": modelId,
            "stream": true,
            "stream_options": {"include_usage": true}
        };

        if (model.supportsReasoning) {
            const budget_tokens = getBudgetTokens({options}, maxTokens); 
            data.extra_body = { "google": {
                                "thinking_config": {
                                    "thinking_budget": budget_tokens,
                                    "include_thoughts": true
                                }
                              }
      }

        }

        if (data.max_tokens > model.outputTokenLimit) {
            data.max_tokens = model.outputTokenLimit
        }

        // append additional system prompt
        if (model.systemPrompt) {
            data.messages[0].content += `\n${model.systemPrompt}`
        }

        if (!model.supportsSystemPrompts) {
            data.messages = data.messages.map(m => { 
                return (m.role === 'system') ? {...m, role: 'user'} : m}
            );
        }
        if (!options.dataSourceOptions?.disableDataSources) {
            data.messages = await includeImageSources(body.imageSources, data.messages, model, writable);
        }
        
        // Gemini OpenAI compatibility endpoint accepts OpenAI format tools directly
        // No transformation needed - just pass tools as-is
        if(tools && tools.length > 0){
            data.tools = tools;
            // Default to auto if no tool_choice specified - this encourages the model to use tools
            data.tool_choice = tool_choice || "auto";
            logger.debug(`Passing ${tools.length} tools to Gemini OpenAI compatibility API with tool_choice: ${data.tool_choice}`);
            logger.debug(`Tool definition: ${JSON.stringify(tools[0])}`);
        } else if(tool_choice){
            // OpenAI compatibility endpoint accepts tool_choice directly
            data.tool_choice = tool_choice;
        }

        if (data.imageSources) delete data.imageSources;

        // OpenAI compatibility endpoint uses OpenAI format - keep string content for text messages
        // Only use array format for multimodal content (images)
        data.messages = data.messages.map(msg => {
            // Convert system role to user if not supported (Gemini OpenAI compat handles this too)
            const role = msg.role === 'system' && !model.supportsSystemPrompts ? 'user' : msg.role;

            // If content is already array (multimodal), ensure structure is correct
            if (Array.isArray(msg.content)) {
                // Make sure all text parts have proper structure
                const formattedContent = msg.content.map(item => {
                    if (item.type === "text") {
                        return {
                            type: "text",
                            text: item.text || "..."
                        };
                    }
                    return item;
                });

                return {
                    role,
                    content: formattedContent
                };
            }

            // String content - keep as string for OpenAI compatibility
            return {
                role,
                content: msg.content || "..."
            };
        });

        // Validate messages to prevent empty content
        if (data.messages && Array.isArray(data.messages)) {
            data.messages = data.messages.map(message => {
                // Handle message with empty content
                if (!message.content) {
                    return { ...message, content: "..." };
                }

                // Handle structured content (multimodal format)
                if (Array.isArray(message.content)) {
                    const updatedContent = message.content.map(item => {
                        if (item.type === "text" && (!item.text || item.text.trim() === "")) {
                            return { ...item, text: "..." };
                        }
                        return item;
                    });

                    // Ensure at least one text item exists in the content array
                    const hasTextItem = updatedContent.some(item => item.type === "text" && item.text);
                    if (!hasTextItem) {
                        updatedContent.push({ type: "text", text: "..." });
                    }

                    return { ...message, content: updatedContent };
                }

                // Handle empty string content
                if (typeof message.content === 'string' && !message.content.trim()) {
                    return { ...message, content: "..." };
                }

                return message;
            });
        }


        const headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + await getGeminiApiKey()  // Fresh API key fetch for security
        };

        const url = constructGeminiUrl();

        // Removed debug logging for performance
        trace(options.requestId, ["chat","gemini"], {modelId, url, data});
        
        // No status timer - let the actual response be the indication
        return streamAxiosResponseToWritable(url, writable, null, data, headers);
    } catch (error) {
        console.error('Exception in chat function:', error);
        
        // CRITICAL: Gemini API failure - capture full axios error details before re-throwing
        const sanitizedData = { ...data };
        delete sanitizedData.messages;
        delete sanitizedData.input;
        
        logCriticalError({
            functionName: 'gemini_chat',
            errorType: 'GeminiAPIFailure',
            errorMessage: `Gemini API failed: ${error.message || "Unknown error"}`,
            currentUser: options?.accountId || 'unknown',
            severity: 'HIGH',
            stackTrace: error.stack || '',
            context: {
                requestId: options?.requestId || 'unknown',
                modelId: modelId || 'unknown',
                httpStatus: error.response?.status || 'N/A',
                httpStatusText: error.response?.statusText || 'N/A',
                apiError: error.response?.data?.error || 'N/A',
                apiErrorMessage: error.response?.data?.error?.message || error.response?.data?.message || 'N/A',
                errorCode: error.code || 'N/A',
                axiosConfig: error.config ? { url: error.config.url, method: error.config.method } : 'N/A',
                requestConfig: sanitizedData
            }
        }).catch(err => logger.error('Failed to log critical error:', err));
        
        try {
            if (writable.writable && !writable.writableEnded) {
                sendErrorMessage(writable, "Error with Gemini request: " + (error.message || "Unknown error"));
            }
        } catch (sendError) {
            console.error('Error sending error message in chat catch block:', sendError);
        }
        
        throw error;
    }
}

function streamAxiosResponseToWritable(url, writableStream, statusTimer, data, headers) {
    return new Promise((resolve, reject) => {
        axios({
            data,
            headers: headers,
            method: 'post',
            url: url,
            responseType: 'stream'
        })
        .then(response => {
            let responseEnded = false;
            
            const streamError = (err) => {
                if (responseEnded) return;
                responseEnded = true;

                if (statusTimer) clearTimeout(statusTimer);

                if (!writableStream.writableEnded && writableStream.writable) {
                    sendErrorMessage(writableStream);
                    // Ensure stream is properly closed on error since we use { end: false }
                    try {
                        writableStream.end();
                    } catch (endErr) {
                        logger.error("Error ending stream:", endErr);
                    }
                }

                reject(err);
            };

            // Check if stream is already closed before starting
            if (writableStream.writableEnded) {
                if (statusTimer) clearTimeout(statusTimer);
                resolve();
                return;
            }
            
            // Simplified SSE formatting check
            response.data.on('data', (chunk) => {
                const data = chunk.toString();
                if (data && data.trim() && !data.startsWith('data:') && !writableStream.writableEnded) {
                    try {
                        // Validate it's JSON then format as SSE
                        JSON.parse(data);
                        writableStream.write(`data: ${data}\n\n`);
                        return; // Skip the pipe for this chunk since we handled it
                    } catch (e) {
                        // Not valid JSON, continue with normal pipe
                    }
                }
            });
            
            // Set up the pipe with proper error handling
            // Use { end: false } to prevent auto-closing the stream (tool loops need it open)
            response.data.pipe(writableStream, { end: false });
            
            // Handle the stream completion
            response.data.on('end', () => {
                // Sometimes Gemini doesn't properly send a [DONE] marker
                if (!responseEnded && !writableStream.writableEnded) {
                    try {
                        writableStream.write("data: [DONE]\n\n");
                    } catch (err) {
                        logger.error("Error sending DONE marker:", err);
                    }
                }
                
                responseEnded = true;
                if (statusTimer) clearTimeout(statusTimer);
                resolve();
            });
            
            // Handle the 'finish' event to resolve the promise if the stream supports events
            if (typeof writableStream.on === 'function') {
                // Handle the 'finish' event to resolve the promise
                writableStream.on('finish', () => {
                    responseEnded = true;
                    if (statusTimer) clearTimeout(statusTimer);
                    resolve();
                });

                // Handle errors
                writableStream.on('error', streamError);
            }
            
            // Always listen for errors on the response data
            response.data.on('error', streamError);
        })
        .catch((e) => {
            if (statusTimer) clearTimeout(statusTimer);

            if (!writableStream.writableEnded && writableStream.writable) {
                sendErrorMessage(writableStream);
                // Ensure stream is properly closed on error since we use { end: false }
                try {
                    writableStream.end();
                } catch (endErr) {
                    logger.error("Error ending stream:", endErr);
                }
            }
            
            let errorMessage = e.message;
            
            if(e.response && e.response.data) {
                logger.error("Error invoking Gemini API:", e.response.statusText);
                
                if(e.response.data.readable) {
                    let errorData = '';
                    
                    const handleErrorChunk = (chunk) => {
                        errorData += chunk;
                    };
                    
                    const handleErrorEnd = () => {
                        logger.error("Error data from Gemini API:", errorData);
                        e.response.data.removeListener('data', handleErrorChunk);
                        e.response.data.removeListener('end', handleErrorEnd);
                        reject(errorData || errorMessage);
                    };
                    
                    e.response.data.on('data', handleErrorChunk);
                    e.response.data.on('end', handleErrorEnd);
                    
                    // Add timeout for error stream
                    setTimeout(() => {
                        if (errorData === '') {
                            logger.error("Error stream timed out");
                            e.response.data.removeListener('data', handleErrorChunk);
                            e.response.data.removeListener('end', handleErrorEnd);
                            reject(errorMessage);
                        }
                    }, 5000);
                } else {
                    reject(e.response.data || errorMessage);
                }
            } else {
                logger.error("Error invoking Gemini API:", errorMessage);
                reject(errorMessage);
            }
        });
    });
}

async function includeImageSources(imageSources, messages, model, responseStream) {
    if (!imageSources || imageSources.length === 0) {
        return messages;
    }

    if (!model.supportsImages) {
        sendStateEventToStream(responseStream, doesNotSupportImagesInstructions);
        return messages;
    }

    try {
        // Process all images
        const imagePromises = imageSources.map(async (source) => {
            const base64Content = await getImageBase64Content(source);
            return {
                type: "image_url",
                image_url: {
                    url: `data:${source.mimeType};base64,${base64Content}`
                }
            };
        });
        
        const imageContents = await Promise.all(imagePromises);
        
        // Find the first user message to add images to
        const firstUserMsgIndex = messages.findIndex(m => m.role === 'user');
        
        if (firstUserMsgIndex !== -1) {
            const userMsg = messages[firstUserMsgIndex];
            
            // Convert to the OpenAI format for multimodal content
            if (typeof userMsg.content === 'string') {
                // Convert string content to array format
                messages[firstUserMsgIndex] = {
                    ...userMsg,
                    content: [
                        { type: "text", text: userMsg.content },
                        ...imageContents
                    ]
                };
            } else if (Array.isArray(userMsg.content)) {
                // Add to existing array content
                messages[firstUserMsgIndex] = {
                    ...userMsg,
                    content: [...userMsg.content, ...imageContents]
                };
            }
        }
        
        sendStateEventToStream(responseStream, additionalImageInstruction);
        return messages;
    } catch (error) {
        console.error("Error processing images:", error);
        return messages;
    }
} 