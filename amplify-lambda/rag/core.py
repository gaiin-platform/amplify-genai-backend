# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas


import hashlib
import os
import time
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
from pycommon.api.critical_logging import log_critical_error, SEVERITY_HIGH, SEVERITY_LOW
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


from pycommon.rag.contracts import NOVA_EMBEDDING_MODEL_ID
from pycommon.rag import timing
from rag.handlers.commaseparatedvalues import CSVHandler
from rag.handlers.excel import ExcelHandler
from rag.handlers.pdf import PDFHandler, PAGE_IMAGE_TAG
from rag.handlers.powerpoint import PPTXHandler
from rag.handlers.word import DOCXHandler
from rag.handlers.text import TextHandler
from rag.handlers.markdown import MarkDownHandler
from rag.handlers.markitdown_extractor import MarkItDownExtractor
from rag import xlsx_markdown
from rag.util import (
    get_text_content_location,
    get_text_metadata_location,
    get_text_hash_content_location,
)
from rag.handlers.shared_functions import is_likely_text
from rag.page_artifacts import build_page_artifact, build_page_manifest, page_artifact_key
from rag import csv_markdown, textract_cost, textract_pipeline, limits, pdf_tables, office_ocr, table_chunks, migration as migration_rules
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
        try:
            sent_tokenize("Test sentence.")
            logger.info("✅ NLTK punkt_tab data successfully downloaded and verified")
            _nltk_data_initialized = True
        except LookupError:
            # punkt_tab didn't work, try the old punkt tokenizer (for older NLTK versions)
            logger.warning("punkt_tab downloaded but sent_tokenize still failed, trying old 'punkt' tokenizer...")
            logger.info("Downloading punkt (legacy) to %s", tmp_path)
            download_result_legacy = nltk.download("punkt", download_dir=tmp_path, quiet=False)

            if not download_result_legacy:
                raise RuntimeError("nltk.download('punkt') returned False - download may have failed")

            # Verify the old tokenizer works
            logger.info("Verifying punkt (legacy) download...")
            sent_tokenize("Test sentence.")
            logger.info("✅ NLTK punkt (legacy) data successfully downloaded and verified")
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
    """Deprecated legacy visual extraction; page embeddings use Nova images."""
    logger.warning("Legacy visual extraction is deprecated for RAG ingestion: %s", key)
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


def render_pdf_page_images(file_extension, file_content, bucket, text_content_key):
    """Render PDF page images for global2 Nova page vectors; never fatal.

    Returns {page_number: {"image_location": "s3://...", ...}}. Anything that
    goes wrong (corrupt/encrypted PDF, S3 error, disabled by env) yields {} so
    the document still ingests with text-only vectors.
    """
    if file_extension != ".pdf" or os.environ.get("GLOBAL2_PAGE_IMAGES_ENABLED", "true").lower() != "true":
        return {}
    try:
        prefix = page_artifact_key(text_content_key, 1).rsplit("/", 1)[0]
        rendered = PDFHandler().render_full_page_images(
            file_content,
            bucket=bucket,
            key_prefix=prefix,
            max_pages=int(os.environ.get("GLOBAL2_MAX_PAGE_IMAGES", "200")),
            time_budget_seconds=int(os.environ.get("GLOBAL2_PAGE_IMAGE_TIME_BUDGET_SECONDS", "20")),
        )
        logger.info("Rendered %d page images under s3://%s/%s", len(rendered), bucket, prefix)
        return {item["page_number"]: item for item in rendered}
    except Exception as render_error:
        logger.warning("Page image rendering failed for %s; continuing text-only: %s", text_content_key, render_error)
        return {}


def render_office_picture_images(file_extension, file_content, bucket, text_content_key):
    """PowerPoint: one picture sheet per slide that has real pictures, stored like a rendered PDF page.

    A slide cannot be drawn without a converter, so Nova embeds the slide's own pictures instead (large ones only,
    repeated backgrounds and logos left out). Returns {slide_number: {"image_location": "s3://..."}}; anything that
    goes wrong yields {} and the deck keeps its text-only vectors.
    """
    if file_extension != ".pptx" or os.environ.get("GLOBAL2_PAGE_IMAGES_ENABLED", "true").lower() != "true":
        return {}
    try:
        sheets = office_ocr.slide_picture_sheets(
            file_content,
            max_slides=int(os.environ.get("GLOBAL2_MAX_PAGE_IMAGES", "200")),
            time_budget_seconds=float(os.environ.get("GLOBAL2_PAGE_IMAGE_TIME_BUDGET_SECONDS", "20")),
        )
        prefix = page_artifact_key(text_content_key, 1).rsplit("/", 1)[0]
        rendered = {}
        for slide_number, png in sheets.items():
            image_key = f"{prefix}/{slide_number}.png"
            s3.put_object(Bucket=bucket, Key=image_key, Body=png, ContentType="image/png", Tagging=PAGE_IMAGE_TAG)
            rendered[slide_number] = {"page_number": slide_number, "image_location": f"s3://{bucket}/{image_key}"}
        logger.info("Stored %d slide picture sheets under s3://%s/%s", len(rendered), bucket, prefix)
        return rendered
    except Exception as render_error:
        logger.warning("Slide picture sheets failed for %s; continuing text-only: %s", text_content_key, render_error)
        return {}


