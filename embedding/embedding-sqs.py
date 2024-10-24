
#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import boto3
import json
import os
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client('sqs')

embedding_chunks_index_queue = os.environ['EMBEDDING_CHUNKS_INDEX_QUEUE']

def queue_document_for_embedding(event, context):
    queue_url = embedding_chunks_index_queue
    logger.info(f"Queue URL: {queue_url}")

    try:
        logger.info(f"Received event: {json.dumps(event)}")
        for record in event['Records']:
            # Send the S3 object data as a message to the SQS queue
            message_body = json.dumps(record)
            logger.info(f"Sending message to queue: {message_body}")
            response = sqs.send_message(QueueUrl=queue_url, MessageBody=message_body)
            logger.info(f"Message sent to queue. SQS response: {response}")
    
        return {'statusCode': 200, 'body': json.dumps('Successfully sent to SQS')}
    except Exception as e:
        logger.error(f"Error occurred: {e}", exc_info=True)
        return {'statusCode': 500, 'body': json.dumps('An error occurred')}