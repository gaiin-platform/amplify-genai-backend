import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand } from "@aws-sdk/lib-dynamodb";
import { extractParams } from "../common/handlers.js";
import { getLogger } from "../common/logging.js";

const logger = getLogger("mtd");
const client = new DynamoDBClient({});
const dynamoDB = DynamoDBDocumentClient.from(client);

const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;

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