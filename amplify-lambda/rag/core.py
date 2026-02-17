# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import hashlib
import os
import mimetypes
import boto3
import json
import urllib.parse
from datetime import datetime
import re
import traceback
from boto3.dynamodb.conditions import Key
import nltk
from nltk.tokenize import sent_tokenize
import asyncio

from pycommon.logger import getLogger
from pycommon.api.critical_logging import log_critical_error, SEVERITY_HIGH
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
logger = getLogger("rag")

# Uncomment for local testing 
# import sys
# if __name__ == "__main__":
#     # Add the parent directory to Python path for local testing
#     current_dir = os.path.dirname(os.path.abspath(__file__))
#     parent_dir = os.path.dirname(current_dir)  # Go up to amplify-lambda/
#     sys.path.insert(0, parent_dir)


from rag.handlers.commaseparatedvalues import CSVHandler
from rag.handlers.excel import ExcelHandler
from rag.handlers.pdf import PDFHandler
from rag.handlers.powerpoint import PPTXHandler
from rag.handlers.word import DOCXHandler
from rag.handlers.text import TextHandler
from rag.handlers.markdown import MarkDownHandler
from rag.handlers.markitdown_extractor import MarkItDownExtractor
from rag.util import (
    get_text_content_location,
    get_text_metadata_location,
    get_text_hash_content_location,
)
from rag.handlers.shared_functions import is_likely_text
from rag.rag_secrets import get_rag_secrets_for_document, delete_rag_secrets_for_document

s3 = boto3.client("s3")
sqs = boto3.client("sqs")

# Global flag to track NLTK data initialization
_nltk_data_initialized = False

def _ensure_nltk_data():
    """
    Ensure NLTK punkt_tab data is available in Lambda environment.
    Downloads to /tmp on first call and reuses on subsequent invocations.

    CRITICAL: Must download to /tmp (not /tmp/nltk_data) because Lambda environment
    already searches /tmp in the default NLTK data path.
    """
    global _nltk_data_initialized

    if _nltk_data_initialized:
        return

    try:
        # Lambda's /tmp is writeable and already in NLTK's search path
        # We download directly to /tmp (not /tmp/nltk_data)
        tmp_path = "/tmp"

        # Ensure /tmp is in the NLTK data path (it usually is by default in Lambda)
        if tmp_path not in nltk.data.path:
            nltk.data.path.insert(0, tmp_path)
            logger.info("Added %s to NLTK data path", tmp_path)

        # Try to use the tokenizer - will raise LookupError if not available
        try:
            sent_tokenize("Test sentence.")
            logger.info("✅ NLTK punkt_tab data already available")
            _nltk_data_initialized = True
            return
        except LookupError:
            logger.info("NLTK punkt_tab data not found, downloading to %s...", tmp_path)

        # Download punkt_tab directly to /tmp
        # This creates /tmp/tokenizers/punkt_tab/... which NLTK will find
        logger.info("Downloading punkt_tab to %s", tmp_path)
        download_result = nltk.download("punkt_tab", download_dir=tmp_path, quiet=False)

        if not download_result:
            raise RuntimeError("nltk.download returned False - download may have failed")

        # Verify download succeeded by actually using the tokenizer
        logger.info("Verifying punkt_tab download...")
        sent_tokenize("Test sentence.")
        logger.info("✅ NLTK punkt_tab data successfully downloaded and verified")
        _nltk_data_initialized = True

    except Exception as e:
        logger.error("Failed to initialize NLTK data: %s", str(e), exc_info=True)
        logger.error("Current NLTK data path: %s", nltk.data.path)
        # Re-raise to fail fast - we can't proceed without tokenization
        raise RuntimeError(f"Cannot initialize NLTK tokenizer: {e}") from e


def decode_text(file_content, encoding):
    # Decode the file content using the detected encoding
    try:
        text = file_content.decode(encoding)
        return text
    except UnicodeDecodeError as e:
        # You can handle decoding errors differently if necessary
        raise ValueError(f"Decoding failed for encoding {encoding}: {e}")


def get_handler_and_split_params(key):
    if key.endswith(".pdf"):
        return PDFHandler(), {}
    elif key.endswith(".docx"):
        return DOCXHandler(), {}
    elif key.endswith(".pptx"):
        return PPTXHandler(), {}
    elif key.endswith(".xls") or key.endswith(".xlsx"):
        return ExcelHandler(), {"min_chunk_size": 512}
    elif key.endswith(".csv"):
        return CSVHandler(), {"min_chunk_size": 512}
    elif key.endswith(".html"):
        return None  # HTMLHandler(), {}
    else:
        return TextHandler(), {}


def get_text_extraction_handler(key):
    if key.endswith(".pdf"):
        return PDFHandler()
    elif key.endswith(".docx"):
        return DOCXHandler()
    elif key.endswith(".pptx"):
        return PPTXHandler()
    elif key.endswith(".xls") or key.endswith(".xlsx"):
        return ExcelHandler()
    elif key.endswith(".csv"):
        return CSVHandler()
    elif key.endswith(".md"):
        return MarkDownHandler()
    else:
        return TextHandler()


def get_file_extension(file_name, mime_type):
    # Attempt to guess the extension for the given mime type
    guessed_extension = mimetypes.guess_extension(mime_type)
    file_extension = os.path.splitext(file_name)[1]
    # If an extension for the mime type is found, return it
    if guessed_extension and guessed_extension.startswith("."):
        # Ensure there's a dot at the beginning of the extension
        return guessed_extension
    # If no extension is found for that mime type, return the original extension with "file." prepended
    elif guessed_extension:
        return "." + guessed_extension
    else:
        return file_extension


def preprocess_visual_content(file_content, key):
    """Extract visuals, replace with markers, return modified content + mapping"""
    if key.endswith(".pdf"):
        return PDFHandler().preprocess_pdf_visuals(file_content)
    elif key.endswith(".docx"):
        return DOCXHandler().preprocess_docx_visuals(file_content)
    elif key.endswith(".pptx"):
        return PPTXHandler().preprocess_pptx_visuals(file_content)
    elif key.endswith(".xls") or key.endswith(".xlsx"):
        return ExcelHandler().preprocess_excel_visuals(file_content)
    else:
        # No visual processing for text files, etc.
        return file_content, {}


