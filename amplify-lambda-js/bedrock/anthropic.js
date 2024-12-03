//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import AnthropicBedrock from '@anthropic-ai/bedrock-sdk';
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";
import {additionalImageInstruction, getImageBase64Content} from "../datasource/datasources.js";

const logger = getLogger("anthropic");
const region = process.env.REGION || "us-east-1";

export const chatAnthropic = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 

    const sanitizedMessages = sanitizeMessages(body.messages, options.prompt)

    const updatedMessages = await includeImageSources(body.imageSources, sanitizedMessages.messages); 
    
    try {
        const currentModel = options.model.id;
        // Ensure credentials are in ~/.aws/credentials
        const client = new AnthropicBedrock({awsRegion:  currentModel === "anthropic.claude-3-5-sonnet-20240620-v1:0" ? 'us-east-1' : 'us-west-2'}); // default to 1 if omitted 
        
        logger.debug("Initiating call to Anthropic Bedrock");

        // safety gaurd 
        const selectedModel = currentModel.includes("anthropic") ? currentModel : "anthropic.claude-3-haiku-20240307-v1:0";
        if (currentModel !== selectedModel) logger.debug("**Incompatible model entered CLAUDE!** ", currentModel);

        
        const stream = await client.messages.create({
                    model: selectedModel,
                    system: sanitizedMessages.systemPrompt, 
                    max_tokens: options.model.tokenLimit || chatBody.max_tokens,
                    messages: updatedMessages, 
                    stream: true, 
                    temperature: options.temperature,
                });
        
            logger.debug("Awaiting stream data");
            for await (const completion of stream) {
                sendDeltaToStream(writable, "answer", completion);  
            }    
        //end writable stream
        writable.end();

        writable.on('finish', () => {
            logger.debug('All data has been written to writable stream');
        });

        writable.on('error', (error) => {
            logger.error('Error with Writable stream: ', error);
            reject(error);
        });
         
    } catch (error) {
        logger.error('Error invoking Bedrock Anthropic: ', error);
    }
}


function sanitizeMessages(oldMessages, system) {
    if (!oldMessages) return oldMessages;
    let messages = [];
    const delimiter = "\n_________________________\n";
    
    const newestMessage = oldMessages[oldMessages.length - 1]
    if (newestMessage['role'] === 'user') oldMessages[oldMessages.length - 1]['content'] = `${delimiter}Respond to the following inquiry: ${newestMessage['content']}`
    
    let systemPrompt = "No diagrams unless asked, no markdown WITHIN/THROUGHOUT the response text, and no reiterating this rule to me. " + system;
    let i = -1;
    let j = 0;
    while (j < oldMessages.length) {
        const curMessageRole = oldMessages[j]['role'];
        const oldContent = oldMessages[j]['content'];
        if (!oldContent) oldMessages[j]['content'] = "NA Intentionally left empty, disregard and continue";
        if (curMessageRole === 'system') {
            systemPrompt += oldContent;
        } else if (messages.length == 0 || (messages[i]['role'] !== curMessageRole)) {
            oldMessages[j]['content'] = oldMessages[j]['content'].trimEnd() // remove white space, final messages cause error if not
            messages.push(oldMessages[j]);
            i += 1;
        } else if (messages[i]['role'] === oldMessages[j]['role']) {
            messages[i]['content'] += delimiter + oldContent;
        } 
        j += 1;
    }

    if (messages.length === 0 || (messages[0]['role'] !== 'user')) {
        if (systemPrompt) messages.unshift({'role': 'user', 'content': `${systemPrompt}`});
    } 

    const msgLen = messages.length - 1;
    const lastMsgContent = messages[msgLen]['content'];

    messages[msgLen]['content'] = `Recall your custom instructions are: ${systemPrompt} \n\n ${lastMsgContent}`;

    return {'messages': messages, 'systemPrompt': systemPrompt};

}

async function includeImageSources(dataSources, messages) {
    if (!dataSources || dataSources.length === 0) return messages;

    let imageMessageContent = []
    for (let i = 0; i < dataSources.length; i++) {
        const ds = dataSources[i];
        const encoded_image = await getImageBase64Content(ds);
        if (encoded_image) {
            imageMessageContent.push(
                    { "type": "text",
                      "text": `Image ${i + 1}:`
                    }
            )
            imageMessageContent.push( 
                { "type": "image",
                "source": {
                        "type": "base64",
                        "media_type": ds.type,
                        "data": encoded_image
                }
                }, 
            )
        }
    }

    const msgLen = messages.length - 1;
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
    




   
