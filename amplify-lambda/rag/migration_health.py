"""Read-only health numbers for the RAG Migration admin tab.

Everything here is computed from rows the migration inventory has already scanned (embedding progress rows and
hash-table pointer rows), so it costs no extra table scans. All counts are exact for the moment of the scan; nothing is
estimated.

Vocabulary
  user file      a row in the hash table keyed by a user's upload (it has a ``hash`` attribute)
  content key    ``global/<hash>.content.json`` (legacy, PostgreSQL) or ``global2/<hash>.content.json`` (DynamoDB);
                 many user files can share one content key
  pre-hash key   an older progress row keyed by the upload itself (``user/date/id.json.content.json``); the
                 migration cannot move these
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from rag import migration as m

logger = logging.getLogger("rag_migration_health")

STUCK_AFTER_SECONDS = 2 * 3600    # processing/starting with no progress update for this long counts as stuck
LIST_LIMIT = 25
NAMESPACES = ("global", "global2", "preHash")
DOC_STATES = ("completed", "failed", "processing", "starting", "noStatus")
CHUNK_STATES = ("completed", "failed", "processing", "starting", "other")


def namespace(key: str) -> str:
    key = str(key)
    if key.startswith(m.NEW_PREFIX):
        return "global2"
    if key.startswith(m.LEGACY_PREFIX):
        return "global"
    return "preHash"


def doc_state(item: Mapping[str, Any]) -> str:
    status = item.get("parentChunkStatus")
    if status == "completed" and not item.get("terminated"):
        return "completed"
    if status == "failed" or item.get("terminated"):
        return "failed"
    if status in ("processing", "starting"):
        return status
    return "noStatus"


def chunk_counts(item: Mapping[str, Any]) -> Dict[str, int]:
    if "_chunkCounts" in item:                  # a row the inventory scan already reduced to its counts
        return dict(item["_chunkCounts"])
    counts = {state: 0 for state in CHUNK_STATES}
    children = ((item.get("data") or {}).get("childChunks")) or {}
    for chunk in children.values():
        status = (chunk or {}).get("status") if isinstance(chunk, Mapping) else None
        counts[status if status in CHUNK_STATES else "other"] += 1
    return counts


def _brief(item: Mapping[str, Any], state: str, counts: Mapping[str, int]) -> Dict[str, Any]:
    return {
        "contentKey": item.get("object_id"),
        "state": state,
        "owner": item.get("originalCreator"),
        "totalChunks": int(item.get("totalChunks") or 0),
        "chunksDone": counts["completed"],
        "chunksFailed": counts["failed"],
        "lastUpdated": item.get("lastUpdated"),
    }


def live_progress(progress_table: Any, docs: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Where each document being migrated stands right now: one cheap read of its new embedding's progress row."""
    rows: List[Dict[str, Any]] = []
    for doc in docs:
        new_key = doc.get("newKey") or m.new_key_for(doc["contentKey"])
        item = progress_table.get_item(Key={"object_id": new_key}).get("Item")
        brief = _brief(item, doc_state(item), chunk_counts(item)) if item else {
            "contentKey": new_key, "state": "starting", "owner": doc.get("owner"), "totalChunks": 0,
            "chunksDone": 0, "chunksFailed": 0, "lastUpdated": None}
        rows.append({**brief, "legacyKey": doc["contentKey"], "startedAt": doc.get("startedAt")})
    return rows