# Extract text from file and return an array of chunks
async def extract_text_from_file(key, file_content, current_user=None, account_data=None):
    # only need to process visuals if account_data is provided since it contains chat-js required data
    processed_content, visual_map = preprocess_visual_content(file_content, key) if account_data else (file_content, {})

    # Process visuals asynchronously if any exist
    if current_user and visual_map:
        logger.info("Processing %d visuals for %s", len(visual_map), key)
        try:
            # ! Import here to avoid circular imports
            from rag.handlers.visual_to_text import batch_process_visuals

            visual_map = await batch_process_visuals(visual_map, current_user, account_data)
            logger.info("Visual processing completed: %d successful transcriptions", len(visual_map))
        except ImportError as e:
            logger.warning("Visual processing not available (circular import): %s", e)
            logger.info("Continuing without visual processing...")

    try:
        # First we will try with markitdown extractor,
        # if that fails then we will proceed as before
        markitdown_extractor = MarkItDownExtractor()
        markitdown_result = markitdown_extractor.extract_from_content(processed_content, key)
        if markitdown_result:
            logger.debug("MarkItDown extraction: %s", markitdown_result)
            md_bytes = markitdown_result.encode('utf-8')
            logger.info("MarkItDown extraction successful for %s", key)
            return MarkDownHandler().extract_text(md_bytes, key, visual_map)

    except Exception as e:
        logger.warning("Unable to extract text from %s using markitdown extractor: %s", key, str(e))
    logger.debug("Continuing with default handler logic...")

    # Get the appropriate handler and split parameters for the file type
    handler = get_text_extraction_handler(key)

    if handler:
        try: # using file_contents due to efficient location insertion, unlike markitdown which needs the altered preprocessed content
            return handler.extract_text(file_content, visual_map)
        except Exception as e:
            logger.error("Error extracting text from %s: %s", key, str(e))
            # Return an empty list to indicate that no chunks could be extracted
            return []
    else:
        is_text, encoding = is_likely_text(file_content)

        if is_text:
            # Decode file_content using the detected encoding
            text_content = decode_text(file_content, encoding)
            try:
                return TextHandler().extract_text(text_content, visual_map)
            except Exception as e:
                logger.error("Error extracting text from %s: %s", key, str(e))
                # Return an empty list to indicate that no chunks could be extracted
                return []


def split_text(sent_tokenize, content):
    if content.get("canSplit"):
        text = re.sub(r"\s+", " ", content["content"].strip())
        # Sentences will not need whitespace normalization.
        sentences = sent_tokenize(text)

        return [
            {**content, "content": sentence, "index": index}
            for index, sentence in enumerate(sentences)
        ]
    else:
        return [content]


def save_chunks(chunks_bucket, key, split_count, chunks, object_key=None, force_reprocess=False, chunks_needing_reprocessing=None):
    # Check if this chunk actually needs processing (applies to reprocessing AND retries after failures)
    if chunks_needing_reprocessing is not None:
        if len(chunks_needing_reprocessing) > 0:
            # Specific chunks need processing - check if this is one of them
            if split_count not in chunks_needing_reprocessing:
                logger.info("[SELECTIVE_SKIP] ⏭️ Chunk %d already completed - skipping upload (no SQS message needed)", split_count)
                return
            else:
                logger.info("[SELECTIVE_UPLOAD] 🔄 Chunk %d needs processing - resetting to starting status before upload", split_count)
                # Reset this chunk to "starting" status so embedding service can process it
                reset_chunk_to_starting_status(key, split_count)
        else:
            # Empty list means no progress data exists - process all chunks
            logger.info("[SELECTIVE_UPLOAD] 🆕 No progress data - uploading chunk %d for initial processing", split_count)
    else:
        # Fallback: selective logic failed, process all chunks for safety
        logger.info("[SELECTIVE_FALLBACK] 🛡️ Selective logic unavailable - uploading chunk %d for safety (processing all chunks)", split_count)
    
    # Upload the chunk
    chunks_key = f"{key}-{split_count}.chunks.json"
    logger.info("Saving %d local chunks to %s", len(chunks), chunks_key)
    
    # Prepare metadata for S3 object
    metadata = {}
    if object_key:
        metadata["object_key"] = object_key
        logger.debug("Adding object_key metadata: %s", object_key)
    
    if force_reprocess:
        metadata["force_reprocess"] = "true"
        logger.debug("Adding force_reprocess metadata: true")
    
    s3.put_object(
        Bucket=chunks_bucket,
        Key=chunks_key,
        Body=json.dumps({"chunks": chunks, "src": key}),
        Metadata=metadata
    )
    logger.info("✅ Uploaded chunks to %s/%s (will generate SQS message)", chunks_bucket, chunks_key)
    if object_key:
        logger.debug("Stored object_key metadata: %s", object_key)



