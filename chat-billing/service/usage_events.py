"""
usage_events.py

Lambda function that processes usage events from SQS and records them via the
standard pycommon accounting helpers (record_usage / record_additional_charge),
so external workloads (e.g. the Open Notebook FastAPI service running on ECS)
bill into Amplify's existing chat-billing tables/views.

This consumer is the receiving end of the UsageEventsQueue defined in
serverless.yml. Producers send fail-safe JSON events shaped like:

    {
      "type": "llm_usage",            # or "additional_charge"
      "user": "alice@vanderbilt.edu", # required — real email, becomes account["user"]
      "account_id": "1234-5678",      # optional COA override; when absent (the
                                      # normal case) the user's default account
                                      # is resolved from ACCOUNTS_DYNAMO_TABLE
      "model_id": "us.anthropic.claude-sonnet-4-6",
      "request_id": "...",            # optional
      "details": { ... },             # optional metadata dict

      # llm_usage:
      "input_tokens": 123,
      "output_tokens": 456,
      "input_cached_tokens": 0,       # optional, default 0
      "input_write_cached_tokens": 0, # optional, default 0

      # additional_charge:
      "item_type": "notebook_embedding",
      "token_count": 789,             # OR
      "flat_cost": 0.03
    }

Design (mirrors amplify-lambda-admin/service/critical_error_processor.py):
  - Returns {"batchItemFailures": [...]} for partial-batch retry.
  - Malformed / invalid messages are DROPPED (not retried) and surfaced via
    log_critical_error as billing data loss. Retrying a poison message would
    re-deliver the whole batch and DOUBLE-BILL every valid message in it.
  - Only unexpected errors (e.g. transient DynamoDB failure inside pycommon that
    still raised) go to batchItemFailures for redrive.

Copyright (c) 2026 Vanderbilt University
Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas
"""

import json
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

import boto3
from pycommon.logger import getLogger
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.api.accounting import record_usage, record_additional_charge
from pycommon.api.critical_logging import log_critical_error

logger = getLogger("usage_events")

dynamodb = boto3.resource("dynamodb")

EVENT_TYPE_LLM_USAGE = "llm_usage"
EVENT_TYPE_ADDITIONAL_CHARGE = "additional_charge"

DEFAULT_ACCOUNT_ID = "general_account"

# Bedrock dated / versioned suffix, e.g. "-20250929-v1:0" on
# us.anthropic.claude-sonnet-4-5-20250929-v1:0. Stripped as a fallback when the
# exact model id has no rate row (the CSV mixes dated and undated ids).
_DATED_SUFFIX_RE = re.compile(r"-\d{8}-v\d+:\d+$")

# Cache of resolved model ids: reported_id -> priced_id (only successes cached).
_model_id_cache: Dict[str, str] = {}

# Cache of resolved default COAs: user email -> (account_id, cached_at_epoch).
# Short TTL because a user can change their default account at any time; a
# stale entry only misattributes usage within this window.
_account_cache: Dict[str, Tuple[str, float]] = {}
ACCOUNT_CACHE_TTL_SECONDS = 300


def _model_has_rate(model_id: str) -> bool:
    """Returns True if MODEL_RATE_TABLE has a row for this exact model id."""
    table = dynamodb.Table(os.environ["MODEL_RATE_TABLE"])
    resp = table.get_item(Key={"ModelID": model_id})
    return "Item" in resp


def resolve_model_id(reported_model_id: str) -> Optional[str]:
    """Map the reported model id to one that exists in MODEL_RATE_TABLE.

    Tries the exact id first, then strips a Bedrock dated/versioned suffix.
    Returns the priced id, or None if nothing matches (caller records at $0 and
    fires an unpriced-model critical error). Only successful resolutions are
    cached so a transient miss doesn't get stuck.
    """
    if not reported_model_id:
        return None

    cached = _model_id_cache.get(reported_model_id)
    if cached:
        return cached

    try:
        if _model_has_rate(reported_model_id):
            _model_id_cache[reported_model_id] = reported_model_id
            return reported_model_id

        stripped = _DATED_SUFFIX_RE.sub("", reported_model_id)
        if stripped != reported_model_id and _model_has_rate(stripped):
            logger.info(
                "Resolved unpriced model id %s -> %s via suffix strip",
                reported_model_id,
                stripped,
            )
            _model_id_cache[reported_model_id] = stripped
            return stripped
    except Exception as e:
        # Treat a lookup failure as "unresolved" — record_usage/charge will do
        # its own rate lookup and simply price at $0 if it also fails.
        logger.error("resolve_model_id failed for %s: %s", reported_model_id, str(e))
        return None

    return None


