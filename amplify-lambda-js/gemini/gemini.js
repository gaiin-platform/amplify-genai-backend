//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import axios from 'axios';
import {getLogger} from "../common/logging.js";
import {trace} from "../common/trace.js";
import {doesNotSupportImagesInstructions, additionalImageInstruction, getImageBase64Content} from "../datasource/datasources.js";
import {sendErrorMessage, sendStateEventToStream, sendStatusEventToStream} from "../common/streams.js";
import {getSecretApiKey} from "../common/secrets.js";
import {newStatus, getThinkingMessage} from "../common/status.js";
import { getBudgetTokens } from "../common/params.js";

const logger = getLogger("gemini");

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

        let tools = options.tools;
        if(!tools && options.functions){
            tools = options.functions.map((fn)=>{return {type: 'function', function: fn}});
            logger.debug(tools);
        }

        let tool_choice = options.tool_choice;
        if(!tool_choice && options.function_call){
            if(options.function_call === 'auto' || options.function_call === 'none'){
                tool_choice = options.function_call;
            }
            else {
                tool_choice = {type: 'function', function: {name: options.function_call}};
            }
            logger.debug(tool_choice);
        }

        logger.debug("Calling Gemini API with modelId: "+modelId);

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
        
        if(tools){
            data.tools = tools;
        }
        if(tool_choice){
            data.tool_choice = tool_choice;
        }

        if (data.imageSources) delete data.imageSources;
        
        data.messages = data.messages.map(msg => {
            // If content is a string, convert to proper format for Gemini
            if (typeof msg.content === 'string') {
                return {
                    role: msg.role === 'system' ? 'user' : msg.role,
                    content: [
                        { 
                            type: "text", 
                            text: msg.content 
                        }
                    ]
                };
            }
            // If content is already array (multimodal), ensure structure is correct
            else if (Array.isArray(msg.content)) {
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
                    role: msg.role === 'system' ? 'user' : msg.role,
                    content: formattedContent
                };
            }
            
            return msg;
        });

        // Validate messages to prevent empty text parameter error
        if (data.messages && Array.isArray(data.messages)) {
            data.messages = data.messages.map(message => {
                // Handle message with empty content
                if (!message.content) {
                    logger.debug("Found empty message content, adding placeholder text");
                    return { ...message, content: "..." };
                }
                
                // Handle structured content (multimodal format)
                if (Array.isArray(message.content)) {
                    const updatedContent = message.content.map(item => {
                        if (item.type === "text" && (!item.text || item.text.trim() === "")) {
                            logger.debug("Found empty text field in multimodal content, adding placeholder text");
                            return { ...item, text: "..." };
                        }
                        return item;
                    });
                    
                    // Ensure at least one text item exists in the content array
                    const hasTextItem = updatedContent.some(item => item.type === "text" && item.text);
                    if (!hasTextItem) {
                        logger.debug("No text items found in multimodal content, adding placeholder text");
                        updatedContent.push({ type: "text", text: "..." });
                    }
                    
                    return { ...message, content: updatedContent };
                }
                
                return message;
            });
        }


        const headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + await getSecretApiKey("GEMINI_API_KEY")
        };

        const url = constructGeminiUrl();

        logger.debug("Calling Gemini API with url: "+url);
        trace(options.requestId, ["chat","gemini"], {modelId, url, data});
        
        // Set up status message timer for long-running Gemini requests
        let statusTimer = null;
        const statusInterval = model.supportsReasoning ? 15000: 8000;
        
        const sendStatusMessage = (responseStream) => {
            const statusInfo = newStatus({
                animated: true,
                inProgress: true,
                sticky: true,
                summary: getThinkingMessage(),
                icon: "info",
            });

            sendStatusEventToStream(responseStream, statusInfo);
            // Force flush to ensure client receives the message
            sendStatusEventToStream(responseStream, newStatus({
                inProgress: false,
                message: " ".repeat(100000)
            }));
        };

        const handleSendStatusMessage = () => {
            sendStatusMessage(writable);
            statusTimer = setTimeout(handleSendStatusMessage, statusInterval);
        };

        // Start the status timer
        statusTimer = setTimeout(handleSendStatusMessage, statusInterval);

        return streamAxiosResponseToWritable(url, writable, statusTimer, data, headers);
    } catch (error) {
        console.error('Exception in chat function:', error);
        
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
            let chunkCounter = 0;
            
            const streamError = (err) => {
                if (responseEnded) return;
                responseEnded = true;
                
                if (statusTimer) clearTimeout(statusTimer);
                
                if (!writableStream.writableEnded && writableStream.writable) {
                    sendErrorMessage(writableStream);
                }
                
                reject(err);
            };

            // Check if stream is already closed before starting
            if (writableStream.writableEnded) {
                if (statusTimer) clearTimeout(statusTimer);
                resolve();
                return;
            }
            
            // Add event counter logging for debugging
            response.data.on('data', (chunk) => {
                chunkCounter++;
                if (chunkCounter % 10 === 0) {
                    logger.debug(`Received ${chunkCounter} chunks from Gemini API`);
                }
                
                // Check for missing SSE format and fix if needed
                const data = chunk.toString();
                if (data && data.trim() && !data.startsWith('data:') && !writableStream.writableEnded) {
                    try {
                        // Try to parse as JSON and then format as SSE
                        const jsonObj = JSON.parse(data);
                        const sseFormatted = `data: ${data}\n\n`;
                        writableStream.write(sseFormatted);
                        return; // Skip the pipe for this chunk since we handled it
                    } catch (e) {
                        // Not valid JSON, continue with normal pipe
                    }
                }
            });
            
            // Set up the pipe with proper error handling
            response.data.pipe(writableStream);
            
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