def compute_health(progress_items: Iterable[Mapping[str, Any]], pointer_rows: Iterable[Mapping[str, Any]], now: float) -> Dict[str, Any]:
    """Counts of uploads, documents and chunks by state, plus the documents that need attention."""
    pointer_rows = list(pointer_rows)
    referenced = {row["textLocationKey"] for row in pointer_rows if row.get("textLocationKey")}
    user_files = {ns: 0 for ns in NAMESPACES}
    unique_content: Dict[str, set] = {ns: set() for ns in NAMESPACES}
    for row in pointer_rows:
        key = row.get("textLocationKey")
        if not key:
            continue
        unique_content[namespace(key)].add(key)
        if "hash" in row:
            user_files[namespace(key)] += 1

    documents = {ns: {**{state: 0 for state in DOC_STATES}, "total": 0} for ns in NAMESPACES}
    chunks = {ns: {**{state: 0 for state in CHUNK_STATES}, "docsWithFailedChunks": 0} for ns in NAMESPACES}
    stuck = {ns: 0 for ns in NAMESPACES}
    orphans = {ns: 0 for ns in NAMESPACES}
    attention: List[Dict[str, Any]] = []
    in_flight: List[Dict[str, Any]] = []

    for item in progress_items:
        key = str(item.get("object_id", ""))
        ns = namespace(key)
        state = doc_state(item)
        counts = chunk_counts(item)
        documents[ns]["total"] += 1
        documents[ns][state] += 1
        for name in CHUNK_STATES:
            chunks[ns][name] += counts[name]
        if counts["failed"]:
            chunks[ns]["docsWithFailedChunks"] += 1
        if ns != "preHash" and key not in referenced:
            orphans[ns] += 1
        age = now - m.to_epoch(item.get("lastUpdated")) if item.get("lastUpdated") else None
        is_stuck = state in ("processing", "starting") and (age is None or age > STUCK_AFTER_SECONDS)
        if is_stuck:
            stuck[ns] += 1
        if ns == "preHash":
            continue
        brief = _brief(item, state, counts)
        if state == "failed" or is_stuck or (state == "completed" and counts["failed"]):
            attention.append({**brief, "reason": "failed" if state == "failed" else "stuck" if is_stuck else "failed chunks"})
        elif state in ("processing", "starting"):
            in_flight.append(brief)

    newest = lambda row: m.to_epoch(row.get("lastUpdated")) if row.get("lastUpdated") else 0
    attention.sort(key=newest, reverse=True)
    in_flight.sort(key=newest, reverse=True)
    return {
        "generatedAt": int(now),
        "uploads": {"userFiles": user_files, "uniqueContent": {ns: len(keys) for ns, keys in unique_content.items()}},
        "documents": documents,
        "chunks": chunks,
        "stuck": stuck,
        "stuckAfterSeconds": STUCK_AFTER_SECONDS,
        "orphans": orphans,
        "attention": attention[:LIST_LIMIT],
        "attentionCount": len(attention),
        "inFlight": in_flight[:LIST_LIMIT],
        "inFlightCount": len(in_flight),
    }


def queue_depths(sqs: Any, queue_urls: Mapping[str, str]) -> Dict[str, Dict[str, Any]]:
    """Messages waiting / being worked on / delayed for each queue. A queue that cannot be read reports why."""
    depths: Dict[str, Dict[str, Any]] = {}
    for label, url in queue_urls.items():
        try:
            attributes = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=[
                "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed",
            ])["Attributes"]
            depths[label] = {
                "waiting": int(attributes.get("ApproximateNumberOfMessages", 0)),
                "working": int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0)),
                "delayed": int(attributes.get("ApproximateNumberOfMessagesDelayed", 0)),
            }
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code") or type(error).__name__
            depths[label] = {"waiting": None, "working": None, "delayed": None, "error": str(code)}
    return depths


def describe_files(hash_table: Any, files_table: Any, content_key: str, max_files: int = 5) -> Dict[str, Any]:
    """Who uploaded this document and what the file is: the upload pointers for a content key, with the name and type
    from the user's file record. ``uploads`` counts every pointer; ``files`` describes the first ``max_files``."""
    from boto3.dynamodb.conditions import Key

    kwargs: Dict[str, Any] = {"IndexName": "TextLocationIndex", "KeyConditionExpression": Key("textLocationKey").eq(content_key)}
    pointers: List[Mapping[str, Any]] = []
    while True:
        response = hash_table.query(**kwargs)
        pointers.extend(row for row in response.get("Items", []) if "hash" in row)       # upload pointers, not the content row
        if not response.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    files = []
    for row in pointers[:max_files]:
        record: Mapping[str, Any] = {}
        if files_table is not None:
            try:
                record = files_table.get_item(Key={"id": row["id"]}).get("Item") or {}
            except Exception:
                record = {}
        info = record.get("data") if isinstance(record.get("data"), Mapping) else {}
        files.append({
            "key": row["id"],
            "sourceUrl": info.get("sourceUrl") if info.get("isScrapedContent") else None,
            **m.file_kind(record or None),
            "owner": row.get("originalCreator") or str(row["id"]).split("/")[0],
            "name": record.get("name"),
            "type": record.get("type"),
            "createdAt": row.get("createdAt"),
        })
    return {"uploads": len(pointers), "files": files}


