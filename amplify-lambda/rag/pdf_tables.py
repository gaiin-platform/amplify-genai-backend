"""Keep PDF tables as tables.

Plain text extraction (the PDF text layer, MarkItDown, Textract's line OCR) flattens a table into
loose lines, so a number can end up separated from its row and column labels. This module applies
the same idea as the PDF image markers in ``PDFHandler.preprocess_pdf_visuals``:

  1. find every table (PyMuPDF's table finder; free and local),
  2. remember its content as a markdown table,
  3. replace the table in the PDF with a short placeholder like ``<Table#7_1>``,
  4. run extraction on the modified PDF (text layer OR Textract: neither ever sees the table),
  5. swap each placeholder for its markdown table in the extracted text.

Nothing is ever lost: a placeholder that extraction does not return (for example OCR mangled it) has
its table appended to that page instead. No LLM or vision model is involved.

Only PDFs need this. DOCX / PPTX / XLSX tables already come out as markdown through MarkItDown, and
CSV rows are already one record per line. Tables inside scanned pages or screenshots are raster
images, not PDF tables; those still go through Textract's normal text output.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from rag import ocr_rules

logger = logging.getLogger("rag_pdf_tables")

PLACEHOLDER = "<Table#{page}_{index}>"          # same style as the image markers: <Visual#7_image_1>
MIN_ROWS, MIN_COLS = 2, 2
MIN_NONEMPTY_CELLS = 3
MIN_WORD_COVERAGE = 0.9        # a real table's cells hold (almost) every word that sits inside its box
WORK_PREFIX = "textract-partial/input/"      # inside the file-text bucket; that prefix expires after 2 days


def enabled() -> bool:
    return os.environ.get("GLOBAL2_PDF_TABLES", "true").lower() == "true"


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def _cell(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("|", "\\|")


def table_markdown(rows: Sequence[Sequence[Any]]) -> str:
    """GitHub-style markdown. The first row becomes the header (markdown requires one)."""
    cleaned = [[_cell(c) for c in row] for row in rows]
    cleaned = [row for row in cleaned if any(row)]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    caption = ""
    if width > 1 and len(cleaned) > 2 and sum(1 for c in cleaned[0] if c) == 1:
        caption = next(c for c in cleaned[0] if c)          # a banner such as "Quick reference: ..." above the real header
        cleaned = cleaned[1:]
    lines = ["| " + " | ".join(cleaned[0]) + " |", "|" + "|".join([" --- "] * width) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in cleaned[1:]]
    table = "\n".join(lines)
    return f"{caption}\n\n{table}" if caption else table


MAX_EMPTY_SHARE = 0.66         # a grid that is mostly empty cells is a diagram (boxes and arrows), not a table
MIN_CELLS_FOR_DIAGRAM_CHECK = 8


def looks_like_diagram(rows: Sequence[Sequence[Any]]) -> bool:
    """True when most cells of the "table" are empty: PyMuPDF reads the boxes of a flow chart or tree as grid cells,
    and a table made that way holds the whole figure's labels in one or two cells, out of order."""
    cells = [c for row in rows for c in row]
    if len(cells) < MIN_CELLS_FOR_DIAGRAM_CHECK:
        return False
    empty = sum(1 for c in cells if not str(c or "").strip())
    return empty / len(cells) > MAX_EMPTY_SHARE


def cells_hold_the_words(page: Any, bbox: Any, rows: Sequence[Sequence[Any]]) -> bool:
    """False when the "table" is really something else, such as a paragraph inside a drawn box.

    PyMuPDF can mistake ruled boxes and brackets for tables; its cells then cut through the text and the
    paragraph would be destroyed when the box is blanked out. A genuine table passes because every word
    inside its box is found intact in one of its cells.
    """
    try:
        words = [token for w in page.get_text("words", clip=bbox) for token in re.findall(r"[a-z0-9]+", str(w[4]).lower())]
        if len(words) < 4:
            return True
        from collections import Counter
        in_cells = Counter(t for row in rows for cell in row for t in re.findall(r"[a-z0-9]+", str(cell or "").lower()))
        matched = sum((Counter(words) & in_cells).values())
        return matched / len(words) >= MIN_WORD_COVERAGE
    except Exception:
        return True


