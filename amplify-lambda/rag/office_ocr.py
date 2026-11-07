"""OCR the pictures inside Word and PowerPoint files, and slot the text where the picture was.

MarkItDown turns a DOCX / PPTX into markdown, tables included, but a picture becomes only a marker like
``![](data:image/png;base64...)`` (Word) or ``![Picture 2](Picture2.jpg)`` (PowerPoint): whatever text is
inside a screenshot is lost. This module applies the same rule used for PDF pages (``ocr_rules``):

    1. split the file into units  (PowerPoint slide | Excel sheet | Word page, when the file carries page
                                   breaks, otherwise the whole Word document)
    2. per unit: count its text characters and its large pictures (icons and logos are ignored)
    3. a unit that needs OCR has its large pictures stacked into ONE image per ~9000 px of height,
       so Textract bills one page per stack, not one per picture
    4. Textract's text lines are given back to the picture they came from (by position in the stack)
    5. that text replaces the picture's marker; if the markers cannot be matched with certainty, the text
       is appended to the slide / end of the document instead. Nothing is dropped.

Everything is injected (``detect_lines``) so it is testable without AWS, and any failure leaves the
MarkItDown output unchanged.
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from rag import ocr_rules

logger = logging.getLogger("rag_office_ocr")

IMAGE_MARKER = re.compile(r"!\[[^\]]*\]\([^)]*\)")
SLIDE_MARKER = re.compile(r"<!-- Slide number: (\d+) -->")
STACK_WIDTH = 1600
STACK_MAX_HEIGHT = 9000
STACK_GAP = 24
MIN_WORDS = 3                        # fewer words than this is a photo / noise, not text
LABEL = ocr_rules.PICTURE_TEXT_LABEL
OCR_WORKERS = 4                      # OCR calls in flight at once (each is a network wait, so threads overlap them)
_BUDGET_LOCK = threading.Lock()
_TRACE_LOCK = threading.Lock()
JUNK_MAX_CHARS = 2                   # a line this short that Textract is unsure about is a mark in a photo, not text
JUNK_MIN_CONFIDENCE = 85.0

# detect_lines(image_bytes) -> [(text, top_fraction_of_image_height, height_fraction[, confidence 0-100])], reading order
DetectLines = Callable[[bytes], List[Tuple[Any, ...]]]

# Diagnostics only (the walkthrough sets it): called once per OCR call with what was sent and what came back.
# {"unit": slide / sheet number or None, "image": bytes, "lines": [(text, top, height)], "pictures": count}
TRACE: Optional[Callable[[Dict[str, Any]], None]] = None


def enabled() -> bool:
    return os.environ.get("OFFICE_OCR_ENABLED", "true").lower() == "true"


def max_stacks() -> int:
    try:
        return max(0, int(os.environ.get("OFFICE_OCR_MAX_STACKS", 30)))
    except (TypeError, ValueError):
        return 30


@dataclass
class Picture:
    blob: bytes
    width: int
    height: int
    fraction: Optional[float]            # share of the slide it covers (None when unknown, e.g. Word)
    decorative: bool = False             # the same image on many slides: a background or logo, not content

    @property
    def big(self) -> bool:
        return not self.decorative and ocr_rules.is_big_visual(self.width, self.height, self.fraction)


@dataclass
class Unit:
    number: int
    text_chars: int
    pictures: List[Picture]
    decision: Optional[ocr_rules.UnitDecision] = None
    fills: List[Picture] = field(default_factory=list)      # pictures used as a shape's fill: no markdown marker

    def big_count(self) -> int:
        return sum(1 for p in self.pictures if p.big) + sum(1 for p in self.fills if p.big)


@dataclass
class OfficeOcrReport:
    kind: str
    unit_source: str
    units: List[ocr_rules.UnitDecision] = field(default_factory=list)
    pictures: int = 0
    pictures_read: int = 0
    billed_pages: int = 0
    placed_in_order: int = 0
    appended: int = 0
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reading the file
# --------------------------------------------------------------------------- #
def _image_size(blob: bytes) -> Tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(blob)) as image:
        return image.size


DECORATIVE_MIN_SLIDES = 3            # an image on at least this many slides ...
BACKGROUND_FILL_FRACTION = 1.0       # a picture fill covering more than the whole slide is a background
DECORATIVE_MIN_SHARE = 0.25          # ... and on at least this share of all slides is a background / logo


def collect_pptx(file_content: bytes) -> List[Unit]:
    """One unit per slide. ``pictures`` are the picture shapes, in the same order MarkItDown writes their markers;
    ``fills`` are pictures used as a shape's fill (common in Canva / template exports), which have no marker.
    An image that appears on many slides is flagged ``decorative`` and never counts as content."""
    import hashlib
    import math
    import pptx
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    presentation = pptx.Presentation(io.BytesIO(file_content))
    slide_area = float((presentation.slide_width or 1) * (presentation.slide_height or 1))
    units = []

    def picture_of(shape) -> Optional[Picture]:
        is_picture = shape.shape_type == MSO_SHAPE_TYPE.PICTURE or (
            shape.shape_type == MSO_SHAPE_TYPE.PLACEHOLDER and hasattr(shape, "image"))
        if not is_picture:
            return None
        try:
            blob = shape.image.blob
            width, height = _image_size(blob)
        except Exception:
            return Picture(b"", 0, 0, None)                  # keeps the order; never OCR'd
        return Picture(blob, width, height, (shape.width * shape.height) / slide_area if slide_area else None)

    def fill_of(shape, slide_part) -> Optional[Picture]:
        try:
            blips = shape._element.xpath("./p:spPr/a:blipFill/a:blip")
            if not blips:
                return None
            blob = slide_part.related_part(blips[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")).blob
            width, height = _image_size(blob)
            area = (shape.width or 0) * (shape.height or 0)
            fraction = area / slide_area if slide_area else None
            # a fill larger than the slide itself bleeds off its edges: that is a background photo, not content
            return Picture(blob, width, height, fraction, decorative=bool(fraction and fraction > BACKGROUND_FILL_FRACTION))
        except Exception:
            return None                                       # SVG, linked or unreadable image: nothing to OCR

    def walk(shape, found, fills, slide_part):
        picture = picture_of(shape)
        if picture is not None:
            found.append(picture)
        else:
            fill = fill_of(shape, slide_part)
            if fill is not None:
                fills.append(fill)
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for sub in shape.shapes:
                walk(sub, found, fills, slide_part)

    for number, slide in enumerate(presentation.slides, 1):
        found: List[Picture] = []
        fills: List[Picture] = []
        for shape in slide.shapes:
            walk(shape, found, fills, slide.part)
        unit = Unit(number, 0, found)
        unit.fills = fills
        units.append(unit)

    slides_with: Dict[str, set] = {}
    for unit in units:
        for picture in unit.pictures + unit.fills:
            if picture.blob:
                slides_with.setdefault(hashlib.sha1(picture.blob).hexdigest(), set()).add(unit.number)
    needed = max(DECORATIVE_MIN_SLIDES, math.ceil(DECORATIVE_MIN_SHARE * len(units)))
    repeated = {digest for digest, where in slides_with.items() if len(where) >= needed}
    for unit in units:
        for picture in unit.pictures + unit.fills:
            if picture.blob and hashlib.sha1(picture.blob).hexdigest() in repeated:
                picture.decorative = True
    return units


def collect_docx(file_content: bytes) -> Tuple[List[Unit], str, List[Picture]]:
    """Units by Word page break (``lastRenderedPageBreak`` / explicit page break), else one unit.

    Returns (units, unit_source, pictures_in_document_order).
    """
    import docx

    document = docx.Document(io.BytesIO(file_content))
    relations = document.part.rels
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

    pages: List[Dict[str, Any]] = [{"chars": 0, "pictures": []}]
    breaks, ordered = 0, []

    def in_fallback(element) -> bool:
        parent = element.getparent()
        while parent is not None:
            if parent.tag == MC_FALLBACK:
                return True
            parent = parent.getparent()
        return False

    for element in document.element.body.iter():
        if element.tag == f"{W}lastRenderedPageBreak" or (
                element.tag == f"{W}br" and element.get(f"{W}type") == "page"):
            if not in_fallback(element):
                breaks += 1
                pages.append({"chars": 0, "pictures": []})
        elif element.tag == f"{W}t" and element.text:
            pages[-1]["chars"] += len(element.text.strip())
        elif element.tag == f"{A}blip":
            if in_fallback(element):
                continue                                         # Word stores shapes twice (Choice + Fallback)
            rid = element.get(f"{R}embed")
            picture = Picture(b"", 0, 0, None)
            try:
                blob = relations[rid].target_part.blob
                width, height = _image_size(blob)
                picture = Picture(blob, width, height, None)
            except Exception:
                pass
            pages[-1]["pictures"].append(picture)
            ordered.append(picture)

    if breaks == 0:
        total = Unit(1, pages[0]["chars"], pages[0]["pictures"])
        return [total], "no page breaks saved in this file: the whole document is one unit", ordered
    units = [Unit(i, p["chars"], p["pictures"]) for i, p in enumerate(pages, 1)]
    return units, f"{breaks} page break(s) saved by Word: {len(units)} pages", ordered


def collect_xlsx(file_content: bytes) -> List[Tuple[str, List[Picture]]]:
    """(sheet name, pictures) per sheet, in sheet order."""
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
    sheets = []
    for sheet in workbook.worksheets:
        found: List[Picture] = []
        for image in getattr(sheet, "_images", []):
            try:
                blob = image._data()
                width, height = _image_size(blob)
                found.append(Picture(blob, width, height, None))
            except Exception:
                found.append(Picture(b"", 0, 0, None))
        sheets.append((sheet.title, found))
    return sheets


# --------------------------------------------------------------------------- #
# OCR: stack pictures, read once, give each its own lines back
# --------------------------------------------------------------------------- #
def _stacks(pictures: Sequence[Picture], indexes: Sequence[int]) -> List[List[int]]:
    """Group picture indexes into stacks no taller than STACK_MAX_HEIGHT."""
    stacks, current, height = [], [], 0
    for index in indexes:
        picture = pictures[index]
        scaled = round(picture.height * min(1.0, STACK_WIDTH / max(picture.width, 1)))
        if current and height + scaled + STACK_GAP > STACK_MAX_HEIGHT:
            stacks.append(current)
            current, height = [], 0
        current.append(index)
        height += scaled + STACK_GAP
    if current:
        stacks.append(current)
    return stacks


def _render_stack(stack: Sequence[Picture]) -> Tuple[bytes, List[Tuple[int, int]]]:
    """White canvas with the pictures one under another. Returns (png/jpeg bytes, [(top, bottom) per picture])."""
    from PIL import Image

    images, bands, y = [], [], 0
    width = min(STACK_WIDTH, max(p.width for p in stack))
    for picture in stack:
        with Image.open(io.BytesIO(picture.blob)) as source:
            image = source.convert("RGB")
        if image.width > width:
            image = image.resize((width, max(1, round(image.height * width / image.width))))
        images.append(image)
        bands.append((y, y + image.height))
        y += image.height + STACK_GAP
    canvas = Image.new("RGB", (width, max(y - STACK_GAP, 1)), "white")
    for image, (top, _) in zip(images, bands):
        canvas.paste(image, (0, top))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    if buffer.tell() > 9 * 1024 * 1024:                          # Textract's inline limit is 10 MB
        buffer = io.BytesIO()
        canvas.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue(), bands


def read_pictures(pictures: Sequence[Picture], detect_lines: DetectLines, budget: List[int], unit: Optional[int] = None) -> Dict[int, str]:
    """OCR the given pictures (indexes into ``pictures``). Returns {index: text} for pictures that hold real text.

    ``budget`` is a one-element list: the number of stacks (billed pages) still allowed for this document.
    """
    result: Dict[int, str] = {}
    usable = [i for i, p in enumerate(pictures) if p.blob]
    pending = _stacks(pictures, usable)
    while pending:
        batch: List[Tuple[List[int], bytes, List[Tuple[int, int]]]] = []
        while pending and len(batch) < OCR_WORKERS:
            with _BUDGET_LOCK:
                allowed = budget[0] > 0
                if allowed:
                    budget[0] -= 1
            if not allowed:
                logger.warning("Office OCR stack limit reached; remaining pictures keep their markers")
                pending = []
                break
            members = pending.pop(0)
            image_bytes, bands = _render_stack([pictures[i] for i in members])
            batch.append((members, image_bytes, bands))
        if not batch:
            break
        if len(batch) == 1:
            detected = [detect_lines(batch[0][1])]
        else:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:      # the Textract calls overlap; order is kept
                detected = list(pool.map(detect_lines, [image for _, image, _ in batch]))
        for (members, image_bytes, bands), lines in zip(batch, detected):
            total_height = max(bands[-1][1], 1)
            lines_by_picture: Dict[int, List[str]] = {i: [] for i in range(len(members))}
            if TRACE is not None:
                try:
                    with _TRACE_LOCK:
                        TRACE({"unit": unit, "image": image_bytes, "lines": list(lines), "pictures": len(members)})
                except Exception:
                    pass                                      # a diagnostics hook must never affect ingestion
            for line in lines:
                text, top, height = line[0], line[1], line[2]
                if len(line) > 3 and len(str(text).strip()) <= JUNK_MAX_CHARS and line[3] < JUNK_MIN_CONFIDENCE:
                    continue                                  # an unsure 1-2 character read: photo noise
                centre = (top + height / 2) * total_height
                for position, (band_top, band_bottom) in enumerate(bands):
                    if band_top - STACK_GAP / 2 <= centre <= band_bottom + STACK_GAP / 2:
                        lines_by_picture[position].append(text)
                        break
            for position, found in lines_by_picture.items():
                text = "\n".join(line for line in found if line.strip()).strip()
                if len(text.split()) >= MIN_WORDS:
                    result[members[position]] = text
    return result


# --------------------------------------------------------------------------- #
# One picture sheet per slide: the image Nova embeds for an Office slide
# --------------------------------------------------------------------------- #
SHEET_SIDE = 1568                    # longest side of a sheet in pixels


def _render_sheet(pictures: Sequence[Picture]) -> bytes:
    """The pictures laid out in a grid on a white canvas no larger than SHEET_SIDE x SHEET_SIDE (PNG)."""
    import math
    from PIL import Image

    count = len(pictures)
    columns = max(1, math.ceil(math.sqrt(count)))
    rows = math.ceil(count / columns)
    cell_w, cell_h = SHEET_SIDE // columns, SHEET_SIDE // rows
    canvas = Image.new("RGB", (cell_w * columns, cell_h * rows), "white")
    for index, picture in enumerate(pictures):
        with Image.open(io.BytesIO(picture.blob)) as source:
            image = source.convert("RGB")
        image.thumbnail((cell_w - 8, cell_h - 8))
        x, y = (index % columns) * cell_w, (index // columns) * cell_h
        canvas.paste(image, (x + (cell_w - image.width) // 2, y + (cell_h - image.height) // 2))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def slide_picture_sheets(file_content: bytes, max_slides: int = 200, time_budget_seconds: float = 20.0) -> Dict[int, bytes]:
    """{slide number: PNG bytes} for every slide that carries real picture content (large, not a repeated
    background / logo). Slides with no such picture are absent: they keep text-only vectors. Never raises."""
    try:
        import time
        started = time.monotonic()
        sheets: Dict[int, bytes] = {}
        for unit in collect_pptx(file_content):
            if len(sheets) >= max_slides or time.monotonic() - started > time_budget_seconds:
                break
            usable = [p for p in unit.pictures + unit.fills if p.big and p.blob]
            if usable:
                sheets[unit.number] = _render_sheet(usable)
        return sheets
    except Exception as error:
        logger.warning("Could not build slide picture sheets: %s", error)
        return {}


# --------------------------------------------------------------------------- #
# Slotting the text into MarkItDown's markdown
# --------------------------------------------------------------------------- #
def _block(text: str) -> str:
    return f"\n\n{LABEL}\n{text}\n\n"


def _slot_in_order(section: str, texts: Dict[int, str], picture_count: int) -> Tuple[str, int, int]:
    """Replace the n-th image marker with the n-th picture's text. Returns (text, placed, appended)."""
    markers = list(IMAGE_MARKER.finditer(section))
    if len(markers) == picture_count:
        out, last, placed = [], 0, 0
        for index, marker in enumerate(markers):
            out.append(section[last:marker.start()])
            if index in texts:
                out.append(_block(texts[index]))
                placed += 1
            else:
                out.append(marker.group(0))
            last = marker.end()
        out.append(section[last:])
        return "".join(out), placed, 0
    if not texts:
        return section, 0, 0
    extra = "".join(_block(texts[i]) for i in sorted(texts))      # counts differ: keep the text, add it at the end
    return section.rstrip() + extra, 0, len(texts)