def build_global2_page_manifest(text_parts, text_content_key, dochash, file_extension, file_content, bucket):
    """Group extracted parts by page and build the global2 page manifest.

    Returns None when the document has no page locations or isn't global2.
    Renders PDF page images (or PowerPoint slide picture sheets) as a side effect (non-fatal).
    """
    if not str(text_content_key).startswith("global2/"):
        return None
    page_parts = {}
    for part in text_parts:
        page_number = (part.get("location") or {}).get("page_number")
        if not page_number:
            continue
        page_parts.setdefault(int(page_number), []).append(part)
    if not page_parts:
        return None

    rendered_images = (render_pdf_page_images(file_extension, file_content, bucket, text_content_key)
                       or render_office_picture_images(file_extension, file_content, bucket, text_content_key))
    manifest_pages = []
    for page_number, parts_for_page in sorted(page_parts.items()):
        page_text = "\n".join(part.get("content", "") for part in parts_for_page if part.get("content"))
        if not page_text:
            continue
        manifest_pages.append(build_page_artifact(
            text_content_key,
            page_number,
            text=page_text,
            image_location=rendered_images.get(page_number, {}).get("image_location", ""),
            extraction_method="existing-extractor",
            locations=[part.get("location", {}) for part in parts_for_page],
        ))
    if not manifest_pages:
        return None
    return build_page_manifest(
        text_content_key,
        dochash,
        extraction_mode="existing-extractor",
        pages=manifest_pages,
        model_id=NOVA_EMBEDDING_MODEL_ID,
        embedding_dimension=int(os.environ.get("GLOBAL2_EMBEDDING_DIM", "1024")),
        embedding_purpose=os.environ.get("GLOBAL2_EMBEDDING_PURPOSE", "GENERIC_INDEX"),
    )


def global2_manifest_key(text_content_key):
    return f"{text_content_key.removesuffix('.content.json')}.manifest.json"


def persist_global2_page_manifest(text_parts, text_content_key, dochash, file_extension, file_content, bucket):
    """Build and store the page manifest next to the extracted text; never raises."""
    try:
        manifest = build_global2_page_manifest(
            text_parts, text_content_key, dochash, file_extension, file_content, bucket
        )
        if manifest is None:
            return None
        manifest_key = global2_manifest_key(text_content_key)
        s3.put_object(Bucket=bucket, Key=manifest_key, Body=manifest.to_json(), ContentType="application/json")
        logger.info("Persisted global2 page manifest to %s/%s (%d pages)", bucket, manifest_key, manifest.page_count)
        return manifest
    except Exception as manifest_error:
        logger.warning("Skipping global2 page manifest for %s: %s", text_content_key, manifest_error)
        return None


def reject_document(files_table, s3_client, bucket, key, reason):
    """Record why a document was refused and drop the raw object we will never process.

    Over-limit uploads are a policy outcome, not a system failure, so this logs a
    warning (no critical-error alert). The frontend sees no metadata file, gives up
    after its wait, and cleans up the file record itself.
    """
    logger.warning("Rejecting %s: %s", key, reason)
    try:
        files_table.update_item(
            Key={"id": key},
            UpdateExpression="SET ingestStatus = :status, ingestError = :error",
            ExpressionAttributeValues={":status": "rejected", ":error": reason},
        )
    except Exception as error:
        logger.warning("Could not record rejection for %s: %s", key, error)
    try:
        s3_client.delete_object(Bucket=bucket, Key=key)
    except Exception as error:
        logger.warning("Could not delete rejected object %s: %s", key, error)


