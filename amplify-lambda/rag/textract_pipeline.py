"""Send only the PDF pages that need OCR (scans, picture-heavy pages) to asynchronous Textract.

The decision is made page by page (``plan_pdf_ocr`` + ``ocr_rules``): a copy of the PDF holding just the
flagged pages, with tables replaced by placeholders, is what Textract reads; its pages are then given
their original numbers and merged into the text-layer text (``merge_ocr_pages``). The notes below about
whole-document classification describe ``classify_pdf``, kept for the all-pages case and its tests.

Why: scanned PDFs have no text layer, and PDFs full of images hold text inside
the images. Textract OCRs them out of band; this module only starts the job and
later collects results, never waiting inside Lambda.

Scope is deliberately narrow and cost-driven:
  * A text-layer PDF is extracted for free in well under a second (1000 pages
    ~0.4 s), so running it through Textract would only add cost. Never OCR it.
  * The frontend gives up on an upload after 120 s and then deletes the file, so
    OCR is only attempted for PDFs short enough to finish inside that window
    (TEXTRACT_MAX_PAGES_INTERACTIVE, default 500), polled quickly, with a hard
    wait limit (TEXTRACT_MAX_WAIT_SECONDS, default 600 s). Anything else uses normal extraction.

Flow (all driven by messages on the existing document-index queue, so there is
no new queue, Lambda, SNS topic, or IAM role to provision):

    1st message   classify -> start job -> re-queue "poll" message (delayed)
    poll message  job IN_PROGRESS -> re-queue again (backoff)
                  job SUCCEEDED   -> read result pages in time-bounded slices;
                                     if time runs out, persist progress to S3
                                     and re-queue a "continue" message
                  job FAILED      -> fall back to normal extraction
    final slice   returns page parts; the caller runs the normal tail
                  (save text, manifest, chunk queue) unchanged

Textract limits (async): PDF/TIFF up to 500 MB and 3,000 pages; password-
protected/XFA PDFs are unsupported (the job fails -> normal extraction).
All AWS clients are injected so the logic is testable without AWS.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from pycommon.rag import timing
from rag import ocr_rules

logger = logging.getLogger("rag_textract")

TEXTRACT_MAX_PAGES = 3000
TEXTRACT_MAX_BYTES = 500 * 1024 * 1024
PARTIAL_PREFIX = "textract-partial"

OUTCOME_PARTS = "parts"        # results ready; continue with these page parts
OUTCOME_DEFERRED = "deferred"  # a follow-up message was queued; stop this one
OUTCOME_NORMAL = "normal"      # use the normal extractor
OUTCOME_TOO_BIG = "too_big"    # more pages need OCR than can be read inside the upload window: refuse the file
OUTCOME_FAILED = "failed"      # OCR was the only way to read this document and it did not complete: refuse the file


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def textract_enabled() -> bool:
    return os.environ.get("TEXTRACT_ENABLED", "true").lower() == "true"


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TextractDecision:
    use_textract: bool
    reason: str
    page_count: int = 0
    size_bytes: int = 0
    chars_per_page: float = 0.0
    images_per_page: float = 0.0


def profile_pdf(file_content: bytes, sample_pages: int = 10) -> Dict[str, float]:
    """Cheaply profile a PDF: page count, text density and image density.

    Samples up to ``sample_pages`` pages spread across the document. Raises on
    corrupt or encrypted PDFs (caller treats that as "use the normal path").
    """
    import fitz  # PyMuPDF (already a dependency for page rendering)

    document = fitz.open(stream=file_content, filetype="pdf")
    try:
        if document.needs_pass:
            raise ValueError("PDF is password protected")
        count = document.page_count
        if count == 0:
            return {"page_count": 0, "chars_per_page": 0.0, "images_per_page": 0.0}
        wanted = min(sample_pages, count)
        indexes = sorted({round(i * (count - 1) / max(wanted - 1, 1)) for i in range(wanted)})
        chars = images = 0
        for index in indexes:
            page = document.load_page(index)
            chars += len(page.get_text().strip())
            images += len(page.get_images())
        return {
            "page_count": count,
            "chars_per_page": chars / len(indexes),
            "images_per_page": images / len(indexes),
        }
    finally:
        document.close()


def classify_pdf(
    profile: Mapping[str, float],
    size_bytes: int,
    *,
    max_pages: Optional[int] = None,
    min_chars_per_page: Optional[int] = None,
    min_images_per_page: Optional[float] = None,
) -> TextractDecision:
    """Decide whether a PDF should be OCR'd by Textract (thresholds are env-tunable).

    OCR only helps when the text layer is missing (scans) or the text lives in
    images; and only pays off when it can finish inside the upload window.
    """
    max_pages = max_pages if max_pages is not None else _env_int("TEXTRACT_MAX_PAGES_INTERACTIVE", 500)
    min_chars = min_chars_per_page if min_chars_per_page is not None else _env_int("TEXTRACT_MIN_CHARS_PER_PAGE", 100)
    min_images = min_images_per_page if min_images_per_page is not None else float(os.environ.get("TEXTRACT_MIN_IMAGES_PER_PAGE", 2))

    pages = int(profile.get("page_count", 0))
    chars = float(profile.get("chars_per_page", 0.0))
    images = float(profile.get("images_per_page", 0.0))

    def decision(use: bool, reason: str) -> TextractDecision:
        return TextractDecision(use, reason, pages, size_bytes, chars, images)

    if pages < 1:
        return decision(False, "no pages")
    if pages > TEXTRACT_MAX_PAGES or size_bytes > TEXTRACT_MAX_BYTES:
        return decision(False, "exceeds Textract async limits (3000 pages / 500 MB)")
    if chars >= min_chars and images < min_images:
        return decision(False, "text PDF: extracted directly, no OCR needed")
    if pages > max_pages:
        return decision(False, f"needs OCR but exceeds {max_pages} pages, too long to finish in the upload window")
    if chars < min_chars:
        return decision(True, "scanned or no text layer")
    return decision(True, "image-heavy")


# --------------------------------------------------------------------------- #
# Per-page planning: which pages need OCR (the rule is shared with Office files in ocr_rules)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PdfOcrPlan:
    page_count: int
    units: Tuple[ocr_rules.UnitDecision, ...]      # one decision per page, in page order
    ocr_pages: Tuple[int, ...]                      # 1-based page numbers that go to Textract
    reason: str
    too_big: bool = False

    @property
    def everything(self) -> bool:
        return bool(self.ocr_pages) and len(self.ocr_pages) == self.page_count

    @property
    def only_readable_with_ocr(self) -> bool:
        """At least half the pages are scans (no usable text layer, or an old hidden OCR layer): without OCR the
        stored text would be empty or poor, so a failed OCR must fail the file and not fall back silently."""
        scans = sum(1 for u in self.units if u.ocr and (u.hidden_layer or u.text_chars < ocr_rules.min_chars()))
        return bool(self.units) and scans * 2 >= len(self.units)


def profile_pdf_pages(file_content: bytes) -> List[Dict[str, Any]]:
    """Cheap profile of EVERY page: characters of text and number of large pictures.

    Raises on corrupt or password-protected PDFs (the caller then uses the normal path).
    """
    import fitz  # PyMuPDF

    document = fitz.open(stream=file_content, filetype="pdf")
    try:
        if document.needs_pass:
            raise ValueError("PDF is password protected")
        rows = []
        for page in document:
            page_area = max(page.rect.width * page.rect.height, 1.0)
            big = bare = 0
            words = None
            for info in page.get_image_info():
                rect = info.get("bbox") or (0, 0, 0, 0)
                placed = max(rect[2] - rect[0], 0) * max(rect[3] - rect[1], 0)
                if ocr_rules.is_big_visual(info.get("width", 0), info.get("height", 0), placed / page_area):
                    big += 1
                    if placed / page_area >= ocr_rules.BARE_PICTURE_MIN_FRACTION:
                        if words is None:
                            words = page.get_text("words")
                        on_picture = sum(1 for w in words if rect[0] <= (w[0] + w[2]) / 2 <= rect[2] and rect[1] <= (w[1] + w[3]) / 2 <= rect[3])
                        if on_picture <= ocr_rules.BARE_PICTURE_MAX_WORDS:
                            bare += 1
            chars = len(page.get_text().strip())
            rows.append({"page": page.number + 1, "text_chars": chars, "big_visuals": big, "bare_visuals": bare,
                         "hidden_layer": chars > 0 and ocr_rules.page_has_hidden_ocr_layer(page)})
        return rows
    finally:
        document.close()


def plan_pdf_ocr(
    rows: List[Mapping[str, Any]],
    size_bytes: int,
    *,
    table_pages: Optional[Mapping[int, int]] = None,
    max_pages: Optional[int] = None,
) -> PdfOcrPlan:
    """Decide page by page. ``table_pages`` (page -> table count) is only for the report."""
    max_pages = max_pages if max_pages is not None else _env_int("TEXTRACT_MAX_PAGES_INTERACTIVE", 500)
    table_pages = table_pages or {}
    units = tuple(
        ocr_rules.decide_unit(int(r["page"]), int(r["text_chars"]), int(r["big_visuals"]),
                              int(table_pages.get(int(r["page"]), 0)), bool(r.get("hidden_layer", False)), int(r.get("bare_visuals", 0)))
        for r in rows
    )
    flagged = tuple(u.unit for u in units if u.ocr)
    count = len(units)
    if count < 1:
        return PdfOcrPlan(0, units, (), "no pages")
    if count > TEXTRACT_MAX_PAGES or size_bytes > TEXTRACT_MAX_BYTES:
        return PdfOcrPlan(count, units, (), "exceeds Textract async limits (3000 pages / 500 MB)")
    if not flagged:
        return PdfOcrPlan(count, units, (), "no page needs OCR: the text layer is used for every page")
    if len(flagged) > max_pages:
        return PdfOcrPlan(
            count, units, flagged,
            f"{len(flagged)} pages need OCR, more than the {max_pages} pages that can be read inside the upload window",
            too_big=True,
        )
    return PdfOcrPlan(count, units, flagged, f"{len(flagged)} of {count} pages need OCR")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def lines_missing_from(layer_text: str, ocr_text: str) -> List[str]:
    """OCR lines whose words are not already in the text layer: text that only exists inside pictures.

    Tolerates small OCR slips (a line counts as present at 85% similarity) and ignores table rows and
    placeholders (tables come from the PDF's own text), stray one- or two-character fragments (logo letters,
    icons) and duplicates.
    """
    layer_lines = [n for n in (_norm(l) for l in layer_text.splitlines()) if n]
    joined = " " + " ".join(layer_lines) + " "
    missing, seen = [], set()
    for line in ocr_text.splitlines():
        stripped = line.strip()
        key = _norm(stripped)
        if len(key.replace(" ", "")) < 3 or stripped.startswith("|") or stripped.startswith("<Table#") or key in seen:
            continue
        seen.add(key)
        if f" {key} " in joined or difflib.get_close_matches(key, layer_lines, n=1, cutoff=0.85):
            continue
        missing.append(stripped)
    return missing


def merge_ocr_pages(
    text_layer_parts: List[Dict[str, Any]],
    ocr_parts: List[Dict[str, Any]],
    count_tokens: Optional[Callable[[str], int]] = None,
    report: Optional[Dict[int, Dict[str, Any]]] = None,
    replace_pages: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Combine the PDF's text layer with Textract's text, page by page.

    - A page whose text layer is (almost) empty, a scan, takes Textract's text whole.
    - A page with a real text layer keeps it, in its reading order, and gains only the lines Textract found
      that the text layer lacks (text inside pictures), under a "[Text in picture]" heading. This keeps the
      accurate text layer and never lets OCR scramble columns or double up logos.
    - Pages in ``replace_pages`` (scans carrying an old hidden OCR layer) also take Textract's text whole.
    - Every other page is untouched. ``report`` (optional) is filled with what happened to each OCR page.
    """
    replace_pages = set(replace_pages or ())
    def page_of(part: Mapping[str, Any]) -> Optional[int]:
        return (part.get("location") or {}).get("page_number")

    layer_by_page = {page_of(p): p for p in text_layer_parts}
    ocr_pages = {page_of(p) for p in ocr_parts}
    merged = [p for p in text_layer_parts if page_of(p) not in ocr_pages]
    scan_limit = ocr_rules.min_chars()
    for ocr in ocr_parts:
        number = page_of(ocr)
        layer = layer_by_page.get(number)
        layer_text = (layer or {}).get("content", "")
        if layer is None or len(layer_text.strip()) < scan_limit or number in replace_pages:
            merged.append(ocr)
            outcome = {"mode": "scan: Textract text used whole", "added_lines": len(ocr["content"].splitlines())}
        else:
            extra = lines_missing_from(layer_text, ocr["content"])
            part = dict(layer)
            if extra:
                part["content"] = f"{layer_text.rstrip()}\n\n{ocr_rules.PICTURE_TEXT_LABEL}\n" + "\n".join(extra)
                if count_tokens:
                    part["tokens"] = count_tokens(part["content"])
            merged.append(part)
            outcome = {"mode": "text layer kept; Textract added the picture-only lines", "added_lines": len(extra)}
        if report is not None:
            report[number] = outcome
    merged.sort(key=lambda p: (page_of(p) is None, page_of(p) or 0))
    return merged


