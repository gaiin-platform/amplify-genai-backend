"""Keep markdown tables intact through chunking.

``core.split_text`` flattens all whitespace and splits text into sentences, which would turn a markdown
table into one run-on line (rows no longer separated) and strand every chunk after the first without its
header row. This module cuts a page's text into prose and table blocks, and cuts a table block into pieces
that each start with the table's header, so every piece reads as a small table on its own.
"""

from __future__ import annotations

import re
from typing import List, Tuple

MAX_PIECE_CHARS = 1500                       # a table piece is one chunk; ~400 tokens, well inside the embedding limit
# CSV rows are independent records, so a piece can hold several of them. Characters, like every size in the chunker:
# 12,000 characters is about 3,000 tokens, inside the embedding model's 8K-token input and the 21,000-character
# ceiling the embedding step applies. The column names are repeated at the top of every piece.
SPREADSHEET_PIECE_CHARS = 12000
TABLE_BLOCK = re.compile(r"(?:^[ \t]*\|[^\n]*\|[ \t]*(?:\n|\Z))+", re.M)
SEPARATOR = re.compile(r"^\s*\|[\s:|\-]+\|\s*$")


def split_blocks(text: str) -> List[Tuple[str, str]]:
    """[("text", prose), ("table", markdown table), ...] in order. A table needs a header separator row."""
    blocks, last = [], 0
    for match in TABLE_BLOCK.finditer(text):
        block = match.group(0).strip("\n")
        if not any(SEPARATOR.match(line) for line in block.splitlines()):
            continue                                       # lines that merely contain pipes are prose
        if match.start() > last:
            blocks.append(("text", text[last:match.start()]))
        blocks.append(("table", block))
        last = match.end()
    if last < len(text):
        blocks.append(("text", text[last:]))
    return blocks


def table_pieces(block: str, max_chars: int = MAX_PIECE_CHARS) -> List[str]:
    """Cut a markdown table into pieces of whole rows, each starting with the header and separator rows."""
    lines = [line.rstrip() for line in block.splitlines() if line.strip()]
    separator = next((i for i, line in enumerate(lines) if SEPARATOR.match(line)), None)
    if separator is None:
        return ["\n".join(lines)]
    header, rows = lines[:separator + 1], lines[separator + 1:]
    if not rows:
        return ["\n".join(header)]
    head_text = "\n".join(header)
    pieces, current, size = [], [], len(head_text)
    for row in rows:
        if current and size + len(row) + 1 > max_chars:
            pieces.append(head_text + "\n" + "\n".join(current))
            current, size = [], len(head_text)
        current.append(row)
        size += len(row) + 1
    if current:
        pieces.append(head_text + "\n" + "\n".join(current))
    return pieces
