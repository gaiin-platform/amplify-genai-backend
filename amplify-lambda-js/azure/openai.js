//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import axios from 'axios';
import {getLogger} from "../common/logging.js";
import {trace} from "../common/trace.js";
import {doesNotSupportImagesInstructions, additionalImageInstruction, getImageBase64Content} from "../datasource/datasources.js";
import {sendErrorMessage, sendStateEventToStream, sendStatusEventToStream} from "../common/streams.js";
import {extractKey} from "../datasource/datasources.js";
import {newStatus, getThinkingMessage} from "../common/status.js";

const logger = getLogger("openai");


export const translateModelToOpenAI = (modelId) => {
    if (modelId === "gpt-4-1106-Preview"){
        return "gpt-4-turbo";
    } else if (modelId === "gpt-35-turbo"){
        return "gpt-3.5-turbo";
    }
    
    return modelId;
}

export const translateDataToResponseBody = (data) => {
    const messages = [...data.messages];
    data.input = messages;
    data.max_output_tokens = data.max_tokens || data.max_completion_tokens || 1000;
    if (data.max_output_tokens < 16) data.max_output_tokens = 16;
    delete data.messages;
    delete data.max_tokens;
    delete data.max_completion_tokens;
    delete data.stream_options;
    delete data.temperature;
    delete data.n;
    return data;
}

const isOpenAIEndpoint = (url) => {
    return url.startsWith("https://api.openai.com");
}

const isCompletionsEndpoint = (url) => {
    return url.includes("/completions");
}

export const chat = async (endpointProvider, chatBody, writable) => {
    let body = {...chatBody};
    const options = {...body.options};
    delete body.options;
    const model = options.model;
    const modelId = (model && model.id) || "gpt-4-1106-Preview";

    let tools = options.tools;
    if(!tools && options.functions){
        tools = options.functions.map((fn)=>{return {type: 'function', function: fn}});
        // Removed debug logging for performance
    }

    let tool_choice = options.tool_choice;
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

    // Clean messages - remove fields that OpenAI doesn't accept
    const cleanMessages = body.messages.map(msg => ({
        role: msg.role,
        content: msg.content,
        ...(msg.name && { name: msg.name }),
        ...(msg.function_call && { function_call: msg.function_call }),
        ...(msg.tool_calls && { tool_calls: msg.tool_calls }),
        ...(msg.tool_call_id && { tool_call_id: msg.tool_call_id })
    }));

    let data = {
       ...body,
       messages: cleanMessages,
       "model": modelId,
       "stream": true,
       "stream_options": {"include_usage": true}
    };

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
        const config = await endpointProvider(modelId, model.provider);
        const isOpenAI = config?.url && isOpenAIEndpoint(config.url);
        const isNonStandardOpenAI = isOpenAI && !isCompletionsEndpoint(config.url) && !config.url.includes('/chat/completions');
        data.messages = await includeImageSources(body.imageSources, data.messages, model, writable, isNonStandardOpenAI);
    }
    

    if (tools) data.tools = tools;
    if (tool_choice) data.tool_choice = tool_choice;

    if (data.hasOwnProperty('imageSources')) delete data.imageSources;
    
    const config = await endpointProvider(modelId, model.provider);

    if (!config || !config.url) {
        throw new Error('Failed to get LLM endpoint configuration');
    }

    const url = config.url;
    const isOpenAiEndpoint = isOpenAIEndpoint(url);
    const isCompletionEndpoint = isCompletionsEndpoint(url);

    const headers = isOpenAiEndpoint ?
        {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + config.key,
        } :
        {
            'Content-Type': 'application/json',
            'api-key': config.key,
        };

    if (isOpenAiEndpoint) data.model = translateModelToOpenAI(body.model);

    const isOmodel = /^o\d/.test(modelId) || /^gpt-5/.test(modelId);

    if (isOmodel) {
        data = {[isCompletionEndpoint ? "max_completion_tokens" : "max_output_tokens"]: model.outputTokenLimit,
                messages: data.messages, model: modelId, stream: true
                }
    }
    if (model.supportsReasoning) {
        const reasoningLvl = options.reasoningLevel ?? "low";
        if (isCompletionEndpoint) {
            data.reasoning_effort = reasoningLvl;
        } else {
            data.reasoning = {effort: reasoningLvl, summary: "auto"};
        }
    }
    
    if (!isCompletionEndpoint) {
        data = translateDataToResponseBody(data);
        // if contains a url the 
        if (isOpenAiEndpoint && containsUrlQuery(data.input)) {
            data.tools = [{"type": "web_search_preview"}];
        }
    }

    // Removed debug logging for performance

    trace(options.requestId, ["chat","openai"], {modelId, url, data})

    function streamAxiosResponseToWritable(url, writableStream, statusTimer, retryWithoutTools = false) {
        return new Promise((resolve, reject) => {
            // Use a copy of data for this attempt
            let requestData = {...data};
            
            // If retrying, remove tools
            if (retryWithoutTools && requestData.tools) {
                delete requestData.tools;
                // Retrying request without tools
            }
            
            axios({
                data: requestData,
                headers: headers,
                method: 'post',
                url: url,
                responseType: 'stream'
            })
                .then(response => {

                    const streamError = (err) => {
                        clearTimeout(statusTimer);
                        sendErrorMessage(writableStream, err.response?.status, err.response?.statusText);
                        reject(err);
                    };
                    const finalizeSuccess = () => {
                        clearTimeout(statusTimer);
                        resolve();
                    };

                    if (!data.stream) {
                    
                        let jsonBuffer = '';
                        let numOfChunks = 0;
                        
                        response.data.on('data', chunk => {
                          jsonBuffer += chunk.toString();
                          numOfChunks++;
                          // O1 chunks received
                        });
                    
                        response.data.on('end', () => {
                          // now we have the entire JSON in jsonBuffer
                          try {
                            const dataObj = JSON.parse(jsonBuffer);
                            const modifiedData = `data: ${JSON.stringify(dataObj)}`; 
                            writableStream.write(modifiedData);
                            writableStream.end();
                            finalizeSuccess();
                          } catch (err) {
                            // handle JSON parse error
                            logger.error("O1 model error:", err);
                            streamError(err);
                          }
                        });

                        // Handle errors during stream
                        response.data.on('error',streamError);
                        // Also handle if writableStream finishes/errors
                        writableStream.on('finish', finalizeSuccess);
                        writableStream.on('error', streamError);



                    } else {
                        response.data.pipe(writableStream);
                        // Handle the 'finish' event to resolve the promise
                        writableStream.on('finish', finalizeSuccess);

                        // Handle errors
                        response.data.on('error', streamError);
                        writableStream.on('error', streamError);
                    }
    
                    
                })
                .catch((e)=>{
                    if (statusTimer) clearTimeout(statusTimer);
                    
                    // If we have tools and haven't already retried, try again without tools
                    if (!retryWithoutTools && data.tools && data.tools.length > 0) {
                        // Request failed with tools, retrying without tools
                        streamAxiosResponseToWritable(url, writableStream, statusTimer, true)
                            .then(resolve)
                            .catch(reject);
                        return;
                    }
                    
                    sendErrorMessage(writableStream, e.response?.status, e.response?.statusText);
                    if (e.response && e.response.data) {
                        logger.error("Error invoking OpenAI API:", e.response.statusText);

                        if (e.response.data.readable) {
                            // Stream the data to a variable or process it as it comes
                            let errorData = '';
                            e.response.data.on('data', (chunk) => {
                                errorData += chunk;
                            });
                            e.response.data.on('end', () => {
                                logger.error("Error data from OpenAI API:", errorData);
                                reject(errorData);
                                return;
                            });
                        }
                    }
                    logger.error("Error invoking OpenAI API:", e.message);
                    reject(e.message);
                });
        });
    }
    // No status timer - let the actual response be the indication
    return streamAxiosResponseToWritable(url, writable, null);
}

