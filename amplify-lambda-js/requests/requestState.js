import {DynamoDBClient, GetItemCommand, PutItemCommand, DeleteItemCommand} from "@aws-sdk/client-dynamodb";
import {getLogger} from "../common/logging.js";

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

// write a function to poll the killswitch table and update the killswitch state
// this function should be called periodically and return a promise that resolves
// to the killswitch state (true is kill, false is continue). It should check the
// returned value and the lastUpdatedTime to make sure that the last update is not
// to long ago in the past
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
        logger.debug("Killswitch state not found, assuming no limits...");
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
        logger.error("KILLSWITCH_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("KILLSWITCH_DYNAMO_TABLE is not provided in the environment variables.");
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

        logger.debug("Deleting killswitch state.");
        await dynamodbClient.send(command);

        logger.debug("Deleted killswitch state.");
    } catch (e) {
        return false;
    }

    return true;
}

export const updateKillswitch = async (user, requestId, killswitch) => {

    if (!requestsTable) {
        logger.error("KILLSWITCH_DYNAMO_TABLE is not provided in the environment variables.");
        throw new Error("KILLSWITCH_DYNAMO_TABLE is not provided in the environment variables.");
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

    logger.debug("Updating killswitch state.");
    const response = await dynamodbClient.send(command);

    logger.debug("Updated killswitch state.");

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
                    logger.error("Error deleting killswitch: " + e);
                }

                responseStream.end();
                logger.info("Killswitch triggered, exiting.");
                return true;
            }
        }
    }
    return false;
}
