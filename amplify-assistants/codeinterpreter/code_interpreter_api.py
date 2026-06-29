import base64
import math
import uuid
import time
from functools import reduce
from io import BytesIO
import boto3
import botocore
from botocore.exceptions import ClientError, NoCredentialsError
from pycommon.api.request_state import request_killed

import os
from PIL import Image
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


def send_file_to_s3(file_bytes, file_key, file_name, user_id, content_type="binary/octet-stream"):
    """Upload output file bytes to the S3 consolidation bucket and return a presigned URL."""
    logger.debug("Sending file to S3: %s", file_key)
    s3 = boto3.client("s3")
    consolidation_bucket = os.environ["S3_CONSOLIDATION_BUCKET_NAME"]
    consolidation_key = f"codeInterpreter/{file_key}"

    try:
        s3.upload_fileobj(
            BytesIO(file_bytes),
            consolidation_bucket,
            consolidation_key,
            ExtraArgs={"ACL": "private", "ContentType": content_type},
        )
        logger.info("File uploaded to consolidation bucket: %s/%s", consolidation_bucket, consolidation_key)

        file_url = get_presigned_download_url(file_key, user_id, file_name)
        if file_url["success"]:
            return {"success": True, "presigned_url": file_url["downloadUrl"]}
        return file_url

    except NoCredentialsError:
        logger.error("Credentials not available")
    except ClientError as e:
        logger.error("ClientError uploading file to S3: %s", e.response["Error"]["Message"])
    except Exception as e:
        logger.error("Unexpected error uploading file to S3: %s", e)

    return {"success": False, "error": "Failed to upload file to S3"}


def create_low_res_version(file_bytes):
    """Resize an image to under 200 KB while maintaining aspect ratio."""
    logger.debug("Creating lower resolution version of image")
    image = Image.open(BytesIO(file_bytes))
    original_width, original_height = image.size
    target_size_bytes = 204800  # 200 KB
    max_width, max_height = 800, 600

    resized_bytes = BytesIO()
    try:
        while True:
            ratio = min(max_width / original_width, max_height / original_height)
            target_size = (int(original_width * ratio), int(original_height * ratio))
            resized_image = image.resize(target_size, Image.LANCZOS)

            resized_bytes.seek(0)
            resized_bytes.truncate()
            resized_image.save(resized_bytes, format=image.format or "PNG")
            resized_size = resized_bytes.tell()

            if resized_size <= target_size_bytes:
                break

            scale_factor = math.sqrt(resized_size / target_size_bytes)
            max_width = int(max_width / scale_factor)
            max_height = int(max_height / scale_factor)

            if max_width < 100 or max_height < 100:
                raise ValueError("Cannot reduce image below 100px — target threshold unreachable.")

        resized_bytes.seek(0)
        return resized_bytes.read()
    finally:
        resized_bytes.close()


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


