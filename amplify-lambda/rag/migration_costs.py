"""What a migration costs: a rough estimate before documents are started, and what it has cost since.

Spent so far, by source:
  * Nova embeddings and Textract pages are recorded per call and added to each document's migration row as they happen
    (pycommon.rag.migration_cost), so they are exact.
  * Question generation is billed by the chat service to the migration account, so the total comes from the cost tables
    (exact, every admin who ran the migration). Per document, each call is counted on its row and priced at the average
    the chat usage records show for recent calls, because the service bills its own instructions and any hidden reasoning
    and does not return what one call cost.

The estimate works from the chosen documents' stored sizes. Its constants are averages measured on dev documents, and they
are returned with the numbers so the admin can see what they rest on.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from boto3.dynamodb.conditions import Attr, Key

from rag import migration as m
from rag import ocr_rules, textract_cost

CHUNK_TOKENS = 130           # text in one chunk: 456 characters on average over a 184-page PDF (1,317 chunks)
CHUNKS_PER_FILE = 10         # the worker handles up to ten chunks per chunk file
NOVA_TOKENS_PER_CHUNK = 800  # what Nova reports for a chunk and its questions (700 to 1,000 on three dev documents)
QA_CALLS_PER_CHUNK = 1.5     # question calls per chunk: 1.7 on the 184-page PDF, because some chunk files run twice
CHARS_PER_TOKEN = 3.5
# Nova embeds the rendered image of each PDF/Office page too. Bedrock reports no tokens for an image, so the embedding records
# show it as 0 tokens and $0. $0.00006 per image comes from a third-party price catalog; it is not confirmed against the AWS
# Price List yet. Rendering stops when its 20 s budget is used, so only the first pages of a very long PDF get an image.
NOVA_IMAGE_PRICE = 0.00006
FALLBACK_CALL = {"input": 1500, "output": 300}      # tokens per question call, used until the migration account has any
PROFILE_CALLS = 300          # recent question calls averaged
PROFILE_DAYS = 30
OFFICE_MARKERS = ("wordprocessingml", "presentationml", "spreadsheetml")      # .docx, .pptx, .xlsx: the old .doc/.ppt/.xls are not picture-read


def native_float(value: Any) -> float:
    return float(value) if value not in (None, "") else 0.0


def rates_for(model_rate_table: Any, model_id: str) -> Dict[str, float]:
    """USD per 1,000 input, cached-input and output tokens of a model, from the model rate table (0 when not listed)."""
    item = model_rate_table.get_item(Key={"ModelID": model_id}).get("Item") or {}
    return {
        "input": native_float(item.get("InputCostPerThousandTokens")),
        "cached": native_float(item.get("InputCachedCostPerThousandTokens")),
        "output": native_float(item.get("OutputCostPerThousandTokens")),
    }


def qa_model_id(admin_table: Any) -> Optional[str]:
    """The model that writes the questions: the admin's cheapest default model."""
    item = admin_table.get_item(Key={"config_id": "defaultModels"}).get("Item") or {}
    return (item.get("data") or {}).get("cheapest")


