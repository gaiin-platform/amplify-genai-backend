"""
usage_events.py

Lambda function that processes usage events from SQS and records them through
the standard pycommon accounting helpers.

External services that run LLM/embedding workloads outside the Amplify request
path (e.g., the Open Notebook integration) measure their own token usage and
send events to the UsageEventsQueue. This function drains the queue and records
each event so external usage lands in the same tables (and MTD cost /
rate-limit views) as usage produced by Amplify's own services.

Event payload (JSON message body):
    {
        "type": "llm_usage" | "additional_charge",
        "user": "someone@example.com",          # required
        "item_type": "notebook_chat",           # required, e.g. notebook_ask,
                                                #   notebook_embedding, notebook_tts
        "model_id": "us.anthropic.claude-sonnet-4-6",
        "request_id": "abc-123",                # optional
        "account_id": "notebook",               # optional, defaults to "notebook"
        "details": { ... },                     # optional metadata

        # llm_usage only:
        "input_tokens": 1200,
        "output_tokens": 340,
        "input_cached_tokens": 0,               # optional
        "input_write_cached_tokens": 0,         # optional

        # additional_charge only:
        "token_count": 5000,                    # or:
        "flat_cost": 0.03                       # fixed USD amount
    }

llm_usage events go through record_usage (chat-usage table + cost-calculations
aggregate, so they appear in MTD costs and count toward rate limits).
additional_charge events go through record_additional_charge (audit table),
matching how Amplify treats its own embedding costs.

Copyright (c) 2026 Vanderbilt University
"""

import json
import os
import re
import sys
import traceback
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3

from pycommon.api.accounting import record_additional_charge, record_usage
from pycommon.api.critical_logging import log_critical_error, SEVERITY_HIGH
from pycommon.decorators import required_env_vars, track_execution
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation
from pycommon.logger import getLogger

logger = getLogger("usage_events")

# Constants
EVENT_TYPE_LLM_USAGE = "llm_usage"
EVENT_TYPE_ADDITIONAL_CHARGE = "additional_charge"

NUMERIC_FIELDS = (
    "input_tokens",
    "output_tokens",
    "input_cached_tokens",
    "input_write_cached_tokens",
    "token_count",
    "flat_cost",
)

# Attribution used when the producer doesn't specify one; keeps external usage
# visible as its own line in the user's cost breakdown.
DEFAULT_ACCOUNT_ID = "notebook"

# Bedrock model ids often carry a date/version suffix (e.g.
# us.anthropic.claude-sonnet-4-6-20250514-v1:0) while the rate table may key
# the undated id. Strip the suffix as a fallback when the exact id has no rate.
MODEL_ID_DATE_SUFFIX = re.compile(r"-\d{8}-v\d+:\d+$")

_dynamodb_client = None
_model_id_cache = {}


def _get_dynamodb_client():
    """Lazy initialization of DynamoDB client to avoid import-time boto3 calls."""
    global _dynamodb_client
    if _dynamodb_client is None:
        _dynamodb_client = boto3.client("dynamodb")
    return _dynamodb_client


def _has_model_rate(model_id: str) -> bool:
    """Check whether the model rate table prices the given model id."""
    try:
        response = _get_dynamodb_client().get_item(
            TableName=os.environ["MODEL_RATE_TABLE"],
            Key={"ModelID": {"S": model_id}},
        )
        return "Item" in response
    except Exception as e:
        logger.warning("Model rate lookup failed for %s: %s", model_id, str(e))
        return False


def resolve_model_id(model_id: str) -> Optional[str]:
    """
    Map an incoming model id to the id the rate table actually prices.

    Args:
        model_id: Model id as reported by the producing service

    Returns:
        Optional[str]: The rate-table model id (the input unchanged, or its
             undated form when only that has a rate row), or None when neither
             is priced.
    """
    if model_id in _model_id_cache:
        return _model_id_cache[model_id]

    if _has_model_rate(model_id):
        _model_id_cache[model_id] = model_id
        return model_id

    stripped = MODEL_ID_DATE_SUFFIX.sub("", model_id)
    if stripped != model_id and _has_model_rate(stripped):
        logger.info("Normalized model id %s -> %s", model_id, stripped)
        _model_id_cache[model_id] = stripped
        return stripped

    # Not cached: a transient lookup failure or a rate row added later
    # should be picked up by subsequent events.
    return None


def _validate_event(payload: Dict[str, Any]) -> Optional[str]:
    """
    Validate a usage event payload.

    Args:
        payload: Parsed message body

    Returns:
        Optional[str]: A description of the problem, or None when valid.
    """
    if not isinstance(payload, dict):
        return f"payload is not a JSON object: {type(payload).__name__}"

    event_type = payload.get("type")
    if event_type not in (EVENT_TYPE_LLM_USAGE, EVENT_TYPE_ADDITIONAL_CHARGE):
        return f"unknown event type: {event_type}"

    missing_fields = [f for f in ["user", "item_type"] if not payload.get(f)]
    if missing_fields:
        return f"missing required fields: {missing_fields}"

    if event_type == EVENT_TYPE_LLM_USAGE and not payload.get("model_id"):
        return "llm_usage event missing model_id"

    for field in NUMERIC_FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        # bool is an int subclass; reject it explicitly
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            return f"non-numeric value for {field}: {value!r}"
        if value < 0:
            return f"negative value for {field}: {value}"

    return None