def chunk_content(key, text_content, split_params, object_key=None, force_reprocess=False):
    # Initialize NLTK data for tokenization
    _ensure_nltk_data()
    # Normalize whitespace once at the start.

    # Get chunk reprocessing status once per document (avoid redundant DynamoDB calls)
    chunks_needing_reprocessing = None
    try:
        chunks_needing_reprocessing = get_chunks_needing_reprocessing(key)
        if chunks_needing_reprocessing is not None:
            if len(chunks_needing_reprocessing) > 0:
                logger.info("[CHUNK_REPROCESS_CHECK] Document %s needs reprocessing for chunks: %s", key, sorted(chunks_needing_reprocessing))
            else:
                logger.info("[CHUNK_REPROCESS_CHECK] Document %s has no progress data - processing all chunks", key)
        else:
            logger.warning("[CHUNK_REPROCESS_CHECK] Could not determine chunk status for %s - processing all chunks for safety", key)
    except Exception as e:
        logger.error("[CHUNK_REPROCESS_CHECK] Error getting chunk status for %s: %s - FALLING BACK TO PROCESS ALL CHUNKS", key, e)
        chunks_needing_reprocessing = None  # Explicit fallback to process all

    chunks = []
    current_chunk = []
    current_chunk_size = 0
    char_index = 0
    content_index = 0
    min_chunk_size = split_params.get("min_chunk_size", 512)

    total_chunks = 0
    max_chunks = int(os.environ.get("MAX_CHUNKS", "1000"))

    parts = [split_text(sent_tokenize, item) for item in text_content["content"]]
    flattened_list = [item for sublist in parts for item in sublist]

    locations = []
    indexes = []

    chunks_bucket = os.environ["S3_RAG_CHUNKS_BUCKET_NAME"]
    split_increment = 10
    split_count = 0
    index = 0

    total_chunks = 0  # Initialize total_chunks
    # HARD LIMIT: 26,000 chars = ~6,500 tokens (20% safety buffer from 8,192 token API limit)
    max_sentence_chars = 26,000
    
    # Track sentence splitting for reprocessing logic
    has_sentence_splits = False
    first_split_chunk_index = None
    current_sentence_index = 0

    for content_part in flattened_list:
        sentence = content_part["content"]
        location = content_part["location"]
        sentence_length = len(sentence)

        # HARD LIMIT: Handle massive sentences inline (rare edge case)
        if sentence_length > max_sentence_chars:
            logger.warning(
                "HARD_CHUNK_LIMIT: Massive sentence detected (%d chars) for %s - splitting at word boundaries to prevent token overflow", 
                sentence_length, key
            )
            
            # REPROCESSING DETECTION: Track that we're splitting sentences
            if not has_sentence_splits:
                has_sentence_splits = True
                first_split_chunk_index = total_chunks
                logger.info("[REPROCESS_DETECTION] First sentence split detected at chunk index %d - will invalidate chunks from this point forward", first_split_chunk_index)
            # Force current chunk completion if it exists
            if current_chunk:
                chunk_text = " ".join(current_chunk)
                chunks.append({
                    "content": chunk_text,
                    "locations": locations,
                    "indexes": indexes,
                    "char_index": char_index,
                })
                # Update char_index for completed chunk
                char_index += len(chunk_text) + 1
                current_chunk = []
                current_chunk_size = 0
                locations = []
                indexes = []
                total_chunks += 1
                
                # Check if we need to save this batch
                if len(chunks) == split_increment:
                    split_count += 1
                    save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing)
                    chunks = []
            
            # Split massive sentence at word boundaries (NO DATA LOSS)
            words = sentence.split()
            current_split = ""
            split_count_for_sentence = 0
            
            for word in words:
                test_length = len(current_split + " " + word if current_split else word)
                if test_length > max_sentence_chars and current_split:
                    # Create chunk for current split
                    chunks.append({
                        "content": current_split.strip(),
                        "locations": [location],
                        "indexes": [index],  # All splits share the same original sentence index
                        "char_index": char_index,
                    })
                    char_index += len(current_split.strip()) + 1
                    total_chunks += 1
                    split_count_for_sentence += 1
                    
                    # Check batch save
                    if len(chunks) == split_increment:
                        split_count += 1
                        save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing)
                        chunks = []
                    
                    current_split = word
                else:
                    current_split = current_split + " " + word if current_split else word
            
            # Handle final split
            if current_split:
                chunks.append({
                    "content": current_split.strip(),
                    "locations": [location],
                    "indexes": [index],  # All splits share the same original sentence index
                    "char_index": char_index,
                })
                char_index += len(current_split.strip()) + 1
                total_chunks += 1
                split_count_for_sentence += 1
                
                # Check batch save
                if len(chunks) == split_increment:
                    split_count += 1
                    save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing)
                    chunks = []
            
            logger.info("HARD_CHUNK_LIMIT: Split massive sentence into %d chunks, all sharing index %d", split_count_for_sentence, index)
            
            # Update index to reflect processed sentence (only increment by 1 since we processed 1 sentence)
            index += 1
            current_sentence_index += 1
            continue  # Skip normal chunking logic for this massive sentence

        if total_chunks >= max_chunks:
            logger.warning("Reached maximum chunks %d for %s", max_chunks, key)
            break

        # Check if adding this sentence would exceed the chunk size.
        if (
            current_chunk
            and (current_chunk_size + sentence_length + 1) >= min_chunk_size
        ):
            # Join the current chunk with space and create the chunk object.
            chunk_text = " ".join(current_chunk)

            chunks.append(
                {
                    "content": chunk_text,
                    "locations": locations,
                    "indexes": indexes,
                    "char_index": char_index,
                }
            )

            # Reset for the next chunk
            locations = []
            indexes = []
            char_index += (
                len(chunk_text) + 1
            )  # Include the space that joins with the next chunk.
            current_chunk = [sentence]  # Start the new chunk with the current sentence.
            current_chunk_size = sentence_length

            total_chunks += 1  # Increment the count after forming a chunk

            if len(chunks) == split_increment:
                split_count += 1
                save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing)
                chunks = []

        else:
            locations.append(location)
            indexes.append(index)
            index += 1
            current_sentence_index += 1
            if current_chunk:
                current_chunk.append(sentence)
                current_chunk_size += sentence_length + 1
            else:
                current_chunk = [sentence]
                current_chunk_size = sentence_length

    # If there's remaining text in the current chunk, add it as the last chunk.
    if current_chunk:
        chunk_text = " ".join(current_chunk)
        chunks.append(
            {
                "content": chunk_text,
                "locations": locations,
                "indexes": indexes,
                "char_index": char_index,
            }
        )
        total_chunks += 1  # Increment the count for the last chunk

    # SMART REPROCESSING: Override reprocessing logic when sentence splits detected
    if has_sentence_splits and chunks_needing_reprocessing is not None:
        # Calculate all chunk indexes that need reprocessing due to index misalignment
        chunks_from_split_onward = list(range(first_split_chunk_index, total_chunks))
        
        if len(chunks_needing_reprocessing) > 0:
            # Combine originally failed chunks with chunks after first split
            original_failed = [chunk for chunk in chunks_needing_reprocessing if chunk < first_split_chunk_index]
            combined_reprocess_chunks = original_failed + chunks_from_split_onward
            logger.info(
                "[SMART_REPROCESS] Sentence splits detected! Original failed chunks: %s, Split invalidated chunks: %s, Combined reprocessing: %s",
                sorted(chunks_needing_reprocessing), 
                chunks_from_split_onward,
                sorted(combined_reprocess_chunks)
            )
        else:
            # Only chunks from split point need reprocessing
            combined_reprocess_chunks = chunks_from_split_onward
            logger.info(
                "[SMART_REPROCESS] Sentence splits detected! No original failures, but invalidating chunks from split point: %s",
                chunks_from_split_onward
            )
        
        chunks_needing_reprocessing = combined_reprocess_chunks
        
        # Calculate computation savings
        chunks_saved = first_split_chunk_index
        chunks_reprocessed = len(chunks_from_split_onward)
        logger.info(
            "[COMPUTATION_SAVINGS] Preserving %d chunks before split, reprocessing %d chunks after split",
            chunks_saved, chunks_reprocessed
        )
    elif has_sentence_splits:
        # Force reprocess all if we can't determine previous state
        logger.warning(
            "[SMART_REPROCESS] Sentence splits detected but no reprocessing data available - forcing full reprocess for safety"
        )
        chunks_needing_reprocessing = None  # Forces full reprocess

    if chunks:  # If there are unfinished chunks, save them
        split_count += 1
        save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing)

    logger.debug("In Chunk Content Function")
    logger.debug(
        "Split Count: %d, Split Increment: %d, Total Chunks: %d",
        split_count, split_increment, total_chunks
    )
    return split_count


