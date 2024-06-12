
import { Readable, pipeline } from 'stream';
import { promisify } from 'util';
import { extractParams } from "./common/handlers.js";  
import { routeRequest } from "./router.js";
import { getLogger } from "./common/logging.js";

// Promisify the pipeline function for async use
const pipelineAsync = promisify(pipeline);

const logger = getLogger("index");
logger.debug("Enter Index.js");

// Use async function and try to handle streams with pipelineAsync
const returnResponse = async (responseStream, response) => {
    const sourceStream = new Readable({
        read() {
            this.push(JSON.stringify(response.body));
            this.push(null); // Signal the end of the stream
        }
    });

    try {
        await pipelineAsync(sourceStream, responseStream);
        logger.debug('Pipeline succeeded.');
    } catch (err) {
        logger.error('Pipeline failed:', err);
    }
};

export const handler = awslambda.streamifyResponse(async (event, responseStream, context) => {
    try {
        logger.debug("Extracting params from event");
        const params = await extractParams(event);
        await routeRequest(params, returnResponse, responseStream);
    } catch (e) {
        logger.error("Error processing request: " + e.message, e);
        await returnResponse(responseStream, {
            statusCode: 400,
            body: {error: e.message}
        });
    }
});