def refuse_document(files_table, s3_client, bucket, key, reason, migration_target=None, creator=None):
    """Refuse a document. A migration replay only records the refusal against its
    global2 twin; it must never delete the user's file or mark their record."""
    if not migration_target:
        reject_document(files_table, s3_client, bucket, key, reason)
        return
    logger.warning("Migration replay of %s refused: %s", key, reason)
    try:
        table = boto3.resource("dynamodb").Table(os.environ["EMBEDDING_PROGRESS_TABLE"])
        table.put_item(Item=migration_rules.failure_item(migration_target, reason, creator, datetime.now().isoformat()))
    except Exception as error:
        logger.error("Could not record migration refusal for %s: %s", migration_target, error)


def ocr_office_pictures(markdown, key, file_content, prefetch=None, account_data=None):
    """Word / PowerPoint / Excel: text inside large pictures is read with Textract and slotted in where the
    picture was (see rag/office_ocr.py). Never fatal: on any problem the plain markdown is returned."""
    lowered = str(key).lower()
    extension = next((e for e in (".docx", ".pptx", ".xlsx") if lowered.endswith(e)), None)
    if extension is None or not office_ocr.enabled():
        return markdown
    try:
        enriched, report = office_ocr.enrich_markdown(
            markdown, extension, file_content, office_ocr.textract_detect_lines(boto3.client("textract")), prefetch)
    except Exception as error:
        logger.warning("Picture OCR skipped for %s: %s", key, error)
        return markdown
    for line in (office_ocr.describe(report) if report else []):
        logger.info("[OCR-DECISION] %s", line)
    if report and report.billed_pages:
        textract_cost.record_pages(account_data, textract_cost.DETECT, report.billed_pages, f"office-{key}", key)
    return enriched


def can_reuse_processed_document(hash_item, force_reprocess, rag_enabled, embedding_done):
    """True when the stored text of an earlier upload of the same content can be used as it is.

    A RAG-on upload also needs its embeddings finished (``embedding_done`` is only called then). A RAG-off upload never
    creates embeddings, so waiting for them would send an identical file through extraction and OCR again every time.
    """
    if not hash_item or force_reprocess or not hash_item.get("textLocationKey") or not hash_item.get("textLocationBucket"):
        return False
    return True if not rag_enabled else bool(embedding_done())


def reusable_stored_parts(hash_item, text_content_key, force_reprocess, rag_enabled, load=None):
    """The page parts already stored for this exact content when a RAG-off upload is being switched to RAG,
    else None. Extraction (and Textract) already ran for it; only chunking and embedding are still to do.

    Only for global2 text, and never when ``force_reprocess`` is set (the caller passes False for a forced reprocess that
    is only retrying unfinished chunks; any other forced reprocess exists to redo the extraction).
    """
    if not hash_item or force_reprocess or not rag_enabled or not str(text_content_key).startswith("global2/"):
        return None
    if hash_item.get("textLocationKey") != text_content_key or not hash_item.get("textLocationBucket"):
        return None
    try:
        stored = json.loads((load or get_file_from_s3)(hash_item["textLocationBucket"], text_content_key))
        parts = stored.get("content")
        if not parts or not all(isinstance(p, dict) and p.get("content") and isinstance(p.get("location"), dict) for p in parts):
            return None
        return parts
    except Exception as error:
        logger.info("Stored text for %s could not be reused (%s); extracting again", text_content_key, error)
        return None


