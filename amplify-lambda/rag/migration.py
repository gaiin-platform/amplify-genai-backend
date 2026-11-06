"""State and rules for migrating legacy (PostgreSQL, ``global/``) RAG documents to
global2 (DynamoDB vectors, Nova). Pure logic + a thin DynamoDB store; the runner
lives in rag/migration_runner.py.

Unit of migration: one legacy content key ``global/<hash>.content.json``. Its
global2 twin is ``global2/<hash>.content.json`` (same hash, same bytes).

A document moves through explicit, reversible steps:

    planned -> building -> built -> cutover -> cleaned
                  |          |         |
                failed    (retry)   rolledback

  planned    chosen by scope (bulk) or by a first retrieval (migrate on access)
  building   a replay was sent; global2 text + embeddings are being created.
             Nothing user-visible changes: live pointers and access stay on global/.
  built      global2 embeddings are complete and verified
  cutover    access copied to the new key, then every pointer repointed (reversible)
  cleaned    legacy PostgreSQL rows removed by the explicit cleanup button

Only embedded documents are migrated. RAG-off chat attachments have no embeddings;
their legacy text keeps working and needs no migration.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

LEGACY_PREFIX = "global/"
NEW_PREFIX = "global2/"
DAY = 86400

PLANNED, BUILDING, BUILT = "planned", "building", "built"
CUTOVER, ROLLED_BACK, FAILED, CLEANED = "cutover", "rolledback", "failed", "cleaned"
ALL_STATUSES = (PLANNED, BUILDING, BUILT, CUTOVER, ROLLED_BACK, FAILED, CLEANED)
# A document in one of these needs no further bulk work.
FINISHED = frozenset({CUTOVER, CLEANED})

# Costs of a migration rebuild go to the admin who started it, under this account, never to the document's owner.
MIGRATION_CHARGE_ACCOUNT = "rag_migration"

ACTIVITY_WINDOWS = (None, 30, 90, 180, 365)   # days; None = any
MODES = ("assistant", "all")
KINDS = ("upload", "website", "drive")                    # where a document came from
ASSISTANT_TYPES = ("personal", "group")        # which kind of assistant uses a document (layered assistants only point at these)


def is_group_assistant(record: Mapping[str, Any]) -> bool:
    """A group assistant has an astg/ record id, an astgp/ public id, or a groupId (verified in dev: 116 of 791 records)."""
    data = record.get("data") if isinstance(record.get("data"), Mapping) else {}
    return bool(
        str(record.get("id", "")).startswith("astg/") or str(record.get("assistantId", "")).startswith("astgp/")
        or record.get("groupId") or data.get("groupId")
    )

DEFAULT_CONFIG: Dict[str, Any] = {
    "scope": {"mode": "assistant", "activeWithinDays": None, "kinds": None, "assistantTypes": None},
    "batchSize": 10,          # documents started per minute
    "maxInFlight": 20,        # documents building at once
    "autoCutover": False,     # cut over as soon as a document is built
    "migrateOnAccess": False, # queue a document the first time it is retrieved
    "maxDocs": None,          # cap on how many documents one start plans
}


# --------------------------------------------------------------------------- #
# Keys and small helpers
# --------------------------------------------------------------------------- #
def is_legacy_key(key: str) -> bool:
    return str(key).startswith(LEGACY_PREFIX)


def new_key_for(legacy_key: str) -> str:
    if not is_legacy_key(legacy_key):
        raise ValueError(f"not a legacy content key: {legacy_key}")
    return NEW_PREFIX + legacy_key[len(LEGACY_PREFIX):]


def admin_credentials(access_token: str, admin_user: str) -> Dict[str, Any]:
    """What the embedding worker needs to generate questions for a rebuild started by an admin.

    The chat call is made with the admin's own login, so it is recorded under the admin and the migration account,
    never under the document's owner. Costs the worker records itself use ``charge_user`` / ``charge_account``.
    """
    return {
        "access_token": access_token,
        "account": MIGRATION_CHARGE_ACCOUNT,
        "rate_limit": {"period": "Unlimited", "rate": None},
        "charge_user": admin_user,
        "charge_account": MIGRATION_CHARGE_ACCOUNT,
    }


def token_expiry(access_token: str) -> Optional[int]:
    """When a login token stops working (its ``exp`` claim), or None if it cannot be read. Not a security check."""
    import base64
    import json
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return int(claims["exp"])
    except Exception:
        return None


def legacy_key_for(new_key: str) -> str:
    """The ``global/`` twin of a ``global2/`` content key (same hash)."""
    if not str(new_key).startswith(NEW_PREFIX):
        raise ValueError(f"not a global2 content key: {new_key}")
    return LEGACY_PREFIX + new_key[len(NEW_PREFIX):]


def native(value: Any) -> Any:
    """DynamoDB Decimals -> int/float so results are JSON-serializable."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {key: native(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(inner) for inner in value]
    return value


