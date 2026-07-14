import base64
import uuid
import time
from functools import reduce
from io import BytesIO
import boto3
import botocore
from botocore.exceptions import ClientError
from pycommon.api.request_state import request_killed

import os
from pycommon.logger import getLogger
logger = getLogger("code_interpreter")


# AgentCore client and configuration
agentcore_client = boto3.client("bedrock-agentcore")
CODE_INTERPRETER_ID = os.environ.get("AGENTCORE_CODE_INTERPRETER_ID", "")
SESSION_TIMEOUT_SECONDS = int(os.environ.get("AGENTCORE_SESSION_TIMEOUT_SECONDS", "3600"))
# Maximum wall-clock seconds to wait for a single code execution to stream back
# results.  Defaults to 240 s (4 min) — comfortably inside Lambda's 5-min max.
EXECUTION_TIMEOUT_SECONDS = int(os.environ.get("AGENTCORE_EXECUTION_TIMEOUT_SECONDS", "240"))
AGENTCORE_MODEL_ID = "agentcore-code-interpreter"

# File extensions we surface to the user as "generated files". Output file
# detection is done via the native AgentCore `listFiles` operation (see
# list_sandbox_files() below) — no code injection into the user's script.
_WATCHED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".csv", ".xlsx"}

# MIME type lookup by extension.
_EXT_TO_MIME = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf":  "application/pdf",
    ".csv":  "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def get(dictionary, *keys):
    return reduce(
        lambda d, key: d.get(key, None) if isinstance(d, dict) else None,
        keys,
        dictionary,
    )


def file_keys_to_s3_bytes(file_keys):
    """Download files from S3 and return list of (file_name, file_bytes, mime_type) tuples."""
    if not file_keys:
        return []

    files_bucket_name = os.environ["S3_RAG_INPUT_BUCKET_NAME"]
    images_bucket_name = os.environ["S3_IMAGE_INPUT_BUCKET_NAME"]
    s3 = boto3.client("s3")

    result = []
    for file_key in file_keys:
        file_key_user = file_key.split("//")[1] if ("//" in file_key) else file_key
        if "@" not in file_key_user or len(file_key_user) <= 6:
            logger.warning("Skipping %s: doesn't look valid.", file_key)
            continue

        file_bytes = None
        mime_type = "binary/octet-stream"

        # Try files bucket first
        try:
            s3.head_object(Bucket=files_bucket_name, Key=file_key_user)
            logger.debug("[FOUND] Key '%s' is in the files bucket.", file_key_user)
            buf = BytesIO()
            s3.download_fileobj(files_bucket_name, file_key_user, buf)
            buf.seek(0)
            file_bytes = buf.read()
            buf.close()
        except botocore.exceptions.ClientError:
            logger.debug("[NOT FOUND] Key '%s' not in files bucket. Checking images bucket.", file_key_user)

        # Fall back to images bucket
        if file_bytes is None:
            try:
                s3.head_object(Bucket=images_bucket_name, Key=file_key_user)
                logger.debug("[FOUND] Key '%s' is in the images bucket.", file_key_user)
                s3_obj = s3.get_object(Bucket=images_bucket_name, Key=file_key_user)
                base64_data = s3_obj["Body"].read().decode("utf-8")
                file_bytes = base64.b64decode(base64_data)
                mime_type = "image/png"
            except botocore.exceptions.ClientError as e:
                logger.error(
                    "[ERROR] Could not find key '%s' in either bucket: %s",
                    file_key_user, e
                )
                continue

        if file_bytes:
            file_name = file_key_user.split("/")[-1] if "/" in file_key_user else file_key_user
            result.append((file_name, file_bytes, mime_type))

    return result


