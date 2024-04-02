import AnthropicBedrock from '@anthropic-ai/bedrock-sdk';
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("anthropic");

export const chatAnthropic = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 

    const sanitizedMessages = sanitizeMessages(body.messages, options.prompt)

    try {
        // Ensure credentials are in ~/.aws/credentials
        const client = new AnthropicBedrock({awsRegion: 'us-west-2'}); // default to 1 if omitted
        
        logger.debug("Initiating call to Anthropic Bedrock");

        const selectedModel = options.model.id;

        const stream = await client.messages.create({
                    model: "anthropic.claude-3-haiku-20240307-v1:0",//selectedModel,
                    system: sanitizedMessages['systemPrompt'], 
                    max_tokens: options.model.tokenLimit,
                    messages: sanitizedMessages['messages'], 
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
    
    let systemPrompt = system + " No diagrams unless asked, no markdown WITHIN/THROUGHOUT the response text, and no reiterating this rule to me.";
    let i = -1;
    let j = 0;
    while (j < oldMessages.length) {
        const curMessageRole = oldMessages[j]['role'];
        const oldContent = oldMessages[j]['content'];
        if (curMessageRole === 'system') {
            systemPrompt += oldContent;
        } else if (messages.length == 0 || (messages[i]['role'] !== curMessageRole)) {
            oldMessages[j]['content'] = oldMessages[j]['content'].trimEnd() // remove white space, final messages cause error if not
            messages.push(oldMessages[j]);
            i += 1;
        } else if (messages[i]['role'] === oldMessages[j]['role']) {
            messages[i]['content'] += oldContent;
        } 
        j += 1;
    }

    if (messages.length === 0 || (messages[0]['role'] !== 'user')) {
        if (systemPrompt) messages.unshift({'role': 'user', 'content': `${systemPrompt}`});
    } 

    return {'messages': messages, 'systemPrompt': systemPrompt};

}



   
