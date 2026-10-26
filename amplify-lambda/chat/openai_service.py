"""OpenAI-compatible API façade: ``POST /v1/chat/completions`` and ``GET /v1/models``.

Translates OpenAI-format requests into the Amplify chat pipeline (the same preparation
``/chat`` uses, see ``service.prepare_chat_payload``) and translates the collected answer
back. Authentication is the standard ``Authorization: Bearer <amp- key>`` header, which
OpenAI SDKs already send. Streaming is not supported.

These routes intentionally do NOT use ``@api_tool``: that registry also exposes functions as
agent ops, and agents call ops with Amplify's ``{"data": ...}`` envelope, which this
OpenAI-shaped body does not use.
"""
import json
import os

import requests
from pycommon.api.get_endpoint import get_endpoint, EndpointType
from pycommon.authz import validated
from pycommon.const import APIAccessType
from pycommon.decorators import required_env_vars
from pycommon.exceptions import HTTPException
from pycommon.llm.chat import chat_streaming
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, SecretsManagerOperation
from pycommon.logger import getLogger

# Importing service also runs setup_validated(...) and registers the chat API access type.
from . import openai_compat as oc
from .service import prepare_chat_payload, ChatRequestError

logger = getLogger("openai_chat_completions")

MODELS_REQUEST_TIMEOUT_SECONDS = 15


def _raise(error):
    """Raise through @validated so the failure is tracked, keeping OpenAI error details."""
    raise HTTPException(error.status_code, error.encoded())


def _require_chat_access(data):
    access = data["allowed_access"]
    if APIAccessType.CHAT.value not in access and APIAccessType.FULL_ACCESS.value not in access:
        _raise(oc.OpenAIRequestError(
            "API key does not have access to chat functionality",
            code="insufficient_permissions", status_code=403))


def _collect_chat(chat_url, access_token, payload):
    """Run the chat pipeline to completion; returns (text, meta_events). Raises on failure."""
    parts, meta_events = [], []
    chat_streaming(
        chat_url,
        access_token,
        payload,
        lambda event: parts.append(oc.delta_text(event.get("d"))),
        meta_events.append,
    )
    return "".join(parts), meta_events


@required_env_vars({
    "FILES_DYNAMO_TABLE": [DynamoDBOperation.BATCH_GET_ITEM],
    "APP_ARN_NAME": [SecretsManagerOperation.GET_SECRET_VALUE],
})
@validated("chat")
def _chat_completions(event, context, current_user, name, data):
    # @validated merges its own keys (access_token, account, ...) into ``data``, so the
    # caller's body is re-read from the event to keep the two apart.
    _require_chat_access(data)
    access_token = data["access_token"]
    try:
        parsed = oc.parse_chat_completion_request(json.loads(event["body"]))
    except oc.OpenAIRequestError as e:
        _raise(e)

    chat_url = get_endpoint(EndpointType.CHAT_ENDPOINT)
    if not chat_url:
        _raise(oc.OpenAIRequestError("The chat service is not configured.", code="service_unavailable",
                                     status_code=503, error_type="api_error"))

    payload = oc.to_amplify_payload(parsed)
    try:
        prepare_chat_payload(payload, access_token)
    except ChatRequestError as e:
        _raise(oc.OpenAIRequestError(str(e), param="assistantId", code="invalid_value"))

    try:
        text, meta_events = _collect_chat(chat_url, access_token, payload)
    except Exception as e:
        logger.error("Chat pipeline failed: %s", e)
        _raise(oc.upstream_error(e))

    notice = oc.rate_limit_notice(meta_events)
    if notice:
        _raise(oc.OpenAIRequestError(notice, code="rate_limit_exceeded", status_code=429))
    if not text.strip():
        _raise(oc.OpenAIRequestError("The model returned an empty response.", code="empty_response",
                                     status_code=502, error_type="api_error"))

    return oc.build_chat_completion(payload["model"], text)


@validated("list")
def _list_models(event, context, current_user, name, data):
    _require_chat_access(data)
    response = requests.get(
        os.environ["API_BASE_URL"] + "/available_models",
        headers={"Authorization": f"Bearer {data['access_token']}", "Content-Type": "application/json"},
        timeout=MODELS_REQUEST_TIMEOUT_SECONDS,
    )
    try:
        body = response.json()
        models = body["data"]["models"] if response.ok and body.get("success") else None
    except (ValueError, KeyError, TypeError, AttributeError):
        models = None
    if models is None:
        logger.error("Could not load available models (status %s)", response.status_code)
        _raise(oc.OpenAIRequestError("Unable to retrieve models.", code="upstream_error",
                                     status_code=502, error_type="api_error"))
    return oc.build_model_list(models)


def _run(handler, event, context):
    try:
        return oc.finalize_response(handler(event, context))
    except Exception as e:  # @validated re-raises anything that is not an HTTPException
        logger.error("Unhandled error in %s: %s", getattr(handler, "__name__", "handler"), e)
        return oc.exception_response(e)


def chat_completions(event, context):
    """Lambda handler for ``POST /v1/chat/completions``."""
    try:
        parsed_body = json.loads(event.get("body") or "")
    except ValueError:
        return oc.error_response(400, "We could not parse the JSON body of your request.",
                                 code="invalid_json")
    if not isinstance(parsed_body, dict):
        return oc.error_response(400, "Request body must be a JSON object.", code="invalid_type")
    return _run(_chat_completions, event, context)


def list_models(event, context):
    """Lambda handler for ``GET /v1/models``."""
    return _run(_list_models, event, context)