def load_files_for_session(session_id, file_keys):
    """Upload files into an AgentCore session via the writeFiles operation.

    The writeFiles operation accepts:
      arguments = {
          "content": [
              {"path": "<filename>", "blob": <bytes>},   # binary files
              {"path": "<filename>", "text": "<str>"},   # text files
          ]
      }
    Each file is placed at the given path inside the session sandbox.
    """
    logger.debug("Loading %d file(s) into AgentCore session %s", len(file_keys), session_id)
    files_data = file_keys_to_s3_bytes(file_keys)
    if not files_data:
        return

    content = []
    for file_name, file_bytes, mime_type in files_data:
        if mime_type.startswith("text/"):
            try:
                content.append({"path": file_name, "text": file_bytes.decode("utf-8")})
            except UnicodeDecodeError:
                content.append({"path": file_name, "blob": file_bytes})
        else:
            content.append({"path": file_name, "blob": file_bytes})

    try:
        response = agentcore_client.invoke_code_interpreter(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
            name="writeFiles",
            arguments={"content": content},
        )
        # Drain the event stream — writeFiles returns a stream that must be consumed
        for _ in response.get("stream", []):
            pass
        logger.info("Successfully loaded %d file(s) into session %s", len(files_data), session_id)
    except Exception as e:
        logger.error("Failed to write files to AgentCore session %s: %s", session_id, e)


def get_presigned_download_url(key, current_user, download_filename=None):
    """Generate a presigned download URL from the consolidation bucket."""
    s3 = boto3.client("s3")
    consolidation_bucket = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]

    logger.debug("Getting presigned download URL for %s for user %s", key, current_user)
    if current_user not in key:
        return {
            "success": False,
            "message": "User is not authorized to access this file",
        }

    response_headers = (
        {"ResponseContentDisposition": f'attachment; filename="{download_filename}"'}
        if download_filename
        else {}
    )

    consolidation_key = f"codeInterpreter/{key}"
    try:
        s3.head_object(Bucket=consolidation_bucket, Key=consolidation_key)
        presigned_url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": consolidation_bucket, "Key": consolidation_key, **response_headers},
            ExpiresIn=28800,  # 8 hours
        )
        return {"success": True, "downloadUrl": presigned_url}
    except ClientError as e:
        logger.debug("File not found in consolidation bucket: %s", str(e))

    logger.error("Failed to retrieve presigned url from consolidation bucket")
    return {"success": False, "message": "File not found"}


def extract_all_file_keys(messages, amplify_messages=True):
    """Collect all unique file keys across every message in the conversation.

    Used on session renewal so that all files ever attached are re-uploaded
    into the fresh AgentCore session.
    """
    seen = set()
    result = []
    for msg in (messages or []):
        if not amplify_messages:
            keys = msg.get("dataSourceIds", [])
        elif (
            msg.get("data")
            and "dataSources" in msg["data"]
            and msg["data"]["dataSources"]
        ):
            keys = [source["id"] for source in msg["data"]["dataSources"]]
        else:
            keys = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                result.append(k)
    return result


def renew_session(record_id, current_user, messages, amplify_messages=True, all_file_keys=None):
    """Create a fresh AgentCore session and update the DynamoDB record.

    Called when a session has expired mid-conversation.  Reloads every file
    the user has ever uploaded into the new session so the sandbox is fully
    restored.  The new session_id is persisted so subsequent requests reuse it.

    all_file_keys: when provided (collected by the JS layer before smart-messages
    may prune body.messages), used directly instead of extracting from messages.
    This guarantees files from pruned messages are still reloaded.
    """
    logger.info("Renewing expired AgentCore session for record %s", record_id)

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])

    if all_file_keys:
        all_keys = all_file_keys
        logger.info("renew_session: using %d pre-collected file key(s)", len(all_keys))
    else:
        all_keys = extract_all_file_keys(messages, amplify_messages=amplify_messages)

    session_info = create_agentcore_session(current_user, all_keys)
    if not session_info["success"]:
        return session_info

    new_session_id = session_info["data"]["sessionId"]

    try:
        table.update_item(
            Key={"id": record_id},
            UpdateExpression="SET #d.sessionId = :sid, updatedAt = :ts",
            ExpressionAttributeNames={"#d": "data"},
            ExpressionAttributeValues={
                ":sid": new_session_id,
                ":ts": int(time.time() * 1000),
            },
        )
        logger.info(
            "Updated DynamoDB record %s with new session_id %s",
            record_id, new_session_id,
        )
    except ClientError as e:
        logger.error(
            "Failed to persist renewed session_id for %s: %s",
            record_id, e.response["Error"]["Message"],
        )
        return {"success": False, "error": "Failed to persist renewed session"}

    return {"success": True, "session_id": new_session_id}


