#!/usr/bin/env node

import { DynamoDBClient, UpdateItemCommand, ScanCommand } from "@aws-sdk/client-dynamodb";
import { config } from 'dotenv';

// Load environment variables
config();

const client = new DynamoDBClient({});
const tableName = process.env.COST_CALCULATIONS_DYNAMO_TABLE;

if (!tableName) {
    console.error("COST_CALCULATIONS_DYNAMO_TABLE environment variable is required");
    process.exit(1);
}

console.log(`Starting backfill for table: ${tableName}`);

async function backfillRecordType() {
    let processedCount = 0;
    let updatedCount = 0;
    let errorCount = 0;
    let lastEvaluatedKey = null;

    do {
        try {
            // Scan the table to get all records
            const scanParams = {
                TableName: tableName,
                FilterExpression: 'attribute_not_exists(record_type)', // Only get records without record_type
                Limit: 25 // Process in small batches to avoid throttling
            };

            if (lastEvaluatedKey) {
                scanParams.ExclusiveStartKey = lastEvaluatedKey;
            }

            console.log(`Scanning for records without record_type...`);
            const scanResult = await client.send(new ScanCommand(scanParams));

            if (!scanResult.Items || scanResult.Items.length === 0) {
                console.log("No more records to process");
                break;
            }

            console.log(`Found ${scanResult.Items.length} records to update`);

            // Update each record
            for (const item of scanResult.Items) {
                try {
                    const updateParams = {
                        TableName: tableName,
                        Key: {
                            id: item.id,
                            accountInfo: item.accountInfo
                        },
                        UpdateExpression: "SET record_type = :recordType",
                        ExpressionAttributeValues: {
                            ":recordType": { S: "cost" }
                        },
                        ConditionExpression: "attribute_not_exists(record_type)" // Only update if record_type doesn't exist
                    };

                    await client.send(new UpdateItemCommand(updateParams));
                    updatedCount++;
                    
                    if (updatedCount % 10 === 0) {
                        console.log(`Updated ${updatedCount} records...`);
                    }
                } catch (updateError) {
                    if (updateError.name === 'ConditionalCheckFailedException') {
                        // Record already has record_type, skip
                        console.log(`Skipping record ${item.id.S}#${item.accountInfo.S} - already has record_type`);
                    } else {
                        console.error(`Error updating record ${item.id.S}#${item.accountInfo.S}:`, updateError.message);
                        errorCount++;
                    }
                }

                processedCount++;
            }

            lastEvaluatedKey = scanResult.LastEvaluatedKey;
            
            // Add a small delay to avoid throttling
            if (lastEvaluatedKey) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }

        } catch (scanError) {
            console.error("Error during scan:", scanError);
            break;
        }

    } while (lastEvaluatedKey);

    console.log("\n=== Backfill Complete ===");
    console.log(`Total records processed: ${processedCount}`);
    console.log(`Records updated: ${updatedCount}`);
    console.log(`Errors: ${errorCount}`);
}

// Run the backfill
backfillRecordType()
    .then(() => {
        console.log("Backfill script completed successfully");
        process.exit(0);
    })
    .catch((error) => {
        console.error("Backfill script failed:", error);
        process.exit(1);
    });