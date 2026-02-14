//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import axios from 'axios';
import {getLogger} from "../common/logging.js";
import {logCriticalError} from "../common/criticalLogger.js";
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
    // Clean messages for responses API format
    // The responses API doesn't support tool_calls, tool_call_id in the same way as chat completions
    // Also, the responses API expects content to be an array of objects with type: 'input_text' or 'input_image'
    const messages = data.messages.map(msg => {
        let content = msg.content;

        // Convert tool messages to a format the responses API can understand
        // Tool results need to be converted to user messages with context
        if (msg.role === 'tool' && msg.tool_call_id) {
            content = `[Tool Result for ${msg.tool_call_id}]: ${msg.content}`;
        }

        // For assistant messages with tool_calls, include the tool call info in content
        if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
            const toolCallInfo = msg.tool_calls.map(tc =>
                `[Called tool: ${tc.function?.name || tc.name} with args: ${tc.function?.arguments || JSON.stringify(tc.arguments)}]`
            ).join('\n');
            content = (msg.content || '') + '\n' + toolCallInfo;
        }

        // Format content for responses API
        // If content is already an array (with images), keep it
        // Otherwise, wrap string content with the appropriate type based on role
        // User/system messages use 'input_text', assistant messages use 'output_text'
        const contentType = msg.role === 'assistant' ? 'output_text' : 'input_text';
        const formattedContent = Array.isArray(content)
            ? content.map(item => {
                // If item already has type, use it; otherwise use role-appropriate type
                if (item.type === 'text') {
                    return { type: contentType, text: item.text };
                } else if (item.type === 'image_url') {
                    return { type: 'input_image', source: { type: 'url', url: item.image_url.url } };
                }
                return item; // Already formatted
              })
            : [{ type: contentType, text: content || '' }];

        const cleaned = {
            role: msg.role === 'tool' ? 'user' : msg.role, // Tool role becomes user
            content: formattedContent
        };

        // Include name if present
        if (msg.name) cleaned.name = msg.name;

        return cleaned;
    });

    data.input = messages;
    data.max_output_tokens = data.max_output_tokens || data.max_tokens || data.max_completion_tokens || 4096;
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

    if (tools) data.tools = tools;
    if (tool_choice) data.tool_choice = tool_choice;

    if (data.hasOwnProperty('imageSources')) delete data.imageSources;
    if (data.hasOwnProperty('mcpClientSide')) delete data.mcpClientSide;
    // Capture webSearchEnabled before deleting it
    const webSearchEnabled = data.webSearchEnabled;
    if (data.hasOwnProperty('webSearchEnabled')) delete data.webSearchEnabled;
    
    const config = await endpointProvider(modelId, model.provider);

    if (!config || !config.url) {
        throw new Error('Failed to get LLM endpoint configuration');
    }

    let url = config.url;
    const isOpenAiEndpoint = isOpenAIEndpoint(url);
    let isCompletionEndpoint = isCompletionsEndpoint(url);

    // When tools are present, we MUST use the chat completions endpoint
    // The responses API doesn't support function calling properly
    if (tools && tools.length > 0 && !isCompletionEndpoint) {
        // Convert responses API URL to chat completions URL
        if (url.includes('/responses')) {
            url = url.replace('/responses', '/chat/completions');
            isCompletionEndpoint = true;
            logger.info(`🔧 Converted to chat completions endpoint for tool support: ${url}`);
        } else if (url.includes('/openai/deployments/')) {
            // Azure format - append /chat/completions if not present
            const baseUrl = url.split('?')[0];
            const queryString = url.includes('?') ? url.substring(url.indexOf('?')) : '';
            if (!baseUrl.endsWith('/chat/completions')) {
                url = baseUrl + '/chat/completions' + queryString;
                isCompletionEndpoint = true;
                logger.info(`🔧 Appended /chat/completions for tool support: ${url}`);
            }
        }
    }

    if (!options.dataSourceOptions?.disableDataSources) {
        const isNonStandardOpenAI = isOpenAiEndpoint && !isCompletionEndpoint && !url.includes('/chat/completions');
        data.messages = await includeImageSources(body.imageSources, data.messages, model, writable, isNonStandardOpenAI);
    }

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
    const hasTools = tools && tools.length > 0;

    if (isOmodel && !hasTools) {
        // Only use O-model special handling when no tools are present
        // When tools are present, use standard chat completions format for function calling support
        data = {[isCompletionEndpoint ? "max_completion_tokens" : "max_output_tokens"]: model.outputTokenLimit,
                messages: data.messages, model: modelId, stream: true
                }
    } else if (isOmodel && hasTools) {
        // O-model with tools: use chat completions format with max_completion_tokens
        // The responses API doesn't support function calling properly
        // Build a clean request with only supported fields
        data = {
            model: modelId,
            messages: data.messages,
            tools: data.tools,
            tool_choice: data.tool_choice || 'auto',
            max_completion_tokens: data.max_tokens || options.maxTokens || model.outputTokenLimit,
            stream: true
        };
        // Note: stream_options and reasoning_effort are NOT included for O-models with tools
        // as they may cause 400 errors
    }
    // Only add reasoning for non-tool requests (reasoning can conflict with tool calling)
    const disableReasoning = options.disableReasoning;
    if (model.supportsReasoning && !hasTools && !disableReasoning) {
        const reasoningLvl = options.reasoningLevel ?? "low";
        if (isCompletionEndpoint) {
            data.reasoning_effort = reasoningLvl;
        } else {
            data.reasoning = {effort: reasoningLvl, summary: "auto"};
        }
        logger.info(`Reasoning enabled with level: ${reasoningLvl}`);
    } else if (model.supportsReasoning && disableReasoning) {
        logger.info(`Reasoning disabled by user (disableReasoning=true)`);
    }
    
    // Only use responses API format when:
    // 1. Not a completions endpoint AND
    // 2. No custom tools are present (responses API doesn't support function calling properly)
    if (!isCompletionEndpoint && !hasTools) {
        data = translateDataToResponseBody(data);
        // if contains a url AND web search is enabled, add web search tool and convert to chat completions
        // (responses API doesn't support tools)
        if (isOpenAiEndpoint && webSearchEnabled && containsUrlQuery(data.input)) {
            data.tools = [{"type": "web_search_preview"}];
            // Convert back from responses API format to chat completions format
            data.messages = data.input;
            data.max_tokens = data.max_output_tokens;
            delete data.input;
            delete data.max_output_tokens;
            // Convert reasoning format from responses API to chat completions
            if (data.reasoning && typeof data.reasoning === 'object' && data.reasoning.effort) {
                data.reasoning_effort = data.reasoning.effort;
                delete data.reasoning;
            }
            // Convert URL to chat completions endpoint
            url = url.replace('/responses', '/chat/completions');
            isCompletionEndpoint = true;
            logger.info(`🔧 Converted to chat completions endpoint for web search tool: ${url}`);
        }
    }

    // Debug logging for tool requests
    if (hasTools) {
        logger.info(`🔧 OpenAI request with tools - isOmodel: ${isOmodel}, isCompletionEndpoint: ${isCompletionEndpoint}`);
        logger.info(`🔧 Request data keys: ${Object.keys(data).join(', ')}`);
        logger.debug(`🔧 Full request data: ${JSON.stringify(data, null, 2)}`);
    }

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
                        // Ensure stream is properly closed on error since we use { end: false }
                        if (!writableStream.writableEnded && writableStream.writable) {
                            try {
                                writableStream.end();
                            } catch (endErr) {
                                logger.error("Error ending stream:", endErr);
                            }
                        }
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
                        // Use { end: false } to prevent auto-closing the stream (tool loops need it open)
                        response.data.pipe(writableStream, { end: false });

                        // Handle the response data 'end' event to resolve the promise
                        // (with { end: false }, the pipe won't trigger 'finish' on writableStream)
                        response.data.on('end', finalizeSuccess);

                        // Handle the 'finish' event as backup
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

                    // Ensure stream is properly closed on error since we use { end: false }
                    if (!writableStream.writableEnded && writableStream.writable) {
                        try {
                            writableStream.end();
                        } catch (endErr) {
                            logger.error("Error ending stream:", endErr);
                        }
                    }

                    // CRITICAL: OpenAI/Azure API failure - capture full axios error details
                    const sanitizedRequestData = { ...requestData };
                    delete sanitizedRequestData.messages;
                    delete sanitizedRequestData.input;

                    logCriticalError({
                        functionName: 'openai_streamAxiosResponseToWritable',
                        errorType: 'OpenAIAPIFailure',
                        errorMessage: `OpenAI/Azure API failed: ${e.message || "Unknown error"}`,
                        currentUser: options?.user || 'unknown',
                        severity: 'HIGH',
                        stackTrace: e.stack || '',
                        context: {
                            httpStatus: e.response?.status || 'N/A',
                            httpStatusText: e.response?.statusText || 'N/A',
                            apiError: e.response?.data?.error || 'N/A',
                            apiErrorMessage: e.response?.data?.error?.message || 'N/A',
                            errorCode: e.code || 'N/A',
                            url: url || 'unknown',
                            modelId: data?.model || 'unknown',
                            hasTools: !!(data?.tools && data.tools.length > 0),
                            isRetry: !!retryWithoutTools,
                            requestConfig: sanitizedRequestData
                        }
                    }).catch(err => logger.error('Failed to log critical error:', err));

                    // Mark error as already having critical logging to prevent duplicate logging in router
                    e.criticalErrorLogged = true;

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
                                reject(e);
                                return;
                            });
                        }
                    }
                    logger.error("Error invoking OpenAI API:", e.message);
                    reject(e);
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


