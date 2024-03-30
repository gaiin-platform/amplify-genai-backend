import AnthropicBedrock from '@anthropic-ai/bedrock-sdk';
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("anthropic");

export const chatAnthropic = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 

    sanitizedMessages = sanitizeMessages(body.messages, options.prompt)

    try {
        // Ensure credentials are in ~/.aws/credentials
        const client = new AnthropicBedrock({awsRegion: 'us-west-2'}); // default to 1 if omitted
        
        logger.debug("Initiating call to Anthropic Bedrock");

        const selectedModel = options.model.id;

        const stream = await client.messages.create({
                    model: selectedModel,
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
    let messages = oldMessages.map(item => ({...item}));
    let systemPrompt = system;
    for (let i = 0; i < messages.length - 1; i++) {
        const userExpectedMessage = messages[i];
        const assistantExpectedMessage = messages[i + 1];
        let role = userExpectedMessage['role'];
        if ((role === 'user' && assistantExpectedMessage['role'] === 'assistant')) {
            i += 1;
            if ((i + 1 < messages.length) && (messages[i+1]['role'] === 'assistant')) {
                i -=1;
            } 
        }  else if ((role === 'user' && assistantExpectedMessage['role'] === 'user') ||
                    (role === 'assistant' && assistantExpectedMessage['role'] === 'assistant')) {
            const repeatedRole = role === 'user' ? 'user' : 'assistant';
            const expectedUserIndex = i;
            while (i+1 < messages.length && messages[i+1]['role'] === repeatedRole) {
                messages[expectedUserIndex]['content'] += `${messages[i + 1]['content']}`;
                i += 1;
            }
            // cut out al the user messsages in between  
            messages = i + 1 < messages.length ? messages.slice(0, expectedUserIndex + 1).concat(messages.slice(i + 1)) : messages.slice(0, expectedUserIndex + 1)
            i -= 2;

        } else if (role === 'system') {
            systemPrompt += userExpectedMessage['content']
            messages.splice(i, 1);
            i -= i === 0 ? 1 : 2;

        } else if (assistantExpectedMessage['role'] === 'system') {
            systemPrompt += assistantExpectedMessage['content']
            messages.splice(i + 1, 1);
            i -= 1;
        }
    }
    return {'messages': messages, 'systemPrompt': systemPrompt};

}



   
