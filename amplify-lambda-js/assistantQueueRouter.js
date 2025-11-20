//Copyright (c) 2024 Vanderbilt University  
//Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import {getLogger} from "./common/logging.js";
import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
import {DeleteMessageCommand, SQSClient} from '@aws-sdk/client-sqs';
import {routeRequest} from "./router.js";
import {findResult, StreamResultCollector} from "./common/streams.js";

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

export const handler = async (event, context) => {

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