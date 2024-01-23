
import { promisify } from 'util';
import { pipeline as pipelineCallback, Writable  } from 'stream';
import zlib from 'zlib';
import axios from 'axios';
import {getLogger} from "../common/logging.js";

const logger = getLogger("openai");

const pipeline = promisify(pipelineCallback);

export const chat = async (apiKey, chatBody, writable) => {
    let body = {...chatBody};
    const options = {...body.options};
    delete body.options;

    const modelId = (options.model && options.model.id) || "gpt-4-1106-Preview";

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

    const data = {
       ...body,
        "stream": true,
    };

    if(tools){
        data.tools = tools;
    }
    if(tool_choice){
        data.tool_choice = tool_choice;
    }

    const headers = {
        'Content-Type': 'application/json',
        'api-key': apiKey,
    };

    const apiVersion = '2023-12-01-preview';
    //const apiVersion = '2023-07-01-preview';
    const endpoint = process.env.OPENAI_ENDPOINT || "https://api.openai.com";
    const url = `${endpoint}/${modelId}/chat/completions?api-version=${apiVersion}`;

    logger.debug("Calling OpenAI API with url: "+url);

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
                    // Pipe the response stream to the writable stream
                    response.data.pipe(writableStream);

                    // Handle the 'finish' event to resolve the promise
                    writableStream.on('finish', resolve);

                    // Handle errors
                    response.data.on('error', reject);
                    writableStream.on('error', reject);
                })
                .catch((e)=>{
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
    // console.log("Calling pipeline");
    //
    // await pipeline(
    //     response.data,
    //     writable
    // );
}
