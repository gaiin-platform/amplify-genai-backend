import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";
import {additionalImageInstruction, getImageBase64Content} from "../datasource/datasources.js";
import { BedrockRuntimeClient, ConverseStreamCommand } from "@aws-sdk/client-bedrock-runtime";

const logger = getLogger("bedrock");

export const chatBedrock = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 
    const currentModel = options.model;

    const systemPrompts = [{"text": options.prompt}];
    if (currentModel.systemPrompt) {
        systemPrompts.push({ "text": currentModel.systemPrompt });
    }

    const withoutSystemMessages = [];
    // options.prompt is a match for the first message in messages 
    for (const msg of body.messages.slice(1)) {
        if (msg.role === "system") {
            systemPrompts.push({ "text": msg.content });
        } else {
            withoutSystemMessages.push(msg);
        }
    }

    const combinedMessages = combineMessages(withoutSystemMessages, options.prompt);
    const sanitizedMessages = await sanitizeMessages(combinedMessages, body.imageSources)
    
    try {
        
        const region = process.env.DEP_REGION ?? 'us-east-1';
        const client = new BedrockRuntimeClient({ region: region}); 
        
        logger.debug("Initiating call to Bedrock");

        const inferenceConfigs = {"temperature": options.temperature, 
                                  "maxTokens": chatBody.max_tokens || options.model.outputTokenLimit};
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
            reject(error);
        });
         
    } catch (error) {
        logger.error(`Error invoking Bedrock chat for model ${currentModel.id}: `, error);
        sendDeltaToStream(writable, "answer", "Error retrieving response. Please try again.");
    }
}

function combineMessages(oldMessages, failSafeUserMessage) {
    if (!oldMessages) return oldMessages;
    let messages = [];
    const delimiter = "\n_________________________\n";
    
    const newestMessage = oldMessages[oldMessages.length - 1]
    if (newestMessage['role'] === 'user') oldMessages[oldMessages.length - 1]['content'] = `${delimiter}Respond to the following inquiry: ${newestMessage['content']}`
    
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


async function sanitizeMessages(messages, imageSources) {
    if (!messages) return messages;
    let updatedMessages = [
        ...(messages.map(m => {
            return { "role": m['role'],
                     "content": [
                        { "text":  m['content'] }
                    ]}
            }))
    ];

    if (imageSources && imageSources.length > 0) {
        updatedMessages = await includeImageSources(imageSources, updatedMessages); 
    }
    return updatedMessages;

}

async function includeImageSources(dataSources, messages) {
    if (!dataSources || dataSources.length === 0) return messages;

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
                    "format": ds.type, //"png | jpeg | gif | webp"
                    "source": {
                        "bytes": encoded_image
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
    




   
