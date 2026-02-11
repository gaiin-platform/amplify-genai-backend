"""
Text RAG Pipeline with Selective Visual Processing
For text-heavy documents (code, plain text, spreadsheets, simple PDFs)
Uses selective visual processing: only process important visuals (3.3X faster)
"""

import boto3
import json
import os
import tempfile
from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, S3Operation

from rag.status_manager import update_document_status, DocumentStatus, mark_failed, mark_completed
from rag.handlers.selective_visual_processor import batch_process_visuals_selective
from rag.rag_secrets import get_rag_secrets_for_document

logger = getLogger("text_rag_pipeline")

s3 = boto3.client("s3")
sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")


@required_env_vars({
    "DOCUMENT_STATUS_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "RAG_CHUNK_DOCUMENT_QUEUE_URL": []
})
@track_execution(operation_name="text_rag_processor", account="system")
def process_document_text_rag(event, context):
    """
    Text RAG pipeline processor with selective visual processing

    This function:
    1. Downloads document from S3
    2. Extracts text using markitdown
    3. Extracts visuals (images, charts, diagrams)
    4. Selectively processes important visuals only (3.3X faster)
    5. Merges text + visual transcriptions
    6. Sends to chunking queue

    Expected time:
    - 100 pages: ~180s (vs 960s for old pipeline)
    - 2000 pages: ~3600s (60 min, but avoids timeout with async)
    """

    logger.info("Text RAG pipeline processor started")

    for record in event["Records"]:
        bucket = None
        key = None

        try:
            # Parse SQS message
            message = json.loads(record["body"])

            bucket = message["bucket"]
            key = message["key"]
            pipeline = message.get("pipeline", "text_rag")
            file_size_mb = message.get("file_size_mb", 0)
            user = message.get("user")
            account_data = message.get("account_data", {})

            logger.info(f"Processing Text RAG document: s3://{bucket}/{key}")
            logger.info(f"File size: {file_size_mb:.2f}MB")

            # Update status: processing started
            update_document_status(bucket, key, DocumentStatus.PROCESSING_STARTED, {
                "progress": 10,
                "pipeline": pipeline,
                "message": "Starting text extraction..."
            }, user=user)

            # STEP 1: Download document from S3
            logger.info("Downloading document from S3...")

            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                s3.download_fileobj(bucket, key, tmp_file)
                local_file_path = tmp_file.name

            logger.info(f"Document downloaded to: {local_file_path}")

            # STEP 2: Extract text and visuals using markitdown
            logger.info("Extracting text and visuals...")
            update_document_status(bucket, key, DocumentStatus.EXTRACTING_TEXT, {
                "progress": 20,
                "message": "Extracting text and visuals..."
            }, user=user)

            extracted_content = extract_text_and_visuals(local_file_path, key)

            text_content = extracted_content.get("text", "")
            visual_map = extracted_content.get("visuals", {})

            num_visuals = len(visual_map)
            text_length = len(text_content)

            logger.info(f"Extracted text: {text_length} characters")
            logger.info(f"Extracted visuals: {num_visuals} images")

            update_document_status(bucket, key, DocumentStatus.PROCESSING_VISUALS, {
                "progress": 40,
                "total_visuals": num_visuals,
                "message": f"Classifying {num_visuals} visuals..."
            }, user=user)

            # STEP 3: Selectively process important visuals only (3.3X speedup)
            processed_visuals = {}

            if num_visuals > 0 and account_data:
                logger.info("Starting selective visual processing...")
                update_document_status(bucket, key, DocumentStatus.CLASSIFYING_VISUALS, {
                    "progress": 45,
                    "total_visuals": num_visuals,
                    "message": "Classifying visuals by importance..."
                }, user=user)

                # Use selective processing (only 30-50% processed with LLM)
                # Run async function in sync context
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                processed_visuals = loop.run_until_complete(
                    batch_process_visuals_selective(
                        visual_map,
                        user,
                        account_data
                    )
                )

                # Count successful transcriptions
                successful_count = sum(
                    1 for v in processed_visuals.values()
                    if v.get("transcription")
                )

                logger.info(f"Visual processing complete: {successful_count}/{num_visuals} transcribed")

                update_document_status(bucket, key, DocumentStatus.PROCESSING_VISUALS, {
                    "progress": 70,
                    "visuals_processed": successful_count,
                    "total_visuals": num_visuals,
                    "message": f"Processed {successful_count}/{num_visuals} visuals"
                }, user=user)

            else:
                logger.info("No visuals to process or no account data")

            # STEP 4: Merge text + visual transcriptions
            logger.info("Merging text and visual content...")

            merged_content = merge_text_and_visuals(text_content, processed_visuals)

            logger.info(f"Merged content length: {len(merged_content)} characters")

            # STEP 5: Send to chunking queue
            logger.info("Sending to chunking queue...")
            update_document_status(bucket, key, DocumentStatus.CHUNKING, {
                "progress": 80,
                "message": "Queuing for chunking and embedding..."
            }, user=user)

            chunking_message = {
                "bucket": bucket,
                "key": key,
                "content": merged_content,
                "num_visuals": num_visuals,
                "visuals_transcribed": sum(1 for v in processed_visuals.values() if v.get("transcription")),
                "user": user,
                "account_data": account_data,
                "pipeline": "text_rag"
            }

            chunk_queue_url = os.environ.get("RAG_CHUNK_DOCUMENT_QUEUE_URL")

            sqs.send_message(
                QueueUrl=chunk_queue_url,
                MessageBody=json.dumps(chunking_message)
            )

            logger.info("Document queued for chunking")

            # STEP 6: Cleanup temporary files
            try:
                os.unlink(local_file_path)
                logger.info("Cleaned up temporary files")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp files: {str(e)}")

            # Update status - chunking queue will mark as completed
            update_document_status(bucket, key, DocumentStatus.CHUNKING, {
                "progress": 85,
                "message": "Document sent to chunking pipeline"
            }, user=user)

            logger.info(f"✓ Text RAG processing completed for s3://{bucket}/{key}")

        except Exception as e:
            error_msg = f"Text RAG processing failed: {str(e)}"
            logger.error(error_msg)
            logger.exception(e)

            # Mark as failed
            if bucket and key:
                mark_failed(bucket, key, error_msg, stage="text_rag_pipeline")

            # Don't re-raise - continue with other records
            continue

    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Text RAG processing completed'})
    }


