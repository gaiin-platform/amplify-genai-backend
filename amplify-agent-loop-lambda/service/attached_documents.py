"""Inline the extracted text of attached documents into the agent prompt.

The agent has no file-reading tools, so a binary attachment (xlsx, docx, pdf...)
that is only downloaded to the work directory is invisible to the model. The
data-source resolver already returns a signed ``contentUrl`` for the text that the
RAG pipeline extracted from each file. This module fetches that text and renders it
into the prompt, the same text the direct (non-agent) chat path uses.
"""

import os

import requests

from pycommon.logger import getLogger

logger = getLogger("attached_documents")

DEFAULT_TOKEN_BUDGET = 60000
CHARS_PER_TOKEN = 4
FETCH_TIMEOUT_SECONDS = 30
SHEET_LABEL_MAX_CHARS = 64
SHEET_LABEL_MAX_TOKENS = 16
# Binary media is described by other means; it has no extracted text to inline.
SKIPPED_TYPE_PREFIXES = ("image/", "video/", "audio/")


def get_token_budget():
    """Total token budget for all inlined documents in one agent invocation."""
    raw = os.environ.get("AGENT_ATTACHED_DOCS_TOKEN_BUDGET")
    try:
        value = int(raw) if raw is not None else DEFAULT_TOKEN_BUDGET
    except ValueError:
        logger.warning("Invalid AGENT_ATTACHED_DOCS_TOKEN_BUDGET %r, using default", raw)
        return DEFAULT_TOKEN_BUDGET
    return max(0, value)


def estimate_tokens(text):
    return (len(text) // CHARS_PER_TOKEN) + 1 if text else 0


def fetch_extracted_parts(content_url):
    """Download the extracted-text JSON. Returns a list of parts, or None on failure."""
    try:
        response = requests.get(content_url, timeout=FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:  # Never let a text fetch failure break the agent run.
        logger.warning("Could not fetch extracted text for attached document: %s", e)
        return None

    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        return payload["content"]
    if isinstance(payload, list):
        return payload
    logger.warning("Unexpected extracted text format: %s", type(payload).__name__)
    return None


def is_spreadsheet(name, mime_type):
    name = (name or "").lower()
    mime_type = (mime_type or "").lower()
    return name.endswith((".xlsx", ".xls")) or "spreadsheet" in mime_type or "excel" in mime_type


def is_sheet_label(part, next_part):
    """The RAG pipeline stores each tab name as its own tiny part before the tab's rows.

    Deliberately strict, because a wrong guess would mislabel ordinary rows as tabs: the
    part must be a short single token-ish line that does not already carry a sheet name,
    and the next part must be a multi-line block of rows.
    """
    text = part.get("content")
    if next_part is None or not isinstance(text, str):
        return False
    stripped = text.strip()
    if not (0 < len(stripped) <= SHEET_LABEL_MAX_CHARS) or "\n" in stripped or "," in stripped:
        return False
    if (part.get("location") or {}).get("sheet_name"):
        return False
    tokens = part.get("tokens")
    if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) and tokens > SHEET_LABEL_MAX_TOKENS:
        return False
    next_text = next_part.get("content")
    return isinstance(next_text, str) and "\n" in next_text.strip()


def format_parts(parts, spreadsheet=False):
    """Render extracted parts as plain text, keeping sheet/page/slide context."""
    parts = [p for p in (parts or []) if isinstance(p, dict)]
    lines = []
    last_heading = None
    for index, part in enumerate(parts):
        text = part.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        if spreadsheet and is_sheet_label(part, parts[index + 1] if index + 1 < len(parts) else None):
            # Mark tab boundaries explicitly: instructions often say "use only the X tab".
            lines.append("[Sheet: %s]" % text.strip())
            continue
        location = part.get("location") or {}
        heading = None
        if location.get("sheet_name"):
            heading = "Sheet: %s" % location["sheet_name"]
        elif location.get("slide"):
            heading = "Slide %s" % location["slide"]
        elif location.get("page"):
            heading = "Page %s" % location["page"]
        if heading and heading != last_heading:
            lines.append("[%s]" % heading)
            last_heading = heading
        lines.append(text.rstrip())
    return "\n".join(lines)


def count_tokens(parts, text):
    """Use the RAG pipeline's own token counts when present (chars/4 undercounts tabular
    data by almost 2x); otherwise estimate."""
    counts = [p.get("tokens") for p in parts or [] if isinstance(p, dict)]
    if counts and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in counts):
        return int(sum(counts))
    return estimate_tokens(text)


