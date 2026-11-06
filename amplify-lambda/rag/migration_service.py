"""Admin-facing operations for the RAG migration (status, settings, actions).

Kept free of the API/auth decorators (see rag/migration_api.py) so the behaviour is
testable without AWS. Nothing is stored for the migration: documents are started with the
login of the admin who pressed the button, as a normal upload would.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from rag import admin_post, assistant_refs, migration as m, orphans
from rag.migration_runner import Migrator, in_secret_sweep_window

ACTIONS = (
    "migrate", "refresh_inventory", "retry_failed",
    "cutover", "rollback", "assistant_usage", "cleanup", "dismiss_error",
    "lookup", "rebuild", "preview_cleanup", "browse", "search", "clear_queue", "orphans", "remove_orphans",
    "estimate", "refresh_costs"
)
CLEANUP_PER_PRESS = 200       # documents cleaned by one press of the cleanup button
MAX_INLINE_DOCS = 50          # cutover/rollback run inside one API request
DOC_FIELDS = (
    "contentKey", "status", "trigger", "owner", "childFiles", "assistantReferenced", "attempts",
    "error", "cleanupNote", "createdAt", "updatedAt", "startedAt", "builtAt", "cutoverAt", "cleanedAt",
    "embeddingCost", "qaCalls", "ocrCost",
)


def trimmed(doc: Mapping[str, Any]) -> Dict[str, Any]:
    return {name: doc[name] for name in DOC_FIELDS if name in doc}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def status_payload(migrator: Migrator, now: Optional[float] = None) -> Dict[str, Any]:
    store = migrator.store
    now = time.time() if now is None else now
    config = store.get_config()
    inventory = store.get_inventory()
    return {
        "config": {key: config.get(key) for key in (
            *m.DEFAULT_CONFIG.keys(), "lastError", "lastTick", "lastPlan", "updatedAt", "updatedBy",
        )},
        "inventory": inventory,
        "inventoryAgeSeconds": int(now - inventory["generatedAt"]) if inventory else None,
        "costs": store.get_costs(),
        "pipeline": store.count_by_status(),
        "inSecretSweepWindow": in_secret_sweep_window(now),
        "failures": [trimmed(d) for d in store.docs_by_status(m.FAILED, limit=20)],
        "queued": [trimmed(d) for d in store.docs_by_status(m.PLANNED, limit=20)],
        "queues": migrator.queue_depths(),
        "migrating": migrator.migrating_now(),
        "serverTime": int(now),
    }


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def update_settings(migrator: Migrator, patch: Mapping[str, Any], admin: str) -> Dict[str, Any]:
    return migrator.store.update_config(patch, updated_by=admin)


def _selected(migrator: Migrator, data: Mapping[str, Any], status: str) -> List[str]:
    if data.get("keys"):
        keys = list(data["keys"])
    elif data.get("all"):
        keys = [d["contentKey"] for d in migrator.store.docs_by_status(status, limit=MAX_INLINE_DOCS)]
    else:
        raise ValueError("choose documents (keys) or all")
    if len(keys) > MAX_INLINE_DOCS:
        raise ValueError(f"at most {MAX_INLINE_DOCS} documents per request")
    return keys


def run_action(
    migrator: Migrator, action: str, data: Mapping[str, Any], admin: str, access_token: str = "",
    now: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    store = migrator.store
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")

    if action == "migrate":
        if not access_token:
            raise ValueError("Your login is needed to start documents")
        count = max(1, min(MAX_INLINE_DOCS, int(data.get("count") or 25)))
        result = migrator.claim(m.admin_credentials(access_token, admin), count)
        store.update_config({}, updated_by=admin)
        return {**result, "loginExpiresAt": m.token_expiry(access_token)}
    if action == "refresh_inventory":
        # Runs now (the endpoint supports polling, so a long scan does not time the request out).
        return {"summary": migrator.run_inventory()}
    if action == "estimate":
        return migrator.estimate_costs(data.get("keys"), data.get("count"))
    if action == "refresh_costs":
        return {"summary": migrator.run_costs()}
    if action == "browse":
        return migrator.browse(data.get("state"), data.get("namespace", "global"), data.get("limit", 50), data.get("start"))
    if action == "lookup":
        return migrator.lookup(data.get("query"))
    if action == "search":
        return migrator.search(data.get("query"))
    if action == "clear_queue":
        return migrator.clear_queue()
    if action == "orphans":
        return migrator.orphan_report()
    if action == "remove_orphans":
        if data.get("confirm") != orphans.CONFIRM_PHRASE:
            raise ValueError(f"Type {orphans.CONFIRM_PHRASE} to confirm")
        return migrator.remove_orphans(str(data.get("category") or ""), admin, data.get("limit"))
    if action == "rebuild":
        keys = list(data.get("keys") or [])
        if not keys or len(keys) > MAX_INLINE_DOCS:
            raise ValueError(f"choose between 1 and {MAX_INLINE_DOCS} documents")
        if not access_token:
            raise ValueError("Your login is needed to start documents")
        queued = migrator.rebuild(keys)
        if not queued["queued"]:
            return {**queued, "started": 0}
        # Picked by hand, so they start now: hand-picked documents go ahead of everything already queued.
        started = migrator.claim(m.admin_credentials(access_token, admin), queued["queued"], keys=keys)
        return {**queued, "started": started["started"], "loginExpiresAt": m.token_expiry(access_token)}
    if action == "assistant_usage":
        # Reads only. Documents are the given keys, or everything built or moved (at most one request's worth).
        keys = list(data.get("keys") or [])
        if not keys:
            keys = [d["contentKey"] for status in (m.BUILT, m.CUTOVER) for d in store.docs_by_status(status, limit=MAX_INLINE_DOCS)]
        if len(keys) > MAX_INLINE_DOCS:
            raise ValueError(f"at most {MAX_INLINE_DOCS} documents per request")
        pairs = {key: (store.get_doc(key) or {}).get("newKey") or m.new_key_for(key) for key in keys if m.is_legacy_key(key)}
        return {"documents": {
            key: {"status": (store.get_doc(key) or {}).get("status"), "onOld": len(use["onOld"]), "onNew": len(use["onNew"]),
                  "names": [a["name"] for a in (use["onOld"] + use["onNew"])][:5]}
            for key, use in migrator.assistant_usage(pairs).items()}}
    if action == "preview_cleanup":
        return migrator.preview_cleanup()
    if action == "dismiss_error":
        store.set_flags(lastError=None)
        return {}
    if action == "retry_failed":
        return {"requeued": migrator.retry_failed()}
    if action == "cleanup":
        # Runs now, in this request, with the admin's own token: the deletion is an admin-only endpoint.
        if not access_token:
            raise ValueError("Your login is needed to remove old embeddings")
        migrator.legacy_cleanup = legacy_cleanup_caller(access_token)
        return migrator.cleanup(limit=CLEANUP_PER_PRESS)

    keys = _selected(migrator, data, m.BUILT if action == "cutover" else m.CUTOVER)
    if action == "cutover" and data.get("all") and len(keys) < MAX_INLINE_DOCS:
        # Move references also finishes the assistants of documents moved earlier whose assistants could not be updated then
        keys += [d["contentKey"] for d in store.docs_by_status(m.CUTOVER, limit=MAX_INLINE_DOCS - len(keys))]
    poster = assistant_refs.api_poster(access_token) if access_token else None      # the admin's own token
    if action == "rollback":
        result = migrator.rollback(keys)
        back = {}                                              # new id -> old id, for the documents that really rolled back
        for key in keys:
            doc = store.get_doc(key)
            if doc and doc["status"] == m.ROLLED_BACK:
                back[doc.get("newKey") or m.new_key_for(key)] = key
        return {**result, "assistants": migrator.update_assistants(back, poster)}
    done, errors, moved = 0, [], {}                            # cutover
    for key in keys:
        doc = store.get_doc(key)
        try:
            if doc and doc["status"] == m.CUTOVER:             # moved before: only its assistants may still need the new id (idempotent)
                moved[key] = doc.get("newKey") or m.new_key_for(key)
                continue
            if not doc or doc["status"] != m.BUILT:
                raise RuntimeError("only built documents can be cut over")
            migrator.cutover_doc(doc)
            moved[key] = doc.get("newKey") or m.new_key_for(key)
            done += 1
        except Exception as error:
            errors.append({"contentKey": key, "error": str(error)})
    # The references are moved first; a failure below never undoes that (retrieval still answers from the new copy).
    return {"cutover": done, "errors": errors, "assistants": migrator.update_assistants(moved, poster)}


# --------------------------------------------------------------------------- #
# Wiring (AWS)
# --------------------------------------------------------------------------- #
def _configured(name: str) -> Optional[str]:
    value = os.environ.get(name, "")
    return value if value and value != "unset" else None


def legacy_cleanup_caller(access_token: str, post: Callable[..., Mapping[str, Any]] = admin_post.post_as_admin) -> Callable[[List[str]], Mapping[str, Any]]:
    """Delete the old PostgreSQL rows of legacy keys through the embedding service's admin-only endpoint."""
    def call(keys: List[str]) -> Mapping[str, Any]:
        return post("/embedding/legacy-cleanup", {"src_ids": list(keys)}, access_token).get("data") or {}

    return call