def chunk_s3_file_content(bucket, key, object_key=None, force_reprocess=False):
    try:
        # Download the file from S3
        logger.info("Fetching text from %s/%s", bucket, key)
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        data = s3_object["Body"].read()
        logger.info("Fetched text from %s/%s", bucket, key)

        file_content = json.loads(data)
        logger.debug("Loaded json from %s/%s", bucket, key)

        # Extract text from the file in S3
        chunks = chunk_content(key, file_content, {}, object_key, force_reprocess)
        logger.info("Chunk S3 File Content Function: Chunked content for %s into %s chunks", key, chunks)

        return chunks

    except Exception as e:
        logger.error("Error getting object %s from bucket %s: %s", key, bucket, str(e), exc_info=True)
        log_critical_error(
            function_name="chunk_s3_file_content",
            error_type="ChunkS3FileContentFailure",
            error_message=f"Failed to chunk S3 file content: {str(e)}",
            current_user='system',
            severity=SEVERITY_HIGH,
            stack_trace=traceback.format_exc(),
            context={
                "document_key": key,
                "bucket": bucket,
                "object_key": object_key if object_key else 'N/A',
                "force_reprocess": force_reprocess
            }
        )
        return None


def scan_directory_and_save_text(directory_path):
    # Iterate over all files in the given directory
    for filename in os.listdir(directory_path):
        # Skip directories, only process files
        if not os.path.isfile(os.path.join(directory_path, filename)):
            continue

        logger.debug("Processing file: %s", filename)
        # Read the file content
        with open(os.path.join(directory_path, filename), "rb") as f:
            file_content = f.read()

        # Use the previously defined extract_text_from_file function to get chunks
        # Notice that this function might need to be adapted depending on the handlers implementations
        document_structure = asyncio.run(extract_text_from_file(filename, file_content))
        data = {
            "name": filename,
            "content": document_structure,
        }

        # Save the chunks to a file
        with open(os.path.join(directory_path, filename + ".content.json"), "w") as f:
            json.dump(data, f, indent=2)


def extract_text_from_s3_file(bucket, key, file_extension):
    try:
        # Download the file from S3
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        file_content = s3_object["Body"].read()

        return asyncio.run(extract_text_from_file(file_extension, file_content))

    except Exception as e:
        logger.error("Error getting object %s from bucket %s: %s", key, bucket, str(e))
        return None


def get_file_from_s3(bucket, key):
    try:
        # Download the file from S3
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        file_content = s3_object["Body"].read()

        return file_content

    except Exception as e:
        logger.error("Error getting object %s from bucket %s: %s", key, bucket, str(e))
        return None


# DEPRECATED: queue_document_for_rag
# Deprecated: queue_document_for_rag_chunking


def update_object_permissions(current_user, data):
    dynamodb = boto3.resource("dynamodb")
    table_name = os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]

    try:
        data_sources = data["dataSources"]
        email_list = data["emailList"]
        provided_permission_level = data[
            "permissionLevel"
        ]  # Permission level provided for other users
        policy = data["policy"]  # No need to use get() since policy is always present
        principal_type = data.get("principalType")
        object_type = data.get("objectType")

        logger.info(
            "Updating permission on %s for %s with %s and %s", data_sources, email_list, provided_permission_level, policy
        )

        # Get the DynamoDB table
        table = dynamodb.Table(table_name)

        for object_id in data_sources:
            # Check if any permissions already exist for the object_id
            query_response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("object_id").eq(
                    object_id
                )
            )
            items = query_response.get("Items")

            # If there are no permissions, create the initial item with the current_user as the owner
            if not items:
                table.put_item(
                    Item={
                        "object_id": object_id,
                        "principal_id": current_user,
                        "principal_type": principal_type,
                        "object_type": object_type,
                        "permission_level": "write",  # The current_user becomes the owner
                        "policy": policy,
                    }
                )
                logger.info(
                    "Created initial item for %s with %s as owner", object_id, current_user
                )

            else:
                # If current_user is the owner or has write permission, proceed with updates
                for principal_id in email_list:
                    # Create or update the permission level for each principal_id
                    principal_key = {
                        "object_id": object_id,
                        "principal_id": principal_id,
                    }
                    # Use the provided permission level for other users
                    update_expression = "SET principal_type = :principal_type, object_type = :object_type, permission_level = :permission_level, policy = :policy"
                    expression_attribute_values = {
                        ":principal_type": principal_type,
                        ":object_type": object_type,
                        ":permission_level": provided_permission_level,  # Use the provided permission level
                        ":policy": policy,
                    }
                    table.update_item(
                        Key=principal_key,
                        UpdateExpression=update_expression,
                        ExpressionAttributeValues=expression_attribute_values,
                    )
                    logger.info(
                        "Updated item for %s with %s to %s", object_id, principal_id, provided_permission_level
                    )

    except Exception as e:
        logger.error("Failed to update permissions: %s", str(e))
        return False

    logger.info(
        "Updated permissions for %s for %s with %s and %s", data_sources, email_list, provided_permission_level, policy
    )
    return True



