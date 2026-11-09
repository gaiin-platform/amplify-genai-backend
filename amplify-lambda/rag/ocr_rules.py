"""One rule, shared by every file type, for "does this unit need OCR?".

A unit is a PDF page, a PowerPoint slide, a Word page (when the file carries page breaks) or the whole
Word document. Tables are never part of this decision: they are always slotted in as markdown tables
(see ``pdf_tables``), so they do not make a unit look "image heavy" or "empty".

    OCR when the unit has several large pictures   (the text probably lives inside them), or
    OCR when it has almost no text but a picture   (a scan / screenshot page).
    A page with almost no text and no picture is simply blank: nothing to read, nothing to pay for.

Thresholds are the existing Textract environment settings, so there is one place to tune them.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple

PICTURE_TEXT_LABEL = "[Text in picture]"      # heads text that OCR found inside pictures (Word, PowerPoint, Excel, PDF)
MIN_VISUAL_SIDE_PX = 100            # smaller than this on either side: icon / bullet / logo
MIN_VISUAL_AREA_PX = 40_000         # ~200x200
MIN_VISUAL_PAGE_FRACTION = 0.04     # covers less than 4% of its page / slide: decorative


SCAN_PICTURE_COVER = 0.6        # one picture covering this much of the page: the page is an image of a page
BARE_PICTURE_MIN_FRACTION = 0.08    # a picture this large (share of the page)...
BARE_PICTURE_MAX_WORDS = 2          # ...with this few text-layer words on it is a diagram / screenshot whose words exist only as pixels
INVISIBLE_TEXT_SHARE = 0.8      # ...and this share of its text is invisible (render mode 3): a leftover OCR layer


def page_has_hidden_ocr_layer(page: Any) -> bool:
    """True for a scanned page image whose text is an invisible layer left by an earlier OCR run (PyMuPDF page)."""
    area = max(page.rect.width * page.rect.height, 1.0)
    widest = max(
        (max(i["bbox"][2] - i["bbox"][0], 0) * max(i["bbox"][3] - i["bbox"][1], 0) / area for i in page.get_image_info()),
        default=0.0,
    )
    if widest < SCAN_PICTURE_COVER:
        return False
    spans = page.get_texttrace()
    total = sum(len(s.get("chars", ())) for s in spans)
    invisible = sum(len(s.get("chars", ())) for s in spans if s.get("type") == 3)
    return total > 0 and invisible / total >= INVISIBLE_TEXT_SHARE


# A word broken across lines by a hyphen ("Sar-" / "kophages"). Not joined when the next word is a connector,
# as in "Vor- und Nachteile" or "pre- and post-war", or when the next word is capitalised ("Kline-" / "Deckel").
_CONNECTORS = {"und", "oder", "bzw", "and", "or", "et", "ou", "y", "e", "o", "en", "of", "to"}
_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])-[ \t]*\n{1,2}[ \t]*([a-zà-öø-ÿ][\w]*)", re.UNICODE)


def join_hyphen_breaks(text: str) -> str:
    """Born-digital text: close a line-end hyphen up but KEEP it ("single-\ndomain" -> "single-domain").

    A text layer cannot tell a typesetter's soft hyphen ("Sar-" / "kophages") from a real compound ("single-" /
    "domain"), and dropping the hyphen of a real compound writes a word that does not exist ("singledomain"),
    which is worse than keeping a hyphen inside a split word. Scans keep using ``dehyphenate``: a printed book's
    line-end hyphens are almost all soft ones.
    """
    return _HYPHEN_BREAK.sub(lambda m: m.group(0) if m.group(1).lower() in _CONNECTORS else "-" + m.group(1), text)


def dehyphenate(text: str) -> str:
    """Rejoin words split by a line-end hyphen so "Sar-\nkophages" is searchable as "Sarkophages"."""
    return _HYPHEN_BREAK.sub(lambda m: m.group(0) if m.group(1).lower() in _CONNECTORS else m.group(1), text)


def min_chars() -> int:
    return _int_env("TEXTRACT_MIN_CHARS_PER_PAGE", 100)


def min_visuals() -> int:
    try:
        return max(1, int(float(os.environ.get("TEXTRACT_MIN_IMAGES_PER_PAGE", 2))))
    except (TypeError, ValueError):
        return 2


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def is_big_visual(width_px: float, height_px: float, page_fraction: Optional[float] = None) -> bool:
    """True for a picture large enough to plausibly contain readable text (not an icon or logo)."""
    if min(width_px, height_px) < MIN_VISUAL_SIDE_PX or width_px * height_px < MIN_VISUAL_AREA_PX:
        return False
    return page_fraction is None or page_fraction >= MIN_VISUAL_PAGE_FRACTION


def decide(text_chars: int, big_visuals: int, hidden_layer: bool = False, bare_visuals: int = 0) -> Tuple[bool, str]:
    """(needs_ocr, plain-English reason).

    ``hidden_layer``: the page is a picture of a page carrying invisible text from an earlier OCR run (a
    searchable scan). That text is of unknown quality, so the page is read again instead of trusted.
    """
    threshold_visuals, threshold_chars = min_visuals(), min_chars()
    if hidden_layer:
        return True, f"a scanned page image with a hidden OCR text layer ({text_chars} characters) of unknown quality: read again"
    if big_visuals >= threshold_visuals:
        return True, f"{big_visuals} large pictures (limit {threshold_visuals}): text is probably inside them"
    if bare_visuals >= 1:
        return True, f"{bare_visuals} large picture(s) with no text of their own in the page text: a diagram or screenshot whose words are only pixels"
    if text_chars < threshold_chars and big_visuals >= 1:
        return True, f"only {text_chars} characters of text and a large picture: scanned or screenshot"
    if text_chars < threshold_chars:
        return False, f"only {text_chars} characters and no picture: blank or nearly blank, nothing to read"
    return False, f"{text_chars} characters of text and {big_visuals} large picture(s): the text is reliable"


@dataclass(frozen=True)
class UnitDecision:
    unit: int
    text_chars: int
    big_visuals: int
    tables: int
    ocr: bool
    reason: str
    hidden_layer: bool = False
    bare_visuals: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def decide_unit(unit: int, text_chars: int, big_visuals: int, tables: int = 0, hidden_layer: bool = False, bare_visuals: int = 0) -> UnitDecision:
    ocr, reason = decide(text_chars, big_visuals, hidden_layer, bare_visuals)
    return UnitDecision(unit, text_chars, big_visuals, tables, ocr, reason, hidden_layer, bare_visuals)
