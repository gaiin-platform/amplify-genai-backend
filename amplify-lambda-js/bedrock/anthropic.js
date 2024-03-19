import AnthropicBedrock from '@anthropic-ai/bedrock-sdk';
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("anthropic");

export const chatAnthropic = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; //
    delete body.options; //

    const systemPrompt = `${options.prompt} Remember NO diagrams unless asked, NO markdown WITHIN/THROUGHOUT the response text, and NO reiterating this rule to me.`

    try {
        // Ensure credentials are in ~/.aws/credentials
        const client = new AnthropicBedrock({awsRegion: 'us-west-2'}); // default to 1 if omitted
        
        logger.debug("Initiating call to Anthropic Bedrock");

        // AnthropicBedrock.HUMAN_PROMPT and AnthropicBedrock.AI_PROMPT are undefined

        const selectedModel = options.model.id;

        const stream = await client.messages.create({
                    model: selectedModel,
                    system: systemPrompt, 
                    max_tokens: options.model.tokenLimit,
                    messages: body.messages.slice(1), 
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


    



   