def resolve_default_account(user: str) -> str:
    """Resolve the user's default COA from ACCOUNTS_DYNAMO_TABLE.

    Mirrors the frontend's home-state derivation (home.tsx): the account with
    isDefault=true, else the noCoaAccount ("general_account"). There is no
    first-account fallback — an account is only billed if the user explicitly
    made it their default. Failures resolve to "general_account" (never raise;
    a COA miss must not drop the usage record itself).

    Results are cached per user for ACCOUNT_CACHE_TTL_SECONDS so a batch of
    events from one user costs one GetItem, while default-account changes still
    take effect quickly.
    """
    cached = _account_cache.get(user)
    if cached and (time.time() - cached[1]) < ACCOUNT_CACHE_TTL_SECONDS:
        return cached[0]

    account_id = DEFAULT_ACCOUNT_ID
    try:
        table = dynamodb.Table(os.environ["ACCOUNTS_DYNAMO_TABLE"])
        response = table.get_item(Key={"user": user})
        accounts = response.get("Item", {}).get("accounts", [])
        for account in accounts:
            if account.get("isDefault") and account.get("id"):
                account_id = str(account["id"])
                break
    except Exception as e:
        logger.error("resolve_default_account failed for %s: %s", user, str(e))
        return DEFAULT_ACCOUNT_ID  # don't cache failures

    _account_cache[user] = (account_id, time.time())
    return account_id


def _report_unpriced_model(reported_model_id: str, user: str, event_type: str) -> None:
    """Fire a critical error so $0 pricing from an unknown model is loud."""
    try:
        log_critical_error(
            function_name="process_usage_event_from_sqs",
            error_type="UsageEventUnpricedModel",
            error_message=(
                f"No rate row for model '{reported_model_id}' "
                f"({event_type}); usage recorded at $0."
            ),
            current_user=user or "unknown",
            severity="HIGH",
            context={"model_id": reported_model_id, "event_type": event_type},
        )
    except Exception as log_err:
        logger.error("Failed to log unpriced-model critical error: %s", str(log_err))


def _report_billing_data_loss(
    message_id: str, reason: str, body: Any, user: str = "unknown"
) -> None:
    """Surface a dropped (non-retried) message as billing data loss."""
    try:
        log_critical_error(
            function_name="process_usage_event_from_sqs",
            error_type="UsageEventDropped",
            error_message=f"Dropped usage event {message_id}: {reason}",
            current_user=user,
            severity="HIGH",
            context={"reason": reason, "raw": str(body)[:500]},
        )
    except Exception as log_err:
        logger.error("Failed to log billing-data-loss critical error: %s", str(log_err))


def _coerce_int(value: Any, default: int = 0) -> int:
    """Coerce a numeric field to int; raises ValueError on garbage."""
    if value is None:
        return default
    if isinstance(value, bool):
        # bool is an int subclass — reject it explicitly as a type error.
        raise ValueError("boolean is not a valid token count")
    return int(value)


def _build_account(body: Dict[str, Any]) -> Dict[str, str]:
    """Build the pycommon account dict.

    record_usage reads the COA from TWO different keys:
      - account["account_id"] -> CHAT_USAGE_DYNAMO_TABLE 'accountId' column
      - account["accountId"]  -> COST_CALCULATIONS composite key ('accountInfo')
    so we set both to the same real COA to keep the composite key correct.
    accessToken is intentionally omitted (no amp- API key here) so pycommon's
    get_api_key_id() returns None and the charge isn't misclassified.

    The COA comes from the event's account_id when a producer sends one (an
    explicit override), otherwise it is resolved server-side from the user's
    default account in ACCOUNTS_DYNAMO_TABLE. An explicit "general_account" is
    treated the same as absent — it is the no-COA sentinel, so we still try to
    find the user's real default.
    """
    account_id = body.get("account_id")
    if not account_id or account_id == DEFAULT_ACCOUNT_ID:
        account_id = resolve_default_account(body["user"])
    return {
        "user": body["user"],
        "account_id": account_id,
        "accountId": account_id,
    }


def _validate_common(body: Any) -> Tuple[bool, str]:
    """Validate fields common to all event types. Returns (ok, reason)."""
    if not isinstance(body, dict):
        return False, "message body is not a JSON object"
    if not body.get("type"):
        return False, "missing 'type'"
    if not body.get("user"):
        return False, "missing 'user'"
    return True, ""


