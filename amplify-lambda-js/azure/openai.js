//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import axios from 'axios';
import {getLogger} from "../common/logging.js";
import {trace} from "../common/trace.js";
import {doesNotSupportImagesInstructions, additionalImageInstruction, getImageBase64Content} from "../datasource/datasources.js";
import { Transform } from "stream";
import {sendErrorMessage, sendStateEventToStream} from "../common/streams.js";
import {extractKey} from "../datasource/datasources.js";
import {jsonrepair} from "jsonrepair";

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
       model: modelId,
        "stream": true,
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

    const isO1model = ["o1-mini", "o1-preview"].includes(modelId);

    if (isOpenAiEndpoint && !isO1model) data.model = translateModelToOpenAI(body.model);

    if (isO1model) {
        data = {max_completion_tokens: model.outputTokenLimit,
                messages: data.messages
                }
        if (isOpenAiEndpoint) {
            data.stram = true
            // data.stream_options = {"include_usage": True}
        }
    }

    logger.debug("Calling OpenAI API with url: "+url);

    trace(options.requestId, ["chat","openai"], {modelId, url, data})

    function streamAxiosResponseToWritable(url, writableStream) {
        return new Promise((resolve, reject) => {
            axios({
                data,
                headers: headers,
                method: 'post',
                url: url,
                responseType: 'stream'
            })
                .then(response => {

                    if (isO1model && !isOpenAiEndpoint) { // azure currently does not support streaming 

                        const transformStream = new Transform({
                            transform(chunk, encoding, callback) {
                                // Convert chunk to string, remove newlines, and add 'data: ' prefix
                                const sanitizeData = jsonrepair( chunk.toString().replace(/\n/g, '') );

                                const modifiedData = `data: ${sanitizeData}`;   
                                // console.log("modifiedData: ", chunk.toString());

                                this.push(modifiedData);
                                callback();
                            }
                        });
                        response.data
                        .pipe(transformStream)
                        .pipe(writableStream);

                    } else {
                        response.data.pipe(writableStream);
                    }
                    // writableStream.destroy(new Error('Forced stream error'));
                    
                    // Handle the 'finish' event to resolve the promise
                    writableStream.on('finish', resolve);

                    // Handle errors
                    response.data.on('error', (err) => {
                        sendErrorMessage(writableStream);
                        reject(err);
                    });
                    
                    writableStream.on('error', (err) => {
                        sendErrorMessage(writableStream);
                        reject(err);
                    });
                    
                })
                .catch((e)=>{
                    sendErrorMessage(writableStream);
                    
                    if(e.response && e.response.data) {
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

    return streamAxiosResponseToWritable(url, writable);
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