import { Writable } from 'stream';
import { extractParams } from "./common/handlers.js";  
import { routeRequest } from "./router.js";
import { getLogger } from "./common/logging.js";

const logger = getLogger("index");
logger.debug("Enter Index.js");

class ResponseStreamForStreamlessResponse extends Writable {
  constructor(options, callback) {
    super(options);
    this.aggregatedData = '';
    if (typeof callback === 'function') {
      this.callback = callback;
    } else {
      throw new TypeError("Callback must be a function.");
    }
  }

  _write(chunk, encoding, callback) {
    // logger.debug("Received chunk");
    const chunkStr = chunk.toString();
    if (chunkStr.startsWith('data:')) {
      try {
        const dataObject = JSON.parse(chunkStr.slice(5));
        if (dataObject && 'd' in dataObject && typeof dataObject.d !== 'object') {
            this.aggregatedData += dataObject.d;
        }
        callback();
      } catch (err) {
        this.callback(err);
      }
    } else {
      callback();
    }
  }

  sendFinalDataResponse() {
    if (!this.aggregatedData) return;
  
    const finalMessage = `data: ${JSON.stringify({ d: this.aggregatedData })}\n\n`;
    logger.debug("Final Message: ", finalMessage);
    this.sendFinalResponse({ statusCode: 200,
                              body: finalMessage,
                              headers: { 'Content-Type': 'application/json' }
                           });
  }

  sendFinalResponse(data) {
    this.callback(null, data);
    logger.debug('Response sent, no more data will get through beyond this point.');

  }
}

const returnResponse = async (responseStream, response) => {
  try {
      responseStream.sendFinalResponse(response);
  } catch (err) {
      logger.error('Response:', err);
  }
};

export const handler = async (event, context, callback) => {
    const responseStream = new ResponseStreamForStreamlessResponse({}, callback);
    try {
      logger.debug("Extracting params from event");
      const params = await extractParams(event);
      await routeRequest(params, returnResponse, responseStream);
  
      // After the routing and processing, send the final aggregated response
      responseStream.sendFinalDataResponse();
      
    } catch (e) {
      logger.error("Error processing request: " + e.message, e);
      callback(null, {
        statusCode: 400,
        body: JSON.stringify({ error: e.message }),
        headers: { 'Content-Type': 'application/json' }
      });
    }
};