def migration_admins(cost_table: Any) -> List[Mapping[str, Any]]:
    """The current-month cost row of every user who has been charged to the migration account."""
    kwargs: Dict[str, Any] = {
        "IndexName": "record-type-user-index",
        "KeyConditionExpression": Key("record_type").eq("cost"),
        "FilterExpression": Attr("accountInfo").begins_with(f"{m.MIGRATION_CHARGE_ACCOUNT}#"),
    }
    rows: List[Mapping[str, Any]] = []
    while True:
        response = cost_table.query(**kwargs)
        rows.extend(response.get("Items", []))
        if not response.get("LastEvaluatedKey"):
            return rows
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def call_profile(
    usage_table: Any, rates: Callable[[str], Mapping[str, float]], users: Iterable[str], now: float,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """What a recent question-generation call billed: the migration account's latest chat usage records, averaged.

    The records carry the real input and output tokens of each call, including the chat service's own instructions and any
    hidden reasoning. With ``model`` only that model's calls count, if there are enough of them (20).
    """
    since = (datetime.fromtimestamp(now, timezone.utc) - timedelta(days=PROFILE_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    calls: List[Mapping[str, Any]] = []
    for user in users:
        kwargs: Dict[str, Any] = {
            "IndexName": "UserUsageTimeIndex",
            "KeyConditionExpression": Key("user").eq(user) & Key("time").gt(since),
            "FilterExpression": Attr("accountId").eq(m.MIGRATION_CHARGE_ACCOUNT),
            "ScanIndexForward": False,
        }
        found = 0
        while found < PROFILE_CALLS:
            response = usage_table.query(**kwargs)
            calls.extend(response.get("Items", []))
            found += len(response.get("Items", []))
            if not response.get("LastEvaluatedKey"):
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    chosen = [call for call in calls if call.get("modelId") == model] if model else []
    if len(chosen) < 20:
        chosen, model = calls, None
    if not chosen:
        return None
    totals = {"cost": 0.0, "input": 0.0, "output": 0.0}
    for call in chosen:
        rate = rates(call.get("modelId", ""))
        cached = native_float(call.get("inputCachedTokens"))
        fresh, out = native_float(call.get("inputTokens")) - cached, native_float(call.get("outputTokens"))
        totals["cost"] += fresh / 1000 * rate["input"] + cached / 1000 * rate["cached"] + out / 1000 * rate["output"]
        totals["input"] += fresh + cached
        totals["output"] += out
    count = len(chosen)
    return {"calls": count, "model": model or "mixed", "avgCost": totals["cost"] / count,
            "avgInput": round(totals["input"] / count), "avgOutput": round(totals["output"] / count)}


def ocr_outlook(file_type: str, tokens: int, parts: int) -> Dict[str, Any]:
    """Whether Textract is likely to be used on a file, and the most it could cost.

    A PDF page is read by Textract when its text layer is nearly empty (a scan), so a PDF with plenty of text per page on
    average is not expected to need it; a sparse one could need every page. An Office file's pictures are read when they
    probably hold text, which cannot be known before the file is opened.
    """
    if file_type == "application/pdf":
        per_page = tokens * CHARS_PER_TOKEN / parts if parts else 0
        if parts and per_page >= ocr_rules.min_chars():
            return {"kind": "none", "max": 0.0}
        return {"kind": "pdf", "max": round(parts * (textract_cost.PRICE_PER_PAGE[textract_cost.LAYOUT] or 0), 6)}
    if any(marker in file_type for marker in OFFICE_MARKERS):
        return {"kind": "pictures", "max": 0.0}
    return {"kind": "none", "max": 0.0}


def estimate_doc(doc: Mapping[str, Any], nova: Mapping[str, float], call_cost: float) -> Optional[Dict[str, Any]]:
    """Estimated cost of rebuilding one document, or None when its size is not known."""
    tokens, files = doc.get("tokens"), doc.get("childFiles")
    if tokens is not None:
        chunks = max(1, math.ceil(int(tokens) / CHUNK_TOKENS)) if tokens else 0
    elif files:
        chunks = max(1, int(files) * CHUNKS_PER_FILE - CHUNKS_PER_FILE // 2)       # the last file is only part full
    else:
        return None
    tokens = int(tokens) if tokens is not None else chunks * CHUNK_TOKENS
    ocr = ocr_outlook(doc.get("type") or "", tokens, int(doc.get("parts") or 0))
    images = int(doc.get("parts") or 0) if ocr["kind"] != "none" or doc.get("type") == "application/pdf" else 0      # at most one per page
    return {
        "chunks": chunks, "tokens": tokens, "images": images,
        "embedding": round(chunks * NOVA_TOKENS_PER_CHUNK / 1000 * nova["input"] + images * NOVA_IMAGE_PRICE, 6),
        "qa": round(chunks * QA_CALLS_PER_CHUNK * call_cost, 6),
        "ocr": ocr,
    }


def estimate(docs: Iterable[Mapping[str, Any]], nova: Mapping[str, float], profile: Optional[Mapping[str, Any]],
             fallback_cost: float) -> Dict[str, Any]:
    """``docs``: contentKey, name, type, tokens, parts, childFiles. Per-document and total figures.

    ``fallback_cost`` prices a question call (from the rate table and ``FALLBACK_CALL``) when no history exists yet.
    """
    call_cost = profile["avgCost"] if profile else fallback_cost
    rows: List[Dict[str, Any]] = []
    totals = {"embedding": 0.0, "qa": 0.0, "ocrMax": 0.0, "ocrPdfs": 0, "ocrOffice": 0, "noOcr": 0, "unknown": 0, "chunks": 0, "images": 0}
    for doc in docs:
        figures = estimate_doc(doc, nova, call_cost)
        if figures is None:
            rows.append({"contentKey": doc["contentKey"], "name": doc.get("name"), "known": False})
            totals["unknown"] += 1
            continue
        rows.append({"contentKey": doc["contentKey"], "name": doc.get("name"), "known": True, **figures})
        totals["embedding"] += figures["embedding"]
        totals["qa"] += figures["qa"]
        totals["chunks"] += figures["chunks"]
        totals["images"] += figures["images"]
        kind = figures["ocr"]["kind"]
        totals["ocrMax"] += figures["ocr"]["max"]
        totals["ocrPdfs"] += kind == "pdf"
        totals["ocrOffice"] += kind == "pictures"
        totals["noOcr"] += kind == "none"
    return {
        "docs": rows,
        "totals": {key: round(value, 6) if isinstance(value, float) else value for key, value in totals.items()},
        "assumptions": {
            "chunkTokens": CHUNK_TOKENS, "novaTokensPerChunk": NOVA_TOKENS_PER_CHUNK, "qaCallsPerChunk": QA_CALLS_PER_CHUNK,
            "callCost": round(call_cost, 6), "imagePrice": NOVA_IMAGE_PRICE,
            "callBasis": ({k: profile[k] for k in ("calls", "model", "avgInput", "avgOutput")} if profile else "assumed"),
        },
    }


def spent(docs: Iterable[Mapping[str, Any]], call_cost: float) -> Dict[str, float]:
    """Embedding and OCR cost added up over every document's row, and question generation as counted calls times their average cost."""
    totals = {"embeddingCost": 0.0, "ocrCost": 0.0, "qaCost": 0.0, "qaCalls": 0, "documents": 0}
    for doc in docs:
        calls = int(native_float(doc.get("qaCalls")))
        figures = (native_float(doc.get("embeddingCost")), native_float(doc.get("ocrCost")), calls)
        totals["documents"] += any(figures)
        totals["embeddingCost"] += figures[0]
        totals["ocrCost"] += figures[1]
        totals["qaCalls"] += calls
        totals["qaCost"] += calls * call_cost
    return {name: round(value, 6) if isinstance(value, float) else value for name, value in totals.items()}


def months_between(first: float, now: float) -> List[str]:
    """YYYY-MM of every month from the one containing ``first`` up to, but not including, the one containing ``now``."""
    start, end = datetime.fromtimestamp(first, timezone.utc), datetime.fromtimestamp(now, timezone.utc)
    months, year, month = [], start.year, start.month
    while (year, month) < (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def billed_question_cost(
    admins: Iterable[Mapping[str, Any]], history_table: Any, first_start: Optional[float], now: float,
) -> Dict[str, Any]:
    """What the chat service billed the migration account, for every admin who has run the migration.

    ``admins`` are their current-month cost rows; earlier months are in the history table (one row per user, account and
    month). The migration account is used for nothing else, so everything on it is migration cost.
    """
    months = months_between(first_start, now) if first_start else []
    people, total = [], 0.0
    for row in admins:
        current = native_float(row.get("monthlyCost"))
        earlier = 0.0
        for month in months:
            item = history_table.get_item(Key={"userDate": f"{row['id']}#{month}", "accountInfo": row["accountInfo"]}).get("Item") or {}
            earlier += native_float(item.get("monthlyCost"))
        people.append({"user": row["id"], "thisMonth": round(current, 6), "total": round(current + earlier, 6)})
        total += current + earlier
    return {"total": round(total, 6), "admins": sorted(people, key=lambda p: -p["total"])}
