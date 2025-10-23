
//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { Readable, Writable, pipeline } from 'stream';
import { promisify } from 'util';
import { extractParams } from "./common/handlers.js";  
import { routeRequest } from "./router.js";
import { getLogger } from "./common/logging.js";
// Removed AWS X-Ray for performance optimization
import {initPythonProcess} from "./litellm/litellmClient.js";
// 🛡️ COST PROTECTION
import { withCircuitBreaker, withTimeout } from "./common/circuitBreaker.js";
import { withCostMonitoring } from "./common/defensiveRouting.js";

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


// 🛡️ COST PROTECTION: Wrap with circuit breaker and timeout
const protectedHandler = (event, responseStream, context) => {
    // Dynamic function name from Lambda context or environment
    const functionName = context.functionName || process.env.SERVICE_NAME || 'amplify-lambda-js';
    
    return withCircuitBreaker(functionName, {
        maxErrorRate: 0.20, // 20% error rate threshold  
        maxCostPerHour: 30,  // $30/hour cost threshold
        cooldownPeriod: 300  // 5-minute cooldown
    })(withCostMonitoring(async (event, responseStream, context) => {

    // 🚀 ULTIMATE OPTIMIZATION: Start Python process IMMEDIATELY - before authentication!
    // This saves 1-8 seconds since Python starts in parallel with auth
    const pythonProcessPromise = initPythonProcess();

    const effectiveStream = streamEnabled ? responseStream : new AggregatorStream();

    try {
      logger.debug("Extracting params from event");
      
      // 🛡️ TIMEOUT PROTECTION: Prevent expensive hangs during param extraction
      const params = await withTimeout(30000)(extractParams(event));
      
      // 🛡️ TIMEOUT PROTECTION: Main routing with 3-minute timeout (down from 15 min)
      await withTimeout(180000)(routeRequest(params, returnResponse, effectiveStream, pythonProcessPromise));
  
      // If we are not streaming, send the final aggregated response now
      if (!streamEnabled) {
        effectiveStream.sendFinalDataResponse(responseStream);
      }
    } catch (e) {
        logger.error("Error processing request: " + e.message, e);
        
        // Enhanced error response with debugging info
        const errorResponse = {
            statusCode: 500,
            body: { 
                error: e.message,
                timestamp: new Date().toISOString(),
                requestId: context.awsRequestId
            }
        };
        
        // Special handling for timeout errors
        if (e.message.includes('timed out')) {
            errorResponse.statusCode = 408;
            errorResponse.body.error = 'Request timeout - operation took too long';
            logger.error("⏰ REQUEST TIMEOUT - prevented expensive hang:", {
                duration: context.getRemainingTimeInMillis ? (900000 - context.getRemainingTimeInMillis()) : 'unknown',
                requestId: context.awsRequestId
            });
        }
        
        await returnResponse(responseStream, errorResponse);
        
        // Re-throw to trigger circuit breaker
        throw e;
    } finally {
        // Removed X-Ray tracing for performance optimization
    }
    }))(event, responseStream, context);
};

export const handler = awslambda.streamifyResponse(protectedHandler);