@required_env_vars({
    "ADDITIONAL_CHARGES_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@track_execution(operation_name="process_document_for_rag", account="system")
def process_document_for_rag(event, context):
    logger.info("Received event: %s", event)
    s3 = boto3.client("s3")

    dynamodb = boto3.resource("dynamodb")
    files_table = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"])
    hash_files_table = dynamodb.Table(os.environ["HASH_FILES_DYNAMO_TABLE"])

    for record in event["Records"]:
        try:
            logger.info("Processing message: %s", record)
            s3_event = json.loads(record["body"])
            logger.debug("Message body: %s", s3_event)
            s3_record = s3_event["Records"][0] if "Records" in s3_event else s3_event
            s3_info = s3_record["s3"]

            # Check if this is a force reprocessing request
            force_reprocess = s3_record.get("force_reprocess", False)

            # Get the bucket and object key from the event
            logger.info("Getting text from %s", s3_info['object']['key'])
            bucket = s3_info["bucket"]["name"]
            key = s3_info["object"]["key"]
            key = urllib.parse.unquote(key)

            logger.info("Bucket / Key %s / %s", bucket, key)

            user = None
            account_data = None
            try:
                response = s3.head_object(Bucket=bucket, Key=key)
                logger.debug("Response Metadata: %s", response['Metadata'])
                rag_enabled = (
                    True
                    if force_reprocess
                    else response["Metadata"].get("rag_enabled", "false") == "true"
                )
               
                logger.info("Retrieve rag details from parameter store")
                rag_details = get_rag_secrets_for_document(key)
                if rag_details['success']:
                    account_data = rag_details['data']
                    user = account_data['user']
                else:
                    logger.error("Failed to retrieve RAG details from parameter store")

            except Exception as e:
                logger.error("Error fetching metadata for %s: %s", key, str(e))

            response = files_table.get_item(Key={"id": key})

            # The rest is the same as above
            item = response.get("Item", None)

            if item:
                logger.info("Found file entry for %s: %s", key, item)
            else:
                logger.warning("File entry not found for %s", key)

            total_items = 0
            text = None
            location_properties = []

            creation_time = datetime.now().isoformat()

            if item:
                try:
                    type = item["type"]
                    tags = item["tags"]
                    props = item["data"]
                    knowledge_base = item["knowledgeBase"]
                    name = item["name"]

                    logger.info(
                        "Processing document chunks for %s of type %s with tags %s and data %s and knowledge base %s", name, type, tags, props, knowledge_base
                    )

                    file_extension = get_file_extension(name, type)

                    logger.debug(
                        "Using file extension of %s based on mime type priority (if present and guessable)", file_extension
                    )

                    # Extract text from the file in S3
                    file_content = get_file_from_s3(bucket, key)
                    dochash = hashlib.sha256(file_content).hexdigest()

                    dochash_resposne = hash_files_table.get_item(Key={"id": dochash})

                    # Create a pointer from the user's key to the actual
                    # location of the document by hash
                    [file_text_content_bucket_name, text_content_key] = (
                        get_text_hash_content_location(bucket, dochash)
                    )

                    user_key_to_hash_entry = {
                        "id": key,
                        "hash": dochash,
                        "textLocationBucket": file_text_content_bucket_name,
                        "textLocationKey": text_content_key,
                        "createdAt": creation_time,
                    }
                    hash_files_table.put_item(Item=user_key_to_hash_entry)

                    if not user:
                        user = key.split("/")[0]
                    permissions_update = {
                        "dataSources": [text_content_key],
                        "emailList": [user],
                        "permissionLevel": "write",
                        "policy": "",
                        "principalType": "user",
                        "objectType": "fileEmbedding",
                    }
                    update_object_permissions(user, permissions_update)

                    text = None
                    # Check if already processed AND not a force reprocess request AND embedding was successful
                    if dochash_resposne.get("Item") is not None and not force_reprocess and is_embedding_successful(key):
                        logger.info("✅ Document %s already processed and embedding completed successfully - skipping", key)
                        text_bucket = dochash_resposne.get("Item").get(
                            "textLocationBucket"
                        )
                        text_key = dochash_resposne.get("Item").get("textLocationKey")
                        logger.info("Getting existing text from %s/%s", text_bucket, text_key)
                        text = json.loads(get_file_from_s3(text_bucket, text_key))
                        logger.info("Got existing text from %s/%s", text_bucket, text_key)
                        total_tokens = text.get("totalTokens", 0)
                        total_items = text.get("totalItems", 0)
                        location_properties = text.get("locationProperties", [])
                        tags = text.get("tags", [])
                        props = text.get("props", [])
                    else:
                        # Process document if: not processed before, OR force reprocess, OR embedding incomplete/failed
                        if force_reprocess:
                            logger.info("🔄 Force reprocessing document %s", key)
                        elif dochash_resposne.get("Item") is not None:
                            logger.info("⚠️ Document %s processed but embedding incomplete/failed - reprocessing", key)
                        else:
                            logger.info("🆕 New document %s - processing", key)
                        try:
                            text = asyncio.run(
                                extract_text_from_file(file_extension, file_content, user, account_data)
                            )
                            logger.info("Extracted text from %s", key)
                            total_tokens = sum(d.get("tokens", 0) for d in text)
                            total_items = len(text)
                        except Exception as extract_error:
                            # CRITICAL: Text extraction failure = data loss, document unreadable
                            log_critical_error(
                                function_name="process_document_for_rag",
                                error_type="TextExtractionFailure",
                                error_message=f"Failed to extract text from document: {str(extract_error)}",
                                current_user=user,
                                severity=SEVERITY_HIGH,
                                stack_trace=traceback.format_exc(),
                                context={
                                    "document_key": key,
                                    "file_extension": file_extension,
                                    "bucket": bucket,
                                    "force_reprocess": force_reprocess
                                }
                            )
                            raise

                        if total_items > 0:
                            location_properties = list(
                                text[0].get("location", {}).keys()
                            )

                        text = {
                            "name": name,
                            "totalItems": total_items,
                            "locationProperties": location_properties,
                            "content": text,
                            "createdAt": creation_time,
                            "totalTokens": total_tokens,
                            "tags": tags,
                            "props": props,
                        }
                        if text is not None:
                            logger.info(
                                "Uploading text to %s/%s", file_text_content_bucket_name, text_content_key
                            )
                            # Put the text into a file and upload to S3 bucket
                            # use a random uuid for the key
                            s3.put_object(
                                Bucket=file_text_content_bucket_name,
                                Key=text_content_key,
                                Body=json.dumps(text),
                            )
                            logger.info(
                                "Uploaded text to %s/%s", file_text_content_bucket_name, text_content_key
                            )

                            hash_file_data = {
                                "id": dochash,
                                "originalCreator": user,
                                "textLocationBucket": file_text_content_bucket_name,
                                "textLocationKey": text_content_key,
                                "createdAt": creation_time,
                            }
                            hash_files_table.put_item(Item=hash_file_data)
                            logger.info("Updated hash files entry for %s", dochash)

                            files_table.update_item(
                                Key={"id": key},
                                UpdateExpression="SET totalTokens = :tokenVal, totalItems = :itemVal, dochash = :hashVal",
                                ExpressionAttributeValues={
                                    ":tokenVal": total_tokens,
                                    ":itemVal": total_items,
                                    ":hashVal": dochash,
                                },
                            )
                            logger.info(
                                "Uploaded user files entry with token and item count for %s: %d / %d", key, total_tokens, total_items
                            )

                            logger.info("RAG enabled: %s", rag_enabled)

                            if not rag_enabled:
                                logger.info( "RAG chunking is disabled, skipping chunk queue...")
                            else:
                                chunk_queue_url = os.environ["RAG_CHUNK_DOCUMENT_QUEUE_URL"]
                                logger.info("Sending message to chunking queue")
                                try:
                                    record = {
                                        "force_reprocess": force_reprocess,
                                        "s3": {
                                            "bucket": { "name": file_text_content_bucket_name },
                                            "object": { "key": text_content_key },
                                            "metadata": { "object_key": key }
                                        }
                                    }
                                    message_body = json.dumps(record)
                                    sqs.send_message(
                                        QueueUrl=chunk_queue_url,
                                        MessageBody=message_body,
                                    )
                                    logger.info("Message sent to queue: %s", message_body)
                                except Exception as e:
                                    logger.error(
                                        "Error sending message to chunking queue: %s", str(e)
                                    )

                except Exception as e:
                    logger.error("Error processing document: %s", str(e))
                    log_critical_error(
                        function_name="process_document_for_rag",
                        error_type="DocumentProcessingFailure",
                        error_message=f"Failed to process document: {str(e)}",
                        current_user=user if 'user' in locals() else 'unknown',
                        severity=SEVERITY_HIGH,
                        stack_trace=traceback.format_exc(),
                        context={
                            "document_key": key if 'key' in locals() else 'unknown',
                            "bucket": bucket if 'bucket' in locals() else 'unknown',
                            "force_reprocess": force_reprocess if 'force_reprocess' in locals() else False
                        }
                    )

            # If text extraction was successful, delete the message from the queue
            if text is not None:

                [_, text_content_key] = get_text_content_location(bucket, key)

                text_metadata = {
                    "name": name,
                    "totalItems": total_items,
                    "locationProperties": location_properties,
                    "contentKey": text_content_key,
                    "createdAt": creation_time,
                    "totalTokens": total_tokens,
                    "tags": tags,
                    "props": props,
                }

                [file_text_metadata_bucket_name, text_metadata_key] = (
                    get_text_metadata_location(bucket, key)
                )

                s3.put_object(
                    Bucket=file_text_metadata_bucket_name,
                    Key=text_metadata_key,
                    Body=json.dumps(text_metadata),
                )
                logger.info(
                    "Uploaded metadata to %s/%s", file_text_metadata_bucket_name, text_metadata_key
                )

        except Exception as e:
            # Check if this is a critical RAG secrets error that should terminate the Lambda
            error_message = str(e)
            if ("RAG secrets" in error_message or 
                "store_secret_parameter" in error_message or 
                "get_secret_parameter" in error_message or
                "delete_secret_parameter" in error_message or
                "Critical error storing RAG secrets" in error_message or
                "Critical error retrieving RAG secrets" in error_message or
                "Critical error deleting RAG secrets" in error_message):
                logger.error(
                    f"[CRITICAL_RAG_ERROR] 🔥 RAG secrets error detected - TERMINATING LAMBDA IMMEDIATELY: {error_message}"
                )
                # Multiple termination strategies to ensure Lambda stops
                logger.error("[LAMBDA_TERMINATION] 💀 Forcing Lambda termination due to RAG secrets failure")
                
                # Strategy 1: Re-raise the exception
                raise Exception(f"LAMBDA_TERMINATION_REQUIRED: {error_message}")
                
                # Strategy 2: If somehow re-raise fails, force exit (this line should never execute)
                import sys
                sys.exit(1)
            
            logger.error("Error processing SQS message: %s", str(e))
            log_critical_error(
                function_name="process_document_for_rag_sqs",
                error_type="RAGSQSMessageProcessingFailure",
                error_message=f"Failed to process RAG SQS message: {str(e)}",
                current_user='system',
                severity=SEVERITY_HIGH,
                stack_trace=traceback.format_exc(),
                context={
                    "record": str(record)[:500] if 'record' in locals() else 'unknown'
                }
            )

    return {"statusCode": 200, "body": json.dumps("SQS Text Extraction Complete!")}


def update_embedding_status(original_creator, object_id, total_chunks, status):
    try:
        progress_table = os.environ["EMBEDDING_PROGRESS_TABLE"]
        logger.info(
            "Updating chunk count status for embedding %s/%s Total Chunks: %d %s", progress_table, object_id, total_chunks, status
        )

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(progress_table)

        # Check if there's existing progress data (for selective reprocessing)
        try:
            response = table.get_item(Key={"object_id": object_id})
            existing_item = response.get("Item")
            
            if existing_item and existing_item.get("data", {}).get("childChunks"):
                # Preserve existing chunk statuses that are completed
                existing_chunks = existing_item["data"]["childChunks"]
                new_chunks = {}
                
                for i in range(total_chunks):
                    chunk_id = str(i + 1)
                    if chunk_id in existing_chunks and existing_chunks[chunk_id].get("status") == "completed":
                        # Preserve completed chunks
                        new_chunks[chunk_id] = existing_chunks[chunk_id]
                        logger.debug("Preserving completed status for chunk %s", chunk_id)
                    else:
                        # Reset non-completed chunks to starting
                        new_chunks[chunk_id] = {"status": status}
                
                child_chunks = new_chunks
                logger.info("Preserved %d completed chunks", len([c for c in new_chunks.values() if c.get('status') == 'completed']))
            else:
                # No existing data, create fresh
                child_chunks = {str(i + 1): {"status": status} for i in range(total_chunks)}
        except Exception as e:
            logger.error("Error checking existing progress: %s", e)
            # Fallback to creating fresh
            child_chunks = {str(i + 1): {"status": status} for i in range(total_chunks)}

        table.put_item(
            Item={
                "object_id": object_id,
                "parentChunkStatus": status,
                "timestamp": datetime.now().isoformat(),
                "originalCreator": original_creator,
                "terminated": False,
                "totalChunks": total_chunks,
                "data": {
                    "childChunks": child_chunks
                },
            }
        )
        logger.info(
            "Updated %d nested childChunks for %s in Embeddings Progress Table", total_chunks, object_id
        )

    except Exception as e:
        logger.error("Failed to create or update item in DynamoDB table.")
        logger.error("%s", e)


def get_original_creator(key):
    original_creator = "unknown"
    try:
        dynamodb = boto3.resource("dynamodb")
        # Get the original creator of the file
        # by extracting the hash from the key
        # and looking up the original creator in the hash_files table
        hash = key
        parts = key.split("/")
        if len(parts) == 2:
            filename = parts[1]
            hash = filename.split(".")[0]
        hash_files = dynamodb.Table(os.environ["HASH_FILES_DYNAMO_TABLE"])
        response = hash_files.get_item(Key={"id": hash})
        item = response.get("Item", None)
        if item:
            logger.info("Found hash entry for %s: %s", key, item)
            original_creator = item.get("originalCreator", "unknown")
            logger.info("Original uploader: %s", original_creator)
        else:
            logger.warning("Hash entry not found for %s", key)
    except Exception as e:
        logger.error(
            "Error getting hash entry for %s to determine original_creator: %s", key, str(e)
        )

    return original_creator


def get_embedding_progress_by_global_id(global_id):
    """
    Get embedding progress data for a document using global ID directly.
    
    Args:
        global_id: The global hash ID (already translated)
        
    Returns:
        dict: Progress data with keys: item, parent_status, terminated, child_chunks
              Returns None if any error occurs
    """
    try:
        # Check embedding progress table
        progress_table = os.environ.get("EMBEDDING_PROGRESS_TABLE")
        if not progress_table:
            logger.warning("[EMBEDDING_PROGRESS] EMBEDDING_PROGRESS_TABLE not configured")
            return None
            
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(progress_table)
        
        response = table.get_item(Key={"object_id": global_id})
        item = response.get("Item")
        
        if not item:
            logger.info("[EMBEDDING_PROGRESS] No embedding progress found for %s", global_id)
            return {"item": None, "parent_status": None, "terminated": False, "child_chunks": {}}
        
        parent_status = item.get("parentChunkStatus")
        terminated = item.get("terminated", False)
        child_chunks = item.get("data", {}).get("childChunks", {})
        
        return {
            "item": item, 
            "parent_status": parent_status, 
            "terminated": terminated,
            "child_chunks": child_chunks
        }
            
    except Exception as e:
        logger.error("[EMBEDDING_PROGRESS] Error getting progress data for %s: %s", global_id, e)
        return None


def is_embedding_successful(document_key):
    """
    Check if embedding process was successful for a document.
    
    Args:
        document_key: The document key to check embedding status for (needs translation)
        
    Returns:
        bool: True if embedding completed successfully, False otherwise
    """
    try:
        from pycommon.api.data_sources import translate_user_data_sources_to_hash_data_sources
        
        # Translate document key to global hash
        translated_sources = translate_user_data_sources_to_hash_data_sources([{"id": document_key, "type": "document"}])
        if not translated_sources or not translated_sources[0].get("id"):
            logger.warning("[EMBEDDING_CHECK] Could not get global hash for %s", document_key)
            return False
            
        global_id = translated_sources[0]["id"]
        
        # Use shared function
        progress_data = get_embedding_progress_by_global_id(global_id)
        
        if not progress_data:
            logger.info("[EMBEDDING_CHECK] Could not get progress data for %s", document_key)
            return False
        
        # Check if terminated
        if progress_data["terminated"]:
            logger.warning("[EMBEDDING_CHECK] Embedding process terminated for %s", document_key)
            return False
        
        # Check parent chunk status
        if progress_data["parent_status"] == "completed":
            logger.info("[EMBEDDING_CHECK] ✅ Embedding completed successfully for %s", document_key)
            return True
        else:
            logger.info("[EMBEDDING_CHECK] Embedding not completed for %s (status: %s)", document_key, progress_data["parent_status"])
            return False
            
    except Exception as e:
        logger.error("[EMBEDDING_CHECK] Error checking embedding status for %s: %s", document_key, e)
        # On error, return False to trigger reprocessing for safety
        return False


def get_chunks_needing_reprocessing(global_id):
    """
    Get list of chunk numbers that need reprocessing for a document using global ID.
    
    Args:
        global_id: The global hash ID (already available in chunking context)
        
    Returns:
        list: List of chunk numbers (integers) that need reprocessing
              Returns empty list if document has no progress data (all chunks need processing)
              Returns None if error occurs
    """
    try:
        progress_data = get_embedding_progress_by_global_id(global_id)
        
        if not progress_data:
            logger.warning("[CHUNK_REPROCESS_CHECK] Could not get progress data for %s", global_id)
            return None
        
        # If no progress data exists, all chunks need processing
        if not progress_data["item"]:
            logger.info("[CHUNK_REPROCESS_CHECK] No progress data - all chunks need processing for %s", global_id)
            return []  # Empty list means process all chunks
        
        # If terminated, don't process any chunks
        if progress_data["terminated"]:
            logger.info("[CHUNK_REPROCESS_CHECK] Document terminated - no chunks need processing for %s", global_id)
            return []
        
        child_chunks = progress_data["child_chunks"]
        
        if not child_chunks:
            logger.info("[CHUNK_REPROCESS_CHECK] No child chunks data - all chunks need processing for %s", global_id)
            return []  # Empty list means process all chunks
        
        # Find chunks that need reprocessing (not completed)
        chunks_needing_reprocessing = []
        completed_chunks = []
        
        for chunk_id, chunk_info in child_chunks.items():
            chunk_status = chunk_info.get("status", "starting")
            chunk_num = int(chunk_id)
            
            if chunk_status == "completed":
                completed_chunks.append(chunk_num)
            else:
                chunks_needing_reprocessing.append(chunk_num)
        
        logger.info("[CHUNK_REPROCESS_CHECK] For document %s:", global_id)
        logger.info("[CHUNK_REPROCESS_CHECK]   ✅ Completed chunks: %s", sorted(completed_chunks))
        logger.info("[CHUNK_REPROCESS_CHECK]   🔄 Need reprocessing: %s", sorted(chunks_needing_reprocessing))
        
        return chunks_needing_reprocessing
        
    except Exception as e:
        logger.error("[CHUNK_REPROCESS_CHECK] Error checking chunks for %s: %s", global_id, e)
        return None


def reset_chunk_to_starting_status(global_id, chunk_number):
    """
    Reset a specific chunk to 'starting' status in DynamoDB before reprocessing.
    This ensures the embedding service can process the chunk without terminal state blocking.
    
    Args:
        global_id: The global hash ID (document identifier)
        chunk_number: The chunk number to reset
    """
    try:
        progress_table = os.environ.get("EMBEDDING_PROGRESS_TABLE")
        if not progress_table:
            logger.error("[CHUNK_RESET] EMBEDDING_PROGRESS_TABLE not configured")
            return False
            
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(progress_table)
        
        logger.info("[CHUNK_RESET] 🔄 Resetting chunk %d to 'starting' status for %s", chunk_number, global_id)
        
        # Update the specific child chunk to starting status
        import datetime
        current_time = datetime.datetime.now().isoformat()
        
        update_expression = """
            SET #data.#childChunks.#chunkId.#status = :starting_status,
                #data.#childChunks.#chunkId.#lastUpdated = :timestamp,
                #data.#childChunks.#chunkId.#version = if_not_exists(#data.#childChunks.#chunkId.#version, :zero) + :one,
                #data.#childChunks.#chunkId.#resetBy = :reset_source
        """
        
        expression_attribute_names = {
            "#data": "data",
            "#childChunks": "childChunks",
            "#chunkId": str(chunk_number),
            "#status": "status", 
            "#lastUpdated": "lastUpdated",
            "#version": "version",
            "#resetBy": "resetBy"
        }
        
        expression_attribute_values = {
            ":starting_status": "starting",
            ":timestamp": current_time,
            ":zero": 0,
            ":one": 1,
            ":reset_source": "RAG_CHUNKING_REPROCESS"
        }
        
        # Use condition to ensure we don't overwrite completed chunks accidentally
        condition_expression = (
            "attribute_exists(#data.#childChunks.#chunkId) AND "
            "#data.#childChunks.#chunkId.#status <> :completed"
        )
        expression_attribute_values[":completed"] = "completed"
        
        result = table.update_item(
            Key={"object_id": global_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ConditionExpression=condition_expression,
            ReturnValues="UPDATED_NEW"
        )
        
        logger.info("[CHUNK_RESET] ✅ Successfully reset chunk %d to 'starting' status", chunk_number)
        return True
        
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info("[CHUNK_RESET] Chunk %d is already completed - not resetting", chunk_number)
        return True  # Consider this success since chunk shouldn't be reprocessed anyway
        
    except Exception as e:
        logger.error("[CHUNK_RESET] ❌ Failed to reset chunk %d status: %s", chunk_number, e)
        return False



@required_env_vars({
    "ADDITIONAL_CHARGES_TABLE": [DynamoDBOperation.PUT_ITEM],
})
@track_execution(operation_name="chunk_document_for_rag", account="system")
def chunk_document_for_rag(event, context):
    logger.info("Received event: %s", event)

    for record in event["Records"]:
        try:
            logger.info("Processing message: %s", record)
            # Assuming the message body is a JSON string, parse it
            message_data = json.loads(record["body"])
            logger.debug("Message body: %s", message_data)
            
            # Check if this is a force reprocessing request
            force_reprocess = message_data.get("force_reprocess", False)
            logger.info("Force reprocess flag: %s", force_reprocess)
            
            s3_info = message_data["s3"]

            # Extract object_key from metadata if present
            object_key = s3_info.get("metadata", {}).get("object_key")
            logger.info("Object key from metadata: %s", object_key)

            # Get the bucket and object key from the event
            logger.info("Getting raw text from %s", s3_info['object']['key'])
            bucket = s3_info["bucket"]["name"]
            key = s3_info["object"]["key"]
            key = urllib.parse.unquote(key)

            logger.info("Bucket / Key %s / %s", bucket, key)

            # Figure out who uploaded this file, even though it's a shared
            # global entry
            original_creator = get_original_creator(key)

            # Use original chunking method - no selective processing for now to avoid complexity
            chunks_created = chunk_s3_file_content(bucket, key, object_key, force_reprocess)
            
            if chunks_created is None:
                logger.error("❌ Chunking failed for %s - chunk_s3_file_content returned None", key)
                log_critical_error(
                    function_name="chunk_document_for_rag",
                    error_type="ChunkingFailure",
                    error_message="chunk_s3_file_content returned None - document chunking failed",
                    current_user='system',
                    severity=SEVERITY_HIGH,
                    stack_trace='',
                    context={
                        "document_key": key,
                        "bucket": bucket,
                        "object_key": object_key if object_key else 'N/A',
                        "force_reprocess": force_reprocess,
                        "failure_type": "chunk_s3_file_content_returned_none"
                    }
                )
                continue
            
            logger.info("[CHUNKING] Created %d chunk files for %s", chunks_created, key)
            
            if chunks_created == 0:
                logger.warning("No chunks were created for %s", key)
                continue

            # Use chunk FILES count, not individual chunks
            # The embedding service processes chunk files, not individual chunks
            update_embedding_status(original_creator, key, chunks_created, "starting")

        except Exception as e:
            # Check if this is a critical RAG secrets error that should terminate the Lambda
            error_message = str(e)
            if ("RAG secrets" in error_message or 
                "store_secret_parameter" in error_message or 
                "get_secret_parameter" in error_message or
                "delete_secret_parameter" in error_message or
                "Critical error storing RAG secrets" in error_message or
                "Critical error retrieving RAG secrets" in error_message or
                "Critical error deleting RAG secrets" in error_message):
                logger.error(
                    f"[CRITICAL_RAG_ERROR] 🔥 RAG secrets error detected - TERMINATING LAMBDA IMMEDIATELY: {error_message}"
                )
                # Multiple termination strategies to ensure Lambda stops
                logger.error("[LAMBDA_TERMINATION] 💀 Forcing Lambda termination due to RAG secrets failure")
                
                # Strategy 1: Re-raise the exception
                raise Exception(f"LAMBDA_TERMINATION_REQUIRED: {error_message}")
                
                # Strategy 2: If somehow re-raise fails, force exit (this line should never execute)
                import sys
                sys.exit(1)
            
            logger.error("Error processing SQS message: %s", str(e))
            log_critical_error(
                function_name="chunk_document_for_rag_sqs",
                error_type="ChunkingSQSMessageProcessingFailure",
                error_message=f"Failed to process chunking SQS message: {str(e)}",
                current_user='system',
                severity=SEVERITY_HIGH,
                stack_trace=traceback.format_exc(),
                context={
                    "record": str(record)[:500] if 'record' in locals() else 'unknown',
                    "bucket": bucket if 'bucket' in locals() else 'unknown',
                    "key": key if 'key' in locals() else 'unknown'
                }
            )

    return {"statusCode": 200, "body": json.dumps("SQS Text Extraction Complete!")}


############ Local Test Extraction ############


def test_extract_text_locally(file_path):
    """
    Test function to extract text from a local file for development/testing purposes.

    Args:
        file_path (str): Path to the local file to process

    Returns:
        list: List of extracted text chunks
    """

    if not os.path.exists(file_path):
        logger.error("Error: File %s does not exist", file_path)
        return []

    try:
        # Read the file content as bytes
        with open(file_path, "rb") as f:
            file_content = f.read()

        # Use the filename as the key
        filename = os.path.basename(file_path)

        # Hardcode user to empty string as requested
        current_user = "your_email"

        logger.info("Testing text extraction for file: %s", filename)
        logger.info("File size: %d bytes", len(file_content))

        # Call the async extract_text_from_file function
        result = asyncio.run(
            extract_text_from_file(filename, file_content, current_user, 
                                   {"account": None,
                                    "rate_limit": None,
                                    "access_token": "amp-",
                                    }
)
        )

        logger.info("Extraction completed. Found %d text chunks.", len(result))

        # Print a summary of the results
        if result:
            total_chars = sum(len(chunk.get("content", "")) for chunk in result)
            logger.info("Total characters extracted: %d", total_chars)
            logger.debug("Chunks: %s", result)

        return result

    except Exception as e:
        logger.error("Error processing file %s: %s", file_path, str(e), exc_info=True)
        return []


if __name__ == "__main__":
    # Test with a local file
    filename = "Test.pdf"  # Change this to your test file name

    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, filename)

    logger.info("Looking for file at: %s", file_path)
    result = test_extract_text_locally(file_path)
    logger.info("Extracted %d chunks", len(result))
