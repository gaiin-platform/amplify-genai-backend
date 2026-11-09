"""Retention for throwaway chat attachments.

Policy: a file attached to a chat (RAG off, not tied to an assistant or group, not
an image/video) is marked ephemeral at upload. It is deleted only after it has gone
unused for the number of days an admin set under "Document retention" (admin config
``documentRetention``: ``isActive`` and ``days``) AND nothing still depends on it.
The setting is off until an admin turns it on, and the daily sweep reads it on every
run, so changing the days (longer or shorter) applies to files already uploaded.
Assistant-attached (RAG on) files are never marked, so they never expire automatically.

A file is "unused" when upload time and ``lastAccessedAt`` (stamped on the document's content row in the hash-files table
by the chat service and by retrieval, whichever way the document was reached) are both more than the admin's days ago.

Safety rules, in order (any "skip" leaves the file untouched):
  1. Used within the admin's days (``lastAccessedAt``) -> skip.
  2. Referenced anywhere in an assistant record (assistants, layered assistants,
     assistant groups) -> skip. The scan looks at every string in those records,
     so it also covers drive/website data sources and future reference types.
  3. Other people or uploads still use the same content -> remove only THIS
     upload; the shared content stays (see rag/delete_policy.py).
  4. The content has embeddings -> skip (deleting them needs a user token).
  5. Otherwise it is the last reference and everything for it is deleted.

A manual invocation with ``{"dryRun": true}`` only logs what would be deleted.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set

from boto3.dynamodb.conditions import Attr, Key

from rag import artifact_cleanup, delete_policy

logger = logging.getLogger("rag_retention")
logger.setLevel(logging.INFO)

DAY = 86400


MAX_DAYS = 3650
# ``lastAccessedAt`` only moves forward once a day (datasources.js, embedding-dual-retrieval.py), so it can be up to a day older
# than the real last use. A day is added back so a file is never deleted sooner than "days" after it was really used.
ACCESS_PRECISION = DAY
POLICY_CONFIG_ID = "documentRetention"      # the admin config (isActive, days); see amplify-lambda-admin


def load_policy(admin_table: Any) -> Dict[str, Any]:
    """The admin's document retention setting: {"isActive": bool, "days": int or None}.

    Anything missing or malformed means OFF: when in doubt nothing is deleted.
    """
    item = admin_table.get_item(Key={"config_id": POLICY_CONFIG_ID}).get("Item") or {}
    data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
    days = data.get("days")
    # DynamoDB hands numbers back as Decimal, so accept any real number that is a whole count of days
    valid = isinstance(days, (int, float, Decimal)) and not isinstance(days, bool) and float(days) == int(days) and 1 <= int(days) <= MAX_DAYS
    return {"isActive": data.get("isActive") is True and valid, "days": int(days) if valid else None}


# --------------------------------------------------------------------------- #
# Upload-time stamping
# --------------------------------------------------------------------------- #
def is_ephemeral_upload(*, rag_on: bool, group_id: Optional[str], props: Any, is_media: bool) -> bool:
    """Chat-only attachments: RAG off, no group, no assistant claim, not media.

    Any assistant claim at all (even one later stripped as invalid) opts out; the
    conservative choice is to keep the file.
    """
    if rag_on or group_id or is_media:
        return False
    if isinstance(props, Mapping) and props.get("astp"):
        return False
    return True


# --------------------------------------------------------------------------- #
# Pure rules
# --------------------------------------------------------------------------- #
def _epoch(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
        return float(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def collect_strings(value: Any, into: Optional[Set[str]] = None) -> Set[str]:
    """Every string anywhere in a (DynamoDB) value; over-inclusive on purpose."""
    into = set() if into is None else into
    if isinstance(value, str):
        into.add(value)
    elif isinstance(value, Mapping):
        for key, inner in value.items():
            into.add(str(key))
            collect_strings(inner, into)
    elif isinstance(value, (list, tuple, set)):
        for inner in value:
            collect_strings(inner, into)
    return into


def is_referenced(identifiers: Iterable[str], referenced: Set[str]) -> bool:
    for identifier in identifiers:
        if not identifier:
            continue
        if identifier in referenced or ("s3://" + identifier) in referenced:
            return True
    return False


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #
def _scan_all(table: Any, **kwargs) -> Iterable[Mapping]:
    while True:
        response = table.scan(**kwargs)
        for item in response.get("Items", []):
            yield item
        if not response.get("LastEvaluatedKey"):
            return
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def referenced_identifiers(assistant_tables: Iterable[Any]) -> Set[str]:
    referenced: Set[str] = set()
    for table in assistant_tables:
        for item in _scan_all(table):
            collect_strings(item, referenced)
    return referenced


class Sweeper:
    """Everything the sweep touches is injected, so it is testable without AWS."""

    def __init__(
        self,
        *,
        files_table: Any,
        hash_table: Any,
        object_access_table: Any,
        progress_table: Any,
        assistant_tables: Iterable[Any],
        s3: Any,
        input_bucket: str,
        text_bucket: str,
        days: Optional[int],
        dry_run: bool = True,
        max_per_run: int = 500,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.files_table, self.hash_table = files_table, hash_table
        self.object_access_table, self.progress_table = object_access_table, progress_table
        self.assistant_tables = list(assistant_tables)
        self.s3, self.input_bucket, self.text_bucket = s3, input_bucket, text_bucket
        self.days = days                     # the admin's setting, read fresh on every run; None = no recency window
        self.dry_run, self.max_per_run, self.now = dry_run, max_per_run, now

    # -- helpers ----------------------------------------------------------- #
    def _content_rows(self, global_key: str) -> List[Mapping]:
        kwargs = {"IndexName": "TextLocationIndex", "KeyConditionExpression": Key("textLocationKey").eq(global_key)}
        rows: List[Mapping] = []
        while True:
            response = self.hash_table.query(**kwargs)
            rows.extend(response.get("Items", []))
            if not response.get("LastEvaluatedKey"):
                return rows
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def _access_principals(self, global_key: str) -> List[str]:
        response = self.object_access_table.query(KeyConditionExpression=Key("object_id").eq(global_key))
        return [item["principal_id"] for item in response.get("Items", [])]

    def _delete_upload(self, key: str) -> None:
        """This upload's own records: files row, pointer row, raw object, per-upload text/metadata."""
        self.files_table.delete_item(Key={"id": key})
        self.hash_table.delete_item(Key={"id": key})
        self.s3.delete_objects(
            Bucket=self.input_bucket, Delete={"Objects": [{"Key": key}], "Quiet": True}
        )
        self.s3.delete_objects(
            Bucket=self.text_bucket,
            Delete={"Objects": [{"Key": key + ".content.json"}, {"Key": key + ".metadata.json"}], "Quiet": True},
        )

    def _delete_shared_content(self, global_key: str, content_hash: Optional[str]) -> None:
        self.s3.delete_objects(Bucket=self.text_bucket, Delete={"Objects": [{"Key": global_key}], "Quiet": True})
        artifact_cleanup.delete_global2_page_artifacts(self.s3, self.text_bucket, global_key)
        if content_hash:
            self.hash_table.delete_item(Key={"id": content_hash})

    # -- decision for one file ---------------------------------------------- #
    def decide(self, row: Mapping, referenced: Set[str]) -> Dict[str, Any]:
        key, owner = row["id"], row.get("createdBy") or str(row["id"]).split("/")[0]
        pointer = self.hash_table.get_item(Key={"id": key}).get("Item")

        if self.effective(row, self._last_accessed(pointer)) > self.now():
            return {"action": "skip", "reason": "used recently", "key": key}

        global_key = (pointer or {}).get("textLocationKey")
        if is_referenced([key, global_key, (pointer or {}).get("hash")], referenced):
            return {"action": "skip", "reason": "referenced by an assistant", "key": key}

        if not pointer:
            return {"action": "delete_upload", "reason": "never processed", "key": key, "owner": owner}

        rows = self._content_rows(global_key)
        principals = self._access_principals(global_key)
        others_have_access = any(principal != owner for principal in principals)
        if others_have_access or delete_policy.other_references(rows, key):
            return {
                "action": "delete_upload", "reason": "content is shared", "key": key, "owner": owner,
                "global_key": global_key,
                "keep_access": delete_policy.user_still_references(rows, key, owner),
            }

        if self.progress_table.get_item(Key={"object_id": global_key}).get("Item"):
            return {"action": "skip", "reason": "content has embeddings", "key": key}

        return {
            "action": "delete_all", "reason": "last reference", "key": key, "owner": owner,
            "global_key": global_key, "hash": pointer.get("hash"),
        }

    def _last_accessed(self, pointer: Optional[Mapping]) -> Any:
        """``lastAccessedAt`` of the content this upload points to (its content row's id is the file's hash)."""
        content_hash = (pointer or {}).get("hash")
        if not content_hash:
            return None
        return (self.hash_table.get_item(Key={"id": content_hash}).get("Item") or {}).get("lastAccessedAt")

    def effective(self, row: Mapping, last_accessed_at: Any = None) -> float:
        """When this file may go: the later of its upload and its last use, plus the CURRENT days, so changing the setting
        applies to files already uploaded. A row with no timestamp at all never expires. With no days (the orphan cleaner when
        the admin has never set retention) nothing counts as recently used."""
        if self.days is None:
            return 0.0
        used = _epoch(last_accessed_at)
        base = max(_epoch(row.get("createdAt")), used + ACCESS_PRECISION if used else 0.0)
        return base + self.days * DAY if base else float("inf")

    def apply(self, decision: Mapping) -> None:
        action, key = decision["action"], decision["key"]
        if action == "delete_upload":
            self._delete_upload(key)
            if decision.get("global_key") and not decision.get("keep_access", False):
                # only when this user has no other upload of the content AND others remain,
                # otherwise the (sole) access entry goes with the shared content
                self.object_access_table.delete_item(
                    Key={"object_id": decision["global_key"], "principal_id": decision["owner"]}
                )
        elif action == "delete_all":
            self._delete_upload(key)
            self._delete_shared_content(decision["global_key"], decision.get("hash"))
            self.object_access_table.delete_item(
                Key={"object_id": decision["global_key"], "principal_id": decision["owner"]}
            )

    # -- run ---------------------------------------------------------------- #
    def run(self) -> Dict[str, Any]:
        now = int(self.now())
        candidates = [
            row for row in _scan_all(self.files_table, FilterExpression=Attr("ephemeral").eq(True))
            # re-checked here so a mistake in the filter can never widen what is deleted
            if row.get("ephemeral") is True and self.effective(row) < now
        ]
        summary: Dict[str, Any] = {"dryRun": self.dry_run, "days": self.days, "candidates": len(candidates), "actions": {}, "examples": []}
        if not candidates:
            return summary
        referenced = referenced_identifiers(self.assistant_tables)

        acted = 0
        for row in candidates:
            if acted >= self.max_per_run:
                summary["truncated"] = True
                break
            try:
                decision = self.decide(row, referenced)
                label = f"{decision['action']}:{decision['reason']}"
                summary["actions"][label] = summary["actions"].get(label, 0) + 1
                if len(summary["examples"]) < 20:
                    summary["examples"].append({k: decision[k] for k in ("action", "reason", "key")})
                if decision["action"] == "skip":
                    continue
                acted += 1
                if not self.dry_run:
                    self.apply(decision)
            except Exception as error:  # one bad file must not stop the sweep
                logger.error("Retention sweep failed for %s: %s", row.get("id"), error)
                summary["actions"]["error"] = summary["actions"].get("error", 0) + 1
        return summary


