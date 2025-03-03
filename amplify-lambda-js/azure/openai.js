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
    if(modelId === "gpt-4-1106-Preview"){
        return "gpt-4-turbo";
    }
    else if(modelId === "gpt-4o"){
        return "gpt-4o";
    }    
    else if(modelId === "gpt-35-turbo"){
        return "gpt-3.5-turbo";
    }
    else if(modelId.startsWith("o3-mini")){
        return "o3-mini";
    }
    else {
        return modelId;
    }
}

const isOpenAIEndpoint = (url) => {
    return url.startsWith("https://api.openai.com");
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

    logger.debug("Calling OpenAI API with modelId: "+modelId);

    let data = {
       ...body,
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
        data.messages = await includeImageSources(body.imageSources, data.messages, model, writable);
    }
    

    if(tools){
        data.tools = tools;
    }
    if(tool_choice){
        data.tool_choice = tool_choice;
    }

    if (data.imageSources) delete data.imageSources;
    
    const config = await endpointProvider(modelId);

    const url = config.url;
    const isOpenAiEndpoint = isOpenAIEndpoint(url);

    const headers = isOpenAiEndpoint ?
        {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + config.key,
        } :
        {
            'Content-Type': 'application/json',
            'api-key': config.key,
        };

    const isOmodel = modelId.includes("o1") || modelId.includes("o3");


    if (isOmodel) {
        data = {max_completion_tokens: model.outputTokenLimit,
                messages: data.messages, model: modelId, stream: true
                }

        // was working previously and now isnt?
        // if (modelId.includes("o3")) {
            // Convert messages to O3 format and handle system->developer role
            // data.messages = data.messages.map(msg => ({
            //     role: msg.role === 'system' ? 'developer' : msg.role,
            //     content: [
            //         {   type: "text",
            //             text: msg.content
            //         }
            //     ]
            // }));
        // }
    }
    if (model.supportsReasoning) data.reasoning_effort = options.reasoningLevel ?? "low";
    
    if (isOpenAiEndpoint) data.model = translateModelToOpenAI(body.model);

    logger.debug("Calling OpenAI API with url: "+url);

    trace(options.requestId, ["chat","openai"], {modelId, url, data})

    function streamAxiosResponseToWritable(url, writableStream, statusTimer) {
        return new Promise((resolve, reject) => {
            axios({
                data,
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
                          console.log("O1 chunks recieved: ",numOfChunks)
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
                            console.log("O1 model error: ", err);
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
                    sendErrorMessage(writableStream, e.response.status, e.response.statusText);
                    if (e.response && e.response.data) {
                        console.log("Error invoking OpenAI API: ",e.response.statusText);

                        if (e.response.data.readable) {
                            // Stream the data to a variable or process it as it comes
                            let errorData = '';
                            e.response.data.on('data', (chunk) => {
                                errorData += chunk;
                            });
                            e.response.data.on('end', () => {
                                console.log("Error data from OpenAI API: ", errorData);
                                reject(errorData);
                                return;
                            });
                        }
                    }
                    console.log("Error invoking OpenAI API: "+e.message);
                    reject(e.message);
                });
        });
    }
    let statusTimer = null;
    const statusInterval = 8000;
    const handleSendStatusMessage = () => {
        // console.log("Sending status message...");
        sendStatusMessage(writable);
        statusTimer = setTimeout(handleSendStatusMessage, statusInterval);
        };

        // Start the timer
    statusTimer = setTimeout(handleSendStatusMessage, statusInterval)

    return streamAxiosResponseToWritable(url, writable, statusTimer);
}


async function includeImageSources(dataSources, messages, model, responseStream) {
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
            imageMessageContent.push( 
                { "type": "image_url",
                  "image_url": {"url": `data:${ds.type};base64,${encoded_image}`, "detail": "high"}
                } 
            )
        }
    }
    
    if (retrievedImages.length > 0) {
        sendStateEventToStream(responseStream, {
            sources: { images: { sources: retrievedImages} }
          });
    }

    // message must be a user message
    messages[msgLen]['content'] = [{ "type": "text",
                                     "text": additionalImageInstruction
                                    }, 
                                    ...imageMessageContent, 
                                    { "type": "text",
                                        "text": messages[msgLen]['content']
                                    }
                                  ]

    return messages 
}


const forceFlush = (responseStream) => {
    sendStatusEventToStream(responseStream, newStatus(
        {
            inProgress: false,
            message: " ".repeat(100000)
        }
    ));

}

const sendStatusMessage = (responseStream) => {
    const statusInfo = newStatus(
        {
            animated: true,
            inProgress: true,
            sticky: true,
            summary: getThinkingMessage(),
            icon: "info",
        }
    );

    sendStatusEventToStream(responseStream, statusInfo);

    forceFlush(responseStream);
    
}