def _queue_urls() -> Dict[str, str]:
    """The RAG pipeline queues and their dead-letter queues, for the live view.

    Built from the URL of a queue this Lambda already knows, because the role may read queue attributes but
    is not allowed to look URLs up by name (``sqs:GetQueueUrl``).
    """
    known = _configured("RAG_PROCESS_DOCUMENT_QUEUE_URL")
    documents = _configured("SQS_RAG_DOCUMENT_INDEX_QUEUE")
    chunks = _configured("SQS_RAG_CHUNK_DOCUMENT_INDEX_QUEUE")
    if not (known and documents and chunks):
        return {}
    base = known.rsplit("/", 1)[0]
    suffix = "rag-document-index-queue"
    queues = {"Reading documents": documents, "Chunking": chunks}
    if documents.endswith(suffix):
        queues["Embedding"] = documents[: -len(suffix)] + "embedding-chunks-index-queue"
    dead_letter = {
        label: name[: -len("queue")] + "dlq" if name.endswith("embedding-chunks-index-queue") else f"{name}-dlq"
        for label, name in queues.items()
    }
    return {
        **{label: f"{base}/{name}" for label, name in queues.items()},
        **{f"{label} (failed)": f"{base}/{name}" for label, name in dead_letter.items()},
    }


def build_migrator() -> Migrator:
    import boto3
    from rag.rag_secrets import store_ds_secrets_for_rag

    table_name = _configured("RAG_MIGRATION_TABLE")
    if not table_name:
        raise RuntimeError("RAG migration is not deployed in this environment (RAG_MIGRATION_TABLE is unset)")
    assistant_names = [
        _configured(name) for name in
        ("ASSISTANTS_DYNAMODB_TABLE", "LAYERED_ASSISTANTS_DYNAMODB_TABLE", "ASSISTANT_GROUPS_DYNAMO_TABLE")
    ]
    if not all(assistant_names):
        # An incomplete view of assistants would mislabel "assistant" documents.
        raise RuntimeError("All assistant tables must be configured to run the migration")

    dynamodb = boto3.resource("dynamodb")
    return Migrator(
        store=m.MigrationStore(dynamodb.Table(table_name)),
        progress_table=dynamodb.Table(os.environ["EMBEDDING_PROGRESS_TABLE"]),
        hash_table=dynamodb.Table(os.environ["HASH_FILES_DYNAMO_TABLE"]),
        object_access_table=dynamodb.Table(os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]),
        assistant_tables=[dynamodb.Table(name) for name in assistant_names],
        s3=boto3.client("s3"),
        sqs=boto3.client("sqs"),
        queue_url=os.environ["RAG_PROCESS_DOCUMENT_QUEUE_URL"],
        input_bucket=os.environ["S3_RAG_INPUT_BUCKET_NAME"],
        text_bucket=os.environ["S3_FILE_TEXT_BUCKET_NAME"],
        store_rag_secrets=store_ds_secrets_for_rag,
        queue_urls=_queue_urls(),
        files_table=dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"]),
        admin_table=dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"]),
        model_rate_table=dynamodb.Table(os.environ["MODEL_RATE_TABLE"]),
        cost_tables=(dynamodb.Table(os.environ["COST_CALCULATIONS_DYNAMO_TABLE"]),
                     dynamodb.Table(os.environ["HISTORY_COST_CALCULATIONS_DYNAMO_TABLE"])),
        usage_table=dynamodb.Table(os.environ["CHAT_USAGE_DYNAMO_TABLE"]),
    )


def tick_handler(event, context):
    """Scheduled every minute; the lock in the store prevents overlapping runs.

    It only moves documents that are already building forward (verify, switch over, clean up). Starting documents
    needs an admin's login, so it happens when an admin presses "Migrate next N".
    """
    migrator = build_migrator()
    config = migrator.store.get_config()
    if not any(config[name] for name in ("autoCutover",)) \
            and not migrator.store.docs_by_status(m.BUILDING, limit=1):
        return {"skipped": "idle"}
    return migrator.tick()