def _decide(units: List[Unit]) -> None:
    for unit in units:
        unit.decision = ocr_rules.decide_unit(unit.number, unit.text_chars, unit.big_count())


def enrich_pptx(markdown: str, file_content: bytes, detect_lines: DetectLines,
                prefetch: Optional["Prefetch"] = None) -> Tuple[str, OfficeOcrReport]:
    units = collect_pptx(file_content)
    report = OfficeOcrReport("pptx", f"{len(units)} slides")
    sections = SLIDE_MARKER.split(markdown)             # [preamble, "1", text1, "2", text2, ...]
    by_slide = {int(sections[i]): sections[i + 1] for i in range(1, len(sections) - 1, 2)}
    for unit in units:
        body = by_slide.get(unit.number, "")
        text_only = IMAGE_MARKER.sub("", body.split("### Notes:")[0])
        unit.text_chars = len("".join(line for line in text_only.splitlines() if not line.lstrip().startswith("|")).strip())
    _decide(units)
    report.units = [u.decision for u in units]
    report.pictures = sum(len(u.pictures) + len(u.fills) for u in units)
    ready = prefetch.wait() if prefetch is not None else {}           # slides already read while the text was converted
    budget = [max(0, max_stacks() - (prefetch.billed if prefetch is not None else 0))]
    start_budget = budget[0]
    report.billed_pages += prefetch.billed if prefetch is not None else 0
    jobs = []
    for unit in units:
        if not unit.decision.ocr or unit.number not in by_slide:
            continue
        marker_idx = [i for i, p in enumerate(unit.pictures) if p.big]
        fill_idx = [j for j, p in enumerate(unit.fills) if p.big]
        # one OCR pass for the slide: the marker pictures first, then the fills, stacked together
        jobs.append((unit, marker_idx, [unit.pictures[i] for i in marker_idx] + [unit.fills[j] for j in fill_idx]))
    todo = [job for job in jobs if job[0].number not in ready]
    if len(todo) > 1:
        with ThreadPoolExecutor(max_workers=OCR_WORKERS) as pool:         # slides are read at the same time
            fresh = list(pool.map(lambda job: read_pictures(job[2], detect_lines, budget, job[0].number), todo))
    else:
        fresh = [read_pictures(job[2], detect_lines, budget, job[0].number) for job in todo]
    fresh_by_unit = {job[0].number: texts for job, texts in zip(todo, fresh)}
    results = [ready[job[0].number] if job[0].number in ready else fresh_by_unit[job[0].number] for job in jobs]
    report.billed_pages += start_budget - budget[0]
    for (unit, marker_idx, _), texts in zip(jobs, results):
        report.pictures_read += len(texts)
        marker_texts = {marker_idx[k]: v for k, v in texts.items() if k < len(marker_idx)}
        fill_texts = [v for k, v in sorted(texts.items()) if k >= len(marker_idx)]
        by_slide[unit.number], placed, appended = _slot_in_order(by_slide[unit.number], marker_texts, len(unit.pictures))
        if fill_texts:                                       # no marker to replace: the text is added to the slide
            by_slide[unit.number] = by_slide[unit.number].rstrip() + "".join(_block(t) for t in fill_texts)
            appended += len(fill_texts)
        report.placed_in_order += placed
        report.appended += appended
    rebuilt = sections[0] + "".join(f"<!-- Slide number: {n} -->{by_slide[n]}" for n in sorted(by_slide))
    return rebuilt, report


