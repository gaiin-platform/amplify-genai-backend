import { DynamoDBClient, UpdateItemCommand, ScanCommand } from "@aws-sdk/client-dynamodb";
import { getLogger } from "../common/logging.js";

const logger = getLogger("backfill");
const client = new DynamoDBClient({});

const costTableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;
const historyTableName = process.env.HISTORY_COST_CALCULATIONS_DYNAMO_TABLE;

async function backfillTable(tableName, tableType) {
    logger.info(`Starting backfill for ${tableType} table: ${tableName}`);
    
    let processedCount = 0;
    let updatedCount = 0;
    let errorCount = 0;
    let lastEvaluatedKey = null;

    do {
        try {
            const scanParams = {
                TableName: tableName,
                FilterExpression: 'attribute_not_exists(record_type)',
                Limit: 25
            };

            if (lastEvaluatedKey) {
                scanParams.ExclusiveStartKey = lastEvaluatedKey;
            }

            const scanResult = await client.send(new ScanCommand(scanParams));

            if (!scanResult.Items || scanResult.Items.length === 0) {
                logger.info(`No more records to process in ${tableType} table`);
                break;
            }

            logger.info(`Found ${scanResult.Items.length} records to update in ${tableType} table`);

            for (const item of scanResult.Items) {
                try {
                    // Determine the key structure based on table type
                    const key = tableType === 'cost' 
                        ? { id: item.id, accountInfo: item.accountInfo }
                        : { userDate: item.userDate, accountInfo: item.accountInfo };

                    const updateParams = {
                        TableName: tableName,
                        Key: key,
                        UpdateExpression: "SET record_type = :recordType",
                        ExpressionAttributeValues: {
                            ":recordType": { S: "cost" }
                        },
                        ConditionExpression: "attribute_not_exists(record_type)"
                    };

                    await client.send(new UpdateItemCommand(updateParams));
                    updatedCount++;
                    
                } catch (updateError) {
                    if (updateError.name === 'ConditionalCheckFailedException') {
                        // Record already has record_type, skip
                    } else {
                        logger.error(`Error updating record in ${tableType} table:`, updateError.message);
                        errorCount++;
                    }
                }
                processedCount++;
            }

            lastEvaluatedKey = scanResult.LastEvaluatedKey;
            
            // Add delay to avoid throttling
            if (lastEvaluatedKey) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }

        } catch (scanError) {
            logger.error(`Error during scan of ${tableType} table:`, scanError);
            break;
        }

    } while (lastEvaluatedKey);

    logger.info(`${tableType} table backfill complete - Processed: ${processedCount}, Updated: ${updatedCount}, Errors: ${errorCount}`);
    return { processedCount, updatedCount, errorCount };
}

export const handler = async (event, context) => {
    try {
        logger.info("Starting record_type backfill process");

        if (!costTableName || !historyTableName) {
            throw new Error("Required environment variables not set");
        }

        // Backfill both tables
        const costResults = await backfillTable(costTableName, 'cost');
        const historyResults = await backfillTable(historyTableName, 'history');

        const response = {
            statusCode: 200,
            body: JSON.stringify({
                message: "Backfill completed successfully",
                results: {
                    costTable: costResults,
                    historyTable: historyResults
                }
            })
        };

        logger.info("Backfill process completed successfully", response.body);
        return response;

    } catch (error) {
        logger.error("Backfill process failed:", error);
        return {
            statusCode: 500,
            body: JSON.stringify({
                error: "Backfill process failed",
                message: error.message
            })
        };
    }
};