def search_files(
    query: str, *, files_table: Any, hash_table: Any, progress_table: Any, store: Any, limit: int = 25,
) -> Dict[str, Any]:
    """Find uploaded files by part of their name or key (case-insensitive) and say where each one stands.

    Scans the file records once, so it is for an admin looking for a particular file, not for listing everything.
    A file with no embedding (RAG off, or uploaded before content hashing) is reported with no content key."""
    needle = str(query or "").strip().lower()
    if len(needle) < 2:
        raise ValueError("Type at least 2 characters")
    kwargs: Dict[str, Any] = {
        "ProjectionExpression": "#i, #n, #t", "ExpressionAttributeNames": {"#i": "id", "#n": "name", "#t": "type"}}
    matches: List[Mapping[str, Any]] = []
    truncated = False
    while True:
        response = files_table.scan(**kwargs)
        for item in response.get("Items", []):
            if needle in str(item.get("name", "")).lower() or needle in str(item.get("id", "")).lower():
                if len(matches) >= limit:
                    truncated = True
                    break
                matches.append(item)
        if truncated or not response.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    found: List[Dict[str, Any]] = []
    for item in matches:
        pointer = hash_table.get_item(Key={"id": item["id"]}).get("Item") or {}
        content_key = pointer.get("textLocationKey")
        row: Dict[str, Any] = {
            "key": item["id"], "name": item.get("name"), "type": item.get("type"),
            "owner": str(item["id"]).split("/")[0], "contentKey": content_key, "state": None,
            "chunksDone": 0, "totalChunks": 0, "migration": None,
        }
        if content_key:
            progress = progress_table.get_item(Key={"object_id": content_key}).get("Item")
            if progress:
                brief = _brief(progress, doc_state(progress), chunk_counts(progress))
                row.update(state=brief["state"], chunksDone=brief["chunksDone"], totalChunks=brief["totalChunks"])
            legacy = content_key if content_key.startswith(m.LEGACY_PREFIX) else (m.legacy_key_for(content_key) if content_key.startswith(m.NEW_PREFIX) else None)
            doc = store.get_doc(legacy) if legacy else None
            row["migration"] = doc["status"] if doc else None
        found.append(row)
    return {"files": found, "truncated": truncated}


def access_summary(rows: List[Mapping[str, Any]]) -> Dict[str, int]:
    """Who can read a document, by kind: assistants are principals whose id starts with ``astp``."""
    summary = {"users": 0, "assistants": 0, "groups": 0}
    for row in rows:
        principal = str(row.get("principal") or row.get("principal_id") or "")
        kind = str(row.get("kind") or row.get("principal_type") or "user")
        if principal.startswith("astp"):
            summary["assistants"] += 1
        elif kind != "user":
            summary["groups"] += 1
        else:
            summary["users"] += 1
    return summary


BROWSE_STATES = ("failed", "stuck", "processing", "starting", "completed", "failedChunks", "noStatus")
BROWSE_MAX = 100


def matches_state(item: Mapping[str, Any], wanted: str, now: float) -> bool:
    """Does this progress row belong in the ``wanted`` list? (same rules as compute_health)"""
    state = doc_state(item)
    if wanted == "failedChunks":
        return state != "failed" and chunk_counts(item)["failed"] > 0
    if wanted == "stuck":
        updated = item.get("lastUpdated")
        age = now - m.to_epoch(updated) if updated else None
        return state in ("processing", "starting") and (age is None or age > STUCK_AFTER_SECONDS)
    return state == wanted


