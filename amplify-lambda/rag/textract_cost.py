"""Records Amazon Textract page charges in the additional-charges table (same table the embedding costs use).

Who is charged: the migration account for a migration rebuild (``charge_user`` / ``charge_account`` in the
document's RAG secret), otherwise the user who uploaded the document.

Prices are per page, in USD, US East (N. Virginia), first-million-pages tier. Source: the AWS Price List API
(service AmazonTextract, region us-east-1; read 2026-11-06), which the public pricing page does not spell out:
  * DetectDocumentText (usage type AsyncTextPagesProcessed / SyncTextPagesProcessed): $0.0015 per page, used for
    Office-picture OCR and the plain-text fallback.
  * AnalyzeDocument with only the LAYOUT feature (AsyncLayoutPagesProcessed): $0.004 per page, what async PDF OCR uses.
Pages beyond the first million a month are cheaper ($0.0006 and $0.003); not applied, so large totals are slightly high.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from pycommon.logger import getLogger
from pycommon.rag.migration_cost import add_doc_cost

logger = getLogger("rag_textract_cost")

DETECT = "detect"
LAYOUT = "layout"
PRICE_PER_PAGE: Mapping[str, Optional[float]] = {DETECT: 0.0015, LAYOUT: 0.004}
MODEL_ID = "amazon-textract"


def api_kind(api: str) -> str:
    """The Textract API name used by the pipeline -> the price row (analysis jobs are layout-only)."""
    return DETECT if "detect" in str(api).lower() else LAYOUT


def charge_for(kind: str, pages: int) -> Optional[float]:
    price = PRICE_PER_PAGE.get(kind)
    return None if price is None else round(price * max(0, int(pages)), 6)


def record_pages(account_data: Optional[Mapping[str, Any]], kind: str, pages: int, request_id: str,
                 document_key: str = "") -> float:
    """Record ``pages`` of Textract use. Safe to call twice for the same ``request_id`` (same row is overwritten).
    Never raises: billing records must not break document processing."""
    if not pages or not account_data:
        return 0.0
    user = account_data.get("charge_user") or account_data.get("user")
    if not user:
        return 0.0
    cost = charge_for(kind, pages)
    try:
        from pycommon.api.accounting import record_additional_charge
        record_additional_charge(
            account={"user": user, "account_id": account_data.get("charge_account") or account_data.get("account")},
            model_id=MODEL_ID,
            token_count=int(pages),
            item_type="textract",
            request_id=request_id,
            details={"api": kind, "pages": int(pages), "priced": cost is not None, "document_key": document_key},
            flat_cost=cost if cost is not None else 0.0,
        )
    except Exception as error:
        logger.warning("Could not record Textract charge for %s: %s", document_key, error)
        return 0.0
    if account_data.get("charge_user"):         # a migration rebuild: also keep the document's running cost
        add_doc_cost(account_data.get("cost_doc"), "ocrCost", cost)
    if cost is None:
        logger.warning("Textract %s has no configured price: recorded %d pages with cost 0 for %s", kind, pages, document_key)
    return cost or 0.0
