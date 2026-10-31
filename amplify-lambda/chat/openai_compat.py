"""OpenAI Chat Completions compatibility layer (pure translation, no AWS/pycommon imports).

Requests arriving in the OpenAI ``POST /v1/chat/completions`` shape are translated into
the payload the Amplify chat pipeline expects, and the collected answer is translated
back into an OpenAI ``chat.completion`` object. Anything we cannot honor is rejected
with an OpenAI-shaped 400 rather than silently ignored.

Deliberately dependency-free so it can be unit tested without the Lambda runtime.
"""
import json
import re
import time
import uuid

DEFAULT_TEMPERATURE = 1.0  # OpenAI's own default
# Amplify requires max_tokens; OpenAI makes it optional. Downstream providers clamp
# this to the selected model's output limit.
DEFAULT_MAX_TOKENS = 4096

# Fields that are accepted as-is by this endpoint.
_SUPPORTED_FIELDS = {
    "model", "messages", "temperature", "max_tokens", "max_completion_tokens", "stream",
}
# Amplify-specific extensions (send via the OpenAI SDK's ``extra_body``).
_EXTENSION_FIELDS = {"dataSources", "assistantId"}
# Accepted and ignored: they carry no behavior we are expected to reproduce.
_IGNORED_FIELDS = {"user", "metadata", "store", "prompt_cache_key", "safety_identifier"}
# Fields we cannot honor. Value = the "neutral" values that mean "not requested"
# (clients often send library defaults such as n=1 or top_p=1). ``None`` is always neutral.
_UNSUPPORTED_FIELDS = {
    "n": (1,),
    "top_p": (1,),
    "frequency_penalty": (0,),
    "presence_penalty": (0,),
    "stop": ([],),
    "seed": (),
    "logit_bias": ({},),
    "logprobs": (False,),
    "top_logprobs": (),
    "tools": ([],),
    "tool_choice": (),
    "parallel_tool_calls": (),
    "functions": ([],),
    "function_call": (),
    "response_format": ({"type": "text"},),
    "modalities": (["text"],),
    "audio": (),
    "prediction": (),
    "reasoning_effort": (),
    "verbosity": (),
    "web_search_options": (),
    "service_tier": ("auto",),
    "stream_options": (),
}

# Message-level fields that OpenAI clients echo back from earlier responses. They are
# harmless when empty and rejected when they carry real content.
_MESSAGE_ROLES = {"system", "user", "assistant"}
_MESSAGE_FIELDS = {"role", "content"}
_MESSAGE_NEUTRAL_FIELDS = {
    "refusal": ("",),
    "annotations": ([],),
    "audio": (),
    "function_call": (),
    "tool_calls": ([],),
    "name": (),
}

_STATUS_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "invalid_request_error",
    429: "rate_limit_error",
}


class OpenAIRequestError(Exception):
    """An error that maps onto an OpenAI ``{"error": {...}}`` response."""

    def __init__(self, message, param=None, code=None, status_code=400, error_type=None):
        super().__init__(message)
        self.message = message
        self.param = param
        self.code = code
        self.status_code = status_code
        self.error_type = error_type or _STATUS_ERROR_TYPES.get(status_code, "api_error")

    def body(self):
        return error_body(self.message, self.error_type, self.param, self.code)

    def encoded(self):
        """Serialize so it survives ``@validated``'s HTTPException(str) round trip."""
        return json.dumps(self.body()["error"])


def _reject(message, param, code="unsupported_parameter"):
    return OpenAIRequestError(message, param=param, code=code)


