//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {getLogger} from "./common/logging.js";
import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
import {DeleteMessageCommand, SQSClient} from '@aws-sdk/client-sqs';
import {routeRequest} from "./router.js";
import {findResult, StreamResultCollector} from "./common/streams.js";
import { 
    withEnvVarsTracking, 
    DynamoDBOperation, 
    S3Operation, 
    SQSOperation 
} from './common/envVarsTracking.js';

const sqsClient = new SQSClient();
const s3Client = new S3Client();

const logger = getLogger("assistantQueueRouter");

const saveResultToS3 = async (resultKey, result, status, message) => {
    // Define the parameters for the putObject operation
    const putObjectParams = {
        Bucket: process.env.ASSISTANT_TASK_RESULTS_BUCKET_NAME,
        Key: resultKey,
        Body: JSON.stringify({result: result, status, message}),
    };

    try {
        // Upload the object to S3
        const data = await s3Client.send(new PutObjectCommand(putObjectParams));
        console.log("Object uploaded successfully. Location:", resultKey);
        return data;
    } catch (error) {
        console.error("Error uploading object:", error);
        throw error;
    }
}

const assistantQueueHandler = async (event, context) => {

    logger.debug("Received event for assistant");

    async function processTask(payload) {
        console.log('Processing task with keys and op:', Object.keys(payload), payload.op);
        const {params, op, resultKey} = payload;

        if (op === "chat") {

            console.log('Processing chat task', {
                ...params.body,
                messages:[{role:"user", content:"Messages Omitted"}]
            });

            const returnResponse = (str, resp) => {

            };

            const responseStream = new StreamResultCollector();

            await routeRequest(params, returnResponse, responseStream);

            const result = findResult(responseStream.result);
            logger.debug("Got chat result");

            // Save the result to S3 with the resultKey
            await saveResultToS3(resultKey, result, 200, "Chat operation completed successfully");

            logger.debug("Chat operation result saved to S3");
        }
    }

    for (const record of event.Records) {
        const payload = JSON.parse(record.body); // Parse the stringified message payload
        console.log('Received payload');

        try {

            // Call the placeholder processTask function with the payload
            await processTask(payload);

            // Delete the message from the queue after successful processing
            const deleteParams = {
                QueueUrl: process.env.assistant_task_queue_url, // Make sure this env variable is set
                ReceiptHandle: record.receiptHandle // Unique identifier for the message
            };
            await sqsClient.send(new DeleteMessageCommand(deleteParams));
            console.log('Deleted message from queue:', record.messageId);

        } catch (error) {
            console.error('Error processing message:', error);
            // Here you might want to handle the error differently,
            // such as sending the message to a dead letter queue.
        }
    }

};

// Export handler with environment variable tracking (using original name)
export const handler = withEnvVarsTracking({
    // SQS queue operations - require IAM permissions
    "assistant_task_queue_url": [SQSOperation.DELETE_MESSAGE],
    
    // DynamoDB tables - require IAM permissions (via routeRequest)
    "API_KEYS_DYNAMODB_TABLE": [DynamoDBOperation.QUERY, DynamoDBOperation.UPDATE_ITEM],
    "CHAT_USAGE_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "COST_CALCULATIONS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM, DynamoDBOperation.QUERY],
    "HISTORY_COST_CALCULATIONS_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
    "MODEL_RATE_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.QUERY],
    "REQUEST_STATE_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM, DynamoDBOperation.DELETE_ITEM],
    "HASH_FILES_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.QUERY],
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
    "ASSISTANTS_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.QUERY],
    "ASSISTANTS_ALIASES_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.QUERY],
    "ASSISTANT_GROUPS_DYNAMO_TABLE": [DynamoDBOperation.QUERY],
    "GROUP_ASSISTANT_CONVERSATIONS_DYNAMO_TABLE": [DynamoDBOperation.QUERY],
    "DATASOURCE_REGISTRY_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "ENV_VARS_TRACKING_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM],
    
    // S3 buckets - require IAM permissions
    "ASSISTANT_TASK_RESULTS_BUCKET_NAME": [S3Operation.PUT_OBJECT],
    "S3_FILE_TEXT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_IMAGE_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT, S3Operation.PUT_OBJECT],
    "S3_RAG_INPUT_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "S3_GROUP_ASSISTANT_CONVERSATIONS_BUCKET_NAME": [S3Operation.GET_OBJECT],
    "TRACE_BUCKET_NAME": [S3Operation.PUT_OBJECT],
    "ASSISTANT_LOGS_BUCKET_NAME": [S3Operation.PUT_OBJECT],
    
    // Secrets Manager - require IAM permissions (via routeRequest)
    "LLM_ENDPOINTS_SECRETS_NAME": [SecretsManagerOperation.GET_SECRET_VALUE],
    "SECRETS_ARN_NAME": [SecretsManagerOperation.GET_SECRET_VALUE]
    
    // Configuration-only variables (no AWS permissions needed):
    // "COGNITO_USER_POOL_ID": [], // Used for JWT verification only
    // "COGNITO_CLIENT_ID": [], // Used for JWT verification only
    // "IDP_PREFIX": [], // String processing only
    // "API_BASE_URL": [], // HTTP requests to other services
    // "SERVICE_NAME": [], // Tracking metadata only
    // "STAGE": [], // Tracking metadata only
    // "TRACING_ENABLED": [], // Boolean flag only
    // "DEP_REGION": [], // Region string for AWS SDK
    // "BEDROCK_GUARDRAIL_ID": [], // Config passed to Bedrock calls
    // "BEDROCK_GUARDRAIL_VERSION": [], // Config passed to Bedrock calls
}, assistantQueueHandler);