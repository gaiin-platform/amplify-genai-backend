const AWS = require('aws-sdk');
const dynamoDB = new AWS.DynamoDB.DocumentClient();

const costDynamoTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;

exports.handler = async (event) => {
    try {
        // Parse the request body
        const body = JSON.parse(event.body);
        const email = body.data.email;

        if (!email) {
            return {
                statusCode: 400,
                body: JSON.stringify({ error: 'Email is required' }),
            };
        }

        // Query DynamoDB
        const params = {
            TableName: costDynamoTableName,
            KeyConditionExpression: 'id = :email',
            ExpressionAttributeValues: {
                ':email': email,
            },
        };

        const result = await dynamoDB.query(params).promise();

        if (result.Items.length === 0) {
            return {
                statusCode: 404,
                body: JSON.stringify({ error: 'No cost data found for the given email' }),
            };
        }

        const costData = result.Items[0];
        const dailyCost = parseFloat(costData?.dailyCost) || 0;
        const monthlyCost = parseFloat(costData?.monthlyCost) || 0;
        const totalCost = dailyCost + monthlyCost;

        return {
            statusCode: 200,
            body: JSON.stringify({
                email: email,
                dailyCost: dailyCost,
                monthlyCost: monthlyCost,
                totalCost: totalCost,
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