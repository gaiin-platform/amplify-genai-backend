//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import { DynamoDBClient, PutItemCommand, QueryCommand, UpdateItemCommand } from "@aws-sdk/client-dynamodb";
import { marshall } from "@aws-sdk/util-dynamodb";
import {getLogger} from "./logging.js";

const logger = getLogger("accounting");
const dynamodbClient = new DynamoDBClient({});
import { v4 as uuidv4 } from 'uuid';

const dynamoTableName = process.env.CHAT_USAGE_DYNAMO_TABLE;
const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
const modelRateDynamoTable = process.env.MODEL_RATE_TABLE;

export const recordUsage = async (account, requestId, model, inputTokens, outputTokens, inputCachedTokens, inputWriteCachedTokens, details) => {

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

    // Move apiKeyId declaration to function scope
    const apiKeyId = getApiKeyId(account);

    try {
        const accountId = account.accountId || 'general_account';
        
        // Validate that account.user is not undefined
        if (!account.user) {
            logger.error("Missing account.user in recordUsage call");
            return false;
        }

        if (apiKeyId) details = {...details, api_key_id: apiKeyId};


        const item = {
            id: { S: `${uuidv4()}` },
            requestId: { S: requestId },
            user: { S: account.user },
            time: { S: new Date().toISOString() },
            accountId: { S: accountId },
            inputTokens: {N: "" + inputTokens},
            outputTokens: {N: "" + outputTokens},
            modelId: { S: model.id },
            details: { M: marshall(details || {}, { removeUndefinedValues: true })}
        };

        const command = new PutItemCommand({
            TableName: dynamoTableName,
            Item: item
        });

        await dynamodbClient.send(command);

    } catch (e) {
        logger.error("Error recording usage:", e);
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
        
        // Handle new cached token cost fields with backward compatibility
        let inputCachedCostPerThousandTokens = 0;
        let inputWriteCachedCostPerThousandTokens = 0;
        
        // Try new fields first
        if (modelRate.InputCachedCostPerThousandTokens?.N !== undefined) {
            inputCachedCostPerThousandTokens = parseFloat(modelRate.InputCachedCostPerThousandTokens.N);
        }
        if (modelRate.InputWriteCachedCostPerThousandTokens?.N !== undefined) {
            inputWriteCachedCostPerThousandTokens = parseFloat(modelRate.InputWriteCachedCostPerThousandTokens.N);
        }
        
        // Backward compatibility: if old field exists and new fields are missing, use old field
        if (modelRate.CachedCostPerThousandTokens?.N !== undefined) {
            const legacyCachedCost = parseFloat(modelRate.CachedCostPerThousandTokens.N);
            // Only use legacy if new fields weren't explicitly set
            if (modelRate.InputCachedCostPerThousandTokens?.N === undefined) {
                inputCachedCostPerThousandTokens = legacyCachedCost;
            }
        }

        const inputCost = (inputTokens / 1000) * inputCostPerThousandTokens;
        const outputCost = (outputTokens / 1000) * outputCostPerThousandTokens;
        
        // Calculate cached token costs separately for precision
        const inputCachedCost = (inputCachedTokens / 1000) * inputCachedCostPerThousandTokens;
        const inputWriteCachedCost = (inputWriteCachedTokens / 1000) * inputWriteCachedCostPerThousandTokens;
        
        // Note: inputWriteCachedCostPerThousandTokens is ready for future use when providers support output caching
        
        const totalCost = inputCost + outputCost + inputCachedCost + inputWriteCachedCost;

        // adds the totalCost to the dailyCost field
        // gets the current hour (0-23), add the totalCost to the hourlyCost field (saves it to the correct index in hourlyCost's 24 index list)
        // ensures the code works if the record exists or if it doesn't exist yet

        const now = new Date();
        const currentHour = now.getUTCHours();

        // Create the accountInfo (secondary key)
        const coaString = account.accountId || 'general_account';
        const apiKeyIdInfo = apiKeyId || 'NA';
        const accountInfo = `${coaString}#${apiKeyIdInfo}`;

        // First update: Ensure dailyCost and hourlyCost are initialized
        const initializeExpression = `SET dailyCost = if_not_exists(dailyCost, :zero), hourlyCost = if_not_exists(hourlyCost, :emptyList), record_type = if_not_exists(record_type, :recordType)`;

        const initializeCommand = new UpdateItemCommand({
            TableName: costDynamoTableName,
            Key: {
                id: { S: account.user },
                accountInfo: { S: accountInfo }
            },
            UpdateExpression: initializeExpression,
            ExpressionAttributeValues: {
                ":zero": { N: "0" },
                ":emptyList": { L: Array(24).fill({ N: "0" }) },
                ":recordType": { S: "cost" }
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

const getApiKeyId = (account) => {
    if (account && account.accessToken?.startsWith("amp-") && account?.apiKeyId) return account.apiKeyId;
    return null;
}