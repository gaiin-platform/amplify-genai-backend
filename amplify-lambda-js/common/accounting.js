import axios from 'axios';
import { DynamoDBClient, PutItemCommand } from "@aws-sdk/client-dynamodb";
import { marshall } from "@aws-sdk/util-dynamodb";
import {getLogger} from "./logging.js";

const logger = getLogger("accounting");
const dynamodbClient = new DynamoDBClient({});
import { v4 as uuidv4 } from 'uuid';

const dynamoTableName = process.env.CHAT_USAGE_DYNAMO_TABLE;

export const recordUsage = async (account, requestId, model, inputTokens, outputTokens, details) => {

    if (!dynamoTableName) {
        logger.error("CHAT_USAGE_DYNAMO_TABLE table is not provided in the environment variables.");
        return false;
    }

    try {
        const accountId = account.accountId || 'general_account';

        if (account.accessToken.startsWith("amp-")) details = {...details, api_key: account.accessToken};

        const item = {
            id: { S: `${uuidv4()}` },
            requestId: { S: requestId },
            user: { S: account.user },
            time: { S: new Date().toISOString() },
            accountId: { S: accountId },
            inputTokens: {N: "" + inputTokens},
            outputTokens: {N: "" + outputTokens},
            modelId: { S: model.id },
            details: { M: marshall(details || {})}
        };

        const command = new PutItemCommand({
            TableName: dynamoTableName,
            Item: item
        });

        const response = await dynamodbClient.send(command);

    } catch (e) {
        return false;
    }

    return true;
};
