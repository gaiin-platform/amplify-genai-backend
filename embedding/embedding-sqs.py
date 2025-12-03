# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import boto3
import json
import os
from pycommon.decorators import required_env_vars
from pycommon.dal.providers.aws.resource_perms import (
    SQSOperation
)
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from pycommon.const import APIAccessType
from schemata.permissions import get_permission_checker
from pycommon.logger import getLogger
from shared_functions import extract_base_key_from_chunk, extract_chunk_number

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.EMBEDDING.value])
logger = getLogger("embedding_sqs")

sqs = boto3.client("sqs")

embedding_chunks_index_queue = os.environ["EMBEDDING_CHUNKS_INDEX_QUEUE"]


@required_env_vars({
    "EMBEDDING_CHUNKS_INDEX_QUEUE": [SQSOperation.RECEIVE_MESSAGE, SQSOperation.DELETE_MESSAGE],
})
@validated(op="get")
def get_in_flight_messages(event, context, current_user, name, data):
    try:
        queue_url = embedding_chunks_index_queue
        messages = {}
        while True:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,  # Fetch up to 10 messages at a time (the maximum allowed)
                WaitTimeSeconds=1,  # Short poll, you can increase to reduce the number of API calls
                VisibilityTimeout=0,  # Set to 0 if you only want to view the messages without hiding them
            )

            # If no messages are in the queue, break the loop
            if "Messages" not in response:
                logger.debug("no messages")
                break

            for message in response["Messages"]:
                message_id = message["MessageId"]
                if message_id in messages:
                    # avoid duplicates
                    continue

                message_body = json.loads(message["Body"])
                logger.debug(f"Message body: {message_body}")
                
                # Handle SQS message with Records array structure
                if "Records" not in message_body or len(message_body["Records"]) == 0:
                    logger.info("No Records found in this message.")
                    continue
                
                record = message_body["Records"][0]
                s3_object = record.get("s3", {}).get("object", {})
                logger.debug(f"S3 object: {s3_object}")
                child_key = s3_object.get("key", None)
                if not child_key:
                    logger.warning("No key in this message.")
                    continue

                text_location_key = extract_base_key_from_chunk(child_key)
                user = get_original_creator(text_location_key) or "unknown"

                chunk_number = extract_chunk_number(child_key)
               
                messages[message_id] = {
                    "messageId": message_id,
                    "eventTime": record.get("eventTime", ""),
                    "object": {
                        "key": text_location_key,
                        "size": s3_object.get("size", 0),
                        "user": user,
                        "chunkNumber": chunk_number,
                    },
                }

        logger.info(f"Total messages in flight: {len(messages)}")
        return {
            "statusCode": 200,
            "body": json.dumps({"success": True, "messages": list(messages.values())}),
        }
    except Exception as e:
        logger.error(f"Error occurred: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"success": False, "error": f"An error occurred {e}"}),
        }

def get_original_creator(text_location_key):
    progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(progress_table)
    
    # Check if there's existing progress data (for selective reprocessing)
    try:
        response = table.get_item(Key={"object_id": text_location_key})
        item = response.get("Item")
        if item and "originalCreator" in item:
            logger.info(f"Found original creator: {item['originalCreator']} for base key: {text_location_key}")
            return item["originalCreator"]
    except Exception as e:
        logger.error(f"Error retrieving progress data for base key {text_location_key}: {e}")
    return None