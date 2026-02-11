"""
Document classifier for routing to appropriate RAG pipeline
Determines whether to use VDR (Visual Document Retrieval) or Text RAG
"""

import os
import mimetypes
from pycommon.logger import getLogger

logger = getLogger("document_classifier")


class PipelineType:
    """Pipeline types"""
    VDR = "vdr"  # Visual Document Retrieval (image-based)
    TEXT_RAG = "text_rag"  # Traditional text-based RAG


def classify_document_for_pipeline(key, file_metadata, file_size_mb=None):
    """
    Classify document to determine which pipeline to use

    Args:
        key: S3 object key (file path)
        file_metadata: S3 head_object metadata dict
        file_size_mb: Optional file size in MB (will extract from metadata if not provided)

    Returns:
        str: Pipeline type (PipelineType.VDR or PipelineType.TEXT_RAG)
    """

    try:
        # Extract file size
        if file_size_mb is None:
            file_size_mb = file_metadata.get('ContentLength', 0) / (1024 * 1024)

        # Extract MIME type
        mime_type = file_metadata.get('ContentType', '')
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(key)
            mime_type = mime_type or 'application/octet-stream'

        # Extract file extension
        file_ext = os.path.splitext(key)[1].lower()
        filename_lower = os.path.basename(key).lower()

        logger.info(f"Classifying document: {key} (size: {file_size_mb:.2f}MB, type: {mime_type})")

        # RULE 1: Presentations always use VDR (layout and visuals are critical)
        if _is_presentation(mime_type, file_ext):
            logger.info(f"→ VDR (presentation format)")
            return PipelineType.VDR

        # RULE 2: Forms and invoices use VDR (structure matters)
        if _is_form_or_invoice(filename_lower, mime_type):
            logger.info(f"→ VDR (form/invoice detected)")
            return PipelineType.VDR

        # RULE 3: Scanned documents use VDR (may have poor OCR)
        if _is_scanned_document(file_metadata):
            logger.info(f"→ VDR (scanned document)")
            return PipelineType.VDR

        # RULE 4: Large visual-heavy PDFs use VDR
        if file_ext == '.pdf' and file_size_mb > 10:
            # TODO: Could add quick page scan to check visual density
            # For now, assume large PDFs may have visuals
            logger.info(f"→ VDR (large PDF, likely visual-heavy)")
            return PipelineType.VDR

        # RULE 5: Code files ALWAYS use Text RAG (syntax matters, no visuals)
        if _is_code_file(file_ext):
            logger.info(f"→ Text RAG (code file)")
            return PipelineType.TEXT_RAG

        # RULE 6: Plain text files use Text RAG
        if _is_plain_text(mime_type, file_ext):
            logger.info(f"→ Text RAG (plain text)")
            return PipelineType.TEXT_RAG

        # RULE 7: Spreadsheets use Text RAG (data extraction works well)
        if _is_spreadsheet(mime_type, file_ext):
            logger.info(f"→ Text RAG (spreadsheet)")
            return PipelineType.TEXT_RAG

        # DEFAULT: Text RAG for unknown types
        logger.info(f"→ Text RAG (default)")
        return PipelineType.TEXT_RAG

    except Exception as e:
        logger.error(f"Error classifying document: {str(e)}")
        # Default to Text RAG on error
        return PipelineType.TEXT_RAG


def _is_presentation(mime_type, file_ext):
    """Check if document is a presentation"""
    presentation_mimes = [
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.oasis.opendocument.presentation'
    ]

    presentation_exts = ['.ppt', '.pptx', '.odp', '.key']

    return mime_type in presentation_mimes or file_ext in presentation_exts


def _is_form_or_invoice(filename_lower, mime_type):
    """Check if document is a form or invoice"""
    form_keywords = ['form', 'invoice', 'receipt', 'application', 'claim', 'tax']

    # Check filename for keywords
    for keyword in form_keywords:
        if keyword in filename_lower:
            return True

    return False


def _is_scanned_document(file_metadata):
    """
    Check if document is scanned (image-based PDF)
    This is a heuristic - ideally would inspect PDF content
    """
    # Check for metadata hints
    metadata = file_metadata.get('Metadata', {})

    # Some scanners add metadata
    if metadata.get('scanned') == 'true':
        return True

    # Check for "scan" in user metadata
    for key, value in metadata.items():
        if 'scan' in key.lower() or 'scan' in str(value).lower():
            return True

    return False


def _is_code_file(file_ext):
    """Check if file is source code"""
    code_extensions = [
        # Common programming languages
        '.py', '.js', '.ts', '.tsx', '.jsx',
        '.java', '.cpp', '.c', '.h', '.hpp',
        '.cs', '.go', '.rs', '.rb', '.php',
        '.swift', '.kt', '.scala', '.r',

        # Web
        '.html', '.htm', '.css', '.scss', '.sass', '.less',
        '.vue', '.svelte',

        # Config and data
        '.json', '.yaml', '.yml', '.toml', '.ini', '.xml',
        '.sql', '.sh', '.bash', '.ps1',

        # Other
        '.log', '.txt', '.md', '.rst',
        '.dockerfile', 'makefile', '.gitignore'
    ]

    return file_ext in code_extensions


def _is_plain_text(mime_type, file_ext):
    """Check if file is plain text"""
    text_mimes = [
        'text/plain',
        'text/markdown',
        'text/csv',
        'text/tab-separated-values'
    ]

    text_exts = ['.txt', '.md', '.markdown', '.csv', '.tsv', '.log']

    return mime_type in text_mimes or file_ext in text_exts


def _is_spreadsheet(mime_type, file_ext):
    """Check if file is a spreadsheet"""
    spreadsheet_mimes = [
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.oasis.opendocument.spreadsheet',
        'text/csv'
    ]

    spreadsheet_exts = ['.xls', '.xlsx', '.ods', '.csv']

    return mime_type in spreadsheet_mimes or file_ext in spreadsheet_exts


def get_pipeline_queue_url(pipeline_type):
    """
    Get SQS queue URL for pipeline type

    Args:
        pipeline_type: PipelineType.VDR or PipelineType.TEXT_RAG

    Returns:
        str: SQS queue URL
    """
    if pipeline_type == PipelineType.VDR:
        return os.environ.get('VDR_PROCESSING_QUEUE_URL')
    else:
        return os.environ.get('TEXT_RAG_PROCESSING_QUEUE_URL')


def get_pipeline_description(pipeline_type):
    """Get human-readable description of pipeline"""
    if pipeline_type == PipelineType.VDR:
        return "Visual Document Retrieval (image-based, preserves layout and visuals)"
    else:
        return "Text RAG (text extraction and embedding)"