def _join_words(words: Sequence[Any]) -> str:
    """Words of one cell in reading order: top line first, left to right within a line."""
    if not words:
        return ""
    ordered = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines: List[List[Any]] = []
    for w in ordered:
        cy, height = (w[1] + w[3]) / 2, max(w[3] - w[1], 1.0)
        if lines and abs(cy - lines[-1][0]) <= 0.6 * height:
            lines[-1][1].append(w)
        else:
            lines.append([cy, [w]])
    text = ""
    for _, line in lines:
        piece = " ".join(str(w[4]) for w in sorted(line, key=lambda w: w[0]))
        # a word hyphenated across two lines ("data-" / "science") is one word; "- " before a capital or digit stays apart
        text = text + piece if re.search(r"[A-Za-z]-$", text) and piece[:1].islower() else (f"{text} {piece}" if text else piece)
    return text


def _column_edges(table: Any) -> List[float]:
    """Left edge of each column, read from the header row (the row PyMuPDF draws most reliably)."""
    return sorted(c[0] for c in table.rows[0].cells if c is not None)


def _column_of(edges: Sequence[float], x: float) -> int:
    index = 0
    for i, edge in enumerate(edges):
        if x >= edge - 1.0:
            index = i
    return index


def _words_in_table(page: Any, table: Any) -> List[Any]:
    x0, y0, x1, y1 = table.bbox
    return [w for w in page.get_text("words", clip=table.bbox)
            if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]


def _is_fragment(cells: Sequence[Any], typical_height: float, first_text: Any) -> bool:
    """A row that is only a slice of a logical row: PyMuPDF cuts a row wherever one of its cells wraps onto a
    second line, leaving sliver rows (no label, or a label cell a few points tall) beside the real one."""
    first = cells[0] if cells else None
    if first is None:
        return True
    return not str(first_text or "").strip() and (first[3] - first[1]) < 0.6 * typical_height


def _centred_label_slivers(grid: Sequence[Sequence[Any]], rows: Sequence[Sequence[Any]]) -> Tuple[Dict[int, Tuple[float, float]], set]:
    """A label that is vertically centred in a tall, wrapped row is flanked by two empty label boxes of equal height
    (the lines above and below it). Returns {label row: (top, bottom) of the whole logical row} and the sliver rows."""
    boxes = sorted((cells[0][1], cells[0][3], r) for r, cells in enumerate(grid) if cells and cells[0] is not None)
    empty = lambda r: not str((rows[r][0] if rows[r] else "") or "").strip()
    spans: Dict[int, Tuple[float, float]] = {}
    used: set = set()
    for i in range(1, len(boxes) - 1):
        (y0, y1, r), above, below = boxes[i], boxes[i - 1], boxes[i + 1]
        if empty(r) or not (empty(above[2]) and empty(below[2])) or above[2] in used or below[2] in used:
            continue
        # the label's box can be shrunk to its text, so allow a small gap, but the whole group must be centred on the label
        if abs(above[1] - y0) > 12 or abs(below[0] - y1) > 12 or abs((above[1] - above[0]) - (below[1] - below[0])) > 4:
            continue
        if abs(((above[0] + below[1]) / 2) - ((y0 + y1) / 2)) > 6:
            continue
        spans[r] = (above[0], below[1])
        used.update((above[2], below[2]))
    return spans, used