# Extract text from file and return an array of chunks
async def extract_text_from_file(key, file_content, current_user=None, account_data=None, page_aware=False):
    # Legacy embedded-image extraction and vision-chat transcription are no
    # longer part of RAG ingestion. Global2 page preparation owns image inputs
    # for Nova; normal text extraction remains independent and lightweight.
    processed_content, visual_map = file_content, {}

    # Global2 PDFs need page numbers: they key the page manifest, the rendered
    # page images, and per-page provenance on every chunk. MarkItDown flattens a
    # PDF into markdown and drops them, so use the page-aware handler first and
    # keep MarkItDown as the fallback (e.g. when the PDF has no text layer).
    if (
        page_aware
        and str(key).lower().endswith(".pdf")
        and os.environ.get("GLOBAL2_PAGE_AWARE_PDF", "true").lower() == "true"
    ):
        try:
            handler = PDFHandler()
            # Tables are replaced by placeholders before extraction and restored as markdown tables after
            # (see rag/pdf_tables.py). Any problem there falls back to plain extraction of the original PDF.
            prepared_content, table_map = handler.preprocess_pdf_tables(file_content)
            # Reading order comes from MarkItDown's layout analysis (what the original pipeline used), split per
            # page; if that is unavailable the page reader is used, top-to-bottom on pages that hold a table.
            page_parts = handler.extract_text_layout(prepared_content) or handler.extract_text(
                prepared_content, visual_map,
                position_ordered_pages={entry["page_number"] for entry in table_map.values()} or None)
            if table_map and page_parts:
                page_parts, table_report = handler.restore_tables(page_parts, table_map)
                logger.info("PDF tables for %s: %s", key, table_report)
            if page_parts:
                logger.info("Page-aware PDF extraction produced %d pages for %s", len(page_parts), key)
                return page_parts
            logger.info("Page-aware PDF extraction found no text for %s; falling back to MarkItDown", key)
        except Exception as e:
            logger.warning("Page-aware PDF extraction failed for %s, falling back: %s", key, str(e))

    if page_aware and str(key).lower().endswith(csv_markdown.SUPPORTED_EXTENSIONS):
        # CSV: a table whose pieces each start with the column names (the plain reader leaves every chunk after the
        # first without them). Not routed through MarkDownHandler, which would flatten the table back to bare rows.
        csv_parts = csv_markdown.parts(file_content, MarkDownHandler().num_tokens_from_string)
        if csv_parts:
            logger.info("CSV table extraction produced %d pieces for %s", len(csv_parts), key)
            return csv_parts

    try:
        # First we will try with markitdown extractor,
        # if that fails then we will proceed as before
        prefetch = None
        if page_aware and str(key).lower().endswith(".pptx") and office_ocr.enabled():
            # global2 only: start reading the deck's pictures now, while the text is converted (both need only the file)
            prefetch = office_ocr.start_prefetch(".pptx", file_content, office_ocr.textract_detect_lines(boto3.client("textract")))
        markitdown_result = None
        if page_aware and str(key).lower().endswith(xlsx_markdown.SUPPORTED_EXTENSIONS):
            markitdown_result = xlsx_markdown.convert(file_content)      # straight from the workbook: far faster and lighter
        if not markitdown_result:
            markitdown_extractor = MarkItDownExtractor()
            markitdown_result = markitdown_extractor.extract_from_content(processed_content, key)
        if markitdown_result:
            if page_aware:      # global2 only: the legacy namespace keeps its original behaviour
                markitdown_result = ocr_office_pictures(markitdown_result, key, file_content, prefetch, account_data)
            logger.debug("MarkItDown extraction: %s", markitdown_result)
            md_bytes = markitdown_result.encode('utf-8')
            logger.info("MarkItDown extraction successful for %s", key)
            parts = None
            if page_aware and str(key).lower().endswith(xlsx_markdown.SUPPORTED_EXTENSIONS):
                # Excel: tables stay tables, cut into pieces that each start with the column names
                parts = xlsx_markdown.parts_from_markdown(markitdown_result, MarkDownHandler().num_tokens_from_string)
            if not parts:
                parts = MarkDownHandler().extract_text(md_bytes, key, visual_map)
            if page_aware and str(key).lower().endswith(".pptx"):
                # A slide is the page of a deck: page_number keys the page manifest, the picture sheet that Nova
                # embeds and the page id of every vector. slide_number stays for readers that want it.
                for part in parts:
                    location = part.get("location")
                    if isinstance(location, dict) and location.get("slide_number") is not None:
                        location.setdefault("page_number", location["slide_number"])
            return parts

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

        # Prose is whitespace-normalized and cut into sentences. A markdown table is kept as rows: it is cut
        # into pieces that each repeat the header (see rag/table_chunks.py), one piece per "sentence".
        sentences = []
        for kind, block in table_chunks.split_blocks(content["content"]):
            if kind == "table":
                sentences.extend(table_chunks.table_pieces(block))
            else:
                text = re.sub(r"\s+", " ", block.strip())
                if text:
                    sentences.extend(sent_tokenize(text))

        return [
            {**content, "content": sentence, "index": index}
            for index, sentence in enumerate(sentences)
        ]
    else:
        return [content]


def save_chunks(chunks_bucket, key, split_count, chunks, object_key=None, force_reprocess=False, chunks_needing_reprocessing=None, rag_enabled=True):
    # Check if this chunk actually needs processing (applies to reprocessing AND retries after failures).
    # `force_reprocess` does not mean "process every child": the status table
    # still determines which existing chunks/pages are incomplete.
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
    metadata["rag_enabled"] = "true" if rag_enabled else "false"

    s3.put_object(
        Bucket=chunks_bucket,
        Key=chunks_key,
        Body=json.dumps({"chunks": chunks, "src": key}),
        Metadata=metadata
    )
    logger.info("✅ Uploaded chunks to %s/%s (will generate SQS message)", chunks_bucket, chunks_key)
    if object_key:
        logger.debug("Stored object_key metadata: %s", object_key)



