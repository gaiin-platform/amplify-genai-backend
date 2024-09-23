import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand, ScanCommand } from "@aws-sdk/lib-dynamodb";
import { extractParams } from "../common/handlers.js";
import { getLogger } from "../common/logging.js";

const logger = getLogger("mtd");
const client = new DynamoDBClient({});
const dynamoDB = DynamoDBDocumentClient.from(client);

const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
const historyCostDynamoTableName = process.env.HISTORY_COST_CALCULATIONS_DYNAMO_TABLE;

export const handler = async (event, context, callback) => {
    try {
        logger.debug("Extracting params from event");
        const params = await extractParams(event);

        if (params.statusCode) {
            return params; // This is an error response from extractParams
        }

        const { body } = params;

        if (!body || !body.data || !body.data.email) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'Email is required' }),
            };
        }

        const email = body.data.email;

        const queryParams = {
            TableName: costDynamoTableName,
            KeyConditionExpression: 'id = :email',
            ExpressionAttributeValues: {
                ':email': email,
            },
        };

        const command = new QueryCommand(queryParams);
        const result = await dynamoDB.send(command);

        if (result.Items.length === 0) {
            return {
                statusCode: 404,
                body: JSON.stringify({ error: 'No cost data found for the given email' }),
            };
        }

        let totalDailyCost = 0;
        let totalMonthlyCost = 0;

        result.Items.forEach(item => {
            totalDailyCost += parseFloat(item.dailyCost) || 0;
            totalMonthlyCost += parseFloat(item.monthlyCost) || 0;
        });

        const totalCost = totalDailyCost + totalMonthlyCost;

        return {
            statusCode: 200,
            body: JSON.stringify({
                email: email,
                dailyCost: totalDailyCost,
                monthlyCost: totalMonthlyCost,
                'MTD Cost': totalCost,
            }),
        };
    } catch (error) {
        logger.error("Error processing request: " + error.message, error);
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};

export const apiKeyUserCostHandler = async (event, context, callback) => {
    try {
        const params = await extractParams(event);
        if (params.statusCode) return params;

        const { body } = params;
        if (!body || !body.data || !Array.isArray(body.data.apiKeys) || !body.data.email) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'API keys array and email are required' }),
            };
        }

        const apiKeys = body.data.apiKeys;
        const email = body.data.email;

        if (!historyCostDynamoTableName || !costDynamoTableName || !email || apiKeys.length === 0) {
            logger.error("Missing required parameters");
            return {
                statusCode: 500,
                body: JSON.stringify({ error: 'Server configuration error' }),
            };
        }

        let results = {};

        for (const apiKey of apiKeys) {
            let totalApiKeyCost = 0;
            let userApiKeyCost = 0;
            let lastEvaluatedKey = null;

            // Query historyCostDynamoTableName
            do {
                const scanParams = {
                    TableName: historyCostDynamoTableName,
                    FilterExpression: 'contains(#accountInfo, :apiKey) AND attribute_exists(#accountInfo)',
                    ExpressionAttributeNames: {
                        '#accountInfo': 'accountInfo'
                    },
                    ExpressionAttributeValues: {
                        ':apiKey': apiKey
                    }
                };

                if (lastEvaluatedKey) {
                    scanParams.ExclusiveStartKey = lastEvaluatedKey;
                }

                try {
                    const command = new ScanCommand(scanParams);
                    const result = await dynamoDB.send(command);

                    result.Items.forEach(item => {
                        const dailyCost = parseFloat(item.dailyCost) || 0;
                        totalApiKeyCost += dailyCost;

                        if (item.userDate && item.userDate.startsWith(email)) {
                            userApiKeyCost += dailyCost;
                        }
                    });

                    lastEvaluatedKey = result.LastEvaluatedKey;
                } catch (error) {
                    logger.error("Error executing DynamoDB scan:", error);
                    throw error;
                }
            } while (lastEvaluatedKey);

            // Query costDynamoTableName
            const queryParams = {
                TableName: costDynamoTableName,
                KeyConditionExpression: 'id = :email',
                ExpressionAttributeValues: {
                    ':email': email,
                },
            };

            try {
                const command = new QueryCommand(queryParams);
                const result = await dynamoDB.send(command);

                result.Items.forEach(item => {
                    if (item.accountInfo && item.accountInfo.includes(apiKey)) {
                        const dailyCost = parseFloat(item.dailyCost) || 0;
                        totalApiKeyCost += dailyCost;
                        userApiKeyCost += dailyCost;
                    }
                });
            } catch (error) {
                logger.error("Error executing DynamoDB query:", error);
                throw error;
            }

            results[apiKey] = { totalApiKeyCost, userApiKeyCost };
        }

        return {
            statusCode: 200,
            body: JSON.stringify({
                email,
                results,
            }),
        };
    } catch (error) {
        logger.error("Error processing request:", error);
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};