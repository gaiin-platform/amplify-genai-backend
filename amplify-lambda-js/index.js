
//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { Readable, Writable, pipeline } from 'stream';
import { promisify } from 'util';
import { extractParams } from "./common/handlers.js";  
import { routeRequest } from "./router.js";
import { getLogger } from "./common/logging.js";
import { debug } from 'console';
import AWSXRay from 'aws-xray-sdk';

const pipelineAsync = promisify(pipeline);
const logger = getLogger("index");
logger.debug("Enter Index.js");

const streamEnabled = process.env.STREAM ? process.env.STREAM === 'true' : true;
logger.debug("Streaming is set to: ", streamEnabled);


// Used when streaming is set to false 
class AggregatorStream extends Writable {
    constructor(options) {
      super(options);
      this.aggregatedData = '';
      this.terminatedRequestState = null;
    }
  
    _write(chunk, encoding, callback) {
      if (this.requestStateTerminated) {
        this.aggregatedData = chunk;
      } else {
        const chunkStr = chunk.toString();
        if (chunkStr.startsWith('data:')) {
            const dataObject = JSON.parse(chunkStr.slice(5));
            if (dataObject && 'd' in dataObject && typeof dataObject.d !== 'object') {
                this.aggregatedData += dataObject.d;
            }
        }
      }
      callback();
    }
  
    sendFinalDataResponse(responseStream) {
      if (this.terminatedRequestState) this.sendFinalResponse(responseStream, this.terminatedRequestState || '');
      const finalMessage = `data: ${JSON.stringify({ d: this.aggregatedData })}\n\n`;
      // logger.debug("Final Message: ", finalMessage);
      this.sendFinalResponse(responseStream, finalMessage);
    }

    sendFinalResponse(responseStream, data) {
        responseStream.write(data);
        responseStream.end();  
        logger.debug('Stream Ended - AggregatorStream');

    }

    returnRequestState(message) {
        this.terminatedRequestState = message;
    }


  }

const returnResponse = async (responseStream, response) => {
    const isAggregatorStream = responseStream instanceof AggregatorStream;

    try {
        if (isAggregatorStream) {
            logger.debug("Return Response for stream == false")
            responseStream.returnRequestState(JSON.stringify(response));
        } else {
            // normal stream route
            const sourceStream = new Readable({
                read() {
                    this.push(JSON.stringify(response.body));
                    this.push(null); // Signal the end of the stream
                }
            });
            await pipelineAsync(sourceStream, responseStream);
        }
        logger.debug('Pipeline succeeded.');
    } catch (err) {
        logger.error('Pipeline failed:', err);
    }
};


export const handler = awslambda.streamifyResponse(async (event, responseStream, context) => {

    const segment = AWSXRay.getSegment();
    const subSegment = segment.addNewSubsegment('chat-js.index.handler');

    const effectiveStream = streamEnabled ? responseStream : new AggregatorStream();

    try {
      logger.debug("Extracting params from event");
      const params = await extractParams(event);
      await routeRequest(params, returnResponse, effectiveStream);
  
      // If we are not streaming, send the final aggregated response now
      if (!streamEnabled) {
        effectiveStream.sendFinalDataResponse(responseStream);
      }
    } catch (e) {
        logger.error("Error processing request: " + e.message, e);
        await returnResponse(responseStream, {
            statusCode: 400,
            body: { error: e.message }
        });
    } finally {
        subSegment.close();
    }
  });