def chat_with_code_interpreter(current_user, record_id, messages, request_id, api_accessed, file_keys=None, all_conversation_file_keys=None):
    """Entry point for a chat request.

    Fetches the persisted session_id and executes the code.

    If the session has expired, a new session is created transparently:
    all file keys are derived from the full conversation history and
    re-uploaded into the new session, and the execution is retried once.
    The response includes sessionRenewed=True so the frontend can show a
    brief informational status message to the user.

    file_keys: explicit list of S3 keys for files attached to the current
    request.  When provided these take precedence over any keys extracted
    from the messages array so that files attached to a message in an
    existing session are reliably loaded into the sandbox before execution.

    all_conversation_file_keys: complete deduplicated list of every file key
    ever attached across all conversation messages, collected by the JS layer
    before smart-messages processing may prune old messages.  Used exclusively
    by renew_session so that a freshly created replacement session receives
    every file the user has uploaded, not just those in the (possibly pruned)
    messages array.
    """
    logger.debug("Entered chat_with_code_interpreter")

    record_existence = check_record_exists(record_id, current_user)
    if not record_existence["success"]:
        return record_existence

    session_id = record_existence["session_id"]
    amplify_messages = not api_accessed
    last_message = extract_last_message(messages, amplify_messages=amplify_messages)

    # If explicit file_keys were passed (from the JS layer's resolved dataSources),
    # override the keys extracted from the message.  This ensures files attached
    # to the current message are loaded even when the session already exists.
    if file_keys:
        last_message = {**last_message, "file_keys": file_keys}
        logger.info("Using %d explicit file_key(s) for this request", len(file_keys))

    active_session_id = session_id
    session_renewed = False
    result = chat(current_user, record_id, active_session_id, last_message, request_id, api_accessed=api_accessed)

    # Session expired — create a fresh session and retry once.
    # Prefer all_conversation_file_keys when available (collected before smart-messages
    # pruning) so every file ever uploaded is reloaded into the new session.
    # Fall back to deriving keys from the messages array for API-direct callers.
    if result.get("error") == "session_expired":
        logger.warning(
            "Session %s expired for record %s — renewing and retrying",
            active_session_id, record_id,
        )
        renewed = renew_session(
            record_id, current_user, messages,
            amplify_messages=amplify_messages,
            all_file_keys=all_conversation_file_keys or None,
        )
        if not renewed["success"]:
            return {
                "success": False,
                "error": "Session expired and could not be renewed. Please create a new session.",
            }
        active_session_id = renewed["session_id"]
        session_renewed = True
        logger.info("Retrying execution on new session %s", active_session_id)
        result = chat(current_user, record_id, active_session_id, last_message, request_id, api_accessed=api_accessed)

        if result.get("error") == "session_expired":
            return {
                "success": False,
                "error": "Code interpreter session could not be established. Please try again.",
            }

    if result.get("success") and session_renewed:
        result["sessionRenewed"] = True

    return result


def extract_last_message(messages, amplify_messages=True):
    """Return the last user message content and its attached file keys.

    AgentCore executes one code prompt per call — only the latest message matters.
    Conversation history is owned by Amplify, not AgentCore.
    """
    if not messages:
        return {"content": "", "file_keys": []}

    last = messages[-1]
    content = last.get("content", "")

    if not amplify_messages:
        file_keys = last.get("dataSourceIds", [])
    elif (
        last.get("data")
        and "dataSources" in last["data"]
        and last["data"]["dataSources"]
    ):
        file_keys = [source["id"] for source in last["data"]["dataSources"]]
    else:
        file_keys = []

    return {"content": content, "file_keys": file_keys}


