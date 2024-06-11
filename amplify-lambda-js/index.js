//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {extractParams} from "./common/handlers.js";
import {routeRequest} from "./router.js";
import {getLogger} from "./common/logging.js";

const logger = getLogger("index");

const returnResponse = (responseStream, response) => {
    responseStream = awslambda.HttpResponseStream.from(responseStream, response);
    responseStream.write(JSON.stringify(response.body));
    responseStream.end();
}

export const handler = awslambda.streamifyResponse(
    async (event, responseStream, context) => {
        try {

            logger.debug("Extracting params from event");
            const params = await extractParams(event);

            await routeRequest(params, returnResponse, responseStream);

        } catch (e) {
            console.error("Error processing request: " + e);
            console.error(e);

            returnResponse(responseStream, {
                statusCode: 400,
                body: {error: e.message}
            });
        }
    });
