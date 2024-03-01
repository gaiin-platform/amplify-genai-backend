import AnthropicBedrock from '@anthropic-ai/bedrock-sdk';
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("anthropic");

export const chatBedrock = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; //
    delete body.options; //

    try {
        // Ensure credentials are in ~/.aws/credentials
        const client = new AnthropicBedrock({awsRegion: 'us-east-1'});
         
        logger.debug("Format Messages array to string");
        const humanPrompt = body.messages.slice(1).map(message => {
                const role = message.role === 'user' ? "\n\nHuman: " : "\n\nAssistant: ";
                return `${role} ${message.content}`;
            });

        // my attempt to encourage the model to behave like Messages would.

        logger.debug("Initiating call to Anthropic Bedrock");

        // AnthropicBedrock.HUMAN_PROMPT and AnthropicBedrock.AI_PROMPT are undefined

        const stream = await client.completions.create({
        prompt: `\n\nHuman: ${options.prompt} Remember no diagrams unless asked,no markdown within the response text, and no reiterating this rule to me. ${humanPrompt} \n\nAssistant:`,
        model: options.model.id,
        stream: true,
        max_tokens_to_sample: options.model.tokenLimit,
        temperature: options.temperature
        });

        logger.debug("Awaiting stream data");

        for await (const completion of stream) {
             //completion.completion
            sendDeltaToStream(writable, "assistant", completion); 
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


    



   
