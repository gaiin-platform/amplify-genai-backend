// this file needs to:
// 1.  add the value in the dailyCost field to the monthlyCost field for all records in COST_CALCULATIONS_DYNAMO_TABLE
// 2.  save the value in the dailyCost field (if it is non-zero) to the HISTORY_COST_CALCULATIONS_DYNAMO_TABLE (for all records in COST_CALCULATIONS_DYNAMO_TABLE)
// 3.  set the dailyCost field to zero and set the hourlyCost field to an list of 24 zeros (set all of the existing values in hourlyCost to zero)
// *4. if this is the first day of the month, the monthlyCost field needs to be saved to the history usage table (if the monthlyCost field is non-zero) and set to zero
// When saving a record to the history table, the primary key should be a user date composite with '#' in the middle, the secondary key should be the accountInfo

import { DynamoDBClient, ScanCommand, UpdateItemCommand, PutItemCommand } from "@aws-sdk/client-dynamodb";
import { marshall, unmarshall } from "@aws-sdk/util-dynamodb";

const dynamodbClient = new DynamoDBClient({});
const costTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
const historyTableName = process.env.HISTORY_COST_CALCULATIONS_DYNAMO_TABLE;

async function getAllItems() {
    let items = [];
    let lastEvaluatedKey = null;

    do {
        const params = {
            TableName: costTableName,
            ExclusiveStartKey: lastEvaluatedKey
        };

        const command = new ScanCommand(params);
        const response = await dynamodbClient.send(command);

        items = items.concat(response.Items.map(item => unmarshall(item)));
        lastEvaluatedKey = response.LastEvaluatedKey;
    } while (lastEvaluatedKey);

    return items;
}

async function updateItem(item) {
    try {
        const params = {
            TableName: costTableName,
            Key: {
                id: { S: item.id },
                accountInfo: { S: item.accountInfo }
            },
            UpdateExpression: "SET monthlyCost = if_not_exists(monthlyCost, :zero) + :dailyCost, dailyCost = :zero, hourlyCost = :emptyList, record_type = if_not_exists(record_type, :recordType)",
            ExpressionAttributeValues: {
                ":dailyCost": { N: item.dailyCost.toString() },
                ":zero": { N: "0" },
                ":emptyList": { L: Array(24).fill({ N: "0" }) },
                ":recordType": { S: "cost" }
            },
            ReturnValues: "UPDATED_NEW"
        };
        const command = new UpdateItemCommand(params);
        return await dynamodbClient.send(command);
    } catch (error) {
        console.error(`Error updating item for ${item.id}:`, error);
        throw error;
    }
}

function getYesterdayDate() {
    const yesterday = new Date();
    yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    return yesterday.toISOString().split('T')[0];
}

function getLastMonth() {
    const today = new Date();
    const lastMonth = new Date(today.getFullYear(), today.getMonth() - 1);
    return lastMonth.toISOString().slice(0, 7);
}

async function saveDailyCostHistory(item) {
    try {
        if (parseFloat(item.dailyCost) > 0) {
            const dateString = getYesterdayDate();
            const params = {
                TableName: historyTableName,
                Item: marshall({
                    userDate: `${item.id}#${dateString}`,
                    accountInfo: item.accountInfo,
                    dailyCost: parseFloat(item.dailyCost),
                    timestamp: new Date().toISOString()
                })
            };
            const command = new PutItemCommand(params);
            await dynamodbClient.send(command);
        }
    } catch (error) {
        console.error(`Error saving daily cost history for ${item.id}:`, error);
        throw error;
    }
}

async function handleMonthlyReset(item) {
    try {
        if (new Date().getUTCDate() === 1 && parseFloat(item.monthlyCost) > 0) {
            const lastMonth = getLastMonth();
            await dynamodbClient.send(new PutItemCommand({
                TableName: historyTableName,
                Item: marshall({
                    userDate: `${item.id}#${lastMonth}`,
                    accountInfo: item.accountInfo,
                    monthlyCost: parseFloat(item.monthlyCost),
                    timestamp: new Date().toISOString()
                })
            }));

            await dynamodbClient.send(new UpdateItemCommand({
                TableName: costTableName,
                Key: {
                    id: { S: item.id },
                    accountInfo: { S: item.accountInfo }
                },
                UpdateExpression: "SET monthlyCost = :zero, record_type = if_not_exists(record_type, :recordType)",
                ExpressionAttributeValues: {
                    ":zero": { N: "0" },
                    ":recordType": { S: "cost" }
                }
            }));
        }
    } catch (error) {
        console.error(`Error handling monthly reset for ${item.id}:`, error);
        throw error;
    }
}

export const handler = async (event) => {
    try {
        const items = await getAllItems();
        for (const item of items) {
            await updateItem(item);
            await saveDailyCostHistory(item);
            await handleMonthlyReset(item);
        }
        return { statusCode: 200, body: JSON.stringify({ message: "Billing reset completed successfully" }) };
    } catch (error) {
        console.error("Error in reset-billing:", error);
        return { statusCode: 500, body: JSON.stringify({ message: "Error occurred during billing reset" }) };
    }
};
