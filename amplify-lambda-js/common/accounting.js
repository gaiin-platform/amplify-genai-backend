//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import axios from 'axios';
import { DynamoDBClient, PutItemCommand, QueryCommand, UpdateItemCommand } from "@aws-sdk/client-dynamodb";
import { marshall } from "@aws-sdk/util-dynamodb";
import {getLogger} from "./logging.js";

const logger = getLogger("accounting");
const dynamodbClient = new DynamoDBClient({});
import { v4 as uuidv4 } from 'uuid';

const dynamoTableName = process.env.CHAT_USAGE_DYNAMO_TABLE;
const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
const modelRateDynamoTable = process.env.MODEL_RATE_TABLE;

export const recordUsage = async (account, requestId, model, inputTokens, outputTokens, details) => {

    if (!dynamoTableName) {
        logger.error("CHAT_USAGE_DYNAMO_TABLE table is not provided in the environment variables.");
        return false;
    }

    if (!costDynamoTableName) {
        logger.error("COST_CALCULATIONS_DYNAMO_TABLE table is not provided in the environment variables.");
        return false;
    }

    if (!modelRateDynamoTable) {
        logger.error("MODEL_RATE_TABLE table is not provided in the environment variables.");
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

    try {
        const modelRateResponse = await dynamodbClient.send(new QueryCommand({
            TableName: modelRateDynamoTable,
            KeyConditionExpression: "ModelID = :modelId",
            ExpressionAttributeValues: {
                ":modelId": { S: model.id }
            }
        }));

        if (!modelRateResponse.Items || modelRateResponse.Items.length === 0) {
            logger.error(`No model rate found for ModelID: ${model.id}`);
            return false;
        }

        const modelRate = modelRateResponse.Items[0];
        const inputCostPerThousandTokens = parseFloat(modelRate.InputCostPerThousandTokens.N);
        const outputCostPerThousandTokens = parseFloat(modelRate.OutputCostPerThousandTokens.N);

        const inputCost = (inputTokens / 1000) * inputCostPerThousandTokens;
        const outputCost = (outputTokens / 1000) * outputCostPerThousandTokens;
        const totalCost = inputCost + outputCost;

        // TODO: need to save account.accountId, account.accessToken and account.user

        // adds the totalCost to the dailyCost field
        // gets the current hour (0-23), add the totalCost to the hourlyCost field (saves it to the correct index in hourlyCost's 24 index list)
        // ensures the code works if the record exists or if it doesn't exist yet

        const now = new Date();
        const currentHour = now.getUTCHours();

        // Create the accountInfo (secondary key)
        const coaString = account.accountId || 'general_account';
        const apiKey = account.accessToken.startsWith("amp-") ? account.accessToken : 'NA';
        const accountInfo = `${coaString}#${apiKey}`;

        // First update: Ensure dailyCost and hourlyCost are initialized
        const initializeExpression = `SET dailyCost = if_not_exists(dailyCost, :zero), hourlyCost = if_not_exists(hourlyCost, :emptyList)`;

        const initializeCommand = new UpdateItemCommand({
            TableName: costDynamoTableName,
            Key: {
                id: { S: account.user },
                accountInfo: { S: accountInfo }
            },
            UpdateExpression: initializeExpression,
            ExpressionAttributeValues: {
                ":zero": { N: "0" },
                ":emptyList": { L: Array(24).fill({ N: "0" }) }
            }
        });

        // Send the initialization command
        await dynamodbClient.send(initializeCommand);

        // Second update: Update dailyCost and the specific hourlyCost index
        const updateExpression = `SET dailyCost = dailyCost + :totalCost
        ADD hourlyCost[${currentHour}] :totalCost`;

        const updateCommand = new UpdateItemCommand({
            TableName: costDynamoTableName,
            Key: {
                id: { S: account.user },
                accountInfo: { S: accountInfo }
            },
            UpdateExpression: updateExpression,
            ExpressionAttributeValues: {
                ":totalCost": { N: totalCost.toString() }
            }
        });

        // Send the update command
        await dynamodbClient.send(updateCommand);
        
    } catch (e) {
        logger.error("Error calculating or updating cost:", e);
        return false;
    }

    return true;
};