def _is_neutral(value, neutral):
    if value is None:
        return True
    for candidate in neutral:
        if isinstance(candidate, bool) or isinstance(value, bool):
            if isinstance(candidate, bool) and isinstance(value, bool) and candidate == value:
                return True
        elif value == candidate:
            return True
    return False


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def delta_text(value):
    """Displayable text from a stream ``d`` value (string, or ``{"delta": {"text": ..}}``)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        delta = value.get("delta")
        if isinstance(delta, dict):
            value = delta
        text = value.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _flatten_content(content, index, role):
    if content is None:
        if role == "assistant":
            return ""
        raise _reject(f"messages[{index}].content is required for role '{role}'.",
                      f"messages[{index}].content", "missing_required_parameter")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part_index, part in enumerate(content):
            param = f"messages[{index}].content[{part_index}]"
            if not isinstance(part, dict):
                raise _reject("Content parts must be objects.", param, "invalid_type")
            part_type = part.get("type")
            if part_type != "text":
                raise _reject(
                    f"Content part type '{part_type}' is not supported; only 'text' parts are. "
                    "Upload files/images separately and reference them with the 'dataSources' extension.",
                    f"{param}.type", "unsupported_value")
            if not isinstance(part.get("text"), str):
                raise _reject("Text content parts require a string 'text'.", f"{param}.text", "invalid_type")
            texts.append(part["text"])
        return "\n".join(texts)
    raise _reject("Message content must be a string or an array of text parts.",
                  f"messages[{index}].content", "invalid_type")


def _translate_messages(messages):
    if not isinstance(messages, list) or not messages:
        raise OpenAIRequestError("'messages' must be a non-empty array.", param="messages",
                                 code="invalid_value")
    translated = []
    for index, message in enumerate(messages):
        param = f"messages[{index}]"
        if not isinstance(message, dict):
            raise _reject("Each message must be an object.", param, "invalid_type")
        role = message.get("role")
        if role == "developer":
            role = "system"
        if role in ("tool", "function"):
            raise _reject(f"Messages with role '{role}' are not supported (tool calling is unavailable).",
                          f"{param}.role", "unsupported_value")
        if role not in _MESSAGE_ROLES:
            raise OpenAIRequestError(
                f"Invalid role '{role}'. Supported roles are system, developer, user and assistant.",
                param=f"{param}.role", code="invalid_value")
        for key, value in message.items():
            if key in _MESSAGE_FIELDS:
                continue
            if key in _MESSAGE_NEUTRAL_FIELDS and _is_neutral(value, _MESSAGE_NEUTRAL_FIELDS[key]):
                continue
            if key in _MESSAGE_NEUTRAL_FIELDS:
                raise _reject(f"'{key}' on messages is not supported.", f"{param}.{key}")
            raise _reject(f"Unrecognized message argument supplied: {key}", f"{param}.{key}",
                          "unknown_parameter")
        translated.append({"role": role, "content": _flatten_content(message.get("content"), index, role)})
    return translated


def _positive_int(value, param):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OpenAIRequestError(f"'{param}' must be a positive integer.", param=param, code="invalid_value")
    return value


def _translate_data_sources(data_sources):
    if not isinstance(data_sources, list):
        raise OpenAIRequestError("'dataSources' must be an array.", param="dataSources", code="invalid_type")
    for index, item in enumerate(data_sources):
        ok = isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("id"), str))
        if not ok:
            raise OpenAIRequestError(
                "Each data source must be a string id or an object with a string 'id'.",
                param=f"dataSources[{index}]", code="invalid_value")
    return data_sources


def parse_chat_completion_request(body):
    """Validate an OpenAI request body and return the fields Amplify needs.

    Raises OpenAIRequestError (400) for anything malformed or not honorable.
    """
    if not isinstance(body, dict):
        raise OpenAIRequestError("Request body must be a JSON object.", code="invalid_type")

    for key, value in body.items():
        if key in _SUPPORTED_FIELDS or key in _EXTENSION_FIELDS or key in _IGNORED_FIELDS:
            continue
        if key in _UNSUPPORTED_FIELDS:
            if not _is_neutral(value, _UNSUPPORTED_FIELDS[key]):
                raise _reject(f"'{key}' is not supported by this endpoint.", key)
            continue
        raise _reject(f"Unrecognized request argument supplied: {key}", key, "unknown_parameter")

    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise OpenAIRequestError("'model' is required and must be a non-empty string.", param="model",
                                 code="missing_required_parameter")

    if body.get("stream") not in (None, False):
        raise _reject("Streaming is not supported by this endpoint. Set 'stream' to false.", "stream")

    temperature = body.get("temperature")
    if temperature is None:
        temperature = DEFAULT_TEMPERATURE
    elif not _is_number(temperature) or not 0 <= temperature <= 2:
        raise OpenAIRequestError("'temperature' must be a number between 0 and 2.",
                                 param="temperature", code="invalid_value")

    # max_completion_tokens is the current name; max_tokens is its deprecated alias.
    if body.get("max_completion_tokens") is not None:
        max_tokens = _positive_int(body["max_completion_tokens"], "max_completion_tokens")
    elif body.get("max_tokens") is not None:
        max_tokens = _positive_int(body["max_tokens"], "max_tokens")
    else:
        max_tokens = DEFAULT_MAX_TOKENS

    assistant_id = body.get("assistantId")
    if assistant_id is not None and (not isinstance(assistant_id, str) or not assistant_id.strip()):
        raise OpenAIRequestError("'assistantId' must be a non-empty string.", param="assistantId",
                                 code="invalid_type")

    return {
        "model": model.strip(),
        "temperature": float(temperature),
        "max_tokens": max_tokens,
        "messages": _translate_messages(body.get("messages")),
        "dataSources": _translate_data_sources(body.get("dataSources", [])),
        "assistantId": assistant_id,
    }


def to_amplify_payload(parsed):
    """Build the payload shape the Amplify chat pipeline (``/chat``) expects."""
    options = {"model": {"id": parsed["model"]}}
    if parsed.get("assistantId"):
        options["assistantId"] = parsed["assistantId"]
    return {
        "model": parsed["model"],
        "temperature": parsed["temperature"],
        "max_tokens": parsed["max_tokens"],
        "dataSources": list(parsed["dataSources"]),
        "messages": parsed["messages"],
        "options": options,
    }


# --------------------------------------------------------------------------- responses

def build_chat_completion(model, content, completion_id=None, created=None):
    """OpenAI ``chat.completion`` object.

    ``usage`` is intentionally omitted: the chat pipeline does not report token counts back
    to this layer, and the field is optional in OpenAI's client models. ``finish_reason`` is
    always ``stop`` for the same reason.
    """
    return {
        "id": completion_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(created if created is not None else time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content, "refusal": None},
            "logprobs": None,
            "finish_reason": "stop",
        }],
    }


def build_model_list(models, created=None):
    """OpenAI ``list`` of ``model`` objects from Amplify's available-models payload."""
    created = int(created if created is not None else time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": model["id"],
                "object": "model",
                "created": created,
                "owned_by": model.get("provider") or "amplify",
            }
            for model in models or []
            if isinstance(model, dict) and model.get("id")
        ],
    }