const containsUrlQuery = (messages) => {
    if (!Array.isArray(messages)) return false;

    const isLikelyUrl = (text) => {
        if (typeof text !== 'string' || text.length === 0) return false;
        if (/^data:/i.test(text)) return false;
        const urlPattern = /(?:https?:\/\/|www\.)[^\s<>"'()]+|(?:\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b(?:\/[^^\s<>"'()]*)?)/i;
        return urlPattern.test(text);
    };

    let hasUrl = false;
    let hasImageOnly = false;

    messages.forEach((message) => {
        const content = message && message.content;
        if (typeof content === 'string') {
            if (isLikelyUrl(content)) hasUrl = true;
        }
        if (Array.isArray(content)) {
            let hasTextUrl = false;
            let hasImage = false;
            
            content.forEach((part) => {
                if (!part) return;
                if (typeof part === 'string' && isLikelyUrl(part)) {
                    hasTextUrl = true;
                }
                if ((part.type === 'text' || part.type === 'input_text') && typeof part.text === 'string') {
                    if (isLikelyUrl(part.text)) hasTextUrl = true;
                }
                if (part.type === 'image' || part.type === 'input_image' || part.type === 'image_url') {
                    hasImage = true;
                }
            });
            
            if (hasTextUrl) hasUrl = true;
            if (hasImage && !hasTextUrl) hasImageOnly = true;
        }
    });

    return hasUrl && !hasImageOnly;
}

async function includeImageSources(dataSources, messages, model, responseStream, isNonStandardOpenAI = false) {
    if (!dataSources || dataSources.length === 0)  return messages;
    const msgLen = messages.length - 1;
    // does not support images
    if (!model.supportsImages) {          
        messages[msgLen]['content'] += doesNotSupportImagesInstructions(model.name);
        return messages;
    }

    sendStateEventToStream(responseStream, {
        sources: {
            images: {
                sources: dataSources.map(ds => {
                    return {...ds, contentKey: extractKey(ds.id)}
                })
            }
        }
      });
    const retrievedImages = [];

    let imageMessageContent = [];
    
    for (let i = 0; i < dataSources.length; i++) {
        const ds = dataSources[i];
        const encoded_image = await getImageBase64Content(ds);
        if (encoded_image) {
            retrievedImages.push({...ds, contentKey: extractKey(ds.id)});
            if (isNonStandardOpenAI) {
                imageMessageContent.push({
                    "type": "input_image",
                    "image_url": `data:${ds.type};base64,${encoded_image}`
                });
            } else {
                imageMessageContent.push( 
                    { "type": "image_url",
                      "image_url": {"url": `data:${ds.type};base64,${encoded_image}`, "detail": "high"}
                    } 
                );
            }
        }
    }
    
    if (retrievedImages.length > 0) {
        sendStateEventToStream(responseStream, {
            sources: { images: { sources: retrievedImages} }
          });
    }

    // message must be a user message
    const textType = isNonStandardOpenAI ? "input_text" : "text";
    messages[msgLen]['content'] = [{ "type": textType,
                                     "text": additionalImageInstruction
                                    }, 
                                    ...imageMessageContent, 
                                    { "type": textType,
                                        "text": messages[msgLen]['content']
                                    }
                                  ]

    return messages 
}


// Status messages removed for better performance - let the actual response be the indication
