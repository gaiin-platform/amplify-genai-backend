"""Runs the legacy -> global2 RAG migration (see rag/migration.py for the model).

Safety properties, each covered by tests:
  * Building a global2 copy changes nothing user-visible: live pointers and access
    stay on ``global/`` until an explicit cutover.
  * Cutover first verifies the new embeddings are complete, then copies access to
    the new key, and only then repoints (so a half-finished cutover still works).
  * Every repointed row is recorded, so rollback restores exactly those rows.
  * Cleanup only runs after the admin presses the cleanup button, and requires complete new
    embeddings, no remaining references, and the PostgreSQL cleanup function; it never runs on a partially configured system.
  * Everything AWS-facing is injected, so the logic is testable without AWS.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from pycommon.rag.contracts import NOVA_EMBEDDING_MODEL_ID
from rag import assistant_refs, delete_policy, migration as m, migration_costs, migration_health, orphans, retention
from rag.migration import (
    BUILDING, BUILT, CLEANED, CUTOVER, FAILED, PLANNED, ROLLED_BACK, new_key_for,
)

logger = logging.getLogger("rag_migration")
logger.setLevel(logging.INFO)

MAX_ATTEMPTS = 3
STALL_SECONDS = 2 * 3600   # a document being built whose progress has not moved for this long has stalled and is marked failed
INVENTORY_MAX_AGE_SECONDS = 24 * 3600
INVENTORY_LOCK = "inventory-scan"
INVENTORY_SCAN_SECONDS = 960   # a count that dies leaves its lock behind; it frees itself just after the longest function (900 s)
# The existing daily job deletes every per-document RAG secret at ~03:00 UTC, so
# nothing is started in a window around it (a started document would lose its secret).
SECRET_SWEEP_WINDOW = ((2, 55), (3, 20))


def in_secret_sweep_window(now: float) -> bool:
    moment = datetime.fromtimestamp(now, tz=timezone.utc)
    start, end = SECRET_SWEEP_WINDOW
    minutes = moment.hour * 60 + moment.minute
    return start[0] * 60 + start[1] <= minutes < end[0] * 60 + end[1]


def _not_found(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


PROGRESS_SCAN = {      # every name is aliased: DynamoDB reserves words such as "terminated", "data" and "hash"
    "ProjectionExpression": "#o, #p, #t, #n, #u, #c, #d.#cc",
    "ExpressionAttributeNames": {"#o": "object_id", "#p": "parentChunkStatus", "#t": "terminated", "#n": "totalChunks",
                                 "#u": "lastUpdated", "#c": "originalCreator", "#d": "data", "#cc": "childChunks"},
}
POINTER_SCAN = {
    "ProjectionExpression": "#i, #h, #l, #a",
    "ExpressionAttributeNames": {"#i": "id", "#h": "hash", "#l": "textLocationKey", "#a": "lastAccessedAt"},
}


def compact_progress(item: Mapping[str, Any]) -> Dict[str, Any]:
    """A progress row without its per-chunk map: only what the counts need, so hundreds of thousands fit in memory."""
    row = {key: item[key] for key in ("object_id", "parentChunkStatus", "terminated", "totalChunks", "lastUpdated", "originalCreator") if key in item}
    row["_chunkCounts"] = migration_health.chunk_counts(item)
    return row


def compact_pointer(row: Mapping[str, Any]) -> Dict[str, Any]:
    """A hash-table row reduced to what the counts read (whether it is an upload, where it points, when it was last used)."""
    out = {"id": row.get("id")}
    if "hash" in row:
        out["hash"] = True
    for key in ("textLocationKey", "lastAccessedAt"):
        if row.get(key):
            out[key] = row[key]
    return out


def _scan_segment(table: Any, kwargs: Dict[str, Any], transform: Callable[[Mapping[str, Any]], Any]) -> List[Any]:
    out: List[Any] = []
    while True:
        response = table.scan(**kwargs)
        out.extend(transform(item) for item in response.get("Items", []))
        if not response.get("LastEvaluatedKey"):
            return out
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def scan_compact(table: Any, transform: Callable[[Mapping[str, Any]], Any], segments: int = 1, **kwargs: Any) -> List[Any]:
    """Every row of a table, each reduced by ``transform`` as it arrives, read by ``segments`` parallel scans."""
    if segments <= 1:
        return _scan_segment(table, dict(kwargs), transform)
    with ThreadPoolExecutor(max_workers=segments) as pool:
        parts = pool.map(lambda n: _scan_segment(table, {**kwargs, "Segment": n, "TotalSegments": segments}, transform), range(segments))
        return [row for part in parts for row in part]


class Migrator:
    def __init__(
        self,
        *,
        store: m.MigrationStore,
        progress_table: Any,
        hash_table: Any,
        object_access_table: Any,
        assistant_tables: List[Any],
        s3: Any,
        sqs: Any,
        queue_url: str,
        input_bucket: str,
        text_bucket: str,
        store_rag_secrets: Callable[[str, Mapping[str, Any]], Any],
        legacy_cleanup: Optional[Callable[[List[str]], Mapping[str, Any]]] = None,
        now: Callable[[], float] = time.time,
        queue_urls: Optional[Mapping[str, str]] = None,
        files_table: Any = None,
        admin_table: Any = None,
        model_rate_table: Any = None,
        cost_tables: Optional[Tuple[Any, Any]] = None,
        usage_table: Any = None,
        scan_segments: int = 1,
    ) -> None:
        self.store, self.progress_table, self.hash_table = store, progress_table, hash_table
        self.object_access_table, self.assistant_tables = object_access_table, assistant_tables
        self.s3, self.sqs, self.queue_url = s3, sqs, queue_url
        self.input_bucket, self.text_bucket = input_bucket, text_bucket
        self.store_rag_secrets = store_rag_secrets
        self.legacy_cleanup, self.now = legacy_cleanup, now
        self.queue_urls = dict(queue_urls or {})
        self.files_table = files_table              # name and type of the uploaded files, for the document details
        self.admin_table = admin_table              # the admin config, for the document retention days and the question model
        self.model_rate_table = model_rate_table    # model prices, for the cost estimate
        self.cost_tables = cost_tables              # (current month, history) cost tables of the chat service
        self.usage_table = usage_table              # the chat service's usage records, for what a question call costs
        self._profiles: Dict[Optional[str], Any] = {}
        self.scan_segments = max(1, int(scan_segments))   # parallel readers per table scan (a large system has hundreds of thousands of rows)

    # ------------------------------------------------------------------ #
    # data access helpers
    # ------------------------------------------------------------------ #
    def _pointer_rows(self, content_key: str) -> List[Mapping[str, Any]]:
        kwargs: Dict[str, Any] = {"IndexName": "TextLocationIndex", "KeyConditionExpression": Key("textLocationKey").eq(content_key)}
        rows: List[Mapping[str, Any]] = []
        while True:
            response = self.hash_table.query(**kwargs)
            rows.extend(response.get("Items", []))
            if not response.get("LastEvaluatedKey"):
                return rows
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def _progress(self, content_key: str) -> Mapping[str, Any]:
        return self.progress_table.get_item(Key={"object_id": content_key}).get("Item") or {}

    def _progress_complete(self, content_key: str) -> bool:
        item = self._progress(content_key)
        return item.get("parentChunkStatus") == "completed" and not item.get("terminated")

    def _source_exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.input_bucket, Key=key)
            return True
        except ClientError as error:
            if _not_found(error):
                return False
            raise

    def _scan_inputs(self):
        progress = scan_compact(self.progress_table, compact_progress, self.scan_segments, **PROGRESS_SCAN)
        pointers = scan_compact(self.hash_table, compact_pointer, self.scan_segments, **POINTER_SCAN)
        # Personal vs group comes from each assistant record itself (group assistants live in the assistants table too).
        # Only the newest version of each assistant counts: it is the one chat uses; older versions are history.
        # The other tables (layered assistants, groups) only add to "in use": they point at assistants, not at files.
        by_kind = {kind: set() for kind in m.ASSISTANT_TYPES}
        referenced = set()
        for index, table in enumerate(self.assistant_tables):
            records = assistant_refs.latest_versions(table).values() if index == 0 else retention._scan_all(table)
            for item in records:
                strings = retention.collect_strings(item)
                referenced |= strings
                if index == 0:
                    by_kind["group" if m.is_group_assistant(item) else "personal"] |= strings
        self._referenced_by = by_kind
        status = {}
        for doc in self.all_docs():
            status[doc["contentKey"]] = doc["status"]
        return progress, pointers, referenced, status

    def _file_info(self, pointers) -> Dict[str, Dict[str, Any]]:
        """Content key -> where it came from (web page, drive, upload), read from the first upload's file record."""
        if self.files_table is None:
            return {}
        first: Dict[str, str] = {}
        for row in pointers:
            if "hash" in row and str(row.get("textLocationKey", "")).startswith(m.LEGACY_PREFIX):
                first.setdefault(row["textLocationKey"], row["id"])
        info: Dict[str, Dict[str, Any]] = {}
        uploads = list(first.items())
        batches = [uploads[i:i + 100] for i in range(0, len(uploads), 100)]       # BatchGetItem takes up to 100 keys

        def read(batch):
            return batch, self._file_records([upload for _, upload in batch])

        with ThreadPoolExecutor(max_workers=self.scan_segments) as pool:
            for batch, records in pool.map(read, batches):
                for key, upload in batch:
                    info[key] = m.file_kind(records.get(upload))
        return info

    def _file_records(self, ids: List[str]) -> Dict[str, Mapping[str, Any]]:
        """The file records of these uploads (only the fields that tell where a file came from), many per request."""
        client = getattr(getattr(self.files_table, "meta", None), "client", None)
        if client is None:                                  # a table without a batch API: one read per upload
            records = {}
            for upload in ids:
                try:
                    item = self.files_table.get_item(Key={"id": upload}).get("Item")
                    if item:
                        records[upload] = item
                except Exception as error:
                    logger.warning("Could not read the file record for %s: %s", upload, error)
            return records
        name = self.files_table.name
        request: Dict[str, Any] = {name: {
            "Keys": [{"id": upload} for upload in ids],
            "ProjectionExpression": "#i, #d, #t",
            "ExpressionAttributeNames": {"#i": "id", "#d": "data", "#t": "tags"},
        }}
        records = {}
        for _ in range(8):                                  # unprocessed keys (throttling) are asked for again
            try:
                response = client.batch_get_item(RequestItems=request)
            except Exception as error:
                logger.warning("Could not read %d file records: %s", len(ids), error)
                break
            for item in response.get("Responses", {}).get(name, []):
                records[item["id"]] = item
            request = response.get("UnprocessedKeys") or {}
            if not request:
                break
            time.sleep(0.2)
        return records

    def all_docs(self):
        for item in retention._scan_all(self.store.table):
            if str(item.get("id", "")).startswith("doc#"):
                yield m.native(item)

    # ------------------------------------------------------------------ #
    # inventory + planning
    # ------------------------------------------------------------------ #
    def run_inventory(self) -> Dict[str, Any]:
        progress, pointers, referenced, status = self._scan_inputs()
        summary = m.build_inventory(
            progress, pointers, referenced, self.now(), status, self._file_info(pointers), self._referenced_by)["summary"]
        summary["pipeline"] = self.store.count_by_status()
        summary["health"] = migration_health.compute_health(progress, pointers, self.now())
        self.store.put_inventory(summary)
        return summary

    def scan_inventory(self) -> Optional[Dict[str, Any]]:
        """run_inventory, unless a count is already running (a large system takes minutes, and every tab visit used to start another).
        Returns None when one is."""
        if not self.store.acquire_lock(INVENTORY_SCAN_SECONDS, INVENTORY_LOCK):
            return None
        try:
            return self.run_inventory()
        finally:
            self.store.release_lock(INVENTORY_LOCK)

    def _question_call_profile(self, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """What a recent question-generation call billed (see migration_costs.call_profile); kept for ten minutes."""
        if not self.cost_tables or self.usage_table is None:
            return None
        cached = self._profiles.get(model)
        if cached and self.now() - cached[0] < 600:
            return cached[1]
        users = [row["id"] for row in self._admin_rows()]
        rates: Dict[str, Dict[str, float]] = {}

        def rate(model_id: str) -> Dict[str, float]:
            if model_id not in rates:
                rates[model_id] = migration_costs.rates_for(self.model_rate_table, model_id)
            return rates[model_id]

        profile = migration_costs.call_profile(self.usage_table, rate, users, self.now(), model)
        self._profiles[model] = (self.now(), profile)
        return profile

    def _admin_rows(self) -> List[Mapping[str, Any]]:
        return migration_costs.migration_admins(self.cost_tables[0]) if self.cost_tables else []

    def estimate_costs(self, keys: Optional[List[str]] = None, count: Optional[int] = None) -> Dict[str, Any]:
        """Rough cost of rebuilding the chosen documents, or of the next ``count`` queued ones. Reads only (apart from topping up
        the queue from the saved scope, as starting documents does)."""
        if keys is None:
            count = max(1, int(count or self.store.get_config()["batchSize"]))
            if len(self.store.docs_by_status(PLANNED, limit=count)) < count:
                self.plan()
            keys = [doc["contentKey"] for doc in self.store.docs_by_status(PLANNED, limit=count)]
        rows = []
        for key in keys:
            upload = next((row for row in self._pointer_rows(key) if delete_policy.is_upload_pointer(row)), None)
            info = (self.files_table.get_item(Key={"id": upload["id"]}).get("Item") or {}) if upload and self.files_table else {}
            tokens = info.get("totalTokens")
            rows.append({"contentKey": key, "name": info.get("name"), "type": info.get("type"),
                         "tokens": None if tokens is None else int(tokens), "parts": int(info.get("totalItems") or 0),
                         "childFiles": int((self._progress(key) or {}).get("totalChunks") or 0)})
        qa_model = migration_costs.qa_model_id(self.admin_table) if self.admin_table is not None else None
        nova = migration_costs.rates_for(self.model_rate_table, NOVA_EMBEDDING_MODEL_ID)
        profile = self._question_call_profile(qa_model)
        qa = migration_costs.rates_for(self.model_rate_table, qa_model) if qa_model else {"input": 0.0, "output": 0.0}
        fallback = (migration_costs.FALLBACK_CALL["input"] * qa["input"] + migration_costs.FALLBACK_CALL["output"] * qa["output"]) / 1000
        return {**migration_costs.estimate(rows, nova, profile, fallback), "qaModel": qa_model}

    def with_question_cost(self, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """A migration row with its counted question calls priced at the recent average (``qaCost``)."""
        calls = int(float((doc or {}).get("qaCalls") or 0))
        if not doc or not calls:
            return doc
        profile = self._question_call_profile()
        return {**doc, "qaCost": round(calls * profile["avgCost"], 6)} if profile else doc

    def lookup(self, query: str) -> Dict[str, Any]:
        result = migration_health.lookup(
            query, hash_table=self.hash_table, progress_table=self.progress_table,
            object_access_table=self.object_access_table, store=self.store, files_table=self.files_table)
        if isinstance(result, dict) and result.get("migration"):
            result = {**result, "migration": self.with_question_cost(result["migration"])}
        return result

    def run_costs(self) -> Dict[str, Any]:
        """What the migration has cost so far, stored for the Overview. Embedding and OCR are the recorded sums; the question
        generation total is what the chat service billed the migration account (every admin who ran it)."""
        docs = list(self.all_docs())
        started = [value for value in (d.get("startedAt") for d in docs) if value]
        summary: Dict[str, Any] = {"generatedAt": int(self.now())}
        try:
            profile = self._question_call_profile()
        except Exception as error:      # the recorded sums below do not need it; the Overview says what is missing
            logger.warning("Could not price question calls: %s", error)
            profile, summary["pricingError"] = None, str(error)[:200]
        summary["spent"] = migration_costs.spent(docs, profile["avgCost"] if profile else 0.0)
        if self.cost_tables:
            try:
                summary["billedQuestions"] = migration_costs.billed_question_cost(
                    self._admin_rows(), self.cost_tables[1], float(min(started)) if started else None, self.now())
            except Exception as error:
                logger.warning("Could not read the billed question cost: %s", error)
                summary["billedError"] = str(error)[:200]
        self.store.put_costs(summary)
        return summary

    def migrating_now(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Live progress of the documents whose new embeddings are being created (read fresh on every status call)."""
        return migration_health.live_progress(self.progress_table, self.store.docs_by_status(BUILDING, limit=limit))

    def queue_depths(self) -> Dict[str, Any]:
        return migration_health.queue_depths(self.sqs, self.queue_urls) if self.queue_urls else {}

    def browse(self, state: str, namespace: str, limit: int = 50, start: Optional[str] = None) -> Dict[str, Any]:
        return migration_health.browse(
            self.progress_table, state=state, ns=namespace, now=self.now(), limit=limit, start=start,
            describe=lambda key: migration_health.describe_files(self.hash_table, self.files_table, key, max_files=1))

    def _sweeper(self, dry_run: bool) -> "retention.Sweeper":
        """The retention sweep's own decision engine, pointed at this system's tables (reused, not copied)."""
        if self.files_table is None:
            raise RuntimeError("The file records are not available")
        days = retention.load_policy(self.admin_table)["days"] if self.admin_table is not None else None
        return retention.Sweeper(
            files_table=self.files_table, hash_table=self.hash_table, object_access_table=self.object_access_table,
            progress_table=self.progress_table, assistant_tables=self.assistant_tables, s3=self.s3,
            input_bucket=self.input_bucket, text_bucket=self.text_bucket, days=days, dry_run=dry_run, now=self.now)

    def _group_table(self):
        return self.assistant_tables[2] if len(self.assistant_tables) >= 3 else None      # assistants, layered, groups

    def orphan_report(self) -> Dict[str, Any]:
        """Files kept only for assistants that no longer use them. Reads only."""
        return orphans.report(self._sweeper(True), self.store, self._group_table(), self.now())

    def remove_orphans(self, category: str, admin: str, limit: Optional[int] = None) -> Dict[str, Any]:
        """``limit`` is how many to delete this time (so a few can be tried first); never above the per-request cap."""
        count = orphans.REMOVE_LIMIT if limit is None else max(1, min(int(limit), orphans.REMOVE_LIMIT))
        return orphans.remove(self._sweeper(False), self.store, self._group_table(), category, admin, self.now(), count)

    def search(self, query: str) -> Dict[str, Any]:
        return migration_health.search_files(
            query, files_table=self.files_table, hash_table=self.hash_table,
            progress_table=self.progress_table, store=self.store)

    def clear_queue(self) -> Dict[str, int]:
        """Take every document that is only waiting to start out of the queue. Started ones are not touched."""
        removed = 0
        while True:
            waiting = self.store.docs_by_status(PLANNED, limit=100)
            gone = sum(self.store.delete_planned_doc(doc["contentKey"]) for doc in waiting)
            removed += gone
            if not waiting or not gone:
                return {"removed": removed}

    def rebuild(self, content_keys: List[str]) -> Dict[str, Any]:
        """Queue specific legacy documents for a rebuild as global2, whatever state their old embedding is in
        (failed or stuck ones are not part of a bulk run). Documents already building or switched are left alone."""
        queued, skipped = 0, []
        for key in content_keys:
            if not m.is_legacy_key(key):
                skipped.append({"contentKey": key, "reason": "not a legacy global/ key"})
                continue
            doc = self.store.get_doc(key)
            if doc and doc["status"] in (BUILDING, BUILT, CUTOVER, CLEANED):
                skipped.append({"contentKey": key, "reason": f"already {doc['status']}"})
            elif doc:
                self.store.update_doc(key, status=PLANNED, attempts=0, error=None, trigger="manual")
                queued += 1
            else:
                queued += int(self.store.add_doc_if_absent(key, {"trigger": "manual"}))
        return {"queued": queued, "skipped": skipped}

    def plan(self) -> Dict[str, Any]:
        """Turn the configured scope into ``planned`` documents (new ones only)."""
        config = self.store.get_config()
        progress, pointers, referenced, status = self._scan_inputs()
        now = self.now()
        docs = m.build_inventory(progress, pointers, referenced, now, status, self._file_info(pointers), self._referenced_by)["docs"]
        selected = [d for d in docs if d["migrationStatus"] is None and m.in_scope(d, config["scope"], now)]
        selected.sort(key=m.last_activity, reverse=True)       # most recently used first
        if config.get("maxDocs"):
            selected = selected[: int(config["maxDocs"])]
        added = sum(
            self.store.add_doc_if_absent(d["contentKey"], {
                "trigger": "bulk", "owner": d["owner"], "childFiles": d["childFiles"],
                "assistantReferenced": d["assistantReferenced"],
            })
            for d in selected
        )
        result = {"at": int(now), "considered": len(selected), "added": added, "scope": config["scope"]}
        self.store.set_flags(lastPlan=result)
        return result

    def retry_failed(self) -> int:
        failed = self.store.docs_by_status(FAILED, limit=500)
        for doc in failed:
            self.store.update_doc(doc["contentKey"], status=PLANNED, attempts=0, error=None)
        return len(failed)

    # ------------------------------------------------------------------ #
    # build (replay into global2 without touching live state)
    # ------------------------------------------------------------------ #
    def _fail(self, doc: Mapping[str, Any], reason: str) -> None:
        logger.warning("Migration failed for %s: %s", doc["contentKey"], reason)
        self.store.update_doc(doc["contentKey"], status=FAILED, error=reason[:500])

    def _start(self, doc: Mapping[str, Any], credentials: Mapping[str, Any]) -> bool:
        key, new_key = doc["contentKey"], new_key_for(doc["contentKey"])
        if self._progress_complete(new_key):                     # already built (e.g. earlier run)
            self.store.update_doc(key, status=BUILT, newKey=new_key, builtAt=int(self.now()))
            return True
        uploads = [row for row in self._pointer_rows(key) if delete_policy.is_upload_pointer(row)]
        source = next((row for row in uploads if self._source_exists(row["id"])), None)
        if source is None:
            self._fail(doc, "no original file found in S3 for any upload of this document")
            return False
        owner = delete_policy.pointer_owner(source)
        # Per-document secret the embedding worker reads (it deletes it when done).
        self.store_rag_secrets(source["id"], {**credentials, "user": owner})
        self.sqs.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(m.replay_message(source["id"], self.input_bucket, new_key)),
        )
        self.store.update_doc(
            key, status=BUILDING, newKey=new_key, sourceKey=source["id"], owner=owner,
            attempts=int(doc.get("attempts", 0)) + 1, startedAt=int(self.now()), error=None,
        )
        return True

    def claim(self, credentials: Mapping[str, Any], count: Optional[int] = None, keys: Optional[List[str]] = None) -> Dict[str, Any]:
        """Start up to ``count`` queued documents now, using the admin's login for their question generation.

        Documents an admin asked for by hand come first, then ones a user retrieved from, then the rest of the scope.
        Planning is topped up from the saved scope when fewer are queued than requested. With ``keys`` only those documents
        are started (hand-picked ones must not be overtaken by older queued documents).
        """
        config = self.store.get_config()
        if in_secret_sweep_window(self.now()):
            raise RuntimeError("Documents cannot be started between 02:55 and 03:20 UTC while the nightly credential cleanup runs")
        building = len(self.store.docs_by_status(BUILDING, limit=500))
        room = int(config["maxInFlight"]) - building
        slots = min(int(count or config["batchSize"]), room)
        if slots <= 0:
            raise RuntimeError(f"{building} documents are already having new embeddings created (the limit is {config['maxInFlight']}); wait for some to finish")
        if keys is not None:
            planned = [doc for doc in (self.store.get_doc(key) for key in keys) if doc and doc.get("status") == PLANNED]
        else:
            planned = self.store.docs_by_status(PLANNED, limit=slots * 3)
            if len(planned) < slots:
                self.plan()
                planned = self.store.docs_by_status(PLANNED, limit=slots * 3)
        priority = {"manual": 0, "access": 1}
        planned.sort(key=lambda d: priority.get(d.get("trigger"), 2))
        started = failed = 0
        for doc in planned[:slots]:
            try:
                if self._start(doc, credentials):
                    started += 1
                else:
                    failed += 1
            except Exception as error:
                self._fail(doc, f"could not start: {error}")
                failed += 1
        return {"started": started, "couldNotStart": failed, "queuedLeft": max(0, len(planned) - slots)}

    def reconcile(self) -> Dict[str, int]:
        outcome = {"built": 0, "retried": 0, "failed": 0}
        now = self.now()
        for doc in self.store.docs_by_status(BUILDING, limit=500):
            item = self._progress(doc.get("newKey") or new_key_for(doc["contentKey"]))
            status = item.get("parentChunkStatus")
            if status == "completed" and not item.get("terminated"):
                self.store.update_doc(doc["contentKey"], status=BUILT, builtAt=int(now), error=None)
                outcome["built"] += 1
                continue
            if item.get("migrationRejected"):         # refused by limits/mismatch: retrying cannot help
                self._fail(doc, str(item.get("migrationError") or "document was refused"))
                outcome["failed"] += 1
                continue
            broken = status == "failed" or item.get("terminated")
            last = max(m.to_epoch(item.get("lastUpdated")), float(doc.get("startedAt", now)))
            if not broken and now - last > STALL_SECONDS:
                # Nothing is queued for the unfinished chunks and restarting needs an admin's login, so say so rather than wait.
                counts = [c.get("status") for c in (item.get("data") or {}).get("childChunks", {}).values()]
                self._fail(doc, f"no progress for {STALL_SECONDS // 3600} hours ({counts.count('completed')} of {len(counts)} chunk files finished)")
                outcome["failed"] += 1
            elif broken:
                if int(doc.get("attempts", 0)) < MAX_ATTEMPTS:
                    self.store.update_doc(doc["contentKey"], status=PLANNED, error="embedding failed; retrying")
                    outcome["retried"] += 1
                else:
                    self._fail(doc, f"embedding failed after {MAX_ATTEMPTS} attempts")
                    outcome["failed"] += 1
        return outcome

    # ------------------------------------------------------------------ #
    # cutover / rollback
    # ------------------------------------------------------------------ #
    def _copy_access(self, old_key: str, new_key: str) -> int:
        return m.copy_access(self.object_access_table, old_key, new_key)

    def _repoint(self, row_id: str, expected: str, target: str) -> bool:
        try:
            self.hash_table.update_item(
                Key={"id": row_id},
                UpdateExpression="SET textLocationKey = :target, migratedFrom = :expected",
                ConditionExpression="textLocationKey = :expected",
                ExpressionAttributeValues={":target": target, ":expected": expected},
            )
            return True
        except ClientError as error:
            if m._is_conditional_failure(error):
                return False                      # someone changed it meanwhile; leave it alone
            raise

    def cutover_doc(self, doc: Mapping[str, Any]) -> Dict[str, Any]:
        key = doc["contentKey"]
        new_key = doc.get("newKey") or new_key_for(key)
        if not self._progress_complete(new_key):
            raise RuntimeError("global2 embeddings are not complete; refusing to cut over")
        copied = self._copy_access(key, new_key)                # 1. access first
        flipped = [row["id"] for row in self._pointer_rows(key) if self._repoint(row["id"], key, new_key)]   # 2. then pointers
        self.store.update_doc(key, status=CUTOVER, cutoverAt=int(self.now()), flipped=flipped, accessCopied=copied, error=None)
        return {"flipped": len(flipped), "accessCopied": copied}

    def cutover_built(self, limit: int = 25) -> Dict[str, int]:
        done = failed = 0
        for doc in self.store.docs_by_status(BUILT, limit=limit):
            try:
                self.cutover_doc(doc)
                done += 1
            except Exception as error:
                failed += 1
                self.store.update_doc(doc["contentKey"], error=f"cutover failed: {error}"[:500])
        return {"cutover": done, "failed": failed}

    def update_assistants(self, mapping: Mapping[str, str], poster: Optional[assistant_refs.Poster]) -> Dict[str, Any]:
        """Swap the document ids saved in assistants (old -> new). ``poster`` calls the assistants service as the admin."""
        return assistant_refs.apply(poster, self.assistant_tables[0] if self.assistant_tables else None, mapping)

    def assistant_usage(self, pairs: Mapping[str, str]) -> Dict[str, Dict[str, Any]]:
        """Which assistants use each document, split by the id they store (old or new). Reads only."""
        return assistant_refs.usage(self.assistant_tables[0] if self.assistant_tables else None, pairs) if self.assistant_tables else {}

    def rollback_doc(self, doc: Mapping[str, Any]) -> Dict[str, Any]:
        key = doc["contentKey"]
        new_key = doc.get("newKey") or new_key_for(key)
        if doc.get("status") == CLEANED or not self._progress(key):
            raise RuntimeError("legacy data has been removed; rollback is no longer possible")
        restored = [row_id for row_id in doc.get("flipped", []) if self._repoint(row_id, new_key, key)]
        self.store.update_doc(key, status=ROLLED_BACK, rolledBackAt=int(self.now()), restored=restored)
        return {"restored": len(restored)}

    def rollback(self, content_keys: List[str]) -> Dict[str, Any]:
        results = {"restored": 0, "errors": []}
        for content_key in content_keys:
            doc = self.store.get_doc(content_key)
            try:
                if not doc or doc["status"] != CUTOVER:
                    raise RuntimeError("only cut-over documents can be rolled back")
                results["restored"] += self.rollback_doc(doc)["restored"]
            except Exception as error:
                results["errors"].append({"contentKey": content_key, "error": str(error)})
        return results

    # ------------------------------------------------------------------ #
    # cleanup (only after the admin presses the button)
    # ------------------------------------------------------------------ #
    def cleanup_eligible(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.store.docs_by_status(CUTOVER, limit=limit)

    def preview_cleanup(self, limit: int = 100) -> Dict[str, Any]:
        """What a cleanup would do right now, without deleting anything."""
        rows = []
        for doc in self.store.docs_by_status(CUTOVER, limit=limit):
            key = doc["contentKey"]
            new_key = doc.get("newKey") or new_key_for(key)
            blockers = []
            if not self._progress_complete(new_key):
                blockers.append("new embeddings are not complete")
            if any(delete_policy.is_upload_pointer(row) for row in self._pointer_rows(key)):
                blockers.append("something still points at the legacy copy")
            rows.append({"contentKey": key, "childFiles": doc.get("childFiles"), "ready": not blockers, "blockers": blockers})
        return {
            "switched": len(rows), "ready": sum(r["ready"] for r in rows), "documents": rows,
            "note": "Cleanup removes only the old PostgreSQL rows, and only for documents whose new copy has vectors.",
        }

    def cleanup_doc(self, doc: Mapping[str, Any]) -> str:
        key = doc["contentKey"]
        new_key = doc.get("newKey") or new_key_for(key)
        if not self._progress_complete(new_key):
            return "skipped: new embeddings are not complete"
        if any(delete_policy.is_upload_pointer(row) for row in self._pointer_rows(key)):
            return "skipped: something still points at the legacy copy"
        if self.legacy_cleanup is None:
            return "blocked: no admin login to call the cleanup endpoint with"
        # Anyone who could read the legacy copy must be able to read the new one before the old vectors go.
        self._copy_access(key, new_key)
        result = self.legacy_cleanup([key])
        if not result or result.get("errors"):
            return f"failed: legacy cleanup reported {result}"
        if result.get("skipped"):
            return "skipped: no global2 vectors were found for this document, so the old copy was kept"
        # Only the PostgreSQL rows (the part that costs money) are removed. The small legacy text object, progress
        # row and access rows stay: callers that still send the legacy id (older assistants, API clients) are
        # checked against them and then answered from global2 (see embedding/migration_hook.alias_sources).
        self.store.update_doc(key, status=CLEANED, cleanedAt=int(self.now()), legacyRowsDeleted=result.get("deleted"), cleanupNote=None)
        return "cleaned"

    def cleanup(self, limit: int = 25) -> Dict[str, Any]:
        outcomes: Dict[str, int] = {}
        eligible = self.cleanup_eligible()
        for doc in eligible[:limit]:
            try:
                outcome = self.cleanup_doc(doc)
            except Exception as error:
                outcome = f"failed: {error}"
            label = outcome.split(":")[0]
            outcomes[label] = outcomes.get(label, 0) + 1
            if label != "cleaned":
                self.store.update_doc(doc["contentKey"], cleanupNote=outcome[:300])
        return {"eligible": len(eligible), "outcomes": outcomes}

    # ------------------------------------------------------------------ #
    # scheduled entry point
    # ------------------------------------------------------------------ #
    def _inventory_step(self) -> str:
        return "ok" if self.scan_inventory() is not None else "already counting"

    def tick(self, budget_seconds: int = 240) -> Dict[str, Any]:
        # the lock must outlive the function (timeout 600s) so a slow run is never overlapped
        if not self.store.acquire_lock(budget_seconds + 420):
            return {"skipped": "another run is in progress"}
        report: Dict[str, Any] = {}
        try:
            config = self.store.get_config()
            inventory = self.store.get_inventory()
            stale = not inventory or self.now() - inventory.get("generatedAt", 0) > INVENTORY_MAX_AGE_SECONDS
            steps = []
            if stale and config.get("updatedAt"):
                steps.append(("inventory", self._inventory_step))
            steps.append(("reconcile", self.reconcile))
            if config["autoCutover"]:
                steps.append(("cutover", lambda: self.cutover_built(int(config["batchSize"]))))
            for name, step in steps:
                try:
                    report[name] = step()
                except Exception as error:               # one failing step must not block the others
                    logger.error("Migration step %s failed: %s", name, error)
                    report[name] = {"error": str(error)}
                    self.store.set_flags(lastError=f"{name}: {error}"[:300])
            self.store.set_flags(lastTick=int(self.now()))
            return report
        finally:
            self.store.release_lock()
