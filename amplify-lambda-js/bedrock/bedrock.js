import {sendDeltaToStream, sendStateEventToStream, sendErrorMessage} from "../common/streams.js";
import {getLogger} from "../common/logging.js";
import { doesNotSupportImagesInstructions, additionalImageInstruction, getImageBase64Content } from "../datasource/datasources.js";
import { BedrockRuntimeClient, ConverseStreamCommand } from "@aws-sdk/client-bedrock-runtime";
import {trace} from "../common/trace.js";
import {extractKey} from "../datasource/datasources.js";


const logger = getLogger("bedrock");

const BLANK_MSG = "Intentionally Left Blank, please ignore";

export const chatBedrock = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 
    const currentModel = options.model;
                                                             //very rare edge case
    const systemPrompts = [{"text": options.prompt.trim() || BLANK_MSG}];
    if (currentModel.systemPrompt.trim()) {
        systemPrompts.push({ "text": currentModel.systemPrompt });
    }

    const withoutSystemMessages = [];
    // options.prompt is a match for the first message in messages 
    for (const msg of body.messages.slice(1)) {
        if (msg.role === "system") {
            if (msg.content.trim()) systemPrompts.push({ "text": msg.content });
        } else {
            withoutSystemMessages.push(msg);
        }
    }

    const combinedMessages = combineMessages(withoutSystemMessages, options.prompt);
    const sanitizedMessages = await sanitizeMessages(combinedMessages, body.imageSources, currentModel, writable);
    
    try {
        
        const region = process.env.DEP_REGION ?? 'us-east-1';
        const client = new BedrockRuntimeClient({ region: region}); 
        
        logger.debug("Initiating call to Bedrock");

        const maxModelTokens = 4096; // currently it seems like bedrock has it maxed out at 4096 not the models output max token
        //options.model.outputTokenLimit;
        const maxTokens = body.max_tokens || 1000;
        const inferenceConfigs = {"temperature": options.temperature, 
                                  "maxTokens": maxTokens > maxModelTokens ? maxModelTokens : maxTokens};

        const input = { modelId: currentModel.id,
                        messages: sanitizedMessages,
                        inferenceConfig: inferenceConfigs,
                        }

        if (currentModel.supportsSystemPrompts) {
            input.system = systemPrompts;
        } else {
            // Gather all text values from the system prompts list
            const systemPromptsText = systemPrompts.map(sp => sp.text).join("\n\n");
            const sanitizedMessagesCopy = [...sanitizedMessages];

            sanitizedMessagesCopy[sanitizedMessagesCopy.length -1].content[0].text +=
            `Recall your custom instructions are: ${systemPromptsText}`;

            input.messages = sanitizedMessagesCopy;
        }

        trace(options.requestId, ["Bedrock"], {modelId : currentModel.id, data: input})

        const response = await client.send( new ConverseStreamCommand(input) );
        const { messageStream } = response.stream.options;
        const decoder = new TextDecoder();
        for await (const chunk of messageStream) {
            const jsonString = decoder.decode(chunk.body);
            // Parse the JSON string to an object
            const message = JSON.parse(jsonString);
            sendDeltaToStream(writable, "answer", message);
        }
        writable.end();

        writable.on('finish', () => {
            logger.debug('All data has been written to writable stream');
        });

        writable.on('error', (error) => {
            logger.error('Error with Writable stream: ', error);
            sendErrorMessage(writable);
            reject(error);
        });
         
    } catch (error) {
        logger.error(`Error invoking Bedrock chat for model ${currentModel.id}: `, error);
        sendErrorMessage(writable);
    }
}


function combineMessages(oldMessages, failSafeUserMessage) {
    if (!oldMessages) return oldMessages;
    let messages = [];
    const delimiter = "\n_________________________\n";
    
    const newestMessage = oldMessages[oldMessages.length - 1]
    if (newestMessage['role'] === 'user') oldMessages[oldMessages.length - 1]['content'] =
        //`${delimiter}Respond to the following inquiry: ${newestMessage['content']}`
        `${delimiter}${newestMessage['content']}`
    
    let i = -1;
    let j = 0;
    while (j < oldMessages.length) {
        const curMessageRole = oldMessages[j]['role'];
        const oldContent = oldMessages[j]['content'];
        if (!oldContent) oldMessages[j]['content'] = "NA Intentionally left empty, disregard and continue";
        if (messages.length == 0 || (messages[i]['role'] !== curMessageRole)) {
            oldMessages[j]['content'] = oldMessages[j]['content'].trimEnd() // remove white space, final messages cause error if not
            messages.push(oldMessages[j]);
            i += 1;
        } else if (messages[i]['role'] === oldMessages[j]['role']) {
            messages[i]['content'] += delimiter + oldContent;
        } 
        j += 1;
    }

    if (messages.length === 0 || (messages[0]['role'] !== 'user')) {
        messages = [{'role': 'user', 'content': failSafeUserMessage}, ...messages];
    } 

    return messages;
}


async function sanitizeMessages(messages, imageSources, model, responseStream) {
    if (!messages) return messages;

    const containsImages = imageSources && imageSources.length > 0;

    if (!model.supportsImages && containsImages) {
        messages.slice(-1)[0].content += doesNotSupportImagesInstructions(model.name)
    }

    let updatedMessages = [
        ...(messages.map(m => {
                    return { "role": m['role'],
                            "content": [
                                { "text":  m['content'].trim() || BLANK_MSG }
                            ]}
                    }))
    ];

    if (model.supportsImages && containsImages) {
        updatedMessages = await includeImageSources(imageSources, updatedMessages, responseStream); 
    }
    return updatedMessages;

}

async function includeImageSources(dataSources, messages, responseStream) {
    if (!dataSources || dataSources.length === 0) return messages;

    sendStateEventToStream(responseStream, {
        sources: {
            images: {
                sources: dataSources.map(ds => {
                    return {...ds, contentKey: extractKey(ds.id)}
                })
            }
        }
      });


    let imageMessageContent = [[]];
    let listIdx = 0;
    for (let i = 0; i < dataSources.length; i++) {
        const ds = dataSources[i];
        const encoded_image = await getImageBase64Content(ds);
        if (encoded_image) {
            // only 20 per content allowed
            if (imageMessageContent[listIdx].length > 19) {
                listIdx++;
                imageMessageContent.push([])
            }
            imageMessageContent[listIdx].push({
                "image": {
                    "format": ds.type.split('/')[1], 
                    "source": {
                        "bytes": Uint8Array.from(atob(encoded_image), char => char.charCodeAt(0))
                    }
                }
            })
        } else {
            logger.info("Failed to get base64 encoded image: ", ds);
        }
    }

    const msgLen = messages.length - 1;
    let content = messages[msgLen]['content'];
    content.push({ "text": additionalImageInstruction});
    content = [...content, ...imageMessageContent[0]];
    messages[msgLen]['content'] = content;
    if (listIdx > 0) {
        imageMessageContent.slice(1).forEach(contents => {
            messages.push({
                "role": "user",
                "content": contents
            })
        })
    }
    return messages;
}
    




   