def decimal_safe(value: Any) -> Any:
    """Floats -> Decimal, so a result can be stored in DynamoDB (the polled-request record rejects floats)."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {key: decimal_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [decimal_safe(inner) for inner in value]
    return value


def to_epoch(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    text = str(value)
    if text.replace(".", "", 1).isdigit():
        return float(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def sanitize_config(patch: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and clamp admin-supplied settings; unknown keys are ignored."""
    clean: Dict[str, Any] = {}
    if "scope" in patch:
        scope = patch["scope"] or {}
        mode = scope.get("mode", "assistant")
        window = scope.get("activeWithinDays")
        if mode not in MODES:
            raise ValueError(f"scope.mode must be one of {MODES}")
        if window not in ACTIVITY_WINDOWS:
            raise ValueError(f"scope.activeWithinDays must be one of {ACTIVITY_WINDOWS}")
        kinds = scope.get("kinds") or None
        types = scope.get("assistantTypes") or None
        if kinds is not None and not set(kinds) <= set(KINDS):
            raise ValueError(f"scope.kinds must be a subset of {KINDS}")
        if types is not None and not set(types) <= set(ASSISTANT_TYPES):
            raise ValueError(f"scope.assistantTypes must be a subset of {ASSISTANT_TYPES}")
        clean["scope"] = {
            "mode": mode, "activeWithinDays": window,
            "kinds": sorted(set(kinds)) if kinds and len(set(kinds)) < len(KINDS) else None,
            "assistantTypes": sorted(set(types)) if types and len(set(types)) < len(ASSISTANT_TYPES) else None,
        }
    for name, low, high in (("batchSize", 1, 50), ("maxInFlight", 1, 200)):
        if name in patch:
            clean[name] = max(low, min(high, int(patch[name])))
    if "maxDocs" in patch:
        clean["maxDocs"] = None if patch["maxDocs"] in (None, "", 0) else max(1, int(patch["maxDocs"]))
    for name in ("autoCutover", "migrateOnAccess"):
        if name in patch:
            clean[name] = bool(patch[name])
    return clean


# --------------------------------------------------------------------------- #
# Replay messages (used by the document processor in core.py)
# --------------------------------------------------------------------------- #
def replay_message(source_key: str, input_bucket: str, new_key: str) -> Dict[str, Any]:
    """The SQS body that rebuilds a document in global2 without touching live state."""
    return {
        "force_reprocess": True, "migration": True, "target_namespace": "global2", "new_key": new_key,
        "s3": {"bucket": {"name": input_bucket}, "object": {"key": source_key}},
    }


def replay_target(message: Mapping[str, Any]) -> Optional[str]:
    """The global2 key a replay message must produce, or None for a normal upload.

    A message flagged ``migration`` without a valid target is treated as malformed
    by the caller rather than silently processed as a normal upload (which would
    repoint live pointers).
    """
    if not message.get("migration"):
        return None
    target = str(message.get("new_key") or "")
    if not target.startswith(NEW_PREFIX):
        raise ValueError("migration message has no valid global2 target key")
    return target


def failure_item(new_key: str, reason: str, creator: Optional[str], now_iso: str) -> Dict[str, Any]:
    """Progress row recording that a replay was refused (limits, mismatch).

    ``migrationRejected`` tells the runner not to retry. The user's file and
    records are never touched by a refused replay.
    """
    return {
        "object_id": new_key, "parentChunkStatus": "failed", "terminated": True,
        "migrationRejected": True, "migrationError": str(reason)[:500],
        "originalCreator": creator or "unknown", "timestamp": now_iso, "lastUpdated": now_iso,
        "totalChunks": 0, "data": {"childChunks": {}, "namespace": "global2"},
    }


