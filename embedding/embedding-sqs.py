import boto3
import json
import os
sqs = boto3.client('sqs')

embedding_process_chunk_queue_url = os.environ['EMBEDDING_PROCESS_CHUNK_QUEUE_URL']

def queue_document_for_embedding(event, context):
    queue_url = embedding_process_chunk_queue_url
    print(f"Queue URL: {queue_url}")

    print(f"Received event: {event}")
    print(f"{event}")
    for record in event['Records']:
        # Send the S3 object data as a message to the SQS queue
        message_body = json.dumps(record)
        print(f"Sending message to queue: {message_body}")
        sqs.send_message(QueueUrl=queue_url, MessageBody=message_body)
        print(f"Message sent to queue: {message_body}")

    return {'statusCode': 200, 'body': json.dumps('Successfully sent to SQS')}
