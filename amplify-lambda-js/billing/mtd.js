import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand } from "@aws-sdk/lib-dynamodb";

const client = new DynamoDBClient({});
const dynamoDB = DynamoDBDocumentClient.from(client);

const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;

export const handler = async (event) => {
    try {
        const body = JSON.parse(event.body);
        const email = body.data.email;

        if (!email) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'Email is required' }),
            };
        }

        const params = {
            TableName: costDynamoTableName,
            KeyConditionExpression: 'id = :email',
            ExpressionAttributeValues: {
                ':email': email,
            },
        };

        const command = new QueryCommand(params);
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
        console.error('Error:', error);
        return {
            statusCode: 500,
            body: JSON.stringify({ error: 'Internal server error' }),
        };
    }
};