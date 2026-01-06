import { Writable } from 'stream';
import { extractParams } from "./common/handlers.js";
import { routeRequest } from "./router.js";
import { getLogger } from "./common/logging.js";
import { getUsageTracker } from "./common/usageTracking.js";

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

    // Initialize usage tracker
    const usageTracker = getUsageTracker();
    let trackingContext = {};
    let params = null;

    try {
      logger.debug("Extracting params from event");
      params = await extractParams(event);

      // 📊 START USAGE TRACKING: Track Lambda execution for cost calculation
      if (params?.user && usageTracker.enabled) {
          const endpoint = event.path || '/api_g_chat/chat';
          const apiAccessed = params.body?.options?.accountId ? true : false; // API key has accountId
          trackingContext = usageTracker.startTracking(
              params.user,
              'api_g_chat',
              endpoint,
              apiAccessed,
              context
          );
      }

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
                  null  // errorType
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
};

