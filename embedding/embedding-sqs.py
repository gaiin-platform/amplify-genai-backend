#Copyright (c) 2024 Vanderbilt University  
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import boto3
import json
import os
import logging

from common.validate import validated
from shared_functions import get_key_details

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client('sqs')

embedding_chunks_index_queue = os.environ['EMBEDDING_CHUNKS_INDEX_QUEUE']

# DEPRECATED: queue_document_for_embedding


@validated(op='get')
def get_in_flight_messages(event, context, current_user, name, data):
    try:
        queue_url = embedding_chunks_index_queue
        messages = {}
        while True:
            print("1")
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,  # Fetch up to 10 messages at a time (the maximum allowed)
                WaitTimeSeconds=1,  # Short poll, you can increase to reduce the number of API calls
                VisibilityTimeout=0  # Set to 0 if you only want to view the messages without hiding them
            )
            
            # If no messages are in the queue, break the loop
            if 'Messages' not in response:
                print("no messages")
                break
            
            for message in response['Messages']:
                message_id = message['MessageId']
                if (message_id in messages):
                    # avoid duplicates 
                    continue

                message_body = json.loads(message['Body'])
                s3_object = message_body.get("s3", {}).get("object", {})
                text_location_key = s3_object.get("key", None)
                if (not text_location_key):
                    print("No key in this message.")
                    continue
                print("3")
                
                key_details = get_key_details(text_location_key)
                user = 'unknown'
                if key_details and 'originalCreator' in key_details:
                    user = key_details['originalCreator']
                print("4")

                messages[message_id] = {
                    "messageId": message_id,
                    "eventTime": message_body.get("eventTime", ""),
                    "object": {
                        "key": text_location_key,
                        "size": s3_object.get("size", 0),
                        "user": user
                    }
                }

        print(f"Total messages in flight: {len(messages)}")
        return {'statusCode': 200, 'body': json.dumps({"success" : True, "messages": list(messages.values())})}
    except Exception as e:
        print(f"Error occurred: {e}")
        return {'statusCode': 500, 'body': json.dumps({"success" : False, "error": f"An error occurred {e}"})}