def list_sandbox_files(session_id, directory_path=None):
    """List files currently present in the AgentCore sandbox filesystem.

    Uses the native `listFiles` operation (see AWS's `ToolName` enum:
    executeCode, executeCommand, readFiles, listFiles, removeFiles, writeFiles,
    startCommandExecution, getTask, stopTask). No code is injected into the
    user's script — this is a plain out-of-band call to the sandbox, the same
    way `readFiles`/`writeFiles` are already used elsewhere in this module.

    AgentCore's ContentBlock supports a `resource_link` type specifically
    designed for "pointer to a resource" results (uri/name/mimeType/size
    carried directly on the block, no content transferred) — this is what a
    directory listing is expected to return, one block per file/directory.
    Some content may instead arrive as a `resource` block (uri nested under
    `resource`) or, for older/alternate sandbox behavior, as a `text` block
    containing a plain listing — all are handled defensively below since the
    exact shape isn't pinned down by AWS's API reference (the operation's
    arguments/response are generic ToolArguments/ContentBlockList).

    Returns:
        A set of normalized file paths, or None if the listing could not be
        retrieved (so callers can distinguish "empty directory" from
        "listing failed" and skip diffing rather than produce false positives).
    """
    arguments = {"directoryPath": directory_path} if directory_path else {}
    # TEMP DIAGNOSTIC (see investigation notes): logs the exact arguments sent and the raw
    # content blocks returned by listFiles, so we can confirm the real response shape and
    # whether the default (no directoryPath) listing root actually matches the directory
    # executeCode writes generated files into. Remove once the file-return issue is diagnosed.
    logger.info("list_sandbox_files: invoking listFiles session=%s arguments=%s", session_id, arguments)

    try:
        response = agentcore_client.invoke_code_interpreter(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
            name="listFiles",
            arguments=arguments,
        )
    except Exception as e:
        logger.warning("listFiles call failed for session %s: %s", session_id, e)
        return None

    paths = set()
    try:
        for event in response.get("stream", []):
            if "result" not in event:
                continue
            result = event["result"]
            logger.info(
                "list_sandbox_files: raw result for session %s: isError=%s structuredContent=%s content=%s",
                session_id, result.get("isError"), result.get("structuredContent"), result.get("content"),
            )
            if result.get("isError"):
                logger.warning(
                    "listFiles returned isError for session %s: %s",
                    session_id, result.get("structuredContent"),
                )
                continue
            for block in result.get("content", []):
                block_type = block.get("type")
                logger.info(
                    "list_sandbox_files: block type=%s keys=%s raw=%s",
                    block_type, list(block.keys()), block,
                )
                if block_type in ("resource_link", "resource"):
                    resource = block.get("resource") or {}
                    uri = block.get("uri") or resource.get("uri", "")
                    path = None
                    if uri:
                        path = uri[len("file://"):] if uri.startswith("file://") else uri
                    elif block.get("name"):
                        path = block["name"]
                    if path:
                        paths.add(path)
                elif block_type == "text":
                    # Best-effort fallback: a plain-text directory listing,
                    # one path per line.
                    for line in block.get("text", "").splitlines():
                        line = line.strip()
                        if line:
                            paths.add(line)
    except Exception as e:
        logger.warning("Error parsing listFiles response for session %s: %s", session_id, e)
        return None

    logger.info("list_sandbox_files: session=%s directory_path=%s -> paths=%s", session_id, directory_path, paths)
    return paths