def error_body(message, error_type="invalid_request_error", param=None, code=None):
    return {"error": {"message": message, "type": error_type, "param": param, "code": code}}


def rate_limit_notice(meta_events):
    """Return the rate-limit message if any meta event reports a reached limit, else None."""
    for event in meta_events or []:
        state = event.get("state") if isinstance(event, dict) else None
        if not isinstance(state, dict):
            continue
        for key in ("rateLimitNotice", "modelRateLimit"):
            notice = state.get(key)
            if isinstance(notice, dict) and notice.get("reachedLimit"):
                return notice.get("message") or "Rate limit reached."
    return None


_UPSTREAM_STATUS = re.compile(r"^Request failed with status (\d+):\s*(.*)$", re.DOTALL)


# The JS router reports an unusable model as an error event: "Invalid model: Model '<id>' not
# available for this user". It arrives over a 200 stream, so only the text identifies it.
_MODEL_NOT_AVAILABLE = re.compile(r"^Invalid model: (Model '.*' not available for this user)\s*$", re.DOTALL)


def upstream_error(exc):
    """Map an exception from the chat pipeline to an OpenAIRequestError."""
    text = str(exc) or exc.__class__.__name__
    model_match = _MODEL_NOT_AVAILABLE.match(text)
    if model_match:
        return OpenAIRequestError(model_match.group(1), param="model", code="model_not_found", status_code=404)
    match = _UPSTREAM_STATUS.match(text)
    if match:
        status, detail = int(match.group(1)), match.group(2) or text
        if status in (400, 401, 403, 404, 429):
            code = "rate_limit_exceeded" if status == 429 else None
            return OpenAIRequestError(detail, code=code, status_code=status)
        text = detail
    return OpenAIRequestError(text, code="upstream_error", status_code=502, error_type="api_error")


# ------------------------------------------------------- response/exception formatting

_HEADERS = {"Content-Type": "application/json"}
_VALIDATED_PREFIX = re.compile(r"^Error: (\d{3}) - ")


def error_response(status_code, message, error_type=None, param=None, code=None):
    return {
        "statusCode": status_code,
        "headers": dict(_HEADERS),
        "body": json.dumps(error_body(
            message, error_type or _STATUS_ERROR_TYPES.get(status_code, "api_error"), param, code)),
    }


def finalize_response(response):
    """Convert the output of an ``@validated`` handler into an OpenAI-shaped HTTP response.

    Successful results pass through. Errors from ``@validated`` (``{"error": "Error: 401 - ..."}``)
    and errors raised by handlers via ``OpenAIRequestError.encoded()`` become OpenAI error bodies.
    """
    status = response.get("statusCode", 200)
    if status == 200:
        return {**response, "headers": {**_HEADERS, **(response.get("headers") or {})}}

    try:
        error = json.loads(response.get("body") or "{}").get("error")
    except (ValueError, AttributeError):
        error = None

    if isinstance(error, dict) and "message" in error:
        return error_response(status, error["message"], error.get("type"), error.get("param"), error.get("code"))

    message = _VALIDATED_PREFIX.sub("", error) if isinstance(error, str) else "Request failed."
    # Handler-raised errors carry their details as a JSON string.
    try:
        details = json.loads(message)
        if isinstance(details, dict) and "message" in details:
            return error_response(status, details["message"], details.get("type"),
                                  details.get("param"), details.get("code"))
    except ValueError:
        pass

    # The API-key rate-limit check surfaces as HTTP 401 from the auth layer.
    if status == 401 and message.lower().startswith("rate limit exceeded"):
        return error_response(429, message, "rate_limit_error", code="rate_limit_exceeded")
    code = "invalid_api_key" if status == 401 else None
    return error_response(status, message, code=code)


def exception_response(exc):
    """Translate exceptions the auth layer re-raises (it only handles HTTPException itself)."""
    name = type(exc).__name__
    if isinstance(exc, LookupError):
        return error_response(401, "Incorrect API key provided.", code="invalid_api_key")
    if isinstance(exc, PermissionError):
        return error_response(403, str(exc) or "Permission denied.", code="insufficient_permissions")
    if name == "UnknownApiUserException":
        return error_response(401, "Incorrect API key provided.", code="invalid_api_key")
    # Never leak internals for unexpected failures.
    return error_response(500, "The server had an error while processing your request.",
                          "api_error", code="server_error")