def _record_event(payload: Dict[str, Any]) -> float:
    """
    Record a validated usage event through the pycommon accounting helpers.

    Args:
        payload: Parsed and validated message body

    Returns:
        float: Cost recorded for the event in USD.
    """
    event_type = payload["type"]
    user = payload["user"]
    item_type = payload["item_type"]
    model_id = payload.get("model_id")

    account_id = payload.get("account_id") or DEFAULT_ACCOUNT_ID
    # record_usage reads account_id for the usage row and accountId for the
    # cost-calculations composite key, so provide both.
    account = {"user": user, "account_id": account_id, "accountId": account_id}

    request_id = payload.get("request_id") or str(uuid.uuid4())
    details = {**(payload.get("details") or {}), "itemType": item_type}

    flat_cost = payload.get("flat_cost")
    resolved_model_id = resolve_model_id(model_id) if model_id else None

    # Pricing is required unless the producer supplied a flat cost. Record the
    # event anyway (audit trail) but alert: it lands at $0 until a rate row
    # for the model is added.
    needs_rate = event_type == EVENT_TYPE_LLM_USAGE or flat_cost is None
    if model_id and resolved_model_id is None and needs_rate:
        _log_unpriced_model(model_id, user, item_type)

    if event_type == EVENT_TYPE_LLM_USAGE:
        cost = record_usage(
            account,
            request_id,
            resolved_model_id or model_id,
            int(payload.get("input_tokens", 0)),
            int(payload.get("output_tokens", 0)),
            int(payload.get("input_cached_tokens", 0)),
            int(payload.get("input_write_cached_tokens", 0)),
            details,
        )
    else:
        cost = record_additional_charge(
            account=account,
            model_id=resolved_model_id or model_id or "NA",
            token_count=int(payload.get("token_count", 0)),
            item_type=item_type,
            request_id=request_id,
            details=details,
            flat_cost=float(flat_cost) if flat_cost is not None else None,
        )

    logger.info(
        "Recorded %s event for user %s: $%.6f (model: %s)",
        item_type,
        user,
        cost,
        model_id,
    )
    return cost


def _log_dropped_event(message_id: str, reason: str, body: str) -> None:
    """Surface a permanently dropped usage event as a critical error (billing data loss)."""
    try:
        log_critical_error(
            function_name="process_usage_event_from_sqs",
            error_type="UsageEventDropped",
            error_message=f"Dropped unprocessable usage event: {reason}",
            current_user="system",
            severity=SEVERITY_HIGH,
            stack_trace=traceback.format_exc() if sys.exc_info()[0] else None,
            context={"message_id": message_id, "body": body[:500]},
        )
    except Exception as log_err:
        logger.error("Failed to log critical error: %s", str(log_err))


def _log_unpriced_model(model_id: str, user: str, item_type: str) -> None:
    """Alert that usage was recorded at $0 because the rate table has no row for the model."""
    logger.warning("No model rate found for %s; usage records at $0", model_id)
    try:
        log_critical_error(
            function_name="process_usage_event_from_sqs",
            error_type="UsageEventUnpricedModel",
            error_message=(
                f"Usage event for model '{model_id}' has no rate table entry; "
                f"cost recorded as $0. Add the model to the rate table."
            ),
            current_user=user,
            severity=SEVERITY_HIGH,
            context={"model_id": model_id, "item_type": item_type},
        )
    except Exception as log_err:
        logger.error("Failed to log critical error: %s", str(log_err))


@required_env_vars(
    {
        "MODEL_RATE_TABLE": [DynamoDBOperation.GET_ITEM],
    }
)
@track_execution(operation_name="process_usage_event_from_sqs", account="system")
def process_usage_event_from_sqs(event: dict, context) -> dict:
    """
    Lambda handler for processing usage events from SQS.

    Args:
        event: SQS event containing Records with usage event data
        context: Lambda context object

    Returns:
        dict: Response with batchItemFailures for partial batch failure handling
    """
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId")
        body = record.get("body", "")

        try:
            # Parse the SQS message body; keep numerics Dynamo-safe (no floats)
            payload = json.loads(body, parse_float=Decimal)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in message %s: %s", message_id, str(e))
            # Don't retry - malformed JSON
            _log_dropped_event(message_id, f"invalid JSON: {str(e)}", body)
            continue

        problem = _validate_event(payload)
        if problem:
            logger.error("Invalid usage event in message %s: %s", message_id, problem)
            # Skip this message - don't retry malformed data
            _log_dropped_event(message_id, problem, body)
            continue

        try:
            _record_event(payload)
        except Exception as e:
            logger.error(
                "Unexpected error processing message %s: %s",
                message_id,
                str(e),
                exc_info=True,
            )
            # Add to batch failures for retry
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}
