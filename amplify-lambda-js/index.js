
//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { Readable, Writable, pipeline } from 'stream';
import { promisify } from 'util';
import { extractParams } from "./common/handlers.js";
import { routeRequest } from "./router.js";
import { getLogger } from "./common/logging.js";
import { getUsageTracker } from "./common/usageTracking.js";
// Removed AWS X-Ray for performance optimization
// Removed Python LiteLLM - now using native JS providers
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


// 🛡️ PER-USER COST PROTECTION: Extract params first, then apply per-user circuit breaker
const protectedHandler = withCostMonitoring(async (event, responseStream, context) => {
    // 🚀 NATIVE JS PROVIDERS: No Python process needed - direct JS execution

    const effectiveStream = streamEnabled ? responseStream : new AggregatorStream();

    // Initialize usage tracker
    const usageTracker = getUsageTracker();
    let trackingContext = {};
    let params = null;

    try {
        logger.debug("Extracting params from event");

        // 🛡️ TIMEOUT PROTECTION: Prevent expensive hangs during param extraction
        params = await withTimeout(30000)(extractParams(event));

        // 🔑 PER-USER CIRCUIT BREAKER: Now we have user info for proper isolation
        const functionName = context.functionName || process.env.SERVICE_NAME || 'amplify-lambda-js';
        const userId = params?.user || null;

        // 📊 START USAGE TRACKING: Track Lambda execution for cost calculation
        if (userId && usageTracker.enabled) {
            const endpoint = event.rawPath || event.path || '/chat';
            const apiAccessed = params.body?.options?.accountId ? true : false; // API key has accountId
            trackingContext = usageTracker.startTracking(
                userId,
                'chat',
                endpoint,
                apiAccessed,
                context
            );
        }

        if (userId) {
            logger.debug(`🔑 Applying per-user circuit breaker for user ${userId.substring(0, 10)}...`);
        } else {
            logger.warn("⚠️ No user found - applying function-wide circuit breaker");
            logger.debug("Event data: ", event); // Log first 500 chars
            logger.debug("Extracted params: ", params); // Log first 500 chars
        }
        
        // Apply per-user circuit breaker protection to the main routing
        const perUserProtectedRouting = withCircuitBreaker(functionName, {
            userId: userId, // 🔑 KEY ADDITION: Per-user isolation
            maxErrorRate: 0.20, // 20% error rate threshold  
            maxCostPerHour: 30,  // $30/hour cost threshold
            cooldownPeriod: 300  // 5-minute cooldown
        })(async (_event, _context, params) => {
            // 🛡️ TIMEOUT PROTECTION: Main routing with 3-minute timeout (down from 15 min)
            return await withTimeout(180000)(routeRequest(params, returnResponse, effectiveStream));
        });
        
        // Execute the protected routing with user context
        await perUserProtectedRouting(event, context, params);
    
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
        
        // Re-throw for upstream error handling
        throw e;
    } finally {
        // 📊 END USAGE TRACKING: Record metrics for cost calculation
        if (usageTracker.enabled && trackingContext.startTime) {
            try {
                const result = { statusCode: 200 }; // Default to success
                const claims = {
                    account: params?.body?.options?.accountId || 'oauth',
                    user: params?.user || 'unknown',
                    api_key_id: params?.body?.options?.accountId ? 'api_key' : null,
                    purpose: params?.body?.options?.purpose || null
                };

                const metrics = usageTracker.endTracking(
                    trackingContext,
                    result,
                    claims,
                    null  // errorType handled in catch block
                );

                // Fire and forget - don't await
                if (metrics) {
                    usageTracker.recordMetrics(metrics).catch(err =>
                        logger.error(`Background metrics recording failed: ${err.message}`)
                    );
                }
            } catch (metricsError) {
                // Never let metrics break the function
                logger.error(`Metrics tracking failed: ${metricsError.message}`);
            }
        }
    }
});

export const handler = awslambda.streamifyResponse(protectedHandler);

// streamHandler: For API Gateway REST API streaming with AWS_PROXY + ResponseTransferMode: STREAM
export const streamHandler = awslambda.streamifyResponse(async (event, responseStream, context) => {
    logger.debug("streamHandler: API Gateway AWS_PROXY streaming mode - using metadata format");

    // API Gateway requires metadata JSON + 8-byte delimiter before payload
    const metadata = JSON.stringify({
        statusCode: 200,
        headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Access-Control-Allow-Origin': '*'
        }
    });

    // Write metadata
    responseStream.write(metadata);

    // Write 8 null bytes as delimiter (required by API Gateway)
    responseStream.write('\x00\x00\x00\x00\x00\x00\x00\x00');

    // Now call protectedHandler which will stream the actual payload
    return await protectedHandler(event, responseStream, context);
});