def _process_llm_usage(body: Dict[str, Any]) -> None:
    reported_model_id = body.get("model_id") or ""
    user = body["user"]

    input_tokens = _coerce_int(body.get("input_tokens"))
    output_tokens = _coerce_int(body.get("output_tokens"))
    input_cached_tokens = _coerce_int(body.get("input_cached_tokens"))
    input_write_cached_tokens = _coerce_int(body.get("input_write_cached_tokens"))

    details = body.get("details") or {}
    if not isinstance(details, dict):
        details = {"details": str(details)}

    priced_model_id = resolve_model_id(reported_model_id)
    if priced_model_id is None:
        _report_unpriced_model(reported_model_id, user, EVENT_TYPE_LLM_USAGE)
        priced_model_id = reported_model_id  # still record usage row at $0
    elif priced_model_id != reported_model_id:
        details = {**details, "reported_model_id": reported_model_id}

    record_usage(
        account=_build_account(body),
        request_id=body.get("request_id") or "notebook-usage",
        model_id=priced_model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cached_tokens=input_cached_tokens,
        input_write_cached_tokens=input_write_cached_tokens,
        details=details,
    )


def _process_additional_charge(body: Dict[str, Any]) -> None:
    reported_model_id = body.get("model_id") or ""
    user = body["user"]
    item_type = body.get("item_type")

    flat_cost = body.get("flat_cost")
    if flat_cost is not None:
        flat_cost = float(flat_cost)
        token_count = 0
    else:
        token_count = _coerce_int(body.get("token_count"))

    details = body.get("details") or {}
    if not isinstance(details, dict):
        details = {"details": str(details)}

    priced_model_id = reported_model_id
    if flat_cost is None:
        # Only need a rate row when we're pricing from tokens.
        resolved = resolve_model_id(reported_model_id)
        if resolved is None:
            _report_unpriced_model(reported_model_id, user, EVENT_TYPE_ADDITIONAL_CHARGE)
        elif resolved != reported_model_id:
            details = {**details, "reported_model_id": reported_model_id}
            priced_model_id = resolved

    record_additional_charge(
        account=_build_account(body),
        model_id=priced_model_id,
        token_count=token_count,
        item_type=item_type,
        request_id=body.get("request_id"),
        details=details,
        flat_cost=flat_cost,
    )


@required_env_vars(
    {
        "CHAT_USAGE_DYNAMO_TABLE": [DynamoDBOperation.PUT_ITEM],
        "COST_CALCULATIONS_DYNAMO_TABLE": [
            DynamoDBOperation.PUT_ITEM,
            DynamoDBOperation.UPDATE_ITEM,
        ],
        "ADDITIONAL_CHARGES_TABLE": [DynamoDBOperation.PUT_ITEM],
        "MODEL_RATE_TABLE": [DynamoDBOperation.GET_ITEM, DynamoDBOperation.QUERY],
        "ACCOUNTS_DYNAMO_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@track_execution(operation_name="process_usage_event_from_sqs", account="system")
def process_usage_event_from_sqs(event: dict, context) -> dict:
    """Lambda handler: process usage events from SQS.

    Returns {"batchItemFailures": [...]} for partial-batch retry. Malformed or
    invalid messages are dropped (not retried) and logged as billing data loss;
    only unexpected errors are added to batchItemFailures.
    """
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId")

        try:
            try:
                body = json.loads(record["body"])
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON in message %s: %s", message_id, str(e))
                _report_billing_data_loss(message_id, f"invalid JSON: {e}", record.get("body"))
                continue  # don't retry malformed JSON

            ok, reason = _validate_common(body)
            if not ok:
                logger.error("Invalid usage event %s: %s", message_id, reason)
                user = body.get("user", "unknown") if isinstance(body, dict) else "unknown"
                _report_billing_data_loss(message_id, reason, body, user)
                continue  # don't retry malformed data

            event_type = body["type"]

            try:
                if event_type == EVENT_TYPE_LLM_USAGE:
                    _process_llm_usage(body)
                elif event_type == EVENT_TYPE_ADDITIONAL_CHARGE:
                    if not body.get("item_type"):
                        raise ValueError("additional_charge missing 'item_type'")
                    if body.get("flat_cost") is None and body.get("token_count") is None:
                        raise ValueError(
                            "additional_charge needs 'token_count' or 'flat_cost'"
                        )
                    _process_additional_charge(body)
                else:
                    raise ValueError(f"unknown event type '{event_type}'")
            except (ValueError, TypeError) as e:
                # Bad payload shape/values — drop, don't retry (would double-bill
                # the rest of the batch on redrive).
                logger.error("Rejecting usage event %s: %s", message_id, str(e))
                _report_billing_data_loss(message_id, str(e), body, body.get("user", "unknown"))
                continue

            logger.info(
                "Processed %s usage event %s for user %s",
                event_type,
                message_id,
                body.get("user"),
            )

        except Exception as e:
            # record_usage/record_additional_charge are themselves fail-safe
            # (they log and return 0.0), so reaching here means an unexpected
            # error worth a retry via redrive.
            logger.error(
                "Unexpected error processing message %s: %s",
                message_id,
                str(e),
                exc_info=True,
            )
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}
