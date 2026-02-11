"""
Async Document Processing Entry Point
Validates document and routes to appropriate processing queue
Returns immediately to avoid Lambda timeout
"""

import boto3
import json
import os
import urllib.parse
from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, S3Operation

from rag.status_manager import update_document_status, DocumentStatus
from rag.document_classifier import classify_document_for_pipeline, get_pipeline_queue_url, get_pipeline_description
from rag.rag_secrets import get_rag_secrets_for_document

logger = getLogger("async_processor")

s3 = boto3.client("s3")
sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")


@required_env_vars({
    "FILES_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    "VDR_PROCESSING_QUEUE_URL": [],
    "TEXT_RAG_PROCESSING_QUEUE_URL": []
})
@track_execution(operation_name="async_document_processor", account="system")
def async_document_processor(event, context):
    """
    Fast async document processor entry point

    This function:
    1. Validates the document exists (5-10s)
    2. Classifies document type (VDR vs Text RAG)
    3. Sends to appropriate processing queue
    4. Returns immediately (no Lambda timeout!)

    Background workers process documents asynchronously
    """

    logger.info("Async document processor started")
    logger.info(f"Received event: {event}")

    for record in event["Records"]:
        try:
            # Parse S3 event
            s3_event = json.loads(record["body"])
            s3_record = s3_event["Records"][0] if "Records" in s3_event else s3_event
            s3_info = s3_record["s3"]

            bucket = s3_info["bucket"]["name"]
            key = urllib.parse.unquote_plus(s3_info["object"]["key"])

            force_reprocess = s3_record.get("force_reprocess", False)

            logger.info(f"Processing: s3://{bucket}/{key}")

            # Update status: validating
            update_document_status(bucket, key, DocumentStatus.VALIDATING, {
                "progress": 0,
                "message": "Validating document..."
            })

            # STEP 1: Validate document exists and get metadata (fast)
            try:
                file_metadata = s3.head_object(Bucket=bucket, Key=key)
                file_size_mb = file_metadata['ContentLength'] / (1024 * 1024)

                logger.info(f"Document size: {file_size_mb:.2f}MB")

            except Exception as e:
                error_msg = f"Failed to access document: {str(e)}"
                logger.error(error_msg)
                update_document_status(bucket, key, DocumentStatus.FAILED, {
                    "error": error_msg,
                    "stage": "validation"
                })
                continue

            # STEP 2: Get RAG configuration
            rag_enabled = (
                True if force_reprocess
                else file_metadata.get("Metadata", {}).get("rag_enabled", "false") == "true"
            )

            if not rag_enabled and not force_reprocess:
                logger.info("RAG disabled for this document, skipping processing")
                update_document_status(bucket, key, DocumentStatus.COMPLETED, {
                    "progress": 100,
                    "message": "RAG disabled, no processing needed"
                })
                continue

            # Retrieve RAG secrets (user credentials, etc.)
            user = None
            account_data = None

            try:
                rag_details = get_rag_secrets_for_document(key)
                if rag_details['success']:
                    account_data = rag_details['data']
                    user = account_data.get('user')
                    logger.info(f"Retrieved RAG secrets for user: {user}")
                else:
                    logger.warning("Failed to retrieve RAG secrets, using defaults")
            except Exception as e:
                logger.error(f"Error retrieving RAG secrets: {str(e)}")

            # STEP 3: Classify document (VDR vs Text RAG)
            pipeline_type = classify_document_for_pipeline(key, file_metadata, file_size_mb)
            pipeline_desc = get_pipeline_description(pipeline_type)

            logger.info(f"Classification result: {pipeline_type}")
            logger.info(f"Pipeline: {pipeline_desc}")

            # STEP 4: Get queue URL for pipeline
            queue_url = get_pipeline_queue_url(pipeline_type)

            if not queue_url:
                error_msg = f"No queue configured for pipeline type: {pipeline_type}"
                logger.error(error_msg)
                update_document_status(bucket, key, DocumentStatus.FAILED, {
                    "error": error_msg,
                    "stage": "routing"
                })
                continue

            # STEP 5: Prepare message for processing queue
            processing_message = {
                "bucket": bucket,
                "key": key,
                "pipeline": pipeline_type,
                "file_size_mb": file_size_mb,
                "mime_type": file_metadata.get('ContentType', 'application/octet-stream'),
                "force_reprocess": force_reprocess,
                "user": user,
                "account_data": account_data
            }

            # STEP 6: Send to processing queue
            sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(processing_message)
            )

            logger.info(f"Document queued for {pipeline_type} processing")

            # Update status: queued
            update_document_status(bucket, key, DocumentStatus.QUEUED, {
                "progress": 5,
                "pipeline": pipeline_type,
                "pipeline_description": pipeline_desc,
                "message": f"Queued for {pipeline_desc}"
            }, user=user)

            logger.info(f"✓ Async processing initiated for s3://{bucket}/{key}")

        except Exception as e:
            logger.error(f"Error in async processor: {str(e)}")

            # Try to update status as failed
            try:
                if 'bucket' in locals() and 'key' in locals():
                    update_document_status(bucket, key, DocumentStatus.FAILED, {
                        "error": str(e),
                        "stage": "async_processor"
                    })
            except:
                pass

            # Don't re-raise - process other records
            continue

    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Documents queued for async processing'})
    }
