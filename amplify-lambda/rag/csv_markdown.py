"""CSV as table pieces: every chunk starts with the column names.

The plain CSV reader writes each record as bare values, which leaves every chunk after the first without its column
names, and MarkDownHandler flattens markdown tables back into bare rows. So the pieces are built here, directly from the
rows, with the table builder Excel uses (``rag/xlsx_markdown.py``). Any problem returns None and the caller keeps the
plain reader.
"""

from __future__ import annotations

import csv
import io
import sys
from typing import Any, Callable, Dict, List, Optional

from pycommon.logger import getLogger
from rag import table_chunks, xlsx_markdown

logger = getLogger("rag_csv_markdown")

SUPPORTED_EXTENSIONS = (".csv",)


def read_rows(file_content: bytes) -> Optional[List[List[str]]]:
    """The records of the file (a quoted cell may span lines), or None if it cannot be decoded or parsed."""
    try:
        text = file_content.decode("utf-8-sig")          # also removes a leading byte-order mark
    except UnicodeDecodeError:
        try:
            from rag.handlers.shared_functions import is_likely_text
            _, encoding = is_likely_text(file_content)
            text = file_content.decode(encoding or "latin-1")
        except Exception as error:
            logger.warning("CSV could not be decoded, keeping the plain reader: %s", error)
            return None
    try:
        csv.field_size_limit(min(sys.maxsize, 2**31 - 1))   # a single cell can be long
        return list(csv.reader(io.StringIO(text, newline="")))
    except Exception as error:
        logger.warning("CSV could not be parsed, keeping the plain reader: %s", error)
        return None


def parts(
    file_content: bytes, count_tokens: Callable[[str], int], piece_chars: int = table_chunks.SPREADSHEET_PIECE_CHARS,
) -> Optional[List[Dict[str, Any]]]:
    """Extraction parts: one per piece, each a small markdown table with the header; ``row_number`` is the first
    record in the piece. ``tokens`` counts the header once. None if the file has no readable rows."""
    rows = read_rows(file_content)
    pieces = xlsx_markdown.row_pieces(rows, piece_chars) if rows else []
    if not pieces:
        return None
    head = "\n".join(pieces[0][1].split("\n")[:2])
    result = []
    for index, (first, text) in enumerate(pieces):
        # The repeated header is formatting, not document content: count it once so the document's size (and the
        # token limit that is checked against it) does not grow with the number of pieces.
        counted = text if index == 0 else text[len(head) + 1:]
        result.append({"content": text, "tokens": count_tokens(counted), "location": {"row_number": first}, "canSplit": False})
    return result