def _fetch_sandbox_file_inline(path, session_id):
    """Read a file's bytes from the AgentCore sandbox via readFiles and return
    them base64-encoded for inline rendering in the chat response.

    Generated files only need to be visible in the chat response — they don't
    need to persist anywhere — so there is no S3 upload / presigned URL step.
    The bytes flow straight from the sandbox back to the frontend.

    Called for each new path discovered by diffing list_sandbox_files() before
    and after code execution.

    Returns:
        {"success": True,  "data": {"type": mime, "values": {"data": <base64 str>, "file_name": ..., "file_size": <int>}}}
        {"success": False, "error": "..."}
    """
    file_name = os.path.basename(path)
    ext = os.path.splitext(file_name)[1].lower()
    mime_type = _EXT_TO_MIME.get(ext, "binary/octet-stream")
    logger.info("readFiles: path=%s mime_type=%s", path, mime_type)

    try:
        read_response = agentcore_client.invoke_code_interpreter(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
            name="readFiles",
            arguments={"paths": [path]},
        )
    except Exception as e:
        logger.error("readFiles API call failed for %s: %s", path, e)
        return {"success": False, "error": f"readFiles call failed: {e}"}

    file_bytes = b""
    text_chunks: list[str] = []
    try:
        for event in read_response.get("stream", []):
            if "result" not in event:
                continue
            for block in event["result"].get("content", []):
                # Bytes can arrive as a top-level blob/data field, or inside a
                # nested resource object (base64-encoded or raw bytes).
                raw = (
                    block.get("data")
                    or block.get("blob")
                    or (block.get("resource") or {}).get("blob")
                )
                if raw:
                    file_bytes += raw if isinstance(raw, bytes) else base64.b64decode(raw)
                    continue
                # Fallback: text content (e.g. CSV files returned as text/plain).
                # Accumulate all chunks before encoding so multi-chunk responses
                # are handled correctly and binary bytes always take precedence.
                text = block.get("text") or (block.get("resource") or {}).get("text")
                if text:
                    text_chunks.append(text)
    except Exception as e:
        logger.error("Error reading readFiles stream for %s: %s", path, e)
        return {"success": False, "error": f"readFiles stream error: {e}"}

    # If no binary data was received but text was, encode the accumulated text.
    if not file_bytes and text_chunks:
        file_bytes = "".join(text_chunks).encode("utf-8")

    if not file_bytes:
        logger.warning("readFiles returned no bytes for %s", path)
        return {"success": False, "error": f"readFiles returned empty content for {path}"}

    logger.info("Encoding sandbox file inline: name=%s size=%d", file_name, len(file_bytes))
    encoded = base64.b64encode(file_bytes).decode("ascii")
    return {
        "success": True,
        "data": {
            "type": mime_type,
            "values": {
                "data": encoded,
                "file_name": file_name,
                "file_size": len(file_bytes),
            },
        },
    }


def _send_stop_task(session_id, task_id):
    """Best-effort call to stopTask after a stream is drained."""
    if not task_id:
        return
    try:
        agentcore_client.invoke_code_interpreter(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
            name="stopTask",
            arguments={"taskId": task_id},
        )
        logger.info("Sent stopTask for task %s", task_id)
    except Exception as e:
        logger.warning("Failed to send stopTask for task %s: %s", task_id, e)


