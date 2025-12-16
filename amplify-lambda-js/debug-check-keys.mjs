import { DynamoDBClient, ScanCommand } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, QueryCommand } from '@aws-sdk/lib-dynamodb';
import { config } from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
config({ path: join(__dirname, '../.env.local') });

const client = new DynamoDBClient({});
const docClient = DynamoDBDocumentClient.from(client);

const tableName = process.env.USER_STORAGE_TABLE;
console.log('Table:', tableName);

// Scan for any items containing "api_keys" or "amplify-tools"
const scanCommand = new ScanCommand({
    TableName: tableName,
    FilterExpression: 'contains(PK, :pk)',
    ExpressionAttributeValues: {
        ':pk': { S: 'api_keys' }
    },
    Limit: 20
});

try {
    const response = await client.send(scanCommand);
    console.log('\n=== Items containing "api_keys" in PK ===');
    console.log('Count:', response.Items?.length || 0);
    if (response.Items) {
        for (let i = 0; i < response.Items.length; i++) {
            const item = response.Items[i];
            console.log('\nItem ' + (i + 1) + ':');
            console.log('  PK:', item.PK?.S);
            console.log('  SK:', item.SK?.S);
        }
    }
} catch (e) {
    console.error('Scan error:', e.message);
}

// Also try scanning for amplify-tools
const scanCommand2 = new ScanCommand({
    TableName: tableName,
    FilterExpression: 'contains(PK, :pk)',
    ExpressionAttributeValues: {
        ':pk': { S: 'amplify-tools' }
    },
    Limit: 20
});

try {
    const response = await client.send(scanCommand2);
    console.log('\n=== Items containing "amplify-tools" in PK ===');
    console.log('Count:', response.Items?.length || 0);
    if (response.Items) {
        for (let i = 0; i < response.Items.length; i++) {
            const item = response.Items[i];
            console.log('\nItem ' + (i + 1) + ':');
            console.log('  PK:', item.PK?.S);
            console.log('  SK:', item.SK?.S);
        }
    }
} catch (e) {
    console.error('Scan error:', e.message);
}

// Direct query with the expected PK
const queryCommand = new QueryCommand({
    TableName: tableName,
    KeyConditionExpression: 'PK = :pk',
    ExpressionAttributeValues: {
        ':pk': 'jagadeesh.r.vanga@vanderbilt.edu#amplify-tools#api_keys'
    }
});

try {
    const response = await docClient.send(queryCommand);
    console.log('\n=== Direct query for jagadeesh.r.vanga@vanderbilt.edu#amplify-tools#api_keys ===');
    console.log('Count:', response.Items?.length || 0);
    if (response.Items) {
        for (let i = 0; i < response.Items.length; i++) {
            console.log('\nItem ' + (i + 1) + ':', JSON.stringify(response.Items[i], null, 2));
        }
    }
} catch (e) {
    console.error('Query error:', e.message);
}