# --------------------------------------------------------------------------- #
# Inventory (pure)
# --------------------------------------------------------------------------- #
def file_kind(record: Optional[Mapping[str, Any]]) -> Dict[str, Optional[str]]:
    """Where an uploaded file came from, from its file record: a scraped web page, a file synced from a cloud
    drive (SharePoint and the like), or an ordinary upload. ``site`` is the web host or the drive provider."""
    record = record or {}
    info = record.get("data")
    if not isinstance(info, Mapping):
        info = {}
    tags = record.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if info.get("isScrapedContent") or info.get("sourceUrl"):
        from urllib.parse import urlparse
        host = urlparse(str(info.get("sourceUrl") or "")).hostname
        return {"kind": "website", "site": host}
    if "drive-integration" in tags or info.get("originalFileId"):
        return {"kind": "drive", "site": "SharePoint" if "sharepoint" in str(info.get("originalFileId", "")).lower() else "Cloud drive"}
    return {"kind": "upload", "site": None}


def content_hash_of(content_key: str) -> str:
    """The sha256 in a ``global/<sha>.content.json`` or ``global2/<sha>.content.json`` key: the id of its content row."""
    return str(content_key).split("/", 1)[-1].removesuffix(".content.json")


def last_activity(doc: Mapping[str, Any]) -> float:
    """Latest known use (a retrieval or a chat that used the document), or else when it was last embedded."""
    return max(to_epoch(doc.get("lastAccessedAt")), to_epoch(doc.get("lastUpdated")))


def in_scope(doc: Mapping[str, Any], scope: Mapping[str, Any], now: float) -> bool:
    if scope.get("mode") == "assistant":
        if not doc.get("assistantReferenced"):
            return False
        wanted = scope.get("assistantTypes")
        if wanted and not set(wanted) & set(doc.get("assistantTypes") or []):
            return False
    kinds = scope.get("kinds")
    if kinds and (doc.get("kind") or "upload") not in kinds:
        return False
    window = scope.get("activeWithinDays")
    if window and last_activity(doc) < now - window * DAY:
        return False
    return True


def _bucket(age_days: float) -> str:
    for limit, label in ((30, "d30"), (90, "d90"), (180, "d180"), (365, "d365")):
        if age_days <= limit:
            return label
    return "older"


def build_inventory(
    progress_items: Iterable[Mapping[str, Any]],
    pointer_rows: Iterable[Mapping[str, Any]],
    referenced: Set[str],
    now: float,
    migration_status: Optional[Mapping[str, str]] = None,
    file_info: Optional[Mapping[str, Mapping[str, Any]]] = None,
    referenced_by: Optional[Mapping[str, Set[str]]] = None,
) -> Dict[str, Any]:
    """Classify every embedded document.

    Returns {"docs": [...legacy docs...], "summary": {...}}. ``migration_status``
    maps legacy content key -> current migration status for documents already in
    the pipeline.
    """
    from rag import retention   # shared "is anything referencing this" rules

    migration_status = migration_status or {}
    file_info = file_info or {}
    referenced_by = referenced_by or {}
    pointers: Dict[str, List[str]] = {}
    accessed: Dict[str, Any] = {}       # content hash -> lastAccessedAt, stamped on the document's content row
    for row in pointer_rows:
        if "hash" in row and row.get("textLocationKey"):
            pointers.setdefault(row["textLocationKey"], []).append(row["id"])
        elif row.get("lastAccessedAt"):
            accessed[row["id"]] = row["lastAccessedAt"]

    docs: List[Dict[str, Any]] = []
    legacy = {"total": 0, "complete": 0, "incomplete": 0}
    global2 = {"total": 0, "complete": 0}
    for item in progress_items:
        object_id = str(item.get("object_id", ""))
        complete = item.get("parentChunkStatus") == "completed" and not item.get("terminated")
        if object_id.startswith(NEW_PREFIX):
            global2["total"] += 1
            global2["complete"] += int(complete)
            continue
        if not is_legacy_key(object_id):
            continue
        legacy["total"] += 1
        if not complete:
            legacy["incomplete"] += 1
            continue
        legacy["complete"] += 1
        keys = pointers.get(object_id, [])
        docs.append({
            "contentKey": object_id,
            "owner": item.get("originalCreator"),
            "childFiles": int(item.get("totalChunks") or 0),
            "lastUpdated": item.get("lastUpdated"),
            "lastAccessedAt": accessed.get(content_hash_of(object_id)),
            "pointerKeys": keys,
            "assistantReferenced": retention.is_referenced([object_id, *keys], referenced),
            "assistantTypes": [kind for kind, ids in referenced_by.items() if retention.is_referenced([object_id, *keys], ids)],
            "kind": (file_info.get(object_id) or {}).get("kind") or "upload",
            "site": (file_info.get(object_id) or {}).get("site"),
            "migrationStatus": migration_status.get(object_id),
        })

    candidates = [d for d in docs if d["migrationStatus"] not in FINISHED]
    ages = {"d30": 0, "d90": 0, "d180": 0, "d365": 0, "older": 0}
    for doc in candidates:
        ages[_bucket((now - last_activity(doc)) / DAY)] += 1

    matrix: Dict[str, Dict[str, Any]] = {}
    for mode in MODES:
        for window in ACTIVITY_WINDOWS:
            scope = {"mode": mode, "activeWithinDays": window}
            selected = [d for d in candidates if in_scope(d, scope, now) and d["migrationStatus"] in (None, FAILED, ROLLED_BACK)]
            child_files = sum(d["childFiles"] for d in selected)
            matrix[f"{mode}:{window or 'any'}"] = {
                "documents": len(selected),
                "childFiles": child_files,
                # upper bounds: <=10 chunks per child file; each chunk = 2 embeddings + 1 QA model call
                "estimatedEmbeddingCalls": child_files * 10 * 2,
                "estimatedQaCalls": child_files * 10,
            }

    # Counts by where a document came from, who uses it and how recently, so the page can estimate any scope.
    facets: Dict[Any, Dict[str, int]] = {}
    sites: Dict[str, int] = {}
    for doc in candidates:
        if doc["migrationStatus"] not in (None, FAILED, ROLLED_BACK):
            continue
        key = (bool(doc["assistantReferenced"]), tuple(sorted(doc["assistantTypes"])), doc["kind"], _bucket((now - last_activity(doc)) / DAY))
        cell = facets.setdefault(key, {"documents": 0, "childFiles": 0})
        cell["documents"] += 1
        cell["childFiles"] += doc["childFiles"]
        if doc["site"]:
            sites[doc["site"]] = sites.get(doc["site"], 0) + 1

    summary = {
        "generatedAt": int(now),
        "facets": [
            {"assistant": a, "types": list(t), "kind": k, "bucket": b, **cell} for (a, t, k, b), cell in sorted(facets.items())
        ],
        "sites": dict(sorted(sites.items(), key=lambda kv: -kv[1])[:20]),
        "legacy": {**legacy, "assistantReferenced": sum(d["assistantReferenced"] for d in docs),
                   "otherEmbedded": sum(not d["assistantReferenced"] for d in docs),
                   "neverAccessedSinceTracking": sum(not d["lastAccessedAt"] for d in docs)},
        "global2": global2,
        "migrated": sum(1 for d in docs if d["migrationStatus"] in FINISHED),
        "ageBuckets": ages,
        "scopes": matrix,
    }
    return {"docs": docs, "summary": summary}


