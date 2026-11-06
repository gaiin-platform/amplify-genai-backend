"""Ingestion limits for uploaded documents.

Why these exist (measured, not guessed):
  * The frontend gives up on a document after 120 s and then deletes it, so
    anything that cannot be ready in about that time is wasted work.
  * Text-layer PDFs are cheap to extract (1000 pages ~0.4 s); their cap is a
    cost policy. Spreadsheets are the slow case: a 3 MB xlsx with 500k cells took
    83 s, so cells - not bytes - bound them. Tokens are what embedding costs, so a
    token ceiling is the universal backstop (a 13 MB CSV is ~5M tokens).

Every limit is an environment variable so it can be tuned from measurements
without a code change. Rejections return a user-facing reason string.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Optional

logger = logging.getLogger("rag_limits")

MB = 1024 * 1024

SPREADSHEET_EXTENSIONS = frozenset({".xlsx", ".xlsm", ".xls", ".ods"})
PLAIN_TEXT_EXTENSIONS = frozenset({
    ".csv", ".tsv", ".txt", ".md", ".markdown", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".log", ".rtf",
})


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def category_for(extension: str) -> str:
    extension = (extension or "").lower()
    if extension in SPREADSHEET_EXTENSIONS:
        return "spreadsheet"
    if extension in PLAIN_TEXT_EXTENSIONS:
        return "text"
    return "document"


def max_bytes_for(extension: str) -> int:
    category = category_for(extension)
    if category == "spreadsheet":
        return _int_env("MAX_SPREADSHEET_MB", 25) * MB
    if category == "text":
        return _int_env("MAX_TEXT_FILE_MB", 10) * MB
    return _int_env("MAX_DOCUMENT_MB", 100) * MB


def max_pdf_pages(rag_enabled: bool) -> int:
    # Chat attachments (RAG off) cost no embeddings, so they may be longer.
    return _int_env("MAX_PDF_PAGES_RAG", 500) if rag_enabled else _int_env("MAX_PDF_PAGES_CHAT", 1000)


def max_tokens(rag_enabled: bool) -> int:
    return _int_env("MAX_DOCUMENT_TOKENS_RAG", 500_000) if rag_enabled else _int_env("MAX_DOCUMENT_TOKENS_CHAT", 1_000_000)


def check_size(extension: str, size_bytes: Optional[int]) -> Optional[str]:
    """Reject by byte size before downloading anything. None means allowed."""
    if not size_bytes:
        return None
    limit = max_bytes_for(extension)
    if size_bytes > limit:
        return (
            f"This file is {size_bytes / MB:.0f} MB. The limit for {category_for(extension)} files "
            f"is {limit // MB} MB."
        )
    return None


def check_pdf_pages(file_content: bytes, rag_enabled: bool) -> Optional[str]:
    """Reject over-long PDFs. Never blocks on a PDF it cannot open (the extractor reports that)."""
    try:
        import fitz

        document = fitz.open(stream=file_content, filetype="pdf")
        try:
            pages = document.page_count
        finally:
            document.close()
    except Exception as error:
        logger.info("Skipping PDF page-count check: %s", error)
        return None
    limit = max_pdf_pages(rag_enabled)
    if pages > limit:
        return f"This PDF has {pages} pages. The limit is {limit} pages; please split it into smaller files."
    return None


def spreadsheet_cell_count(file_content: bytes) -> Optional[int]:
    """Cheap upper bound on cells (rows x columns per sheet) without reading values."""
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(file_content), read_only=True, data_only=True)
        try:
            return sum((sheet.max_row or 0) * (sheet.max_column or 0) for sheet in workbook.worksheets)
        finally:
            workbook.close()
    except Exception as error:
        logger.info("Skipping spreadsheet size check: %s", error)
        return None


def check_spreadsheet_cells(extension: str, file_content: bytes) -> Optional[str]:
    """A guard against runaway files only. Measured on the direct Excel reader (rag/xlsx_markdown.py): 1M cells take
    4 s and 80 MB, so time and memory are not what stops a big sheet; the token ceiling (check_tokens) is, at about
    270k cells. Only the MarkItDown fallback (global/ and unreadable workbooks) is slow: 230k cells 11 s, 1M cells 133 s."""
    if (extension or "").lower() not in {".xlsx", ".xlsm"}:
        return None
    cells = spreadsheet_cell_count(file_content)
    limit = _int_env("MAX_SPREADSHEET_CELLS", 2_000_000)
    if cells is not None and cells > limit:
        return (
            f"This spreadsheet has about {cells:,} cells. The limit is {limit:,}; "
            "please split it or remove unused rows and columns."
        )
    return None


def check_tokens(total_tokens: int, rag_enabled: bool) -> Optional[str]:
    limit = max_tokens(rag_enabled)
    if total_tokens > limit:
        return (
            f"The extracted text is about {total_tokens:,} tokens. The limit is {limit:,}; "
            "please split the document into smaller files."
        )
    return None


def check_before_extraction(extension: str, file_content: bytes, rag_enabled: bool) -> Optional[str]:
    """Checks that need the file bytes but not a full extraction."""
    if (extension or "").lower() == ".pdf":
        return check_pdf_pages(file_content, rag_enabled)
    return check_spreadsheet_cells(extension, file_content)