def chat(current_user, record_id, session_id, last_message, request_id, api_accessed=False):
    """Execute code via AgentCore and return structured results.

    AgentCore is a stateless code execution sandbox — it does not maintain
    conversation history. The session_id keeps the Python execution environment
    alive (variables, loaded files) across calls within the same session, but
    all prompt/response history is owned and managed by Amplify.

    Timeout note: AGENTCORE_EXECUTION_TIMEOUT_SECONDS caps the wall-clock time
    spent draining the stream.  When the deadline is exceeded we drain the rest
    of the stream as fast as possible to capture the taskId, then send stopTask
    and return a timeout error.

    Cancellation note: stopTask requires a new API call with the taskId captured
    from the stream's structuredContent. Because the event stream is a blocking
    iterator in the same thread, we cannot send stopTask while consuming the
    stream. Instead we check the kill switch before starting and drain the stream
    as fast as possible, sending stopTask after the stream closes if cancelled.

    Output file detection: rather than injecting tracking code into the user's
    script (a preamble/postamble that snapshots os.listdir() before/after and
    prints markers to stdout), we call the native `listFiles` operation via
    list_sandbox_files() immediately before and after `executeCode` and diff
    the two listings. This uses AgentCore's own tool surface exactly the way
    readFiles/writeFiles are already used elsewhere in this module, and it
    means output files are still detected even if the user's code raises an
    exception (the preamble/postamble approach could never run its cleanup
    code in that case since it depended on control flow reaching the postamble
    within the same script).
    """
    # Check kill switch before starting execution (skip for direct API access — no frontend managing request state)
    if not api_accessed and request_killed and request_id:
        try:
            if request_killed(current_user, request_id):
                logger.info("Request %s cancelled before execution", request_id)
                return {
                    "success": False,
                    "error": "Request was cancelled by user",
                    "cancelled": True,
                }
        except Exception as e:
            logger.warning("Failed to check kill switch: %s", e)

    user_code = last_message["content"]
    file_keys = last_message.get("file_keys", [])
    if file_keys:
        load_files_for_session(session_id, file_keys)

    # Snapshot the sandbox filesystem via the native listFiles operation before
    # running the user's code, unmodified, so we can detect newly created files
    # afterward by diffing (see list_sandbox_files() docstring for details).
    # A None result means the listing failed — we simply skip output-file
    # detection for this execution rather than risk false positives.
    files_before = list_sandbox_files(session_id)
    code = user_code

    task_id = None
    text_content = ""
    output_files = []
    cancelled = False
    timed_out = False
    deadline = time.monotonic() + EXECUTION_TIMEOUT_SECONDS

    try:
        logger.info("Invoking AgentCore code interpreter on session %s", session_id)
        response = agentcore_client.invoke_code_interpreter(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
            name="executeCode",
            arguments={
                "code": code,
                "language": "python",
                # clearContext=False preserves the session's execution state
                # (variables, imports, loaded dataframes) across calls.
                "clearContext": False,
            },
        )
    except Exception as e:
        logger.error("Failed to invoke AgentCore code interpreter: %s", e)
        err_str = str(e)
        if "ResourceNotFoundException" in err_str or (
            "ValidationException" in err_str and "not active" in err_str
        ):
            return {"success": False, "error": "session_expired"}
        return {"success": False, "error": f"Failed to invoke code interpreter: {err_str}"}

    execution_error = None

    try:
        for event in response.get("stream", []):
            # ── Timeout check ─────────────────────────────────────────────────
            # Once the deadline passes we stop collecting results and drain the
            # remaining stream events as fast as possible (to obtain the taskId
            # so we can send stopTask).  We do NOT break early because the SDK
            # stream iterator may hold open the underlying HTTP connection until
            # it is fully consumed.
            if not timed_out and time.monotonic() > deadline:
                logger.warning(
                    "Execution timeout (%ds) exceeded on session %s — draining to send stopTask",
                    EXECUTION_TIMEOUT_SECONDS, session_id,
                )
                timed_out = True

            # ── Kill-switch check ──────────────────────────────────────────────
            # We cannot call stopTask here (same thread, blocking stream) so we
            # drain the stream and send stopTask after the loop if cancelled.
            # Skip for direct API access — no frontend managing request state.
            if not api_accessed and not cancelled and request_killed and request_id:
                try:
                    if request_killed(current_user, request_id):
                        logger.info("Request %s cancelled during stream — draining", request_id)
                        cancelled = True
                except Exception:
                    pass

            # ── Service-level error events (throttling, auth, quota, etc.) ────
            error_keys = (
                "internalServerException",
                "throttlingException",
                "resourceNotFoundException",
                "accessDeniedException",
                "serviceQuotaExceededException",
                "validationException",
                "conflictException",
            )
            err_key = next((k for k in error_keys if k in event), None)
            if err_key:
                err_msg = event[err_key].get("message", err_key)
                logger.error("AgentCore stream error '%s': %s", err_key, err_msg)
                if err_key == "resourceNotFoundException" or (
                    err_key == "validationException" and "not active" in err_msg.lower()
                ):
                    return {"success": False, "error": "session_expired"}
                return {"success": False, "error": err_msg}

            logger.info("AgentCore stream event keys: %s", list(event.keys()))
            if "result" in event:
                r = event["result"]
                logger.info(
                    "AgentCore result: isError=%s structuredContent=%s content_block_types=%s",
                    r.get("isError"),
                    r.get("structuredContent"),
                    [b.get("type") for b in r.get("content", [])],
                )

            if "result" not in event:
                continue

            result = event["result"]
            structured = result.get("structuredContent", {})

            # Capture taskId for potential post-stream stopTask call
            task_id = structured.get("taskId", task_id)

            # Skip collecting output once we have timed out or been cancelled —
            # we only continue iterating to drain the stream and get the taskId.
            if timed_out or cancelled:
                continue

            # Collect text output from content blocks.
            # AgentCore only emits "text" blocks for executeCode — image/resource
            # blocks are never produced for savefig()-style files. Those are
            # detected after the stream by diffing list_sandbox_files() before/after.
            for block in result.get("content", []):
                if block.get("type") == "text":
                    text_content += block.get("text", "") + "\n"

            # isError signals a Python-level execution error (traceback in stderr).
            # We still collect any text/file content produced before the error.
            if result.get("isError"):
                stderr = structured.get("stderr", "")
                logger.error("Code execution error in session %s: %s", session_id, stderr)
                execution_error = stderr

    except Exception as e:
        logger.error("Exception while consuming AgentCore event stream: %s", e)
        return {"success": False, "error": f"Stream processing error: {e}"}

    # ── Post-stream: handle timeout and cancellation ───────────────────────────
    if timed_out:
        _send_stop_task(session_id, task_id)
        return {
            "success": False,
            "error": (
                f"Code execution timed out after {EXECUTION_TIMEOUT_SECONDS} seconds. "
                "Your code may be in an infinite loop or processing too much data."
            ),
        }

    if cancelled:
        _send_stop_task(session_id, task_id)
        return {
            "success": False,
            "error": "Request was cancelled by user during execution",
            "cancelled": True,
        }

    if execution_error is not None:
        return {"success": False, "error": f"Code execution error: {execution_error}"}

    # ── Post-execution: detect new files via listFiles diffing ────────────────
    # Take a second snapshot and diff against the pre-execution listing to find
    # files the user's code created. Only files with a recognized/expected
    # extension are surfaced (matches prior behavior) — this avoids attaching
    # incidental sandbox artifacts (e.g. __pycache__) that aren't meaningful
    # outputs to show the user.
    discovered_paths = []
    files_after = list_sandbox_files(session_id)
    if files_before is not None and files_after is not None:
        new_paths = sorted(files_after - files_before)
        discovered_paths = [
            p for p in new_paths
            if os.path.splitext(p)[1].lower() in _WATCHED_EXTENSIONS
        ]
        logger.info("listFiles diff discovered %d file(s): %s", len(discovered_paths), discovered_paths)
    else:
        logger.warning(
            "Could not determine sandbox file listing for session %s — "
            "skipping output file detection for this execution.", session_id,
        )

    for path in discovered_paths:
        file_result = _fetch_sandbox_file_inline(path, session_id)
        if file_result.get("success"):
            output_files.append(file_result["data"])
            logger.info("Sandbox file encoded inline: %s -> %s", path, file_result["data"].get("type"))
        else:
            logger.warning("Failed to fetch sandbox file %s: %s", path, file_result.get("error"))

    return {
        "success": True,
        "message": "Chat completed successfully",
        "data": {
            "data": {
                "codeInterpreterRecordId": record_id,
                "textContent": text_content.rstrip("\n"),
                "content": output_files,
            }
        },
    }


