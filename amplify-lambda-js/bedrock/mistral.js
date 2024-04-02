import {BedrockRuntimeClient,InvokeModelWithResponseStreamCommand} from "@aws-sdk/client-bedrock-runtime";
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("mistral");


export const chatMistral = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 

    const sanitizedMessages = sanitizeMessages(body.messages, options.prompt)


    try {
        // Ensure credentials are in ~/.aws/credentials
        logger.debug("Initializing Bedrock Client");
        const client = new BedrockRuntimeClient({region: "us-west-2"}); 

        logger.debug("Format Messages array to string");

        const payload = {
            prompt: sanitizedMessages,
            max_tokens: options.model.tokenLimit,
            temperature: options.temperature
        };        
        const command = new InvokeModelWithResponseStreamCommand({
                body: JSON.stringify(payload),
                contentType: "application/json",
                accept: "application/json",
                modelId: options.model.id 
        });

        logger.debug("Initiating call to Mistral Bedrock");
        const response = await client.send(command);
        
        logger.debug("Awaiting stream data");
    
        for await (const completion of response.body) {
            //completion.completion
            if (completion.chunk && completion.chunk.bytes) {
                let chatResponse = JSON.parse(Buffer.from(completion.chunk.bytes).toString("utf-8"));
            sendDeltaToStream(writable, "answer", chatResponse.outputs[0].text);  
            
            } else {
                logger.error(completion);
            }
            
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

        return
    } catch (error) {
        logger.error('Error invoking Bedrock Anthropic: ', error);


    }
}

function sanitizeMessages(oldMessages, system) {
    if (!oldMessages) return '';
    let messages = [];
    let systemPrompt = system + " No diagrams unless asked, no markdown WITHIN/THROUGHOUT the response text, and no reiterating this rule to me.";

    let i = -1;
    let j = 0;
    while (j < oldMessages.length) {
        const curMessageRole = oldMessages[j]['role'];
        const oldContent = oldMessages[j]['content'];
        if (curMessageRole === 'system') {
            systemPrompt += oldContent;
        } else if (messages.length == 0 || (messages[i]['role'] !== curMessageRole)) {
            messages.push(oldMessages[j]);
            i += 1;
        } else if (messages[i]['role'] === oldMessages[j]['role']) {
            messages[i]['content'] += oldContent;
        } 
        j += 1;
    }

    if (messages.length === 0) {
        return `<s> [INST] ${systemPrompt} [/INST] </s> `
    } else if (messages[0]['role'] !== 'user') {
        messages.unshift({'role': 'user', 'content': `${systemPrompt}`});
    } 

    return  messages.map((message, index) => {
        if (index === messages.length - 1 && message['role'] === 'user') return `[INST] System Prompt: ${systemPrompt}  \n User Message: ${message.content} [/INST]`;
        return message.role === 'user' ? `<s>[INST] ${message.content} [/INST] ` :  `${message.content} </s>`;
                      
    }).join('');
}