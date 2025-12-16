import { DynamoDBClient, ScanCommand } from '@aws-sdk/client-dynamodb';
import { config } from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
config({ path: join(__dirname, '../.env.local') });

const client = new DynamoDBClient({});
const tableName = process.env.USER_STORAGE_TABLE;
console.log('Table:', tableName);

// Scan for the specific UUID
const scanCommand = new ScanCommand({
    TableName: tableName,
    FilterExpression: 'contains(#uuid, :uuid)',
    ExpressionAttributeNames: {
        '#uuid': 'UUID'
    },
    ExpressionAttributeValues: {
        ':uuid': { S: '85f8581c-4313-46fa-8d6e-116ff0df3150' }
    },
    Limit: 20
});

try {
    const response = await client.send(scanCommand);
    console.log('=== Items with UUID 85f8581c-4313-46fa-8d6e-116ff0df3150 ===');
    console.log('Count:', response.Items?.length || 0);
    if (response.Items) {
        for (const item of response.Items) {
            console.log('\nFull item:', JSON.stringify(item, null, 2));
        }
    }
} catch (e) {
    console.error('Error:', e.message);
}

// Also scan for any item containing "jagadeesh"
const scanCommand2 = new ScanCommand({
    TableName: tableName,
    FilterExpression: 'contains(PK, :pk)',
    ExpressionAttributeValues: {
        ':pk': { S: 'jagadeesh' }
    },
    Limit: 50
});

try {
    const response = await client.send(scanCommand2);
    console.log('\n=== Items with "jagadeesh" in PK ===');
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
