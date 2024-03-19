import {DynamoDBClient, GetItemCommand, PutItemCommand, DeleteItemCommand} from "@aws-sdk/client-dynamodb";
import {getLogger} from "../common/logging.js";


import { config } from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

// Since __dirname is not available in ES module scope, you have to construct the path differently.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Now, use the constructed path to point to your .env.local file
config({ path: join(__dirname, '../../.env.local') });


const requestsTable = process.env.REQUEST_STATE_DYNAMO_TABLE;

const logger = getLogger("requestState");
const dynamodbClient = new DynamoDBClient({});

const getRequestState = async (user, requestId) => {
    if (!requestsTable) {
        logger.error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
    }

    const command = new GetItemCommand({
        TableName: requestsTable,
        Key: {
            user: {S: user},
            requestId: {S: requestId}
        }
    });

    logger.debug("Checking requests table state.");
    const response = await dynamodbClient.send(command);

    return response.Item;
}

export const shouldKill = async (user, requestId) => {

    if (!requestsTable) {
        logger.error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
    }

    const command = new GetItemCommand({
        TableName: requestsTable,
        Key: {
            user: {S: user},
            requestId: {S: requestId}
        }
    });

    logger.debug("Checking requests table for killswitch state.");
    const response = await dynamodbClient.send(command);

    if (!response.Item) {
        logger.debug("Request state not found, assuming no limits...");
        return false;
    }

    let killswitch = response.Item.exit.BOOL;

    logger.debug(`Killswitch state is ${killswitch ? "kill" : "continue"}.`);

    return killswitch;
}

export const createRequestState = async (user, requestId) => {
    return await updateKillswitch(user, requestId, false);
}

export const deleteRequestState = async (user, requestId) => {
    // should remove the entry from dyanmo

    if (!requestsTable) {
        logger.error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
    }

    try {
        // Delete the item
        const command = new DeleteItemCommand({
            TableName: requestsTable,
            Key: {
                user: {S: user},
                requestId: {S: requestId}
            }
        });

        logger.debug("Deleting request state.");
        await dynamodbClient.send(command);

        logger.debug("Deleted request state.");
    } catch (e) {
        return false;
    }

    return true;
}

export const updateKillswitch = async (user, requestId, killswitch) => {

    if (!requestsTable) {
        logger.error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("REQUEST_STATE_DYNAMO_TABLE is not provided in the environment variables.");
    }

    const command = new PutItemCommand({
        TableName: requestsTable,
        Item: {
            user: {S: user},
            requestId: {S: requestId},
            exit: {BOOL: killswitch},
            lastUpdatedTime: {N: "" + new Date().getTime()}
        }
    });

    logger.debug("Updating request state.");
    const response = await dynamodbClient.send(command);

    logger.debug("Updated request state.");

    return true;
}

export const isKilled = async (user, responseStream, chatRequest) => {
    if (chatRequest && chatRequest.options) {
        const requestId = chatRequest.options.requestId;
        if (requestId) {
            const doExit = await shouldKill(user, requestId);
            if (doExit) {
                try {
                    await deleteRequestState(user, requestId);
                } catch (e) {
                    logger.error("Error deleting request state: " + e);
                }

                responseStream.end();
                logger.info("Killswitch triggered, exiting.");
                return true;
            }
        }
    }
    return false;
}