def truncate_to_budget(text, token_budget, total_tokens=None):
    """Cut text at a line boundary so it fits the token budget.

    ``total_tokens`` is the real token count of ``text``; the allowed length is scaled by
    this document's own characters-per-token ratio.
    """
    total_tokens = total_tokens if total_tokens else estimate_tokens(text)
    if total_tokens <= max(0, token_budget):
        return text, False
    max_chars = int(len(text) * max(0, token_budget) / total_tokens)
    cut = text.rfind("\n", 0, max_chars)
    cut = cut if cut > 0 else max_chars
    return text[:cut], True


def render_document_block(name, text, truncated, total_tokens, shown_tokens=None):
    """The prompt text appended to the message the file was attached to."""
    if text:
        block = (
            "\n\nThe text content extracted from %s is included below, so you do not "
            "need a tool to open the file.\n"
            "----- BEGIN CONTENT OF %s -----\n%s\n----- END CONTENT OF %s -----"
            % (name, name, text, name)
        )
    else:
        block = ""
    if truncated:
        shown = shown_tokens if shown_tokens is not None else estimate_tokens(text)
        block += (
            "\n[Only the first ~%d of ~%d tokens of %s fit within the size limit. "
            "Do not assume anything about the omitted remainder; tell the user the "
            "file was only partially available.]" % (shown, total_tokens, name)
        )
    return block


def apply_attachment_text(prompt, text_by_message_idx):
    """Return a copy of ``prompt`` with attachment text appended to the right messages.

    This is applied only to the text the agent LLM sees. The original ``prompt`` is
    left untouched so the extracted text does not leak into the tool-selection call or
    into the conversation state that gets saved to S3.
    """
    if not text_by_message_idx:
        return prompt
    updated = []
    for index, entry in enumerate(prompt):
        extra = text_by_message_idx.get(index)
        if extra and isinstance(entry, dict) and isinstance(entry.get("content"), str):
            entry = {**entry, "content": entry["content"] + extra}
        updated.append(entry)
    return updated


def build_document_blocks(processed_data_sources, source_message_idx, token_budget=None):
    """Return {source_id: prompt text} for sources that have extracted text.

    Newer messages are served first, so the budget goes to what the user attached most
    recently. ``processed_data_sources`` maps source id -> {name, type, content_url, ...}.
    """
    budget = get_token_budget() if token_budget is None else token_budget
    candidates = [
        (source_id, info)
        for source_id, info in processed_data_sources.items()
        if info.get("content_url")
        and not str(info.get("type", "")).startswith(SKIPPED_TYPE_PREFIXES)
    ]
    candidates.sort(key=lambda item: source_message_idx.get(item[0], -1), reverse=True)

    blocks = {}
    remaining = budget
    for source_id, info in candidates:
        parts = fetch_extracted_parts(info["content_url"])
        if parts is None:
            continue
        text = format_parts(parts, spreadsheet=is_spreadsheet(info.get("name"), info.get("type")))
        if not text:
            continue
        total_tokens = count_tokens(parts, text)
        shown_text, truncated = truncate_to_budget(text, remaining, total_tokens)
        shown_tokens = int(total_tokens * len(shown_text) / len(text)) if truncated else total_tokens
        remaining -= shown_tokens
        blocks[source_id] = render_document_block(
            info.get("name", "file"), shown_text, truncated, total_tokens, shown_tokens
        )
        logger.info(
            "Inlined %d of %d tokens of extracted text for an attached document (truncated=%s)",
            shown_tokens, total_tokens, truncated,
        )
    return blocks
