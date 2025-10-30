import {sendStateEventToStream, sendErrorMessage} from "../common/streams.js";
import {getLogger} from "../common/logging.js";
import { getBudgetTokens } from "../common/params.js";
import { doesNotSupportImagesInstructions, additionalImageInstruction, getImageBase64Content } from "../datasource/datasources.js";
import { BedrockRuntimeClient, ConverseStreamCommand } from "@aws-sdk/client-bedrock-runtime";
import {trace} from "../common/trace.js";
import {extractKey} from "../datasource/datasources.js";


const logger = getLogger("bedrock");
const BLANK_MSG = "Intentionally Left Blank, please ignore";

// Cache the Bedrock client - create once, reuse for all requests
let cachedBedrockClient = null;
const getBedrockClient = () => {
    if (!cachedBedrockClient) {
        const region = process.env.DEP_REGION ?? 'us-east-1';
        cachedBedrockClient = new BedrockRuntimeClient({ region });
        // Created and cached Bedrock client
    }
    return cachedBedrockClient;
};

export const chatBedrock = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 
    const currentModel = options.model;

    const systemPrompts = [{"text": options.prompt?.trim() || BLANK_MSG}];
    if (currentModel.systemPrompt?.trim()) {
        systemPrompts.push({ "text": currentModel.systemPrompt });
    }

    const withoutSystemMessages = [];
    // options.prompt is a match for the first message in messages 
    for (const msg of body.messages) {
        if (msg.role === "system") {
                                      // avoid duplicate system prompts
            if (msg.content.trim() && msg.content !== options.prompt) systemPrompts.push({ "text": msg.content });
        } else {
            withoutSystemMessages.push(msg);
        }
    }
    const imageSources =  !options.dataSourceOptions?.disableDataSources ? body.imageSources : [];
    
    // Parallelize ALL processing for faster execution
    const client = getBedrockClient();  // Get client immediately (already cached)
    const combinedMessages = combineMessages(withoutSystemMessages, options.prompt);
    const sanitizedMessages = await sanitizeMessages(combinedMessages, imageSources, currentModel, writable);
    
    try {
        // Client already fetched in parallel above
        // Initiating call to Bedrock

        const maxModelTokens = options.model.outputTokenLimit;

        const maxTokens = body.max_tokens || 2000;
        const inferenceConfigs = {"temperature": options.temperature, 
                                  "maxTokens": maxTokens > maxModelTokens ? maxModelTokens : maxTokens, };
        
        const input = { modelId: currentModel.id,
                        messages: sanitizedMessages,
                        inferenceConfig: inferenceConfigs,
                        }

        if (process.env.BEDROCK_GUARDRAIL_ID && process.env.BEDROCK_GUARDRAIL_VERSION) {
            // Using guardrail
            input.guardrailConfig = {
                guardrailIdentifier: process.env.BEDROCK_GUARDRAIL_ID,
                guardrailVersion: process.env.BEDROCK_GUARDRAIL_VERSION
            }
       
        }
        if (currentModel.supportsReasoning && maxTokens > 1024) {
            const budget_tokens = getBudgetTokens({options}, maxTokens); 
            input.additionalModelRequestFields={
                "reasoning_config": {
                    "type": "enabled",
                "budget_tokens": budget_tokens
                },
            }
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

        // Set up event listeners before streaming for better error handling
        writable.on('finish', () => {
            // All data has been written to writable stream
        });

        writable.on('error', (error) => {
            logger.error('Error with Writable stream: ', error);
            const statusCode = error.code || error.statusCode;
            const statusText = error.message || error.name || 'Stream Error';
            if (!writable.writableEnded) {
                sendErrorMessage(writable, statusCode, statusText);
            }
        });

        const response = await client.send( new ConverseStreamCommand(input) );
        const { messageStream } = response.stream.options;
        const decoder = new TextDecoder();
        
        // Process stream with minimal overhead
        for await (const chunk of messageStream) {
            const jsonString = decoder.decode(chunk.body);
            // Write directly as SSE format without re-parsing (already valid JSON)
            writable.write(`data: ${jsonString}\n\n`);
        }
        writable.end();
         
    } catch (error) {
        if (error.message || error.$response?.message) console.log("Error invoking Bedrock API:", error.message || error.$response?.message);
        logger.error(`Error invoking Bedrock chat for model ${currentModel.id}: `, error);
        sendErrorMessage(writable, error.$metadata?.httpStatusCode, error.$response?.reason);
    }
}


function combineMessages(oldMessages, failSafeUserMessage) {
    if (!oldMessages || oldMessages.length === 0) return oldMessages;
    
    const delimiter = "\n_________________________\n";
    const messages = [];
    let currentMessage = null;
    
    // Single pass optimization
    for (let j = 0; j < oldMessages.length; j++) {
        const msg = oldMessages[j];
        const role = msg.role;
        let content = msg.content || "NA Intentionally left empty, disregard and continue";
        
        // Add delimiter to last user message
        if (j === oldMessages.length - 1 && role === 'user') {
            content = `${delimiter}${content}`;
        }
        
        content = content.trimEnd(); // remove white space
        
        if (!currentMessage || currentMessage.role !== role) {
            // New role, push previous and start new
            if (currentMessage) messages.push(currentMessage);
            currentMessage = { role, content };
        } else {
            // Same role, combine content
            currentMessage.content += delimiter + content;
        }
    }
    
    // Push last message
    if (currentMessage) messages.push(currentMessage);
    
    // Ensure first message is user
    if (messages.length === 0 || messages[0].role !== 'user') {
        messages.unshift({ role: 'user', content: failSafeUserMessage });
    }
    
    return messages;
}


async function sanitizeMessages(messages, imageSources, model, responseStream) {
    if (!messages) return messages;

    const containsImages = imageSources && imageSources.length > 0;

    if (!model.supportsImages && containsImages) {
        messages[messages.length - 1].content += doesNotSupportImagesInstructions(model.name);
    }

    // Direct map without spread for better performance
    let updatedMessages = messages.map(m => ({
        role: m.role,
        content: [{ text: m.content.trim() || BLANK_MSG }]
    }));

    if (model.supportsImages && containsImages) {
        updatedMessages = await includeImageSources(imageSources, updatedMessages, responseStream); 
    }
    return updatedMessages;
}

async function includeImageSources(dataSources, messages, responseStream) {
    if (!dataSources || dataSources.length === 0) return messages;

    // Process all images in parallel for faster execution
    const imagePromises = dataSources.map(async (ds) => {
        try {
            const encoded_image = await getImageBase64Content(ds);
            if (encoded_image) {
                return {
                    ds: {...ds, contentKey: extractKey(ds.id)},
                    imageData: {
                        "image": {
                            "format": ds.type.split('/')[1], 
                            "source": {
                                "bytes": Uint8Array.from(atob(encoded_image), char => char.charCodeAt(0))
                            }
                        }
                    }
                };
            }
        } catch (err) {
            logger.info("Failed to get base64 encoded image: ", ds);
        }
        return null;
    });
    
    const results = await Promise.all(imagePromises);
    const retrievedImages = [];
    let imageMessageContent = [[]];
    let listIdx = 0;
    
    // Process results
    for (const result of results) {
        if (result) {
            retrievedImages.push(result.ds);
            // only 20 per content allowed
            if (imageMessageContent[listIdx].length > 19) {
                listIdx++;
                imageMessageContent.push([]);
            }
            imageMessageContent[listIdx].push(result.imageData);
        }
    }

    if (retrievedImages.length > 0) {
        sendStateEventToStream(responseStream, {
            sources: { images: { sources: retrievedImages} }
          });
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
    




   
