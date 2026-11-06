"""Files that exist only because an assistant needed them, and that no assistant uses any more.

Four kinds, found from the file records alone (an ordinary upload in someone's library is NEVER a candidate):

  assistantGone  made for an assistant (``data.astp``) that no longer exists
  drive          synced from a cloud drive (SharePoint and the like), used by no assistant
  website        a scraped web page, used by no assistant
  group          owned by a group (the file key starts with the group id), used by no assistant

"Used" is the same rule the retention sweep uses (rag/retention.py): the file's key, its content key or its hash appears
anywhere in any assistant, layered-assistant or group record, in any version. That is deliberately broad, so a file is
reported as an orphan only when nothing at all mentions it.

Whether anything may actually be deleted is decided by ``retention.Sweeper.decide`` (reused, not copied), plus two more
guards here: files created or updated in the last 14 days are left alone (an editor uploads files before the assistant is
saved), and so is any document that has a migration record (the migration owns it). Images and videos live in their own bucket and are not handled. A file whose content still has
embeddings is reported, never deleted: removing embeddings needs the owner's access, which an admin does not have.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from rag import migration as m, retention

logger = logging.getLogger("rag_orphans")

MIN_AGE_DAYS = 14
SCAN_LIMIT = 3000            # candidates examined per request (each costs several reads)
REMOVE_LIMIT = 100           # most files removed per request
CONFIRM_PHRASE = "DELETE"
DAY = 86400

CATEGORIES: Dict[str, str] = {
    "assistantGone": "Made for an assistant that no longer exists",
    "drive": "Cloud-drive files (SharePoint…) that no assistant uses",
    "website": "Scraped web pages that no assistant uses",
    "group": "Group-owned files that no assistant uses",
}
BUCKETS = ("removable", "inUse", "embeddings", "recent", "tooNew", "migrating")


def classify(row: Mapping[str, Any], group_ids: Set[str], referenced: Set[str]) -> Optional[str]:
    """Which kind of orphan candidate this file is, or None for a file that is never a candidate."""
    if str(row.get("type") or "").startswith(("image/", "video/")):
        return None           # media is stored in its own bucket and is not handled here
    info = row.get("data") if isinstance(row.get("data"), Mapping) else {}
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    astp = info.get("astp")
    if astp and not retention.is_referenced([str(astp)], referenced):
        return "assistantGone"
    if "drive-integration" in tags or info.get("originalFileId"):
        return "drive"
    if info.get("isScrapedContent") or info.get("sourceUrl"):
        return "website"
    if str(row.get("id", "")).split("/")[0] in group_ids:
        return "group"
    return None


def group_ids_of(group_table: Any) -> Set[str]:
    if group_table is None:
        return set()
    return {str(item["group_id"]) for item in retention._scan_all(group_table) if item.get("group_id")}


def decide(sweeper: retention.Sweeper, store: Any, row: Mapping[str, Any], referenced: Set[str], now: float) -> Dict[str, Any]:
    """The sweep's own decision for this file, plus the guards above. Never deletes anything."""
    key = row["id"]
    stamp = max(retention._epoch(row.get("createdAt")), retention._epoch(row.get("updatedAt")))
    if stamp and now - stamp < MIN_AGE_DAYS * DAY:
        return {"action": "skip", "reason": "too new", "key": key}
    decision = dict(sweeper.decide(row, referenced))
    content_key = decision.get("global_key")
    if decision["action"] != "skip" and content_key:
        legacy = content_key if content_key.startswith(m.LEGACY_PREFIX) else (m.legacy_key_for(content_key) if content_key.startswith(m.NEW_PREFIX) else None)
        if legacy and store.get_doc(legacy):
            return {"action": "skip", "reason": "being migrated", "key": key}
    return decision


def bucket_of(decision: Mapping[str, Any]) -> str:
    if decision["action"] in ("delete_upload", "delete_all"):
        return "removable"
    return {
        "referenced by an assistant": "inUse", "content has embeddings": "embeddings",
        "used recently": "recent", "too new": "tooNew", "being migrated": "migrating",
    }.get(decision.get("reason", ""), "inUse")


def candidates(files: Iterable[Mapping[str, Any]], group_ids: Set[str], referenced: Set[str], only: Optional[str] = None):
    for row in files:
        category = classify(row, group_ids, referenced)
        if category and (only is None or category == only):
            yield category, row


def _checked_references(sweeper: retention.Sweeper) -> Set[str]:
    referenced = retention.referenced_identifiers(sweeper.assistant_tables)
    if not referenced:
        # an empty view of the assistants would make every file look unused
        raise RuntimeError("The assistant tables returned nothing, so no file can be called unused")
    return referenced


def report(sweeper: retention.Sweeper, store: Any, group_table: Any, now: float, limit: int = SCAN_LIMIT) -> Dict[str, Any]:
    """Counts and examples per kind. Reads only."""
    referenced = _checked_references(sweeper)
    groups = group_ids_of(group_table)
    summary = {c: {"label": label, **{b: 0 for b in BUCKETS}, "examples": [], "embeddingExamples": []} for c, label in CATEGORIES.items()}
    seen = 0
    truncated = False
    for category, row in candidates(retention._scan_all(sweeper.files_table), groups, referenced):
        if seen >= limit:
            truncated = True
            break
        seen += 1
        decision = decide(sweeper, store, row, referenced, now)
        bucket = bucket_of(decision)
        entry = summary[category]
        entry[bucket] += 1
        example = {"key": row["id"], "name": row.get("name"), "type": row.get("type"), "owner": str(row["id"]).split("/")[0], "why": decision.get("reason")}
        if bucket == "removable" and len(entry["examples"]) < 10:
            entry["examples"].append(example)
        elif bucket == "embeddings" and len(entry["embeddingExamples"]) < 5:
            entry["embeddingExamples"].append(example)
    return {"categories": [{"id": c, **v} for c, v in summary.items()], "examined": seen, "truncated": truncated}


def remove(
    sweeper: retention.Sweeper, store: Any, group_table: Any, category: str, admin: str, now: float, limit: int = REMOVE_LIMIT,
) -> Dict[str, Any]:
    """Remove up to ``limit`` removable files of one kind. Every file is judged again at this moment, from fresh reads."""
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {sorted(CATEGORIES)}")
    if sweeper.dry_run:
        raise RuntimeError("This cleaner is in dry-run mode")
    referenced = _checked_references(sweeper)
    groups = group_ids_of(group_table)
    removed = {"upload": 0, "withContent": 0}
    kept = {b: 0 for b in BUCKETS if b != "removable"}
    errors = 0
    more = False
    for _, row in candidates(retention._scan_all(sweeper.files_table), groups, referenced, only=category):
        decision = decide(sweeper, store, row, referenced, now)
        bucket = bucket_of(decision)
        if bucket != "removable":
            kept[bucket] += 1
            continue
        if removed["upload"] + removed["withContent"] >= limit:
            more = True
            break
        try:
            sweeper.apply(decision)
        except Exception as error:            # one file failing must not stop the rest, or hide what was done
            logger.error("ORPHAN_DELETE failed admin=%s key=%s: %s", admin, row["id"], error)
            errors += 1
            continue
        removed["withContent" if decision["action"] == "delete_all" else "upload"] += 1
        logger.warning("ORPHAN_DELETE admin=%s category=%s action=%s reason=%s key=%s", admin, category, decision["action"], decision["reason"], row["id"])
    return {"category": category, "removed": removed["upload"] + removed["withContent"], "uploadOnly": removed["upload"],
            "withContent": removed["withContent"], "kept": kept, "errors": errors, "more": more}