def chunk_content(key, text_content, split_params, object_key=None, force_reprocess=False, rag_enabled=True):
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
    max_sentence_chars = 26000  # Fixed: was 26,000 which creates a tuple (26, 0) instead of int
    
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
                    save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing, rag_enabled)
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
                        save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing, rag_enabled)
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
                    save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing, rag_enabled)
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

            # Reset for the next chunk. The sentence that starts it must keep its own location and index,
            # otherwise a chunk made of one long sentence or table piece has no page number at all.
            locations = [location]
            indexes = [index]
            index += 1
            char_index += (
                len(chunk_text) + 1
            )  # Include the space that joins with the next chunk.
            current_chunk = [sentence]  # Start the new chunk with the current sentence.
            current_chunk_size = sentence_length

            total_chunks += 1  # Increment the count after forming a chunk

            if len(chunks) == split_increment:
                split_count += 1
                save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing, rag_enabled)
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
        save_chunks(chunks_bucket, key, split_count, chunks, object_key, force_reprocess, chunks_needing_reprocessing, rag_enabled)

    logger.debug("In Chunk Content Function")
    logger.debug(
        "Split Count: %d, Split Increment: %d, Total Chunks: %d",
        split_count, split_increment, total_chunks
    )
    return split_count


def chunk_s3_file_content(bucket, key, object_key=None, force_reprocess=False, rag_enabled=True):
    try:
        # Download the file from S3
        logger.info("Fetching text from %s/%s", bucket, key)
        s3_object = s3.get_object(Bucket=bucket, Key=key)
        data = s3_object["Body"].read()
        logger.info("Fetched text from %s/%s", bucket, key)

        file_content = json.loads(data)
        logger.debug("Loaded json from %s/%s", bucket, key)

        # Global2 keeps the existing chunk-file/SQS contract while preserving
        # page metadata on each local chunk. The selective status lookup is
        # shared, so failed children are the only files re-uploaded.
        chunks = chunk_content(key, file_content, {}, object_key, force_reprocess, rag_enabled=rag_enabled)
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

            # If there are no permissions, create entries for all principals in email_list
            if not items:
                for principal_id in email_list:
                    table.put_item(
                        Item={
                            "object_id": object_id,
                            "principal_id": principal_id,
                            "principal_type": principal_type,
                            "object_type": object_type,
                            "permission_level": "write" if principal_id == current_user else provided_permission_level,
                            "policy": policy,
                        }
                    )
                logger.info(
                    "Created initial items for %s with principals %s", object_id, email_list
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
            stopwatch = timing.Stopwatch()   # per-stage timings, reported as [TIMING] lines

            # Migration replay: rebuild a legacy document in global2 only. Live
            # pointers, permissions and the shared content row are left alone
            # until an explicit cutover (see rag/migration_runner.py).
            migration_target = migration_rules.replay_target(s3_record)

            # Get the bucket and object key from the event
            logger.info("Getting text from %s", s3_info['object']['key'])
            # A message that names no bucket is about a file in the RAG input bucket (see start_rag_from_file in embedding)
            bucket = (s3_info.get("bucket") or {}).get("name") or os.environ["S3_RAG_INPUT_BUCKET_NAME"]
            key = s3_info["object"]["key"]
            key = urllib.parse.unquote(key)

            logger.info("Bucket / Key %s / %s", bucket, key)

            user = None
            account_data = None
            object_size = None
            rag_enabled = False  # until the S3 metadata says otherwise
            try:
                response = s3.head_object(Bucket=bucket, Key=key)
                object_size = response.get("ContentLength")
                logger.debug("Response Metadata: %s", response['Metadata'])
                # Force reprocessing is used for failed/incomplete embeddings.
                # It must bypass the successful-content skip below, while the
                # source's RAG opt-in still controls whether work is queued.
                rag_enabled = response["Metadata"].get("rag_enabled", "false") == "true"
               
                logger.info("Retrieve rag details from parameter store")
                rag_details = get_rag_secrets_for_document(key)
                if rag_details['success']:
                    account_data = rag_details['data']
                    if migration_target:        # costs of a rebuild are also added to that document's migration row
                        account_data = {**account_data, "cost_doc": migration_target}
                    user = account_data['user']
                else:
                    logger.error("Failed to retrieve RAG details from parameter store")

            except Exception as e:
                logger.error("Error fetching metadata for %s: %s", key, str(e))

            if migration_target:
                rag_enabled = True      # only embedded documents are migrated
            if s3_record.get("rag_enabled") is True:
                # A file uploaded with RAG off is switched to RAG for this run (a library file attached to an assistant):
                # the stored text is reused, the page manifest and images are built, then chunking and embedding follow.
                rag_enabled = True

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

                    # Reject by size before downloading anything (see rag/limits.py).
                    rejection = limits.check_size(file_extension, object_size)
                    if rejection:
                        refuse_document(files_table, s3, bucket, key, rejection, migration_target, user)
                        continue

                    # Extract text from the file in S3
                    file_content = stopwatch.call("download", get_file_from_s3, bucket, key)

                    # Page-count / spreadsheet-cell checks: cheap, need the bytes, not a full extraction.
                    rejection = limits.check_before_extraction(file_extension, file_content, rag_enabled)
                    if rejection:
                        refuse_document(files_table, s3, bucket, key, rejection, migration_target, user)
                        continue

                    dochash = hashlib.sha256(file_content).hexdigest()

                    dochash_resposne = hash_files_table.get_item(Key={"id": dochash})

                    # Create a pointer from the user's key to the actual
                    # location of the document by hash
                    # New uploads use the global2 text namespace. Legacy
                    # global/ artifacts remain readable for migration/retrieval.
                    [file_text_content_bucket_name, text_content_key] = (
                        get_text_hash_content_location(bucket, dochash, "global2")
                    )

                    if migration_target and text_content_key != migration_target:
                        # The twin must be built from the exact same bytes as the legacy copy.
                        refuse_document(
                            files_table, s3, bucket, key,
                            f"file hash does not match the legacy document ({text_content_key} != {migration_target})",
                            migration_target, user,
                        )
                        continue

                    if not migration_target:
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

                     # If this file was uploaded on behalf of an assistant (e.g. scraped web
                    # content or drive integration), grant that assistant principal read access
                    # to the global hash key so dual-retrieval can authorize it.
                    astp_id = props.get("astp") if isinstance(props, dict) else None
                    if astp_id:
                        permissions_update["emailList"].append(astp_id)
                        logger.info(
                            "Also granting assistant %s access to global hash key %s", astp_id, text_content_key
                        )

                    if not migration_target:      # a migration replay gets its access at cutover
                        # A document embedded under global/ before keeps whoever could read it (shared users,
                        # groups, assistants) when its global2 copy is built by a re-upload or a reprocess.
                        migration_rules.copy_access(
                            dynamodb.Table(os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]),
                            migration_rules.legacy_key_for(text_content_key), text_content_key,
                        )
                        update_object_permissions(user, permissions_update)

                    text = None
                    # Already processed, not a force reprocess, and either embedding finished or this upload does not use
                    # embeddings at all (RAG off): the stored text is reused instead of extracting (and OCR-ing) it again.
                    existing_text = None
                    if can_reuse_processed_document(dochash_resposne.get("Item"), force_reprocess, rag_enabled, lambda: is_embedding_successful(key)):
                        text_bucket = dochash_resposne.get("Item").get("textLocationBucket")
                        text_key = dochash_resposne.get("Item").get("textLocationKey")
                        logger.info("Getting existing text from %s/%s", text_bucket, text_key)
                        try:
                            existing_text = json.loads(get_file_from_s3(text_bucket, text_key))
                        except Exception as error:
                            logger.warning("Stored text %s/%s could not be read (%s); processing %s again", text_bucket, text_key, error, key)
                    if existing_text is not None:
                        logger.info("✅ Document %s already processed (RAG %s) - skipping", key, "on, embedding completed" if rag_enabled else "off, text already stored")
                        text = existing_text
                        logger.info("Got existing text from %s/%s", text_bucket, text_key)
                        total_tokens = text.get("totalTokens", 0)
                        total_items = text.get("totalItems", 0)
                        location_properties = text.get("locationProperties", [])
                        tags = text.get("tags", [])
                        props = text.get("props", [])
                        if not migration_target:      # this upload's own record still needs its size, as after a fresh extraction
                            files_table.update_item(
                                Key={"id": key},
                                UpdateExpression="SET totalTokens = :tokenVal, totalItems = :itemVal, dochash = :hashVal",
                                ExpressionAttributeValues={":tokenVal": total_tokens, ":itemVal": total_items, ":hashVal": dochash},
                            )
                    else:
                        # Process document if: not processed before, OR force reprocess, OR embedding incomplete/failed
                        if force_reprocess:
                            logger.info("🔄 Force reprocessing document %s", key)
                        elif dochash_resposne.get("Item") is not None:
                            logger.info("⚠️ Document %s processed but embedding incomplete/failed - reprocessing", key)
                        else:
                            logger.info("🆕 New document %s - processing", key)
                        # Large, scanned, or image-heavy PDFs are OCR'd by asynchronous
                        # Textract instead of inside this Lambda. "deferred" means a
                        # follow-up message was queued (job started, still running, or
                        # more result pages to read), so this message is done for now.
                        textract_outcome, textract_payload = textract_pipeline.OUTCOME_NORMAL, ""
                        # A forced reprocess that is retrying failed or unfinished chunks must chunk the SAME text the
                        # finished chunks came from (so they can be skipped), and has no reason to read it again.
                        retrying_chunks = False
                        if force_reprocess:
                            try:
                                retrying_chunks = bool(get_chunks_needing_reprocessing(text_content_key))
                            except Exception:
                                retrying_chunks = False
                        reused_parts = reusable_stored_parts(
                            dochash_resposne.get("Item"), text_content_key, force_reprocess and not retrying_chunks, rag_enabled)
                        if reused_parts is not None:
                            logger.info("Reusing the text already extracted for %s (%d parts): only chunking and embedding remain",
                                        key, len(reused_parts))
                        if reused_parts is None and file_extension == ".pdf" and text_content_key.startswith("global2/"):
                            textract_outcome, textract_payload = stopwatch.call(
                                "textract_decide_or_poll", textract_pipeline.advance,
                                message=s3_record,
                                bucket=bucket,
                                key=key,
                                content_hash=dochash,
                                file_content=file_content,
                                force_reprocess=force_reprocess,
                                textract=boto3.client("textract"),
                                sqs=sqs,
                                s3=s3,
                                queue_url=os.environ.get("RAG_PROCESS_DOCUMENT_QUEUE_URL", ""),
                                text_bucket=file_text_content_bucket_name,
                                remaining_ms=(lambda: context.get_remaining_time_in_millis()) if context else (lambda: 10**9),
                                count_tokens=PDFHandler().num_tokens_from_string,
                                # Textract never sees the tables: it reads a copy with placeholders instead
                                prepare_input=lambda pages: pdf_tables.prepare_textract_input(
                                    file_content, dochash, s3, file_text_content_bucket_name, pages=pages),
                                on_pages_read=lambda api, pages, job_id: textract_cost.record_pages(
                                    account_data, textract_cost.api_kind(api), pages, job_id, key),
                            )
                            if textract_outcome in (textract_pipeline.OUTCOME_TOO_BIG, textract_pipeline.OUTCOME_FAILED):
                                pdf_tables.discard_textract_inputs(s3_record.get("textract"), s3)
                                refuse_document(files_table, s3, bucket, key, textract_payload, migration_target, user)
                                continue
                            if textract_outcome == textract_pipeline.OUTCOME_DEFERRED:
                                logger.info("Textract in progress for %s; continuing on a later message", key)
                                continue
                            if textract_outcome == textract_pipeline.OUTCOME_NORMAL:
                                logger.info("Using normal extraction for %s: %s", key, textract_payload)
                                pdf_tables.discard_textract_inputs(s3_record.get("textract"), s3)
                        try:
                            # Every page's text layer (with tables slotted in); pages that went through
                            # Textract then replace theirs, so OCR only ever changes the pages that needed it.
                            text = reused_parts if reused_parts is not None else stopwatch.call(
                                "extract", asyncio.run,
                                extract_text_from_file(
                                    file_extension, file_content, user, account_data,
                                    page_aware=text_content_key.startswith("global2/"),
                                )
                            )
                            if textract_outcome == textract_pipeline.OUTCOME_PARTS:
                                ocr_parts, table_report = pdf_tables.finish_textract_tables(
                                    textract_payload, s3_record.get("textract") or {}, s3, PDFHandler().num_tokens_from_string)
                                merge_report = {}
                                text = textract_pipeline.merge_ocr_pages(
                                    text or [], ocr_parts, PDFHandler().num_tokens_from_string, merge_report,
                                    replace_pages=(s3_record.get("textract") or {}).get("replacePages"))
                                for page_number, outcome in sorted(merge_report.items()):
                                    logger.info("[OCR-DECISION] page=%s: %s (%d line(s))", page_number, outcome["mode"], outcome["added_lines"])
                                logger.info("Textract used for %d page(s) of %s; tables on those pages: %s", len(ocr_parts), key, table_report)
                            logger.info("Extracted text from %s", key)
                            total_tokens = sum(d.get("tokens", 0) for d in text)
                            total_items = len(text)

                            # Universal backstop: tokens are what embedding and chat context cost.
                            rejection = limits.check_tokens(total_tokens, rag_enabled)
                            if rejection:
                                refuse_document(files_table, s3, bucket, key, rejection, migration_target, user)
                                continue

                            # Persist a replayable global2 page manifest (and PDF page
                            # images) when extraction provides page locations. Optional
                            # and non-fatal: it never affects the legacy text artifact,
                            # the RAG-off path, or whether the document ingests.
                            if rag_enabled:
                                stopwatch.call(
                                    "manifest_and_page_images", persist_global2_page_manifest,
                                    text, text_content_key, dochash, file_extension, file_content,
                                    file_text_content_bucket_name,
                                )
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
                            stopwatch.call(
                                "store_text", s3.put_object,
                                Bucket=file_text_content_bucket_name,
                                Key=text_content_key,
                                Body=json.dumps(text),
                            )
                            logger.info(
                                "Uploaded text to %s/%s", file_text_content_bucket_name, text_content_key
                            )

                            if not migration_target:
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
                                # Text is still cached for JS document retrieval,
                                # but no embedding chunk queue is emitted.
                                logger.info("RAG embeddings disabled; retained text cache without chunk queue")
                                stopwatch.emit("process_document", key=key, bytes=len(file_content), parts=total_items, rag=False)
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
                                    stopwatch.call(
                                        "queue_chunking", sqs.send_message,
                                        QueueUrl=chunk_queue_url,
                                        MessageBody=message_body,
                                    )
                                    logger.info("Message sent to queue: %s", message_body)
                                    stopwatch.emit("process_document", key=key, bytes=len(file_content), parts=total_items, rag=True)
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
        existing_item = None
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
                    "childChunks": child_chunks,
                    "namespace": "global2" if str(object_id).startswith("global2/") else "global",
                },
            }
        )
        logger.info(
            "Updated %d nested childChunks for %s in Embeddings Progress Table", total_chunks, object_id
        )

    except Exception as e:
        logger.error("Failed to create or update item in DynamoDB table.")
        logger.error("%s", e)


