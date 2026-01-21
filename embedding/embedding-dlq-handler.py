# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import boto3
import json
import os
import urllib.parse
from pycommon.logger import getLogger
from pycommon.api.critical_logging import log_critical_error, SEVERITY_HIGH
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
import traceback
from shared_functions import extract_base_key_from_chunk, extract_chunk_number

logger = getLogger("embedding_dlq_handler")

sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")

@required_env_vars({
    "ADDITIONAL_CHARGES_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@track_execution(operation_name="embedding_dlq_handler", account="system")
def lambda_handler(event, context):
    """
    Process messages from the DLQ and mark corresponding chunks as failed in DynamoDB.
    This prevents chunks from being stuck in 'starting' status when lambda processing fails.
    """
    logger.info(f"[DLQ_HANDLER_START] 🚨 Processing {len(event['Records'])} DLQ messages")
    
    progress_table_name = os.environ["EMBEDDING_PROGRESS_TABLE"]
    table = dynamodb.Table(progress_table_name)
    
    processed_count = 0
    failed_count = 0
    
    for record_index, record in enumerate(event["Records"]):
        try:
            logger.info(f"[DLQ_MESSAGE] Processing message {record_index + 1}/{len(event['Records'])}")
            
            # Parse the original SQS message that failed
            original_message = json.loads(record["body"])
            
            # Handle the nested SQS message structure (SQS -> S3 Event)
            if "Records" not in original_message:
                logger.warning("[DLQ_MESSAGE] No Records found in DLQ message - skipping")
                continue
                
            s3_event_record = original_message["Records"][0]
            s3_info = s3_event_record["s3"]
            bucket_name = s3_info["bucket"]["name"]
            url_encoded_key = s3_info["object"]["key"]
            object_key = urllib.parse.unquote(url_encoded_key)
            
            logger.info(f"[DLQ_MESSAGE] Failed S3 object: {object_key}")
            
            # Extract chunk information
            try:
                child_chunk = extract_chunk_number(object_key)
                trimmed_src = extract_base_key_from_chunk(object_key)
                
                if child_chunk is None:
                    logger.error(f"[DLQ_MESSAGE] Could not extract chunk number from {object_key}")
                    failed_count += 1
                    continue
                    
                logger.info(f"[DLQ_MESSAGE] Extracted - Document: {trimmed_src}, Chunk: {child_chunk}")
                
            except Exception as e:
                logger.error(f"[DLQ_MESSAGE] Error extracting chunk info from {object_key}: {e}")
                failed_count += 1
                continue
            
            # Update chunk status to failed in DynamoDB
            try:
                error_message = f"Processing failed and moved to DLQ from bucket {bucket_name}"
                
                success = update_chunk_status_to_failed(
                    table, trimmed_src, str(child_chunk), error_message
                )
                
                if success:
                    logger.info(f"[DLQ_SUCCESS] ✅ Marked chunk {child_chunk} as failed for {trimmed_src}")
                    processed_count += 1
                else:
                    logger.error(f"[DLQ_FAILED] ❌ Failed to update chunk {child_chunk} for {trimmed_src}")
                    failed_count += 1
                    
            except Exception as e:
                logger.error(f"[DLQ_ERROR] Exception updating chunk {child_chunk} status: {e}")
                logger.exception(e)
                failed_count += 1
                
        except Exception as e:
            logger.error(f"[DLQ_ERROR] Critical error processing DLQ message {record_index + 1}: {e}")
            logger.exception(e)
            
            # CRITICAL: DLQ handler failing = orphaned chunks never marked as failed
            log_critical_error(
                function_name="lambda_handler",
                error_type="DLQProcessingFailure",
                error_message=f"Failed to process DLQ message: {str(e)}",
                severity=SEVERITY_HIGH,
                stack_trace=traceback.format_exc(),
                context={
                    "record_index": record_index + 1,
                    "total_records": len(event['Records']),
                    "failed_count": failed_count
                }
            )
            
            failed_count += 1
    
    logger.info(f"[DLQ_HANDLER_COMPLETE] ✅ Processed {processed_count} chunks, Failed {failed_count} chunks")
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "processed": processed_count,
            "failed": failed_count,
            "message": f"DLQ handler completed processing {len(event['Records'])} messages"
        })
    }


def update_chunk_status_to_failed(table, trimmed_src, child_chunk, error_message):
    """
    Update a specific child chunk status to 'failed' in DynamoDB progress table.
    
    Args:
        table: DynamoDB table resource
        trimmed_src: Document identifier 
        child_chunk: Chunk number as string
        error_message: Error description
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        import datetime
        current_time = datetime.datetime.now().isoformat()
        
        logger.info(f"[DLQ_UPDATE] Marking chunk {child_chunk} as failed for {trimmed_src}")
        
        # Ensure parent structure exists first
        try:
            table.update_item(
                Key={"object_id": trimmed_src},
                UpdateExpression="SET #data = if_not_exists(#data, :full_structure)",
                ExpressionAttributeNames={"#data": "data"},
                ExpressionAttributeValues={":full_structure": {"childChunks": {}}}
            )
        except Exception:
            # Structure creation errors are non-critical
            pass
        
        # Update the specific child chunk to failed status
        update_expression = """
            SET #data.#childChunks.#chunkId.#status = :failed_status,
                #data.#childChunks.#chunkId.#lastUpdated = :timestamp,
                #data.#childChunks.#chunkId.#error = :error,
                #data.#childChunks.#chunkId.#version = if_not_exists(#data.#childChunks.#chunkId.#version, :zero) + :one,
                #data.#childChunks.#chunkId.#source = :dlq_source
        """
        
        expression_attribute_names = {
            "#data": "data",
            "#childChunks": "childChunks", 
            "#chunkId": str(child_chunk),
            "#status": "status",
            "#lastUpdated": "lastUpdated",
            "#error": "error",
            "#version": "version",
            "#source": "source"
        }
        
        expression_attribute_values = {
            ":failed_status": "failed",
            ":timestamp": current_time,
            ":error": error_message,
            ":zero": 0,
            ":one": 1,
            ":dlq_source": "DLQ_HANDLER"
        }
        
        # Only update if chunk is not already in a terminal state
        condition_expression = (
            "attribute_not_exists(#data.#childChunks.#chunkId.#status) OR "
            "(#data.#childChunks.#chunkId.#status <> :completed AND #data.#childChunks.#chunkId.#status <> :failed)"
        )
        
        expression_attribute_values[":completed"] = "completed"
        expression_attribute_values[":failed"] = "failed"
        
        result = table.update_item(
            Key={"object_id": trimmed_src},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression=condition_expression,
            ReturnValues="UPDATED_NEW"
        )
        
        logger.info(f"[DLQ_UPDATE] ✅ Successfully marked chunk {child_chunk} as failed")
        logger.info(f"[DLQ_UPDATE] Parent status will be updated by embedding service when next chunk is processed")
        
        return True
        
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info(f"[DLQ_UPDATE] Chunk {child_chunk} already in terminal state - no update needed")
        return True  # Consider this success since chunk is already handled
        
    except Exception as e:
        logger.error(f"[DLQ_UPDATE] ❌ Failed to update chunk {child_chunk}: {e}")
        return False


# Removed update_parent_status_after_dlq_update() function
# DLQ handler now only marks individual chunks as failed
# Parent status updates are handled by the embedding service's existing logic