def enrich_docx(markdown: str, file_content: bytes, detect_lines: DetectLines) -> Tuple[str, OfficeOcrReport]:
    units, source, ordered = collect_docx(file_content)
    report = OfficeOcrReport("docx", source)
    _decide(units)
    report.units = [u.decision for u in units]
    report.pictures = len(ordered)
    flagged = {id(p) for u in units if u.decision.ocr for p in u.pictures if p.big}
    indexes = [i for i, p in enumerate(ordered) if id(p) in flagged]
    if not indexes:
        return markdown, report
    budget = [max_stacks()]
    texts = read_pictures([ordered[i] for i in indexes], detect_lines, budget)
    report.billed_pages = max_stacks() - budget[0]
    texts = {indexes[k]: v for k, v in texts.items()}
    report.pictures_read = len(texts)
    markdown, placed, appended = _slot_in_order(markdown, texts, len(ordered))
    report.placed_in_order, report.appended = placed, appended
    return markdown, report


SHEET_HEADING = re.compile(r"(?m)^## (.+)$")


def enrich_xlsx(markdown: str, file_content: bytes, detect_lines: DetectLines) -> Tuple[str, OfficeOcrReport]:
    """Excel: cell text is already exact, so only pictures on a sheet matter. A sheet has no marker in the
    markdown, so the picture text is added at the end of that sheet's section."""
    sheets = collect_xlsx(file_content)
    report = OfficeOcrReport("xlsx", f"{len(sheets)} sheets")
    pieces = SHEET_HEADING.split(markdown)              # [preamble, name1, body1, name2, body2, ...]
    bodies = {pieces[i]: pieces[i + 1] for i in range(1, len(pieces) - 1, 2)}
    units = []
    for number, (name, pictures) in enumerate(sheets, 1):
        body = bodies.get(name, "")
        cells = "".join(re.findall(r"[^|\s-][^|]*", body))
        units.append(Unit(number, len(cells.strip()), pictures))
    _decide(units)
    report.units = [u.decision for u in units]
    report.pictures = sum(len(u.pictures) for u in units)
    budget = [max_stacks()]
    for unit, (name, _) in zip(units, sheets):
        if not unit.decision.ocr or name not in bodies:
            continue
        indexes = [i for i, p in enumerate(unit.pictures) if p.big]
        before = budget[0]
        texts = read_pictures([unit.pictures[i] for i in indexes], detect_lines, budget, unit.number)
        report.billed_pages += before - budget[0]
        report.pictures_read += len(texts)
        if texts:
            bodies[name] = bodies[name].rstrip() + "".join(_block(texts[k]) for k in sorted(texts))
            report.appended += len(texts)
    rebuilt = pieces[0] + "".join(f"## {name}{bodies[name]}" for name in [pieces[i] for i in range(1, len(pieces) - 1, 2)])
    return rebuilt, report