# --------------------------------------------------------------------------- #
# Textract calls
# --------------------------------------------------------------------------- #
def job_token(bucket: str, key: str, content_hash: str, nonce: str = "") -> str:
    """64-char idempotency token: SQS redeliveries return the same job."""
    raw = json.dumps({"b": bucket, "k": key, "h": content_hash, "n": nonce}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


API_ANALYSIS = "analysis"      # StartDocumentAnalysis with LAYOUT: lines come back in reading (column) order
API_DETECTION = "detection"    # StartDocumentTextDetection: plain lines, row by row across the page


def start_job(textract: Any, *, bucket: str, key: str, token: str, api: str = API_DETECTION) -> str:
    location = {"S3Object": {"Bucket": bucket, "Name": key}}
    if api == API_ANALYSIS:
        return textract.start_document_analysis(
            DocumentLocation=location, FeatureTypes=["LAYOUT"], ClientRequestToken=token)["JobId"]
    return textract.start_document_text_detection(DocumentLocation=location, ClientRequestToken=token)["JobId"]


def read_result_slice(
    textract: Any,
    job_id: str,
    *,
    next_token: Optional[str],
    pages: Dict[int, Any],
    remaining_ms: Callable[[], int],
    reserve_ms: int = 90_000,
    api: str = API_DETECTION,
) -> Dict[str, Any]:
    """Collect LINE text per page until the job's results end or time runs low.

    With ``api=API_ANALYSIS`` each page also keeps its LAYOUT blocks, which give the reading order.

    Returns {"state": "in_progress" | "failed" | "more" | "complete", ...}.
    ``pages`` is updated in place so the caller can persist partial progress.
    """
    while True:
        kwargs: Dict[str, Any] = {"JobId": job_id, "MaxResults": 1000}
        if next_token:
            kwargs["NextToken"] = next_token
        fetch = textract.get_document_analysis if api == API_ANALYSIS else textract.get_document_text_detection
        response = fetch(**kwargs)
        status = response.get("JobStatus")
        if status == "IN_PROGRESS":
            return {"state": "in_progress"}
        if status == "FAILED":
            return {"state": "failed", "message": response.get("StatusMessage", "")}
        # SUCCEEDED or PARTIAL_SUCCESS: use what Textract produced.
        for block in response.get("Blocks", []):
            kind, page_number = block.get("BlockType"), int(block.get("Page") or 1)
            if api == API_ANALYSIS:
                entry = pages.setdefault(page_number, {"lines": {}, "order": [], "layout": []})
                if kind == "LINE" and block.get("Text"):
                    entry["lines"][block["Id"]] = block["Text"]
                    entry["order"].append(block["Id"])
                elif kind and kind.startswith("LAYOUT_"):
                    children = [i for rel in block.get("Relationships", []) if rel.get("Type") == "CHILD" for i in rel.get("Ids", [])]
                    entry["layout"].append({"id": block.get("Id"), "t": kind, "ids": children})
            elif kind == "LINE" and block.get("Text"):          # WORD children repeat the same text
                pages.setdefault(page_number, []).append(block["Text"])
        next_token = response.get("NextToken")
        if not next_token:
            return {"state": "complete", "partial": status == "PARTIAL_SUCCESS"}
        if remaining_ms() < reserve_ms:
            return {"state": "more", "next_token": next_token}


def layout_text(entry: Mapping[str, Any]) -> str:
    """A page's text in Textract's reading order: layout blocks (a paragraph, a list, a title ...) one after
    another, each block's lines together. Lines no layout block claims are added at the end, so none is lost."""
    lines, layout = entry.get("lines", {}), entry.get("layout", [])
    by_id = {block["id"]: block for block in layout}
    nested = {i for block in layout for i in block["ids"] if i in by_id}
    used: set = set()

    def collect(block: Mapping[str, Any], found: List[str]) -> None:
        for child in block["ids"]:
            if child in lines and child not in used:
                used.add(child)
                found.append(lines[child])
            elif child in by_id:
                collect(by_id[child], found)

    blocks = []
    for block in layout:
        if block["id"] in nested:
            continue
        found: List[str] = []
        collect(block, found)
        if found:
            blocks.append("\n".join(found))
    leftovers = [lines[i] for i in entry.get("order", []) if i not in used]
    if leftovers:
        blocks.append("\n".join(leftovers))
    return "\n\n".join(blocks)


def pages_to_parts(pages: Mapping[int, Any], count_tokens: Callable[[str], int]) -> List[Dict[str, Any]]:
    """One part per page - the same shape the page-aware PDF extractor emits."""
    parts = []
    for page_number in sorted(pages):
        entry = pages[page_number]
        text = ocr_rules.dehyphenate(layout_text(entry) if isinstance(entry, Mapping) else "\n".join(entry)).strip()
        if text:
            parts.append({
                "content": text,
                "tokens": count_tokens(text),
                "location": {"page_number": int(page_number)},
                "canSplit": True,
            })
    return parts


# --------------------------------------------------------------------------- #
# Partial progress (so a huge result set can span several invocations)
# --------------------------------------------------------------------------- #
def partial_key(job_id: str) -> str:
    return f"{PARTIAL_PREFIX}/{job_id}.json"


def save_partial(s3: Any, bucket: str, job_id: str, pages: Mapping[int, Any]) -> None:
    s3.put_object(Bucket=bucket, Key=partial_key(job_id), Body=json.dumps({str(n): lines for n, lines in pages.items()}))


def load_partial(s3: Any, bucket: str, job_id: str) -> Dict[int, Any]:
    try:
        body = s3.get_object(Bucket=bucket, Key=partial_key(job_id))["Body"].read()
    except Exception:
        return {}
    return {int(n): lines for n, lines in json.loads(body).items()}


def delete_partial(s3: Any, bucket: str, job_id: str) -> None:
    try:
        s3.delete_object(Bucket=bucket, Key=partial_key(job_id))
    except Exception as error:  # cleanup only
        logger.warning("Could not delete Textract partial %s: %s", job_id, error)


# --------------------------------------------------------------------------- #
# Stage machine
# --------------------------------------------------------------------------- #
def poll_delay_seconds(attempt: int) -> int:
    """10s, 15s, 20s ... capped at 60s. Short, because a user is waiting."""
    return min(10 + 5 * attempt, 60)


def _requeue(sqs: Any, queue_url: str, message: Mapping[str, Any], state: Mapping[str, Any], delay: int) -> None:
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({**message, "textract": dict(state)}),
        DelaySeconds=max(0, min(int(delay), 900)),
    )


