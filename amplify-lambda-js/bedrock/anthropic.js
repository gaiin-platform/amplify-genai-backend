import AnthropicBedrock from '@anthropic-ai/bedrock-sdk';
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("anthropic");

export const chatAnthropic = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; //
    delete body.options; //

    const enhancedPrompt = `${options.prompt} Remember no diagrams unless asked, no markdown WITHIN/THROUGHOUT the response text, and no reiterating this rule to me.`

    try {
        // Ensure credentials are in ~/.aws/credentials
        const client = new AnthropicBedrock({awsRegion: 'us-west-2'}); // default to 1 if omitted
        
        logger.debug("Initiating call to Anthropic Bedrock");

        // AnthropicBedrock.HUMAN_PROMPT and AnthropicBedrock.AI_PROMPT are undefined

        const selectedModel = options.model.id;
        
        let stream;
        if (selectedModel.includes("sonnet")) {
            
            logger.debug("Formatting Messages array as needed");
            body.messages[0] = {role : "user", content : enhancedPrompt};
            body.messages.splice(1, 0, {role:"assistant", content: "Understood!"});

            //Claude 3
            stream = await client.messages.create({
                model: selectedModel,
                max_tokens: options.model.tokenLimit,
                messages: body.messages, 
                stream: true, 
                temperature: options.temperature,
            });
        

        } else { 
            // Claude Models 2.1 and instant 1.2
            logger.debug("Format Messages array to string");
            const humanPrompt = body.messages.slice(1).map(message => {
                    const role = message.role === 'user' ? "\n\nHuman: " : "\n\nAssistant: ";
                    return `${role} ${message.content}`;
                    });

            stream = await client.completions.create({
                prompt: `\n\nHuman: ${enhancedPrompt} ${humanPrompt} \n\nAssistant:`,
                model: selectedModel,
                stream: true,
                max_tokens_to_sample: options.model.tokenLimit,
                temperature: options.temperature
                });
        }

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


    



   