def browse(
    progress_table: Any, *, state: str, ns: str, now: float, limit: int = 50, start: Optional[str] = None,
    describe: Optional[Callable[[str], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """One page of documents in a state, newest first within the page. Read-only; ``next`` resumes the scan."""
    if state not in BROWSE_STATES:
        raise ValueError(f"state must be one of {BROWSE_STATES}")
    if ns not in ("global", "global2"):
        raise ValueError("namespace must be global or global2")
    limit = max(1, min(BROWSE_MAX, int(limit)))
    prefix = m.LEGACY_PREFIX if ns == "global" else m.NEW_PREFIX
    kwargs: Dict[str, Any] = {"FilterExpression": "begins_with(object_id, :p)", "ExpressionAttributeValues": {":p": prefix}}
    if start:
        kwargs["ExclusiveStartKey"] = {"object_id": start}
    found: List[Dict[str, Any]] = []
    next_key: Optional[str] = None
    while True:
        response = progress_table.scan(**kwargs)
        for item in response.get("Items", []):
            if matches_state(item, state, now):
                found.append(_brief(item, doc_state(item), chunk_counts(item)))
        last = response.get("LastEvaluatedKey")
        if len(found) >= limit:
            next_key = (last or {}).get("object_id")
            break
        if not last:
            break
        kwargs["ExclusiveStartKey"] = last
    found.sort(key=lambda d: m.to_epoch(d.get("lastUpdated")) if d.get("lastUpdated") else 0, reverse=True)
    if describe:
        for doc in found:
            try:
                doc["file"] = describe(doc["contentKey"])
            except Exception as error:                     # a missing detail never hides the document
                logger.warning("Could not describe %s: %s", doc["contentKey"], error)
    return {"documents": found, "next": next_key}


def lookup(
    query: str, *, hash_table: Any, progress_table: Any, object_access_table: Any, store: Any, files_table: Any = None,
) -> Dict[str, Any]:
    """Everything known about one document, found by user file key or content key."""
    from boto3.dynamodb.conditions import Key

    query = str(query or "").strip()
    if not query:
        raise ValueError("Enter a user file key or a content key")
    pointer: Optional[Mapping[str, Any]] = None
    content_key = query
    if not (query.startswith(m.LEGACY_PREFIX) or query.startswith(m.NEW_PREFIX)):
        pointer = hash_table.get_item(Key={"id": query}).get("Item")
        if not pointer or not pointer.get("textLocationKey"):
            raise ValueError("No document with that key")
        content_key = pointer["textLocationKey"]
    twin = m.new_key_for(content_key) if content_key.startswith(m.LEGACY_PREFIX) else m.legacy_key_for(content_key)

    def progress_summary(key: str) -> Optional[Dict[str, Any]]:
        item = progress_table.get_item(Key={"object_id": key}).get("Item")
        if not item:
            return None
        counts = chunk_counts(item)
        failed = sorted(
            (int(number) for number, chunk in ((item.get("data") or {}).get("childChunks") or {}).items()
             if isinstance(chunk, Mapping) and chunk.get("status") == "failed"))
        return {**_brief(item, doc_state(item), counts), "chunks": counts, "failedChunkNumbers": failed[:50]}

    def principals(key: str) -> List[Dict[str, Any]]:
        rows = object_access_table.query(KeyConditionExpression=Key("object_id").eq(key)).get("Items", [])
        return [{"principal": r.get("principal_id"), "level": r.get("permission_level"), "kind": r.get("principal_type")} for r in rows][:100]

    def uploads_of(key: str) -> Dict[str, Any]:
        return describe_files(hash_table, files_table, key)

    def with_summary(access: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"access": access, "accessSummary": access_summary(access)}

    legacy, new = (content_key, twin) if content_key.startswith(m.LEGACY_PREFIX) else (twin, content_key)
    doc = store.get_doc(legacy)
    return {
        "query": query,
        "contentKey": content_key,
        "pointer": ({k: pointer.get(k) for k in ("id", "hash", "textLocationKey", "createdAt", "migratedFrom")} if pointer else None),
        # uploads point at the old key until the switch, then at the new one
        "file": uploads_of(legacy) if uploads_of(legacy)["uploads"] else uploads_of(new),
        "legacy": {"key": legacy, "progress": progress_summary(legacy), **with_summary(principals(legacy))},
        "global2": {"key": new, "progress": progress_summary(new), **with_summary(principals(new))},
        "migration": m.native(doc) if doc else None,
    }