def advance(
    *,
    message: Mapping[str, Any],
    bucket: str,
    key: str,
    content_hash: str,
    file_content: bytes,
    force_reprocess: bool,
    textract: Any,
    sqs: Any,
    s3: Any,
    queue_url: str,
    text_bucket: str,
    remaining_ms: Callable[[], int],
    count_tokens: Callable[[str], int],
    page_profile: Callable[[bytes], List[Mapping[str, Any]]] = profile_pdf_pages,
    max_attempts: Optional[int] = None,
    now: Callable[[], float] = time.time,
    prepare_input: Optional[Callable[[List[int]], Optional[Mapping[str, Any]]]] = None,
    on_pages_read: Optional[Callable[[str, int, str], None]] = None,
) -> Tuple[str, Any]:
    """Move one document through the Textract stages for the current message.

    ``on_pages_read(api, pages, job_id)`` is called once when a job's results have been read (for billing).

    Returns (OUTCOME_PARTS, parts) | (OUTCOME_DEFERRED, None) | (OUTCOME_NORMAL, reason) | (OUTCOME_TOO_BIG, reason).
    Only the pages the per-page rule flags are sent to Textract; ``prepare_input(pages)`` builds the copy
    of the PDF Textract reads (those pages only, with tables replaced by placeholders).
    Never raises for Textract/SQS problems: any failure degrades to the normal
    extractor so a document is never stuck waiting on OCR.
    """
    max_attempts = max_attempts if max_attempts is not None else _env_int("TEXTRACT_MAX_POLL_ATTEMPTS", 40)
    max_wait = _env_int("TEXTRACT_MAX_WAIT_SECONDS", 600)
    state = message.get("textract")
    must_ocr = bool((state or {}).get("mustOcr"))

    def give_up(reason: str) -> Tuple[str, Any]:
        """OCR did not deliver. A text document falls back to its text layer; a scan has nothing to fall back to."""
        if must_ocr:
            return OUTCOME_FAILED, f"Text recognition (OCR) is required to read this document and did not complete: {reason}"
        return OUTCOME_NORMAL, reason

    base_message = {k: v for k, v in message.items() if k != "textract"}

    if not queue_url:
        return OUTCOME_NORMAL, "no queue configured for Textract follow-up messages"

    try:
        # ---- first pass: classify and start ------------------------------- #
        if not state:
            if not textract_enabled():
                return OUTCOME_NORMAL, "textract disabled"
            try:
                plan = plan_pdf_ocr(page_profile(file_content), len(file_content))
            except Exception as error:
                return OUTCOME_NORMAL, f"could not profile PDF: {error}"
            flagged = [u.unit for u in plan.units if u.ocr]
            logger.info("Textract plan for %s: %s; pages sent to OCR: %s", key, plan.reason, flagged or "none")
            for unit in plan.units:
                logger.info("[OCR-DECISION] page=%d chars=%d big_visuals=%d -> %s (%s)",
                            unit.unit, unit.text_chars, unit.big_visuals, "OCR" if unit.ocr else "text", unit.reason)
            if plan.too_big:
                return OUTCOME_TOO_BIG, plan.reason
            if not plan.ocr_pages:
                return OUTCOME_NORMAL, plan.reason
            must_ocr = plan.only_readable_with_ocr
            nonce = str(int(now())) if force_reprocess else ""
            # Hand Textract a copy of the PDF that holds only the pages that need OCR, with tables replaced
            # by placeholders. The extras it returns ride along in the job state so whichever invocation
            # finishes the job can map pages back and restore the tables.
            input_bucket, input_key, extras, discard = bucket, key, {}, None
            hidden_layer_pages = [u.unit for u in plan.units if u.hidden_layer]
            if prepare_input:
                try:
                    prepared = prepare_input(list(plan.ocr_pages))
                    if prepared:
                        input_bucket, input_key, extras = prepared["bucket"], prepared["key"], dict(prepared.get("state") or {})
                        discard = prepared.get("discard")
                except Exception as error:
                    if not plan.everything:
                        return OUTCOME_NORMAL, f"could not build the OCR copy of the PDF: {error}"
                    logger.warning("Could not prepare a modified Textract input for %s, using the original: %s", key, error)
            elif not plan.everything:
                return OUTCOME_NORMAL, "OCR of selected pages needs a prepared copy of the PDF"
            try:
                # The page selection is part of the token: the same file read with a different set of pages is a
                # different job (otherwise Textract hands back the earlier job and its other pages).
                selection = "" if plan.everything else ",".join(str(n) for n in plan.ocr_pages)
                token = job_token(input_bucket, input_key, content_hash, f"{nonce}|{selection}" if selection else nonce)
                api = API_ANALYSIS
                try:
                    job_id = start_job(textract, bucket=input_bucket, key=input_key, token=token, api=API_ANALYSIS)
                except Exception as error:      # e.g. the role lacks textract:StartDocumentAnalysis: plain detection still works
                    logger.warning("Textract layout analysis could not start for %s (%s); using plain text detection", key, error)
                    api = API_DETECTION
                    job_id = start_job(textract, bucket=input_bucket, key=input_key, token=token, api=API_DETECTION)
                _requeue(
                    sqs, queue_url, base_message,
                    {"jobId": job_id, "api": api, "attempt": 0, "reason": plan.reason, "startedAt": now(), **extras,
                     **({"replacePages": hidden_layer_pages} if hidden_layer_pages else {}),
                     **({"mustOcr": True} if must_ocr else {})},
                    poll_delay_seconds(0),
                )
            except Exception:
                if discard:                       # the job never started: do not leave the OCR copy behind
                    discard()
                raise
            timing.emit("textract_start", 0.0, key=key, pages=len(plan.ocr_pages), of=plan.page_count, reason=plan.reason)
            return OUTCOME_DEFERRED, None

        # ---- poll / continue ---------------------------------------------- #
        job_id = state["jobId"]
        attempt = int(state.get("attempt", 0))
        pages = load_partial(s3, text_bucket, job_id) if state.get("nextToken") else {}
        result = read_result_slice(
            textract, job_id, next_token=state.get("nextToken"), pages=pages, remaining_ms=remaining_ms,
            api=state.get("api", API_DETECTION),
        )
        outcome = result["state"]
        if outcome == "in_progress":
            waited = now() - float(state.get("startedAt", now()))
            if attempt + 1 > max_attempts or waited > max_wait:
                # The user's upload window has passed (the UI gives up and deletes the file).
                timing.emit("textract_timeout", waited, key=key, polls=attempt + 1)
                return give_up(f"textract job did not finish in time ({waited:.0f}s)")
            _requeue(sqs, queue_url, base_message, {**state, "attempt": attempt + 1}, poll_delay_seconds(attempt + 1))
            return OUTCOME_DEFERRED, None
        if outcome == "failed":
            delete_partial(s3, text_bucket, job_id)
            return give_up(f"textract job failed: {result.get('message', '')}")
        if outcome == "more":
            save_partial(s3, text_bucket, job_id, pages)
            _requeue(sqs, queue_url, base_message, {**state, "nextToken": result["next_token"]}, 0)
            return OUTCOME_DEFERRED, None

        parts = pages_to_parts(pages, count_tokens)
        delete_partial(s3, text_bucket, job_id)
        timing.emit("textract_job", now() - float(state.get("startedAt", now())), key=key, polls=attempt + 1, pages=len(pages))
        if on_pages_read:
            try:
                on_pages_read(state.get("api", API_DETECTION), len(pages), job_id)
            except Exception as error:      # billing must never fail the document
                logger.warning("Textract usage callback failed for %s: %s", key, error)
        if not parts:
            return give_up("textract returned no text")
        return OUTCOME_PARTS, parts
    except Exception as error:
        logger.warning("Textract stage failed for %s: %s", key, error)
        return give_up(f"textract error: {error}")