class Prefetch:
    """Starts reading a deck's pictures while the text is still being converted.

    Only slides whose pictures settle the OCR decision on their own (the "2+ large pictures" rule, which does not
    depend on the slide's text) are read ahead, so nothing is read that the decision would then throw away.
    The slides that depend on the converted text (one picture and little text) are read afterwards as before.
    """

    def __init__(self, file_content: bytes, detect_lines: DetectLines):
        self.texts: Dict[int, Dict[int, str]] = {}
        self.billed = 0
        self._thread = threading.Thread(target=self._run, args=(file_content, detect_lines), daemon=True)
        self._thread.start()

    def _run(self, file_content: bytes, detect_lines: DetectLines) -> None:
        try:
            units = [u for u in collect_pptx(file_content) if u.big_count() >= ocr_rules.min_visuals()]
            budget = [max_stacks()]
            jobs = []
            for unit in units:
                marker_idx = [i for i, p in enumerate(unit.pictures) if p.big]
                fill_idx = [j for j, p in enumerate(unit.fills) if p.big]
                jobs.append((unit, [unit.pictures[i] for i in marker_idx] + [unit.fills[j] for j in fill_idx]))
            if jobs:
                with ThreadPoolExecutor(max_workers=OCR_WORKERS) as pool:
                    results = list(pool.map(lambda job: read_pictures(job[1], detect_lines, budget, job[0].number), jobs))
                self.texts = {job[0].number: texts for job, texts in zip(jobs, results)}
            self.billed = max_stacks() - budget[0]
        except Exception as error:                     # the normal path then reads these slides itself
            logger.warning("Reading deck pictures ahead failed, they will be read after the text: %s", error)
            self.texts, self.billed = {}, 0

    def wait(self) -> Dict[int, Dict[int, str]]:
        self._thread.join()
        return self.texts