def get_original_creator(key, object_key=None):
    """Who first uploaded this content: the content row's ``originalCreator``, else the uploader recorded on the user's file
    record (``createdBy``) for ``object_key``. Older content rows have no ``originalCreator``, which used to show as "unknown"."""
    original_creator = "unknown"
    dynamodb = None
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

    if original_creator in (None, "", "unknown") and object_key and dynamodb is not None:
        try:
            record = dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"]).get_item(Key={"id": object_key}).get("Item") or {}
            original_creator = record.get("createdBy") or "unknown"
        except Exception as e:
            logger.error("Could not read the uploader of %s from the file record: %s", object_key, e)

    return original_creator or "unknown"


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
            rag_enabled = str(s3_info.get("metadata", {}).get("rag_enabled", "true")).lower() == "true"
            logger.info("Object key from metadata: %s", object_key)

            # Get the bucket and object key from the event
            logger.info("Getting raw text from %s", s3_info['object']['key'])
            bucket = s3_info["bucket"]["name"]
            key = s3_info["object"]["key"]
            key = urllib.parse.unquote(key)

            logger.info("Bucket / Key %s / %s", bucket, key)

            # Figure out who uploaded this file, even though it's a shared
            # global entry
            original_creator = get_original_creator(key, object_key)

            # Chunking always consults shared progress. On force reprocess it
            # uploads only failed/incomplete child chunk files, preserving
            # completed work; changed chunk structure invalidates later children.
            chunk_started = time.perf_counter()
            chunks_created = chunk_s3_file_content(bucket, key, object_key, force_reprocess, rag_enabled=rag_enabled)
            timing.emit("chunk_document", time.perf_counter() - chunk_started, key=key, chunk_files=chunks_created)
            
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
                log_critical_error(
                    function_name="chunk_document_for_rag",
                    error_type="ZeroChunksProduced",
                    error_message="Document text extraction succeeded but chunking produced 0 chunks - file may be empty, unsupported format, or contain only binary data",
                    current_user=original_creator,
                    severity=SEVERITY_LOW,
                    stack_trace='',
                    context={
                        "document_key": key,
                        "bucket": bucket,
                        "object_key": object_key if object_key else 'N/A',
                        "original_creator": original_creator,
                        "force_reprocess": force_reprocess,
                        "diagnostic": "Common causes: empty file, unsupported file type, or extraction produced no readable text"
                    }
                )
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