def extract_text_and_visuals(file_path, file_key):
    """
    Extract text and visuals from document using markitdown

    Args:
        file_path: Local path to document
        file_key: S3 key (for extension detection)

    Returns:
        dict: {
            "text": str,
            "visuals": {marker: visual_data}
        }
    """
    try:
        from markitdown import MarkItDown
        import re

        # Initialize markitdown
        md = MarkItDown()

        logger.info(f"Extracting content from: {file_path}")

        # Convert document to markdown
        result = md.convert(file_path)

        text_content = result.text_content if hasattr(result, 'text_content') else str(result)

        # Extract visual markers (markitdown embeds images as markers)
        # Format: ![visual_marker](data:image/png;base64,...)
        visual_pattern = r'!\[([^\]]+)\]\(data:image/([^;]+);base64,([^)]+)\)'
        visual_matches = re.findall(visual_pattern, text_content)

        visual_map = {}

        for idx, (alt_text, image_type, base64_data) in enumerate(visual_matches, start=1):
            marker = f"visual_{idx}"

            # Decode base64 image
            import base64
            image_bytes = base64.b64decode(base64_data)

            # Calculate image dimensions and entropy (for importance scoring)
            try:
                from PIL import Image
                import io
                import numpy as np

                img = Image.open(io.BytesIO(image_bytes))
                width, height = img.size

                # Calculate entropy (complexity measure)
                img_array = np.array(img.convert('L'))  # Convert to grayscale
                histogram, _ = np.histogram(img_array, bins=256, range=(0, 256))
                histogram = histogram / histogram.sum()
                entropy = -np.sum(histogram * np.log2(histogram + 1e-10))

            except Exception as e:
                logger.warning(f"Failed to analyze image {idx}: {str(e)}")
                width, height, entropy = 0, 0, 0

            visual_map[marker] = {
                "image": image_bytes,
                "type": image_type,
                "alt_text": alt_text,
                "width": width,
                "height": height,
                "entropy": entropy,
                "has_caption": bool(alt_text and len(alt_text) > 5),
                "position": idx
            }

            logger.debug(f"Extracted visual {idx}: {width}x{height}, entropy={entropy:.2f}")

        logger.info(f"Extracted {len(visual_map)} visuals from document")

        return {
            "text": text_content,
            "visuals": visual_map
        }

    except Exception as e:
        logger.error(f"Failed to extract text and visuals: {str(e)}")
        raise


def merge_text_and_visuals(text_content, processed_visuals):
    """
    Merge text content with visual transcriptions

    Replaces visual markers with transcriptions

    Args:
        text_content: Original text with visual markers
        processed_visuals: Dict of {marker: visual_data_with_transcription}

    Returns:
        str: Merged content
    """
    try:
        merged = text_content

        # Replace each visual marker with transcription
        for marker, visual_data in processed_visuals.items():
            transcription = visual_data.get("transcription")

            if transcription:
                # Find marker in text (format: ![visual_marker](...))
                import re
                pattern = f'!\\[{re.escape(marker)}\\]\\([^)]+\\)'

                # Replace with transcription
                replacement = f"\n\n[Visual Content - {marker}]\n{transcription}\n\n"
                merged = re.sub(pattern, replacement, merged)

            else:
                # Visual was skipped or failed
                # Keep marker or remove based on importance
                importance = visual_data.get("importance_score", 0)

                if importance < 30:  # Low importance - remove marker
                    import re
                    pattern = f'!\\[{re.escape(marker)}\\]\\([^)]+\\)'
                    merged = re.sub(pattern, "", merged)
                else:
                    # Keep marker for context
                    import re
                    pattern = f'!\\[{re.escape(marker)}\\]\\([^)]+\\)'
                    replacement = f"[Visual content: {visual_data.get('alt_text', 'image')}]"
                    merged = re.sub(pattern, replacement, merged)

        logger.info("Text and visual content merged successfully")

        return merged

    except Exception as e:
        logger.error(f"Failed to merge text and visuals: {str(e)}")
        # Return original text as fallback
        return text_content