def start_prefetch(extension: str, file_content: bytes, detect_lines: DetectLines) -> Optional[Prefetch]:
    """Begin reading pictures ahead of the text conversion (PowerPoint only: a deck is where the OCR time is)."""
    if (extension or "").lower() != ".pptx" or not enabled():
        return None
    return Prefetch(file_content, detect_lines)


def enrich_markdown(
    markdown: str, extension: str, file_content: bytes, detect_lines: DetectLines, prefetch: Optional[Prefetch] = None
) -> Tuple[str, Optional[OfficeOcrReport]]:
    """Entry point: returns (markdown, report). Any problem returns the markdown untouched."""
    extension = (extension or "").lower()
    if not enabled() or extension not in (".pptx", ".docx", ".xlsx") or not markdown:
        return markdown, None
    try:
        if extension == ".pptx":
            return enrich_pptx(markdown, file_content, detect_lines, prefetch)
        if extension == ".xlsx":
            return enrich_xlsx(markdown, file_content, detect_lines)
        return enrich_docx(markdown, file_content, detect_lines)
    except Exception as error:
        logger.warning("Office picture OCR failed for %s, keeping the plain text: %s", extension, error)
        return markdown, None


def textract_detect_lines(client: Any) -> DetectLines:
    """Adapter: Textract's synchronous DetectDocumentText on image bytes -> (text, top, height, confidence) lines."""
    def detect(image_bytes: bytes) -> List[Tuple[str, float, float]]:
        response = client.detect_document_text(Document={"Bytes": image_bytes})
        lines = []
        for block in response.get("Blocks", []):
            if block.get("BlockType") == "LINE" and block.get("Text"):
                box = (block.get("Geometry") or {}).get("BoundingBox") or {}
                lines.append((block["Text"], float(box.get("Top", 0.0)), float(box.get("Height", 0.0)),
                              round(float(block.get("Confidence", 0.0)), 1)))
        return lines
    return detect


def describe(report: OfficeOcrReport) -> List[str]:
    """Plain-English lines for logs and the walkthrough."""
    lines = [f"{report.kind}: {report.unit_source}"]
    for unit in report.units:
        lines.append(f"  unit {unit.unit}: {unit.text_chars} chars, {unit.big_visuals} large picture(s) -> "
                     f"{'OCR' if unit.ocr else 'text'} ({unit.reason})")
    lines.append(f"  pictures: {report.pictures}; with readable text: {report.pictures_read}; "
                 f"Textract pages billed: {report.billed_pages}; placed at their marker: {report.placed_in_order}; "
                 f"appended instead: {report.appended}")
    return lines
