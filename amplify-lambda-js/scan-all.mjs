import { DynamoDBClient, ScanCommand } from '@aws-sdk/client-dynamodb';
import { config } from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
config({ path: join(__dirname, '../.env.local') });

const client = new DynamoDBClient({});
const tableName = process.env.USER_STORAGE_TABLE;

// Scan for IDP prefix username
const scanCommand = new ScanCommand({
    TableName: tableName,
    FilterExpression: 'contains(PK, :pk)',
    ExpressionAttributeValues: {
        ':pk': { S: 'dev-amplifygenai' }
    },
    Limit: 20
});

try {
    const response = await client.send(scanCommand);
    console.log('=== Items with "dev-amplifygenai" in PK ===');
    console.log('Count:', response.Items?.length || 0);
    if (response.Items) {
        for (const item of response.Items) {
            console.log('\nPK:', item.PK?.S);
            console.log('SK:', item.SK?.S);
        }
    }
} catch (e) {
    console.error('Error:', e.message);
}

// Also scan for brave_search in SK
const scanCommand2 = new ScanCommand({
    TableName: tableName,
    FilterExpression: 'contains(SK, :sk)',
    ExpressionAttributeValues: {
        ':sk': { S: 'brave_search' }
    },
    Limit: 20
});

try {
    const response = await client.send(scanCommand2);
    console.log('\n=== Items with "brave_search" in SK ===');
    console.log('Count:', response.Items?.length || 0);
    if (response.Items) {
        for (const item of response.Items) {
            console.log('\nPK:', item.PK?.S);
            console.log('SK:', item.SK?.S);
        }
    }
} catch (e) {
    console.error('Error:', e.message);
}
