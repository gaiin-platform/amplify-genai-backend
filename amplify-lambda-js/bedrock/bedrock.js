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

    const sanitizedMessages = await sanitizeMessages(withoutSystemMessages, body.imageSources)
    
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

            // Create a user message with the gathered system prompts
            const systemPromptMessage = {
                role: "user",
                content: [{ text: `System Prompts: ${systemPromptsText}` }],
            };

            // Insert the system prompt message
            const sanitizedMessagesCopy = [...sanitizedMessages];
            sanitizedMessagesCopy.splice(-1, 0, systemPromptMessage);

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
        return null;
    }
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
    




   
