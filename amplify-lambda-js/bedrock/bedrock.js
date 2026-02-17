import {sendStateEventToStream, sendErrorMessage} from "../common/streams.js";
import {getLogger} from "../common/logging.js";
import {logCriticalError} from "../common/criticalLogger.js";
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

    const prompt = typeof options.prompt === 'string' ? options.prompt : '';
    const systemPrompts = [{"text": prompt.trim() || BLANK_MSG}];
    if (currentModel.systemPrompt?.trim()) {
        systemPrompts.push({ "text": currentModel.systemPrompt });
    }

    const withoutSystemMessages = [];
    // options.prompt is a match for the first message in messages 
    for (const msg of body.messages) {
        if (msg.role === "system") {
                                      // avoid duplicate system prompts
            if (msg.content.trim() && msg.content !== prompt) systemPrompts.push({ "text": msg.content });
        } else {
            withoutSystemMessages.push(msg);
        }
    }
    const imageSources =  !options.dataSourceOptions?.disableDataSources ? body.imageSources : [];

    // Parallelize ALL processing for faster execution
    const client = getBedrockClient();  // Get client immediately (already cached)
    const combinedMessages = combineMessages(withoutSystemMessages, prompt || BLANK_MSG);
    const sanitizedMessages = await sanitizeMessages(combinedMessages, imageSources, currentModel, writable);

    // Declare input at function scope so it's available in catch block
    let input = null;
    // Declare hasToolRelatedContent at function scope so it's available in catch block for error logging
    let hasToolRelatedContent = false;

    try {
        // Client already fetched in parallel above
        // Initiating call to Bedrock

        const maxModelTokens = options.model.outputTokenLimit;

        const maxTokens = body.max_tokens || 2000;

        // Note: Disable reasoning when tools are present because Bedrock requires thinking blocks
        // in assistant messages when using extended thinking with tools, which complicates the tool loop
        const hasTools = body.tools && body.tools.length > 0;
        const disableReasoning = options.disableReasoning;
        const isReasoningEnabled = currentModel.supportsReasoning && maxTokens > 1024 && !hasTools && !disableReasoning;

        // CRITICAL: When extended thinking is enabled, temperature MUST be 1.0
        // https://docs.claude.com/en/docs/build-with-claude/extended-thinking#important-considerations-when-using-extended-thinking
        const temperature = isReasoningEnabled ? 1.0 : options.temperature;

        const inferenceConfigs = {
            "temperature": temperature,
            "maxTokens": maxTokens > maxModelTokens ? maxModelTokens : maxTokens,
        };

        input = { modelId: currentModel.id,
                  messages: sanitizedMessages,
                  inferenceConfig: inferenceConfigs,
                }

        // Add structured output configuration if provided
        if (body.outputConfig) {
            input.outputConfig = body.outputConfig;
            logger.info('\u2705 [Bedrock] Added native structured output configuration');
        }

        if (process.env.BEDROCK_GUARDRAIL_ID && process.env.BEDROCK_GUARDRAIL_VERSION) {
            logger.info("Using Bedrock Guardrail with ID:", process.env.BEDROCK_GUARDRAIL_ID);
            // Using guardrail
            input.guardrailConfig = {
                guardrailIdentifier: process.env.BEDROCK_GUARDRAIL_ID,
                guardrailVersion: process.env.BEDROCK_GUARDRAIL_VERSION
            }

        }

        if (isReasoningEnabled) {
            const budget_tokens = getBudgetTokens({options}, maxTokens);

            // Final validation: Bedrock strictly requires maxTokens > budget_tokens
            if (budget_tokens >= maxTokens) {
                logger.warn(`Extended thinking disabled: budget_tokens (${budget_tokens}) >= maxTokens (${maxTokens}). Bedrock requires maxTokens > budget_tokens.`);
                // Disable reasoning to prevent ValidationException
            } else {
                input.additionalModelRequestFields={
                    "reasoning_config": {
                        "type": "enabled",
                        "budget_tokens": budget_tokens
                    },
                }
                logger.info(`Extended thinking enabled with temperature=1.0 (original: ${options.temperature}), budget_tokens=${budget_tokens}, maxTokens=${maxTokens}`);
            }
        } else if (currentModel.supportsReasoning && disableReasoning) {
            logger.info(`Extended thinking disabled by user (disableReasoning=true)`);
        }

        if (currentModel.supportsSystemPrompts) {
            input.system = systemPrompts;
        } else {
            // Gather all text values from the system prompts list
            const systemPromptsText = systemPrompts.map(sp => sp.text).join("\n\n");
            const sanitizedMessagesCopy = [...sanitizedMessages];

            // May not need anymore, testing for a while
            // sanitizedMessagesCopy[sanitizedMessagesCopy.length -1].content[0].text +=
            // `Recall your custom instructions are: ${systemPromptsText}`;

            input.messages = sanitizedMessagesCopy;
        }

        // Check if messages contain tool-related content (toolUse or toolResult)
        // Bedrock requires toolConfig to be present whenever tool blocks exist in conversation history
        hasToolRelatedContent = sanitizedMessages.some(msg =>
            msg.content && Array.isArray(msg.content) &&
            msg.content.some(block => block.toolUse || block.toolResult)
        );

        // Add tool configuration if tools are provided OR if messages contain tool content
        if ((body.tools && body.tools.length > 0) || hasToolRelatedContent) {
            const tools = body.tools && body.tools.length > 0 ? body.tools : [];

            input.toolConfig = {
                tools: tools.map(tool => {
                    // Convert OpenAI tool format to Bedrock toolSpec format
                    const fn = tool.function || tool;
                    return {
                        toolSpec: {
                            name: fn.name,
                            description: fn.description,
                            inputSchema: {
                                json: fn.parameters || { type: "object", properties: {} }
                            }
                        }
                    };
                })
            };

            if (tools.length > 0) {
                logger.info(`Added ${tools.length} tools to Bedrock request`);
            } else {
                logger.info('Added empty toolConfig (required for tool-related content in history)');
            }
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
            // Debug: Log chunks that contain tool-related events
            if (jsonString.includes('toolUse') || jsonString.includes('contentBlockStart') || jsonString.includes('contentBlockStop')) {
                logger.debug(`🔧 Bedrock tool-related chunk: ${jsonString.substring(0, 500)}`);
            }
            // Write directly as SSE format without re-parsing (already valid JSON)
            writable.write(`data: ${jsonString}\n\n`);
        }
        writable.end();
         
    } catch (error) {
        if (error.message || error.$response?.message) console.log("Error invoking Bedrock API:", error.message || error.$response?.message);
        logger.error(`Error invoking Bedrock chat for model ${currentModel.id}: `, error);

        // Capture raw response body for HTML error pages (502 Bad Gateway, etc.)
        let rawResponsePreview = 'N/A';
        if (error.$response?.body) {
            try {
                // Try to read the body as text if it's an HTML error page
                const bodyText = typeof error.$response.body === 'string'
                    ? error.$response.body
                    : JSON.stringify(error.$response.body);
                rawResponsePreview = bodyText.substring(0, 500); // First 500 chars
            } catch (e) {
                rawResponsePreview = 'Unable to read response body';
            }
        }

        // CRITICAL: Bedrock API failure - user cannot get LLM response (capture AWS-specific error details)
        const sanitizedInput = input ? { ...input } : { modelId: currentModel?.id || 'unknown' };
        if (sanitizedInput.messages) delete sanitizedInput.messages;
        if (sanitizedInput.system) delete sanitizedInput.system;

        logCriticalError({
            functionName: 'chatBedrock',
            errorType: 'BedrockAPIFailure',
            errorMessage: `Bedrock API failed: ${error.message || error.$response?.message || "Unknown error"}`,
            currentUser: options?.user || options?.accountId || 'unknown',
            severity: 'HIGH',
            stackTrace: error.stack || '',
            context: {
                requestId: options?.requestId || 'unknown',
                modelId: currentModel?.id || 'unknown',
                httpStatusCode: error.$metadata?.httpStatusCode || 'N/A',
                awsReason: error.$response?.reason || 'N/A',
                awsMessage: error.$response?.message || 'N/A',
                rawResponsePreview: rawResponsePreview,
                fullError: error,
                errorCode: error.code || error.name || 'N/A',
                hasGuardrail: !!(process.env.BEDROCK_GUARDRAIL_ID && process.env.BEDROCK_GUARDRAIL_VERSION),
                bedrockConfig: sanitizedInput,
                hasToolContent: hasToolRelatedContent || false,
            }
        }).catch(err => logger.error('Failed to log critical error:', err));

        // Mark error as already having critical logging to prevent duplicate logging in router
        error.criticalErrorLogged = true;

        sendErrorMessage(writable, error.$metadata?.httpStatusCode, error.$response?.reason);
        throw error;
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

        // Don't combine tool messages or assistant messages with tool_calls - preserve full structure
        if (role === 'tool' || (role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0)) {
            // Push any pending message first
            if (currentMessage) {
                messages.push(currentMessage);
                currentMessage = null;
            }
            // Push tool-related message as-is (preserves tool_call_id and tool_calls)
            messages.push({ ...msg });
            continue;
        }

        let content = msg.content || "NA Intentionally left empty, disregard and continue";

        // Add delimiter to last user message
        if (j === oldMessages.length - 1 && role === 'user') {
            content = `${delimiter}${content}`;
        }

        content = typeof content === 'string' ? content.trimEnd() : content; // remove white space

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

    // Convert messages to Bedrock format, handling tool calls and tool results
    let updatedMessages = [];
    for (const m of messages) {
        if (m.role === 'tool') {
            // Convert OpenAI tool result to Bedrock toolResult format
            // Bedrock expects: { role: 'user', content: [{ toolResult: { toolUseId, content: [{text}] } }] }
            if (!m.tool_call_id) {
                // Skip tool messages without tool_call_id - convert to regular user message
                logger.warn(`Tool message missing tool_call_id, converting to regular user message`);
                updatedMessages.push({
                    role: 'user',
                    content: [{ text: `Tool result: ${m.content || ''}` }]
                });
            } else {
                updatedMessages.push({
                    role: 'user',
                    content: [{
                        toolResult: {
                            toolUseId: m.tool_call_id,
                            content: [{ text: m.content || '' }]
                        }
                    }]
                });
            }
        } else if (m.role === 'assistant' && m.tool_calls && m.tool_calls.length > 0) {
            // Convert OpenAI assistant tool_calls to Bedrock toolUse format
            // Bedrock expects: { role: 'assistant', content: [{ toolUse: { toolUseId, name, input } }, ...] }
            const content = [];
            // Add text content if present
            if (m.content && m.content.trim()) {
                content.push({ text: m.content.trim() });
            }
            // Add tool use blocks
            for (const tc of m.tool_calls) {
                // Skip tool calls without id
                if (!tc.id) {
                    logger.warn(`Skipping tool call without id: ${tc.function?.name || tc.name}`);
                    continue;
                }
                let inputObj = {};
                try {
                    inputObj = JSON.parse(tc.function?.arguments || '{}');
                } catch (e) {
                    logger.warn(`Failed to parse tool call arguments: ${tc.function?.arguments}`);
                }
                content.push({
                    toolUse: {
                        toolUseId: tc.id,
                        name: tc.function?.name || tc.name,
                        input: inputObj
                    }
                });
            }
            updatedMessages.push({
                role: 'assistant',
                content: content
            });
        } else {
            // Regular message
            const contentText = typeof m.content === 'string' ? m.content.trim() : JSON.stringify(m.content);
            updatedMessages.push({
                role: m.role,
                content: [{ text: contentText || BLANK_MSG }]
            });
        }
    }

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
    




   
