const { scrapeUrl } = require('./puppeteer/scrape_url');
const { getLogger } = require("./common/logging");
const { returnResponse } = require('./common/response');

const logger = getLogger("router");

exports.routeRequest = async (event, context, body) => {
    try {
        logger.debug("Routing request with body:", body);

        if (body.action === "scrape_url" && body.url) {
            const scrapeResult = await scrapeUrl(body.url);
            return returnResponse(200, { result: scrapeResult });
        } else {
            return returnResponse(400, { error: "Invalid request parameters" });
        }
    } catch (e) {
        logger.error("Error in routing: ", e);
        return returnResponse(500, { error: "Internal server error, please try again later." });
    }
};