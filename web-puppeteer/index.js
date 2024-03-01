const { routeRequest } = require("./router");
const { getLogger } = require("./common/logging");
const { returnResponse } = require("./common/response");

// Initialize logger for this file
const logger = getLogger("index");

exports.handler = async (event, context) => {
    try {
        // Parse the body only once at the entry point
        const body = JSON.parse(event.body);
        logger.debug("Extracting params from event");

        // Call routeRequest with parsed body and additional parameters if needed
        return await routeRequest(event, context, body);
    } catch (e) {
        // Consider more granular error handling here
        logger.error("Error processing request: ", e);
        return returnResponse(500, { error: "Internal server error, please try again later." });
    }
};