def renew_session(record_id, current_user, messages, amplify_messages=True):
    """Create a fresh AgentCore session and update the DynamoDB record.

    Called when a session has expired mid-conversation.  Derives all file keys
    directly from the full conversation messages so that every file ever
    attached is re-uploaded into the new session.  The new session_id is
    persisted so that subsequent requests reuse the session automatically.
    """
    logger.info("Renewing expired AgentCore session for record %s", record_id)

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])

    all_keys = extract_all_file_keys(messages, amplify_messages=amplify_messages)

    session_info = create_agentcore_session(current_user, all_keys)
    if not session_info["success"]:
        return session_info

    new_session_id = session_info["data"]["sessionId"]

    try:
        table.update_item(
            Key={"id": record_id},
            UpdateExpression="SET #d.sessionId = :sid",
            ExpressionAttributeNames={"#d": "data"},
            ExpressionAttributeValues={":sid": new_session_id},
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


def chat_with_code_interpreter(current_user, record_id, messages, request_id, api_accessed):
    """Entry point for a chat request.

    Fetches the persisted session_id and executes the code.

    If the session has expired, a new session is created transparently:
    all file keys are derived from the full conversation messages and
    re-uploaded into the new session, and the execution is retried once.
    The response includes sessionRenewed=True so the frontend can show a
    brief informational status message to the user.
    """
    logger.debug("Entered chat_with_code_interpreter")

    record_existence = check_record_exists(record_id, current_user)
    if not record_existence["success"]:
        return record_existence

    session_id = record_existence["session_id"]
    amplify_messages = not api_accessed
    last_message = extract_last_message(messages, amplify_messages=amplify_messages)

    active_session_id = session_id
    session_renewed = False
    result = chat(current_user, record_id, active_session_id, last_message, request_id)

    # Session expired — create a fresh session and retry once.
    # Derive all file keys from the full conversation messages and re-upload
    # them into the new session before retrying the execution.
    if result.get("error") == "session_expired":
        logger.warning(
            "Session %s expired for record %s — renewing and retrying",
            active_session_id, record_id,
        )
        renewed = renew_session(record_id, current_user, messages, amplify_messages=amplify_messages)
        if not renewed["success"]:
            return {
                "success": False,
                "error": "Session expired and could not be renewed. Please create a new session.",
            }
        active_session_id = renewed["session_id"]
        session_renewed = True
        logger.info("Retrying execution on new session %s", active_session_id)
        result = chat(current_user, record_id, active_session_id, last_message, request_id)

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


def extract_file_from_block(block, current_user):
    """Extract a file from an AgentCore result content block and upload it to S3.

    AgentCore result content blocks have this structure:
      {
        "type": "text" | "image" | "resource" | "resource_link",
        "text": "...",          # for type=text
        "data": <bytes>,        # for type=image (raw bytes, NOT base64)
        "mimeType": "image/png",# for type=image
        "uri": "...",           # for type=resource / resource_link
        "name": "...",
        "size": <int>,
        "resource": {           # for type=resource
            "type": "...",
            "uri": "...",
            "mimeType": "...",
            "blob": <bytes>,    # binary resource content
            "text": "...",      # text resource content
        }
      }
    """
    block_type = block.get("type")

    if block_type == "image":
        # data is raw bytes from the SDK (not base64)
        file_bytes = block.get("data", b"")
        if isinstance(file_bytes, str):
            file_bytes = base64.b64decode(file_bytes)
        mime_type = block.get("mimeType", "image/png")
        ext = mime_type.split("/")[-1] if "/" in mime_type else "png"
        file_name = f"generated_image.{ext}"
        s3_file_key = f"{current_user}/{uuid.uuid4()}-FN-{file_name}"

    elif block_type == "resource":
        resource = block.get("resource", {})
        mime_type = resource.get("mimeType", "binary/octet-stream")
        uri = resource.get("uri", "") or block.get("uri", "")
        file_name = uri.split("/")[-1] if uri else ""
        if not file_name:
            ext = mime_type.split("/")[-1] if "/" in mime_type else "bin"
            file_name = f"generated_file.{ext}"
        s3_file_key = f"{current_user}/{uuid.uuid4()}-FN-{file_name}"

        # Prefer blob (bytes), fall back to text encoded as UTF-8
        raw = resource.get("blob")
        if raw is None:
            text = resource.get("text", "")
            raw = text.encode("utf-8") if text else b""
        file_bytes = raw if isinstance(raw, bytes) else base64.b64decode(raw)

    else:
        return {"success": False, "error": f"Unhandled block type: {block_type}"}

    if not file_bytes:
        return {"success": False, "error": f"Empty file content for block type: {block_type}"}

    return upload_file_and_get_urls(file_bytes, mime_type, s3_file_key, current_user, file_name)


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


def chat(current_user, record_id, session_id, last_message, request_id):
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
    """
    # Check kill switch before starting execution
    if request_killed and request_id:
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

    code = last_message["content"]
    file_keys = last_message.get("file_keys", [])
    if file_keys:
        load_files_for_session(session_id, file_keys)

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
        if "ResourceNotFoundException" in err_str:
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
            if not cancelled and request_killed and request_id:
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
                if err_key == "resourceNotFoundException":
                    return {"success": False, "error": "session_expired"}
                return {"success": False, "error": err_msg}

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

            # Collect text and file outputs from content blocks
            for block in result.get("content", []):
                btype = block.get("type")
                if btype == "text":
                    text_content += block.get("text", "") + "\n"
                elif btype in ("image", "resource"):
                    file_result = extract_file_from_block(block, current_user)
                    if file_result.get("success"):
                        output_files.append(file_result["data"])
                    else:
                        logger.warning("Failed to extract file block: %s", file_result.get("error"))

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

    return {
        "success": True,
        "message": "Chat completed successfully",
        "data": {
            "data": {
                "codeInterpreterRecordId": record_id,
                "role": "assistant",
                "textContent": text_content.rstrip("\n"),
                "content": output_files,
            }
        },
    }


def upload_file_and_get_urls(file_bytes, content_type, file_key, current_user, file_name=None):
    """Upload file bytes to S3 and return presigned URL(s).

    For PNG images over 200 KB, also creates and uploads a low-res version.
    """
    values = {}

    presigned = send_file_to_s3(file_bytes, file_key, file_name, current_user, content_type)
    if presigned and presigned.get("success"):
        values["file_key"] = file_key
        values["presigned_url"] = presigned["presigned_url"]
        values["file_size"] = len(file_bytes)

    if "png" in content_type and len(file_bytes) > 204800:
        logger.debug("PNG exceeds 200 KB — creating low-res version")
        try:
            low_res_bytes = create_low_res_version(file_bytes)
            file_key_low_res = file_key + "-low-res"
            presigned_low_res = send_file_to_s3(
                low_res_bytes, file_key_low_res, file_name, current_user, content_type
            )
            if presigned_low_res and presigned_low_res.get("success"):
                values["file_key_low_res"] = file_key_low_res
                values["presigned_url_low_res"] = presigned_low_res["presigned_url"]
        except Exception as e:
            logger.warning("Failed to create low-res image: %s", e)

    if values:
        return {"success": True, "data": {"type": content_type, "values": values}}
    return {"success": False, "error": "Failed to upload file to S3"}


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
            return {"success": False, "error": "Assistant not found"}

        item = response["Item"]
        if item["user"] != current_user:
            return {"success": False, "error": "Not authorized to access this assistant"}

        session_id = get(item, "data", "sessionId")
        if session_id:
            return {"success": True, "record_id": record_id, "session_id": session_id}
        return {"success": False, "error": "Assistant has no active session"}

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


def create_new_assistant(user_id, file_keys, account_id="", request_id=""):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])
    timestamp = int(time.time() * 1000)

    for file_key in file_keys:
        file_key_user = file_key.split("//")[1] if "//" in file_key else file_key
        if "@" not in file_key_user or len(file_key_user) < 6 or user_id not in file_key_user:
            return {"success": False, "error": "You are not authorized to access the referenced files"}

    session_info = create_agentcore_session(user_id, file_keys)
    if not session_info["success"]:
        return session_info

    record_id = f"{user_id}/ast/{str(uuid.uuid4())}"
    table.put_item(Item={
        "id": record_id,
        "user": user_id,
        "createdAt": timestamp,
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
        "message": "Assistant created successfully",
        "data": {"codeInterpreterRecordId": record_id},
    }


def delete_record_by_id(record_id, user_id):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])

    try:
        response = table.get_item(Key={"id": record_id})
    except ClientError as e:
        logger.error("ClientError: %s", e.response["Error"]["Message"])
        return {"success": False, "message": "Assistant not found"}

    if "Item" not in response:
        return {"success": False, "message": "Assistant not found"}

    item = response["Item"]
    if item["user"] != user_id:
        return {"success": False, "message": "Not authorized to delete this assistant"}

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
        return {"success": False, "message": "Failed to delete assistant record from database"}

    return {"success": True, "message": "Assistant deleted successfully"}