# --------------------------------------------------------------------------- #
# Lambda entry point
# --------------------------------------------------------------------------- #
def sweep_handler(event, context):
    import boto3

    dynamodb = boto3.resource("dynamodb")
    # The admin's "Document retention" setting decides whether anything happens at all. An unreadable setting counts as off.
    policy = load_policy(dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"]))
    if not policy["isActive"]:
        logger.info("Document retention is off (admin setting); nothing to do")
        return {"enabled": False}

    # Validate configuration before touching AWS: a partial view of the assistant
    # tables could miss a reference, and deleting on a partial view is irreversible.
    assistant_table_names = [
        os.environ.get(name, "")
        for name in ("ASSISTANTS_DYNAMODB_TABLE", "LAYERED_ASSISTANTS_DYNAMODB_TABLE", "ASSISTANT_GROUPS_DYNAMO_TABLE")
    ]
    configured = [name for name in assistant_table_names if name and name != "unset"]
    if len(configured) < len(assistant_table_names):
        raise RuntimeError(
            f"Refusing to sweep: only {len(configured)} of {len(assistant_table_names)} assistant tables configured"
        )

    sweeper = Sweeper(
        files_table=dynamodb.Table(os.environ["FILES_DYNAMO_TABLE"]),
        hash_table=dynamodb.Table(os.environ["HASH_FILES_DYNAMO_TABLE"]),
        object_access_table=dynamodb.Table(os.environ["OBJECT_ACCESS_DYNAMODB_TABLE"]),
        progress_table=dynamodb.Table(os.environ["EMBEDDING_PROGRESS_TABLE"]),
        assistant_tables=[dynamodb.Table(name) for name in configured],
        s3=boto3.client("s3"),
        input_bucket=os.environ["S3_RAG_INPUT_BUCKET_NAME"],
        text_bucket=os.environ["S3_FILE_TEXT_BUCKET_NAME"],
        days=policy["days"],
        dry_run=bool((event or {}).get("dryRun")),
        max_per_run=int(os.environ.get("EPHEMERAL_CLEANUP_MAX_PER_RUN", "500")),
    )
    summary = sweeper.run()
    logger.info("Ephemeral file sweep: %s", json.dumps(summary, default=str))
    return summary
