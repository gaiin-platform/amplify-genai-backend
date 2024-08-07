// this file needs to:
// 1.  add the value in the dailyCost field to the monthlyCost field
// 2.  save the value in the dailyCost field to the HISTORY_COST_CALCULATIONS_DYNAMO_TABLE. 
// 3.  set the dailyCost field to zero and set the hourlyCost field to an list of 24 zeros (set all of the existing values in hourlyCost to zero)
// *4. if this is the first day of the month, the monthlyCost field needs to be saved to the history usage table, and the  reset as well

const AWS = require('aws-sdk');
const dynamoDB = new AWS.DynamoDB.DocumentClient();

exports.handler = async (event) => {
    const costCalculationsTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
    const historyUsageTableName = process.env.HISTORY_USAGE_TABLE;
    const now = new Date();
    const isMonthlyReset = now.getUTCDate() === 1;

    try {
        const params = {
            TableName: tableName,
            Key: { id: 'cost_calculation' },
        };

        const result = await dynamoDB.get(params).promise();
        const item = result.Item || {};

        if (isMonthlyReset) {
            await performMonthlyReset(tableName);
        } else {
            await performDailyReset(tableName, item);
        }

        return { statusCode: 200, body: 'Reset operation completed successfully' };
    } catch (error) {
        console.error('Error during reset operation:', error);
        return { statusCode: 500, body: 'Error during reset operation' };
    }
};

async function performDailyReset(tableName, item) {
    const updatedMonthlyCost = (item.monthlyCost || 0) + (item.dailyCost || 0);
    const updateExpression = 'SET monthlyCost = :monthlyCost, dailyCost = :zero, ' +
        Object.keys(Array(24).fill()).map(hour => `hour_${hour} = :zero`).join(', ');

    const updateParams = {
        TableName: tableName,
        Key: { id: 'cost_calculation' },
        UpdateExpression: updateExpression,
        ExpressionAttributeValues: {
            ':monthlyCost': updatedMonthlyCost,
            ':zero': 0,
        },
    };

    await dynamoDB.update(updateParams).promise();
}

async function performMonthlyReset(tableName) {
    const updateExpression = 'SET dailyCost = :zero, monthlyCost = :zero, ' +
        Object.keys(Array(24).fill()).map(hour => `hour_${hour} = :zero`).join(', ');

    const updateParams = {
        TableName: tableName,
        Key: { id: 'cost_calculation' },
        UpdateExpression: updateExpression,
        ExpressionAttributeValues: {
            ':zero': 0,
        },
    };

    await dynamoDB.update(updateParams).promise();
}
