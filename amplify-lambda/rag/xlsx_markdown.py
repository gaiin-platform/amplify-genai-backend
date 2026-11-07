"""Excel (.xlsx / .xlsm) to markdown tables, read straight from the workbook.

Why not MarkItDown: it loads every sheet into pandas and converts through HTML, which costs time that grows faster
than the file (measured on one data sheet: 230k cells 10.9 s / 514 MB, 500k cells 38 s / 958 MB, 1M cells 133 s /
1.9 GB) and turns whole numbers in a column that has gaps into floats (an age of 42 comes out as "42.0").
Reading the workbook row by row gives the same tables in a fraction of the time and memory
(230k cells 0.9 s / 53 MB, 1M cells 3.8 s / 80 MB).

Output matches what the rest of the pipeline expects from MarkItDown: one "## <sheet name>" heading per sheet,
then a markdown table whose first non-empty row is the header, sheets separated by a blank line.
Any problem returns None and the caller falls back to MarkItDown.
"""

from __future__ import annotations

import datetime as _dt
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from pycommon.logger import getLogger
from rag import table_chunks

logger = getLogger("rag_xlsx_markdown")

SUPPORTED_EXTENSIONS = (".xlsx", ".xlsm")
_LINE_BREAKS = re.compile(r"[\r\n]+")


def _text(value: Any) -> str:
    """One cell as text, without changing what it says."""
    if value is None:
        return ""
    if isinstance(value, _dt.datetime):
        return value.date().isoformat() if value.time() == _dt.time(0) else value.isoformat(sep=" ")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))                   # 42.0 -> 42: the same number, as it is written in the sheet
    text = str(value)
    if "\n" in text or "\r" in text:
        text = _LINE_BREAKS.sub(" ", text)
    return text.replace("|", "\\|").strip()


def _table(raw_rows: Any) -> Optional[Tuple[List[str], List[Tuple[int, List[str]]], int, int]]:
    """(header, [(source row number, cells)], width, header row number). Empty rows and trailing empty cells are
    dropped; a blank header cell becomes ``Column N``. Row numbers are the position in the source, empty rows included."""
    rows: List[Tuple[int, List[str]]] = []
    width = 0
    for number, raw in enumerate(raw_rows, start=1):
        cells = [_text(v) for v in raw]
        last = len(cells)
        while last and not cells[last - 1]:
            last -= 1
        if not last:
            continue                             # a row with nothing in it
        del cells[last:]
        width = max(width, last)
        rows.append((number, cells))
    if not rows:
        return None
    first = rows[0][1]
    header = [first[i] if i < len(first) and first[i] else f"Column {i + 1}" for i in range(width)]
    return header, rows[1:], width, rows[0][0]


def _header_text(header: List[str], width: int) -> str:
    return "| " + " | ".join(header) + " |\n|" + " --- |" * width


def _row_line(cells: List[str], width: int) -> str:
    return "| " + " | ".join(cells + [""] * (width - len(cells))) + " |"


def rows_to_markdown(title: Optional[str], raw_rows: Any) -> Optional[str]:
    """One table from rows of cells: ``## title`` (when given), the first non-empty row as the header, then the rest."""
    table = _table(raw_rows)
    if table is None:
        return None
    header, data, width, _ = table
    lines = ([f"## {title}"] if title else []) + _header_text(header, width).split("\n")
    lines += [_row_line(cells, width) for _, cells in data]
    return "\n".join(lines)


def row_pieces(raw_rows: Any, max_chars: int) -> List[Tuple[int, str]]:
    """The table cut into pieces of whole rows of at most ``max_chars`` (a row longer than that is a piece on its own).
    Every piece starts with the header, so it reads as a small table by itself. Returns [(first row number, text)]."""
    table = _table(raw_rows)
    if table is None:
        return []
    header, data, width, header_number = table
    head = _header_text(header, width)
    if not data:
        return [(header_number, head)]
    pieces: List[Tuple[int, str]] = []
    current: List[str] = []
    first, size = header_number, len(head)
    for number, cells in data:
        line = _row_line(cells, width)
        if current and size + len(line) + 1 > max_chars:
            pieces.append((first, head + "\n" + "\n".join(current)))
            current, size = [], len(head)
        if not current:
            first = number
        current.append(line)
        size += len(line) + 1
    if current:
        pieces.append((first, head + "\n" + "\n".join(current)))
    return pieces


def _sheet_markdown(worksheet: Any) -> Optional[str]:
    try:
        worksheet.reset_dimensions()             # files from some tools declare a wrong sheet size
    except Exception:
        pass
    return rows_to_markdown(worksheet.title, worksheet.iter_rows(values_only=True))


def convert(file_content: bytes) -> Optional[str]:
    """Markdown for the whole workbook, or None if it cannot be read (the caller then uses MarkItDown)."""
    try:
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(file_content), read_only=True, data_only=True)
    except Exception as error:
        logger.warning("Direct Excel read failed, falling back: %s", error)
        return None
    try:
        sheets = [md for md in (_sheet_markdown(ws) for ws in workbook.worksheets) if md]
    except Exception as error:
        logger.warning("Direct Excel read failed part-way, falling back: %s", error)
        return None
    finally:
        workbook.close()
    return "\n\n".join(sheets) + "\n" if sheets else None


SHEET_HEADING = re.compile(r"^## (.+)$", re.M)


def parts_from_markdown(markdown: str, count_tokens: Any, piece_chars: int = table_chunks.SPREADSHEET_PIECE_CHARS) -> List[Dict[str, Any]]:
    """Extraction parts from ``convert()`` output (after picture text was added): per sheet, the table cut into pieces
    that each start with the header, then any other text (picture text) as ordinary prose parts.

    MarkDownHandler would flatten the tables into bare rows, so the column names would only be on the first chunk.
    The repeated header is counted once per sheet so the document's size does not grow with the number of pieces.
    """
    parts: List[Dict[str, Any]] = []
    sections = SHEET_HEADING.split(markdown)             # [preamble, name1, body1, name2, body2, ...]
    for name, body in zip(sections[1::2], sections[2::2]):
        location = {"sheet_name": name.strip()}
        head: Optional[str] = None
        for kind, text in table_chunks.split_blocks(body):
            if kind == "table":
                for piece in table_chunks.table_pieces(text, piece_chars):
                    if head is None:
                        head = "\n".join(piece.split("\n")[:2])
                        counted = piece
                    else:
                        counted = piece[len(head) + 1:] if piece.startswith(head) else piece
                    parts.append({"content": piece, "tokens": count_tokens(counted), "location": dict(location), "canSplit": False})
            elif text.strip():
                parts.append({"content": text.strip(), "tokens": count_tokens(text), "location": dict(location), "canSplit": True})
    return parts