# --------------------------------------------------------------------------- #
# State store
# --------------------------------------------------------------------------- #
def _is_conditional_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def copy_access(table: Any, old_key: str, new_key: str) -> int:
    """Give every principal that can read ``old_key`` the same access to ``new_key``.
    Never overwrites a grant that already exists; safe to repeat. Returns how many grants were added."""
    copied = 0
    kwargs: Dict[str, Any] = {"KeyConditionExpression": Key("object_id").eq(old_key)}
    while True:
        response = table.query(**kwargs)
        for row in response.get("Items", []):
            try:
                table.put_item(
                    Item={**row, "object_id": new_key},
                    ConditionExpression="attribute_not_exists(principal_id)",
                )
                copied += 1
            except ClientError as error:
                if not _is_conditional_failure(error):
                    raise
        if not response.get("LastEvaluatedKey"):
            return copied
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


class MigrationStore:
    """Single DynamoDB table: ``config``, ``inventory``, ``lock`` and ``doc#<key>`` items."""

    STATUS_INDEX = "status-index"

    def __init__(self, table: Any, now: Any = time.time) -> None:
        self.table = table
        self.now = now

    # ---- generic ---------------------------------------------------------- #
    def _update(self, item_id: str, fields: Mapping[str, Any]) -> None:
        names, values, sets, removes = {}, {}, [], []
        for index, (name, value) in enumerate(fields.items()):
            names[f"#f{index}"] = name
            if value is None:
                removes.append(f"#f{index}")
            else:
                values[f":v{index}"] = value
                sets.append(f"#f{index} = :v{index}")
        expression = ""
        if sets:
            expression += "SET " + ", ".join(sets)
        if removes:
            expression += (" " if expression else "") + "REMOVE " + ", ".join(removes)
        kwargs: Dict[str, Any] = {"Key": {"id": item_id}, "UpdateExpression": expression, "ExpressionAttributeNames": names}
        if values:
            kwargs["ExpressionAttributeValues"] = values
        self.table.update_item(**kwargs)

    # ---- config ----------------------------------------------------------- #
    def get_config(self) -> Dict[str, Any]:
        item = native(self.table.get_item(Key={"id": "config"}).get("Item") or {})
        item.pop("id", None)
        return {**DEFAULT_CONFIG, **item}

    def update_config(self, patch: Mapping[str, Any], updated_by: Optional[str] = None) -> Dict[str, Any]:
        clean = sanitize_config(patch)
        if updated_by:
            clean["updatedBy"] = updated_by
        clean["updatedAt"] = int(self.now())
        self._update("config", clean)
        return self.get_config()

    def set_flags(self, **flags: Any) -> None:
        """Internal flag changes (no sanitizing of extra keys)."""
        self._update("config", {**flags, "updatedAt": int(self.now())})

    # ---- inventory -------------------------------------------------------- #
    def get_inventory(self) -> Optional[Dict[str, Any]]:
        item = native(self.table.get_item(Key={"id": "inventory"}).get("Item") or {})
        return item.get("summary")

    def put_inventory(self, summary: Mapping[str, Any]) -> None:
        self.table.put_item(Item={"id": "inventory", "summary": dict(summary)})

    # ---- costs (what the migration has cost, refreshed on demand) ---------- #
    def get_costs(self) -> Optional[Dict[str, Any]]:
        return native(self.table.get_item(Key={"id": "costs"}).get("Item") or {}).get("summary")

    def put_costs(self, summary: Mapping[str, Any]) -> None:
        import json
        self.table.put_item(Item={"id": "costs", "summary": json.loads(json.dumps(summary), parse_float=Decimal)})

    # ---- lock (so overlapping scheduled runs never double-process) -------- #
    def acquire_lock(self, ttl_seconds: int, name: str = "lock") -> bool:
        now = int(self.now())
        try:
            self.table.put_item(
                Item={"id": name, "expiresAt": now + ttl_seconds},
                ConditionExpression="attribute_not_exists(id) OR expiresAt < :now",
                ExpressionAttributeValues={":now": now},
            )
            return True
        except ClientError as error:
            if _is_conditional_failure(error):
                return False
            raise

    def release_lock(self, name: str = "lock") -> None:
        self.table.delete_item(Key={"id": name})

    def lock_active(self, name: str = "lock") -> bool:
        """True while someone holds the named lock (one that has expired no longer counts)."""
        item = native(self.table.get_item(Key={"id": name}).get("Item") or {})
        return bool(item) and item.get("expiresAt", 0) >= int(self.now())

    # ---- documents -------------------------------------------------------- #
    @staticmethod
    def doc_id(content_key: str) -> str:
        return f"doc#{content_key}"

    def get_doc(self, content_key: str) -> Optional[Dict[str, Any]]:
        item = self.table.get_item(Key={"id": self.doc_id(content_key)}).get("Item")
        return native(item) if item else None

    def add_doc_if_absent(self, content_key: str, fields: Mapping[str, Any]) -> bool:
        now = int(self.now())
        item = {"id": self.doc_id(content_key), "contentKey": content_key, "status": PLANNED,
                "attempts": 0, "createdAt": now, "updatedAt": now, **fields}
        try:
            self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
            return True
        except ClientError as error:
            if _is_conditional_failure(error):
                return False
            raise

    def update_doc(self, content_key: str, **fields: Any) -> None:
        self._update(self.doc_id(content_key), {**fields, "updatedAt": int(self.now())})

    def delete_planned_doc(self, content_key: str) -> bool:
        """Remove a document that is still only queued. One that has started is left alone."""
        try:
            self.table.delete_item(
                Key={"id": self.doc_id(content_key)},
                ConditionExpression="#s = :planned", ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":planned": PLANNED})
            return True
        except ClientError as error:
            if _is_conditional_failure(error):
                return False
            raise

    def docs_by_status(self, status: str, limit: int = 100) -> List[Dict[str, Any]]:
        response = self.table.query(
            IndexName=self.STATUS_INDEX,
            KeyConditionExpression=Key("status").eq(status),
            Limit=limit,
            ScanIndexForward=True,   # oldest first
        )
        return [native(item) for item in response.get("Items", [])]

    def count_by_status(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for status in ALL_STATUSES:
            total = 0
            kwargs: Dict[str, Any] = {"IndexName": self.STATUS_INDEX, "KeyConditionExpression": Key("status").eq(status), "Select": "COUNT"}
            while True:
                response = self.table.query(**kwargs)
                total += response.get("Count", 0)
                if not response.get("LastEvaluatedKey"):
                    break
                kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            counts[status] = total
        return counts