def repair_table_rows(page: Any, table: Any, rows: List[List[Any]]) -> List[List[Any]]:
    """Fix the two ways PyMuPDF's cell boxes damage a table, using the words that really sit on the page.

    1. Wrapped cells split one logical row into several slivers (and can even put a continuation line above its
       label), so "saved / applied / interviewing /" and "offered / declined" ended up on rows of their own.
       Every word in the table is re-assigned to the logical row (the label cell) it belongs to and to its column.
    2. A cell box can be narrower than its text, cutting off the tail ("STUDENT#{userId" without the brace,
       "undergraduate / " without "graduate"). Each cell is re-read across its whole column width.

    Any problem returns the rows unchanged.
    """
    try:
        grid = [list(row.cells) for row in table.rows]
        edges = _column_edges(table)
        if not grid or len(grid) != len(rows) or len(edges) < 2:
            return rows
        words = _words_in_table(page, table)
        if not words:
            return rows
        right = table.bbox[2]
        heights = sorted(c[0][3] - c[0][1] for c in grid if c and c[0] is not None)
        typical = heights[len(heights) // 2] if heights else 0.0
        spans, slivers = _centred_label_slivers(grid, rows)
        fragment = [r in slivers or _is_fragment(cells, typical, rows[r][0] if rows[r] else "") for r, cells in enumerate(grid)]
        owners = [r for r, f in enumerate(fragment) if not f]
        if not owners:
            return rows

        def column_range(c: int) -> Tuple[float, float]:
            return edges[c], (edges[c + 1] if c + 1 < len(edges) else right + 1.0)

        if not any(fragment):
            # step 2 only: widen each cell to its column
            out = [list(row) for row in rows]
            for r, cells in enumerate(grid):
                for c, box in enumerate(cells):
                    if box is None or c >= len(edges) or out[r][c] is None:
                        continue
                    left, edge = column_range(c)
                    inside = [w for w in words if left <= (w[0] + w[2]) / 2 < edge and box[1] <= (w[1] + w[3]) / 2 <= box[3]]
                    wide = _join_words(inside)
                    if len(re.sub(r"\s+", "", wide)) > len(re.sub(r"\s+", "", str(out[r][c] or ""))):
                        out[r][c] = wide
            return out

        # step 1: regroup every word under its label cell
        bands = [spans.get(r, (grid[r][0][1], grid[r][0][3])) for r in owners]
        centres = [(a + b) / 2 for a, b in bands]
        buckets: Dict[Tuple[int, int], List[Any]] = {}
        for w in words:
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            hit = next((i for i, (a, b) in enumerate(bands) if a <= cy <= b), None)
            if hit is None:
                hit = min(range(len(owners)), key=lambda i: abs(cy - centres[i]))
            buckets.setdefault((hit, min(_column_of(edges, cx), len(edges) - 1)), []).append(w)
        return [[_join_words(buckets.get((i, c), [])) for c in range(len(edges))] for i in range(len(owners))]
    except Exception as error:
        logger.debug("Could not repair table rows: %s", error)
        return rows


def is_text_box(table: Any) -> bool:
    """True for a paragraph in a drawn frame: most of its rows are one full-width piece of text, one per line."""
    try:
        width = table.bbox[2] - table.bbox[0]
        banners = 0
        for row in table.rows:
            filled = [c for c in row.cells if c is not None]
            if len(filled) == 1 and (filled[0][2] - filled[0][0]) >= 0.9 * width:
                banners += 1
        return table.row_count >= 3 and banners * 2 >= table.row_count
    except Exception:
        return False


def expand_merged_cells(table: Any, rows: List[List[Any]]) -> List[List[Any]]:
    """Give every cell that a merged cell covers the merged cell's text.

    PyMuPDF reports a merged cell (e.g. "20% after deductible" spanning twelve rows) once, in its top-left
    position, and leaves the others empty, so most rows would read as having no value. Here each empty slot is
    looked up by position; the cell that covers it supplies the text. A banner spanning the whole width (a
    section heading) is left alone. Any problem returns the rows unchanged.
    """
    try:
        grid = [list(row.cells) for row in table.rows]
        if not grid or len(grid) != len(rows):
            return rows
        full = max(grid, key=lambda cells: sum(1 for c in cells if c is not None))
        if any(c is None for c in full):
            return rows                                        # no row shows every column: positions are unreliable
        centres = [(c[0] + c[2]) / 2 for c in full]
        out = [list(row) for row in rows]
        for r, cells in enumerate(grid):
            top, bottom = table.rows[r].bbox[1], table.rows[r].bbox[3]
            middle = (top + bottom) / 2
            for c, box in enumerate(cells):
                if box is not None or c >= len(centres) or (out[r][c] not in (None, "")):
                    continue
                for r2 in range(r, -1, -1):                    # nearest covering cell above (or to the left)
                    hit = next((c2 for c2, b in enumerate(grid[r2])
                                if b is not None and not (r2 == r and c2 > c)
                                and b[0] <= centres[c] <= b[2] and b[1] <= middle <= b[3]), None)
                    if hit is None:
                        continue
                    b = grid[r2][hit]
                    if b[0] <= centres[0] and b[2] >= centres[-1]:
                        break                                  # a full-width banner: not a value for this column
                    out[r][c] = rows[r2][hit]
                    break
        return out
    except Exception as error:
        logger.debug("Could not expand merged cells: %s", error)
        return rows


# --------------------------------------------------------------------------- #
# Step 1-3: find tables and replace them in the PDF
# --------------------------------------------------------------------------- #
def trailing_text_rows(table: Any) -> int:
    """How many rows at the bottom are not table rows but the text that follows it: one cell spanning the full
    width and several times taller than the table's ordinary rows (the detector sometimes runs on to the page end)."""
    try:
        width = table.bbox[2] - table.bbox[0]
        rows = list(table.rows)
        banner = [len([c for c in r.cells if c is not None]) == 1 and (r.bbox[2] - r.bbox[0]) >= 0.9 * width for r in rows]
        heights = sorted(r.bbox[3] - r.bbox[1] for r, b in zip(rows, banner) if not b)
        if not heights:
            return 0
        typical = heights[len(heights) // 2]
        count = 0
        for row, is_banner in zip(reversed(rows), reversed(banner)):
            if not (is_banner and (row.bbox[3] - row.bbox[1]) > 3 * typical):
                break
            count += 1
        return count
    except Exception:
        return 0


def preprocess_pdf_tables(
    file_content: bytes, *, time_budget_seconds: Optional[float] = None
) -> Tuple[bytes, Dict[str, Dict[str, Any]]]:
    """Return (modified PDF bytes, table_map). With no tables, or on any problem, the original
    bytes and an empty map come back untouched."""
    if not enabled() or not file_content or b"%PDF-" not in file_content[:1024]:
        return file_content, {}
    try:
        import fitz
        document = fitz.open(stream=file_content, filetype="pdf")
    except Exception as error:
        logger.warning("Could not open PDF for table detection: %s", error)
        return file_content, {}

    budget = time_budget_seconds if time_budget_seconds is not None else float(os.environ.get("PDF_TABLES_TIME_BUDGET_SECONDS", "90"))
    started = time.monotonic()
    table_map: Dict[str, Dict[str, Any]] = {}
    try:
        if document.needs_pass:
            return file_content, {}
        for page in document:
            if time.monotonic() - started > budget:
                logger.warning("Table detection stopped at page %d (time budget %ss)", page.number + 1, budget)
                break
            if ocr_rules.page_has_hidden_ocr_layer(page):
                continue          # a scan: its text is an old OCR layer, so cells built from it would carry its mistakes
            try:
                found = page.find_tables().tables
            except Exception as error:
                logger.warning("Table detection failed on page %d: %s", page.number + 1, error)
                continue
            accepted = []
            for table in found:
                if is_text_box(table):
                    continue                                  # a framed paragraph, not a table
                rows = expand_merged_cells(table, repair_table_rows(page, table, table.extract()))
                bbox = fitz.Rect(table.bbox)
                tail = trailing_text_rows(table)
                if tail and len(rows) - tail >= MIN_ROWS:
                    rows = rows[: len(rows) - tail]               # the text after the table stays text
                    bbox.y1 = table.rows[len(table.rows) - tail - 1].bbox[3]
                nonempty = sum(1 for row in rows for c in row if str(c or "").strip())
                if table.row_count < MIN_ROWS or table.col_count < MIN_COLS or nonempty < MIN_NONEMPTY_CELLS:
                    continue                                  # layout boxes, rules, one-cell frames
                if looks_like_diagram(rows):
                    logger.info("Page %d: a diagram drawn with boxes is not a table; its text was left as text", page.number + 1)
                    continue
                if not cells_hold_the_words(page, bbox, rows):
                    logger.info("Page %d: a ruled box that is not a table was left as text", page.number + 1)
                    continue                                  # its text would be cut apart and lost
                markdown = table_markdown(rows)
                if markdown:
                    accepted.append((table, markdown, bbox, len(rows)))
            if not accepted:
                continue
            page_number = page.number + 1
            for _, _, bbox, _ in accepted:
                page.add_redact_annot(bbox, fill=(1, 1, 1))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
            for index, (table, markdown, rect, row_count) in enumerate(accepted, 1):
                token = PLACEHOLDER.format(page=page_number, index=index)
                baseline = min(rect.y0 + 12, rect.y1 - 2)
                page.insert_text(fitz.Point(rect.x0 + 2, baseline), token, fontsize=11, fontname="helv", color=(0, 0, 0))
                table_map[token] = {
                    "page_number": page_number, "index": index, "markdown": markdown,
                    "rows": row_count, "cols": table.col_count, "bbox": [round(v, 1) for v in rect],
                }
        if not table_map:
            return file_content, {}
        return document.tobytes(garbage=3, deflate=True), table_map
    except Exception as error:
        logger.warning("Table preprocessing failed, extracting without it: %s", error)
        return file_content, {}
    finally:
        document.close()


# --------------------------------------------------------------------------- #
# Step 5: put the tables back
# --------------------------------------------------------------------------- #
def _placeholder_pattern(page: int, index: int) -> "re.Pattern[str]":
    """Tolerant of OCR: optional angle brackets, stray spaces/line breaks, '_' / '-' / '.' between the numbers.
    The '#' is required so ordinary text such as "Table 7.1" is never mistaken for a placeholder."""
    # (?!\d) keeps 1 from matching inside 10
    return re.compile(rf"<*\s*Table\s*#\s*{page}(?!\d)[\s\-_.]+{index}(?!\d)\s*>*", re.IGNORECASE)


def restore_tables(
    parts: List[Dict[str, Any]],
    table_map: Mapping[str, Mapping[str, Any]],
    count_tokens: Callable[[str], int],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Replace placeholders in per-page parts with their markdown tables.

    Returns (parts, report). A table whose placeholder is not found is appended to its page.
    """
    report = {"tables": len(table_map), "restored": 0, "appended": 0}
    if not table_map:
        return parts, report
    by_page: Dict[int, List[Tuple[str, Mapping[str, Any]]]] = {}
    for token, entry in table_map.items():
        by_page.setdefault(int(entry["page_number"]), []).append((token, entry))

    def page_of(part: Mapping[str, Any]) -> Optional[int]:
        return (part.get("location") or {}).get("page_number")

    seen_pages = set()
    for part in parts:
        page = page_of(part)
        seen_pages.add(page)
        if page not in by_page:
            continue
        text = part["content"]
        pending = []
        for token, entry in by_page[page]:
            text, count = _placeholder_pattern(page, int(entry["index"])).subn(
                lambda _m, md=entry["markdown"]: f"\n\n{md}\n\n", text, count=1)
            if count:
                report["restored"] += 1
            else:
                pending.append(entry)
        for entry in pending:                                   # never lose a table
            text = f"{text.rstrip()}\n\n{entry['markdown']}\n"
            report["appended"] += 1
        text = re.sub(r"\n{3,}", "\n\n", text)
        part["content"], part["tokens"] = text, count_tokens(text)

    for page, entries in by_page.items():                       # a page that produced no text part at all
        if page in seen_pages:
            continue
        text = "\n\n".join(entry["markdown"] for _, entry in entries)
        parts.append({"content": text, "tokens": count_tokens(text), "location": {"page_number": page}, "canSplit": True})
        report["appended"] += len(entries)
    parts.sort(key=lambda p: (page_of(p) is None, page_of(p) or 0))
    return parts, report


# --------------------------------------------------------------------------- #
# Textract: it reads a PDF from S3, so the modified PDF has to be put somewhere
# --------------------------------------------------------------------------- #
def select_pages(pdf_bytes: bytes, pages: Sequence[int]) -> bytes:
    """A copy of the PDF holding only the given 1-based pages, in order."""
    import fitz
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        document.select([p - 1 for p in pages])
        return document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()


def prepare_textract_input(
    file_content: bytes, content_hash: str, s3: Any, work_bucket: str, key_prefix: str = WORK_PREFIX,
    pages: Optional[Sequence[int]] = None,
) -> Optional[Dict[str, Any]]:
    """Upload what Textract should read: the table-free PDF, limited to ``pages`` when given.

    Returns None when there is nothing to change (no tables and every page is wanted), so the original
    file is used. The returned dict is what ``textract_pipeline.advance(prepare_input=...)`` expects;
    ``state`` rides along on every follow-up message, so whichever invocation finishes the job can map
    the page numbers back and restore the tables.
    """
    modified, table_map = preprocess_pdf_tables(file_content)
    state: Dict[str, Any] = {}
    page_count = _page_count(modified)
    if pages and len(pages) < page_count:
        modified = select_pages(modified, pages)
        state["ocrPages"] = list(pages)
        wanted = set(pages)
        table_map = {token: entry for token, entry in table_map.items() if entry["page_number"] in wanted}
    elif not table_map:
        return None
    base = f"{key_prefix.rstrip('/')}/{content_hash}/"
    s3.put_object(Bucket=work_bucket, Key=base + "input.pdf", Body=modified, ContentType="application/pdf")
    s3.put_object(Bucket=work_bucket, Key=base + "tables.json", Body=json.dumps(table_map), ContentType="application/json")
    state.update({"tablesBucket": work_bucket, "tablesKey": base + "tables.json", "inputKey": base + "input.pdf"})
    return {"bucket": work_bucket, "key": base + "input.pdf", "state": state,
            "discard": lambda: _discard(s3, state)}          # called if the Textract job could not be started


def _page_count(pdf_bytes: bytes) -> int:
    import fitz
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return document.page_count
    finally:
        document.close()


def map_ocr_pages(parts: List[Dict[str, Any]], ocr_pages: Optional[Sequence[int]]) -> List[Dict[str, Any]]:
    """Textract numbered the pages of the page-subset copy 1..k; give them their original page numbers."""
    if not ocr_pages:
        return parts
    for part in parts:
        location = part.get("location") or {}
        number = location.get("page_number")
        if isinstance(number, int) and 1 <= number <= len(ocr_pages):
            location["page_number"] = int(ocr_pages[number - 1])
    return parts


def _discard(s3: Any, state: Mapping[str, Any]) -> None:
    for key_name in ("inputKey", "tablesKey"):
        if state.get(key_name):
            try:
                s3.delete_object(Bucket=state["tablesBucket"], Key=state[key_name])
            except Exception as error:                          # the bucket lifecycle rule is the backstop
                logger.warning("Could not delete %s: %s", state.get(key_name), error)


def finish_textract_tables(
    parts: List[Dict[str, Any]], state: Mapping[str, Any], s3: Any, count_tokens: Callable[[str], int]
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Give Textract's pages their original numbers, restore the tables, delete the working copies."""
    parts = map_ocr_pages(parts, (state or {}).get("ocrPages"))
    if not state or not state.get("tablesKey"):
        return parts, {"tables": 0, "restored": 0, "appended": 0}
    try:
        table_map = json.loads(s3.get_object(Bucket=state["tablesBucket"], Key=state["tablesKey"])["Body"].read())
        return restore_tables(parts, table_map, count_tokens)
    except Exception as error:
        logger.warning("Could not restore tables into the Textract text: %s", error)
        return parts, {"tables": 0, "restored": 0, "appended": 0, "error": 1}
    finally:
        _discard(s3, state)


def discard_textract_inputs(state: Optional[Mapping[str, Any]], s3: Any) -> None:
    """Clean up when Textract was abandoned (timeout / failure) and extraction fell back."""
    if state and state.get("tablesKey"):
        _discard(s3, state)