def record_session_charge(info):
    """Record a flat per-session charge when a new AgentCore session is created."""
    from pycommon.api.accounting import record_additional_charge
    from datetime import datetime, timezone

    logger.debug("Recording session charge")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    try:
        record_additional_charge(
            account={"user": info["current_user"], "account_id": info["account_id"]},
            model_id=AGENTCORE_MODEL_ID,
            token_count=0,
            item_type="agentCoreCodeInterpreterSession",
            request_id=info["request_id"],
            details={
                "session_timestamp": timestamp,
                "record_id": info.get("record_id"),
                "session_id": info.get("session_id"),
            },
            ttl_days=None,
            flat_cost=0.03,
        )
        logger.debug("Session charge recorded")
    except Exception as e:
        logger.error("Failed to record session charge: %s", e)


def get_record(record_id, current_user):
    """Fetch a code interpreter record from DynamoDB and return its AgentCore session ID."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])

    try:
        response = table.get_item(Key={"id": record_id})

        if "Item" not in response:
            return {"success": False, "error": "Code interpreter record not found"}

        item = response["Item"]
        if item["user"] != current_user:
            return {"success": False, "error": "Not authorized to access this code interpreter session"}

        session_id = get(item, "data", "sessionId")
        if session_id:
            return {"success": True, "record_id": record_id, "session_id": session_id}
        return {"success": False, "error": "Code interpreter record has no active session"}

    except ClientError as e:
        logger.error("ClientError: %s", e.response["Error"]["Message"])
        return {"success": False, "error": str(e)}


def check_record_exists(record_id, current_user):
    """Verify a code interpreter record exists and return its AgentCore session ID."""
    record_info = get_record(record_id, current_user)
    if not record_info["success"]:
        return record_info
    return {"success": True, "session_id": record_info["session_id"]}


def create_agentcore_session(user_id, file_keys):
    """Start a new AgentCore code interpreter session and optionally load files."""
    logger.info("Creating AgentCore code interpreter session for user %s", user_id)
    try:
        response = agentcore_client.start_code_interpreter_session(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionTimeoutSeconds=SESSION_TIMEOUT_SECONDS,
        )
        session_id = response["sessionId"]
        logger.info("Created AgentCore session: %s", session_id)

        if file_keys:
            load_files_for_session(session_id, file_keys)

        return {"success": True, "data": {"sessionId": session_id}}
    except Exception as e:
        logger.error("Failed to create AgentCore session: %s", e)
        return {"success": False, "error": f"Failed to create AgentCore session: {e}"}


def create_new_session(user_id, file_keys, account_id="", request_id=""):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])
    timestamp = int(time.time() * 1000)

    for file_key in file_keys:
        file_key_user = file_key.split("//")[1] if "//" in file_key else file_key
        # TEMP DIAGNOSTIC (see investigation notes): logs the raw values being compared so
        # we can tell apart a genuine authorization mismatch from a casing/format mismatch
        # between the uploading Lambda's current_user and this Lambda's current_user, and
        # from group-assistant key substitution (groupId has no "@").  Remove once the
        # session-creation/file-authorization issue is confirmed diagnosed.
        logger.info(
            "create_new_session auth check: user_id=%r file_key=%r file_key_user=%r "
            "has_at=%s len=%d user_in_key=%s",
            user_id, file_key, file_key_user,
            "@" in file_key_user, len(file_key_user), user_id in file_key_user,
        )
        if "@" not in file_key_user or len(file_key_user) < 6 or user_id not in file_key_user:
            logger.warning(
                "create_new_session: authorization FAILED for user_id=%r against file_key_user=%r",
                user_id, file_key_user,
            )
            return {"success": False, "error": "You are not authorized to access the referenced files"}

    session_info = create_agentcore_session(user_id, file_keys)
    if not session_info["success"]:
        return session_info

    record_id = f"{user_id}/ast/{str(uuid.uuid4())}"
    table.put_item(Item={
        "id": record_id,
        "user": user_id,
        "assistant": "AgentCoreCodeInterpreter",
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "data": {"sessionId": session_info["data"]["sessionId"]},
    })
    logger.info("Created code interpreter record %s for user %s", record_id, user_id)

    record_session_charge({
        "current_user": user_id,
        "account_id": account_id,
        "request_id": request_id,
        "record_id": record_id,
        "session_id": session_info["data"]["sessionId"],
    })

    return {
        "success": True,
        "message": "Code interpreter session created successfully",
        "data": {"codeInterpreterRecordId": record_id},
    }


def delete_record_by_id(record_id, user_id):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])

    try:
        response = table.get_item(Key={"id": record_id})
    except ClientError as e:
        logger.error("ClientError: %s", e.response["Error"]["Message"])
        return {"success": False, "message": "Code interpreter record not found"}

    if "Item" not in response:
        return {"success": False, "message": "Code interpreter record not found"}

    item = response["Item"]
    if item["user"] != user_id:
        return {"success": False, "message": "Not authorized to delete this code interpreter session"}

    # Stop the AgentCore session
    session_id = get(item, "data", "sessionId")
    if session_id:
        try:
            agentcore_client.stop_code_interpreter_session(
                codeInterpreterIdentifier=CODE_INTERPRETER_ID,
                sessionId=session_id,
            )
            logger.info("Stopped AgentCore session: %s", session_id)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ("ConflictException", "ResourceNotFoundException"):
                logger.info("Session %s already stopped or gone (%s)", session_id, error_code)
            else:
                logger.warning("Could not stop AgentCore session %s: %s", session_id, e)
        except Exception as e:
            logger.warning("Could not stop AgentCore session %s: %s", session_id, e)

    try:
        table.delete_item(Key={"id": record_id})
    except ClientError as e:
        logger.error("ClientError: %s", e.response["Error"]["Message"])
        return {"success": False, "message": "Failed to delete code interpreter record from database"}

    return {"success": True, "message": "Code interpreter session deleted successfully"}
