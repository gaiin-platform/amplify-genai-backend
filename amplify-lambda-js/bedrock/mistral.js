import {BedrockRuntimeClient,InvokeModelWithResponseStreamCommand} from "@aws-sdk/client-bedrock-runtime";
import {sendDeltaToStream} from "../common/streams.js";
import {getLogger} from "../common/logging.js";

const logger = getLogger("mistral");


export const chatMistral = async (chatBody, writable) => {

    let body = {...chatBody};
    const options = {...body.options}; 
    delete body.options; 

    try {
        // Ensure credentials are in ~/.aws/credentials
        logger.debug("Initializing Bedrock Client");
        const client = new BedrockRuntimeClient({region: "us-west-2"}); 

        logger.debug("Format Messages array to string");
        const prompt = body.messages.map((message, index) => {
            return message.role === 'user' || index === 0 ? `[INST] ${message.content} [/INST] ` : message.content;
            });

        const payload = {
            prompt: `<s> ${prompt} </s>`,
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
