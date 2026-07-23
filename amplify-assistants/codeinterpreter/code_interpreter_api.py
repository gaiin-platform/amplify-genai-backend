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


agentcore_client = boto3.client("bedrock-agentcore")
CODE_INTERPRETER_ID = os.environ.get("AGENTCORE_CODE_INTERPRETER_ID", "")
SESSION_TIMEOUT_SECONDS = int(os.environ.get("AGENTCORE_SESSION_TIMEOUT_SECONDS", "3600"))
EXECUTION_TIMEOUT_SECONDS = int(os.environ.get("AGENTCORE_EXECUTION_TIMEOUT_SECONDS", "240"))
AGENTCORE_MODEL_ID = "agentcore-code-interpreter"

# Rough AgentCore Code Interpreter compute pricing used to estimate a per-execution
# charge from the executionTime (seconds) AgentCore reports for each executeCode call.
# These are approximations of AWS's consumption-based rate (vCPU-hour + GB-hour,
# billed per-second) — not exact, since AgentCore doesn't expose actual vCPU/memory
# consumed per execution. Used only when we can't get a better signal.
AGENTCORE_EXECUTION_VCPU_HOUR_RATE = 0.0895
AGENTCORE_EXECUTION_MEM_GB_HOUR_RATE = 0.00945
AGENTCORE_EXECUTION_DEFAULT_MEM_GB = 1
# Fallback flat estimate (USD) recorded when AgentCore does not report executionTime.
AGENTCORE_EXECUTION_FLAT_COST_ESTIMATE = 0.01


def _estimate_execution_cost(execution_time_seconds):
    """Estimate the USD cost of a single AgentCore executeCode call.

    Uses the reported executionTime (seconds) against an approximate combined
    vCPU-hour/GB-hour rate when available, otherwise falls back to a flat estimate.
    """
    if execution_time_seconds is None:
        return AGENTCORE_EXECUTION_FLAT_COST_ESTIMATE
    hourly_rate = (
        AGENTCORE_EXECUTION_VCPU_HOUR_RATE
        + (AGENTCORE_EXECUTION_MEM_GB_HOUR_RATE * AGENTCORE_EXECUTION_DEFAULT_MEM_GB)
    )
    per_second_rate = hourly_rate / 3600.0
    return execution_time_seconds * per_second_rate

# Output file extensions surfaced to the user, detected via listFiles diffing.
# Deliberately excludes executable/script types (.sh, .exe, .js, .bat, .ps1) and .zip
# (unbounded/nested content, zip-bomb risk) — there is no file-size cap on uploads here,
# so we only allow bounded, user-consumable output types.
_WATCHED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".pdf", ".csv", ".xlsx", ".py",
    ".txt", ".json", ".md", ".svg", ".html",
    ".docx", ".pptx", ".parquet", ".gif",
    ".yaml", ".yml", ".tsv", ".geojson",
}

_EXT_TO_MIME = {
    ".png":     "image/png",
    ".jpg":     "image/jpeg",
    ".jpeg":    "image/jpeg",
    ".pdf":     "application/pdf",
    ".csv":     "text/csv",
    ".xlsx":    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".py":      "text/x-python",
    ".txt":     "text/plain",
    ".json":    "application/json",
    ".md":      "text/markdown",
    ".svg":     "image/svg+xml",
    ".html":    "text/html",
    ".docx":    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx":    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".parquet": "application/octet-stream",
    ".gif":     "image/gif",
    ".yaml":    "application/x-yaml",
    ".yml":     "application/x-yaml",
    ".tsv":     "text/tab-separated-values",
    ".geojson": "application/geo+json",
}


def get(dictionary, *keys):
    return reduce(
        lambda d, key: d.get(key, None) if isinstance(d, dict) else None,
        keys,
        dictionary,
    )


def owns_file_key(user_id, file_key):
    """Return True if file_key's first path segment exactly matches user_id.

    File keys are written as "{current_user}/{date}/{uuid}.ext", optionally
    prefixed with a protocol like "s3://". Exact-segment matching (rather than
    a substring/"@" check) works for both email and non-email user identifiers.
    """
    if not file_key or not user_id:
        return False
    key_no_protocol = file_key.split("//", 1)[1] if "//" in file_key else file_key
    key_owner = key_no_protocol.split("/", 1)[0]
    return key_owner == user_id


def file_keys_to_s3_bytes(file_keys, file_names=None):
    """Download files from S3 and return list of (file_name, file_bytes, mime_type) tuples.

    file_names: optional dict mapping file_key -> original filename. The S3 key's
    basename is a random UUID, not the real filename, so this override is needed
    for the sandbox to receive files under names the LLM's code can reference.
    """
    if not file_keys:
        return []
    file_names = file_names or {}

    files_bucket_name = os.environ["S3_RAG_INPUT_BUCKET_NAME"]
    images_bucket_name = os.environ["S3_IMAGE_INPUT_BUCKET_NAME"]
    s3 = boto3.client("s3")

    result = []
    for file_key in file_keys:
        file_key_user = file_key.split("//")[1] if ("//" in file_key) else file_key
        if len(file_key_user) <= 6:
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
            # Prefer the caller-supplied original filename over the S3 key's UUID basename.
            file_name = (
                file_names.get(file_key)
                or file_names.get(file_key_user)
                or (file_key_user.split("/")[-1] if "/" in file_key_user else file_key_user)
            )
            result.append((file_name, file_bytes, mime_type))

    return result


def load_files_for_session(session_id, file_keys, file_names=None):
    """Upload files into an AgentCore session via the writeFiles operation."""
    logger.debug("Loading %d file(s) into AgentCore session %s", len(file_keys), session_id)
    files_data = file_keys_to_s3_bytes(file_keys, file_names=file_names)
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

        # No ResponseContentDisposition here so <img> tags can render it directly.
        file_url = get_presigned_download_url(file_key, user_id, download_filename=None)
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
    target_size_bytes = 204800
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
                raise ValueError("Cannot reduce image below 100px.")

        resized_bytes.seek(0)
        return resized_bytes.read()
    finally:
        resized_bytes.close()


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


def renew_session(record_id, current_user, messages, amplify_messages=True, all_file_keys=None, all_file_names=None, account_id="", request_id=""):
    """Create a fresh AgentCore session, reload all files, and persist the new session_id.

    all_file_keys/all_file_names, when provided, are used directly instead of
    extracting from messages, so files from smart-messages-pruned history are
    still reloaded.

    A new AgentCore session is a real, separately billed session — the same
    per-session charge recorded on initial creation is recorded here too.
    """
    logger.info("Renewing expired AgentCore session for record %s", record_id)

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])

    if all_file_keys:
        all_keys = all_file_keys
        logger.info("renew_session: using %d pre-collected file key(s)", len(all_keys))
    else:
        all_keys = extract_all_file_keys(messages, amplify_messages=amplify_messages)

    session_info = create_agentcore_session(current_user, all_keys, file_names=all_file_names)
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

    record_session_charge({
        "current_user": current_user,
        "account_id": account_id,
        "request_id": request_id,
        "record_id": record_id,
        "session_id": new_session_id,
    })

    return {"success": True, "session_id": new_session_id}


def chat_with_code_interpreter(current_user, record_id, messages, request_id, api_accessed, file_keys=None, all_conversation_file_keys=None, file_names=None, all_conversation_file_names=None, account_id=""):
    """Entry point for a chat request. Fetches the session and executes the code.

    If the session expired, a new one is created transparently: all file keys
    from the conversation are re-uploaded and execution is retried once, with
    sessionRenewed=True set on the response.

    file_keys/file_names override the keys/names extracted from the last message.
    all_conversation_file_keys/all_conversation_file_names are used by
    renew_session to restore every file ever uploaded, including from
    messages that may have been pruned by smart-messages.
    """
    logger.debug("Entered chat_with_code_interpreter")

    record_existence = check_record_exists(record_id, current_user)
    if not record_existence["success"]:
        return record_existence

    session_id = record_existence["session_id"]
    amplify_messages = not api_accessed
    last_message = extract_last_message(messages, amplify_messages=amplify_messages)

    if file_keys:
        last_message = {**last_message, "file_keys": file_keys, "file_names": file_names or {}}
        logger.info("Using %d explicit file_key(s) for this request", len(file_keys))

    active_session_id = session_id
    session_renewed = False
    result = chat(current_user, record_id, active_session_id, last_message, request_id, api_accessed=api_accessed, account_id=account_id)

    if result.get("error") == "session_expired":
        logger.warning(
            "Session %s expired for record %s — renewing and retrying",
            active_session_id, record_id,
        )
        renewed = renew_session(
            record_id, current_user, messages,
            amplify_messages=amplify_messages,
            all_file_keys=all_conversation_file_keys or None,
            all_file_names=all_conversation_file_names or None,
            account_id=account_id,
            request_id=request_id,
        )
        if not renewed["success"]:
            return {
                "success": False,
                "error": "Session expired and could not be renewed. Please create a new session.",
            }
        active_session_id = renewed["session_id"]
        session_renewed = True
        logger.info("Retrying execution on new session %s", active_session_id)
        result = chat(current_user, record_id, active_session_id, last_message, request_id, api_accessed=api_accessed, account_id=account_id)

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
    """List files in the AgentCore sandbox via the native listFiles operation.

    Returns a set of normalized file paths, or None if the listing could not
    be retrieved, so callers can distinguish "empty directory" from "listing
    failed" and skip diffing rather than produce false positives.
    """
    arguments = {"directoryPath": directory_path} if directory_path else {}

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
            if result.get("isError"):
                logger.warning("listFiles returned isError for session %s", session_id)
                continue
            for block in result.get("content", []):
                block_type = block.get("type")
                if block_type in ("resource_link", "resource"):
                    resource = block.get("resource") or {}
                    uri = block.get("uri") or resource.get("uri", "")
                    path = None
                    if uri:
                        path = uri[len("file://"):] if uri.startswith("file://") else uri
                    elif block.get("name"):
                        path = block["name"]
                    if path:
                        # readFiles rejects absolute paths ("potential path traversal
                        # detected") — file:// URIs resolve to "/name", so strip the
                        # leading slash to get the relative path readFiles expects.
                        paths.add(path.lstrip("/"))
                elif block_type == "text":
                    for line in block.get("text", "").splitlines():
                        line = line.strip()
                        if line:
                            paths.add(line)
    except Exception as e:
        logger.warning("Error parsing listFiles response for session %s: %s", session_id, e)
        return None

    return paths


def _fetch_and_upload_sandbox_file(path, session_id, current_user):
    """Read a file from the AgentCore sandbox via readFiles and upload it to S3.

    Called for each new path discovered by diffing list_sandbox_files() before
    and after code execution. Returns the same shape as upload_file_and_get_urls:
        {"success": True,  "data": {"type": mime, "values": {...}}}
        {"success": False, "error": "..."}
    """
    file_name = os.path.basename(path)
    ext = os.path.splitext(file_name)[1].lower()
    mime_type = _EXT_TO_MIME.get(ext, "binary/octet-stream")

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

    bytes_chunks: list[bytes] = []
    base64_chunks: list[str] = []
    text_chunks: list[str] = []
    try:
        for event in read_response.get("stream", []):
            if "result" not in event:
                continue
            result = event["result"]
            # TEMP DIAGNOSTIC: log the raw readFiles result/blocks to confirm the actual
            # content shape. Remove once the file-size bug is confirmed fixed.
            logger.info(
                "_fetch_and_upload_sandbox_file: raw result for %s: isError=%s structuredContent=%s content=%s",
                path, result.get("isError"), result.get("structuredContent"), result.get("content"),
            )
            if result.get("isError"):
                err_text = next(
                    (b.get("text") for b in result.get("content", []) if b.get("type") == "text"),
                    "readFiles returned an error",
                )
                logger.error("readFiles error for %s: %s", path, err_text)
                return {"success": False, "error": err_text}
            for block in result.get("content", []):
                logger.info(
                    "_fetch_and_upload_sandbox_file: block type=%s keys=%s raw=%s",
                    block.get("type"), list(block.keys()), block,
                )
                # AWS's own bedrock_agentcore SDK (download_file/download_files in
                # code_interpreter_client.py) ONLY ever reads type=="resource" blocks
                # and ONLY ever pulls bytes from resource["blob"]/resource["text"] —
                # never a top-level block["data"]/block["blob"]. Checking those
                # top-level fields first (as this code previously did) can pick up
                # an unrelated small value on the block and never reach the real
                # image bytes in resource.blob, producing a tiny/corrupt file.
                if block.get("type") == "resource":
                    resource = block.get("resource") or {}
                    raw = resource.get("blob")
                    if raw is not None:
                        if isinstance(raw, bytes):
                            bytes_chunks.append(raw)
                        else:
                            base64_chunks.append(raw)
                        continue
                    text = resource.get("text")
                    if text:
                        text_chunks.append(text)
                        continue

                # Defensive fallback for other block shapes (resource_link, or a
                # top-level data/blob/text field) in case AgentCore's response
                # doesn't match the resource-block shape AWS's SDK expects.
                raw = (
                    block.get("data")
                    or block.get("blob")
                )
                if raw:
                    if isinstance(raw, bytes):
                        bytes_chunks.append(raw)
                    else:
                        base64_chunks.append(raw)
                    continue
                text = block.get("text")
                if text:
                    text_chunks.append(text)
    except Exception as e:
        logger.error("Error reading readFiles stream for %s: %s", path, e)
        return {"success": False, "error": f"readFiles stream error: {e}"}

    file_bytes = b"".join(bytes_chunks)
    if base64_chunks:
        try:
            file_bytes += base64.b64decode("".join(base64_chunks))
        except Exception as e:
            logger.error("Error decoding base64 content for %s: %s", path, e)
            return {"success": False, "error": f"base64 decode error: {e}"}

    if not file_bytes and text_chunks:
        file_bytes = "".join(text_chunks).encode("utf-8")

    if not file_bytes:
        logger.warning("readFiles returned no bytes for %s", path)
        return {"success": False, "error": f"readFiles returned empty content for {path}"}

    s3_file_key = f"{current_user}/{uuid.uuid4()}-FN-{file_name}"
    logger.info("Uploading sandbox file to S3: key=%s size=%d", s3_file_key, len(file_bytes))
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


def chat(current_user, record_id, session_id, last_message, request_id, api_accessed=False, account_id=""):
    """Execute code via AgentCore and return structured results.

    AgentCore does not maintain conversation history — session_id only keeps
    the Python execution environment (variables, loaded files) alive across
    calls. Prompt/response history is owned by Amplify.

    Output files are detected by calling listFiles before and after
    executeCode and diffing the two listings, rather than injecting tracking
    code into the user's script — this also means files are still detected
    even if the user's code raises an exception partway through.
    """
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
    file_names = last_message.get("file_names", {})
    if file_keys:
        unauthorized = [k for k in file_keys if not owns_file_key(current_user, k)]
        if unauthorized:
            logger.warning("chat: authorization FAILED for %r against %r", current_user, unauthorized)
            return {"success": False, "error": "You are not authorized to access the referenced files"}
        load_files_for_session(session_id, file_keys, file_names=file_names)

    files_before = list_sandbox_files(session_id)
    code = user_code

    task_id = None
    text_content = ""
    output_files = []
    cancelled = False
    timed_out = False
    execution_time_seconds = None
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
            # Drain the rest of the stream (don't break early) once timed out
            # or cancelled, so we still capture the taskId to send stopTask.
            if not timed_out and time.monotonic() > deadline:
                logger.warning("Execution timeout exceeded on session %s", session_id)
                timed_out = True

            if not api_accessed and not cancelled and request_killed and request_id:
                try:
                    if request_killed(current_user, request_id):
                        logger.info("Request %s cancelled during stream — draining", request_id)
                        cancelled = True
                except Exception:
                    pass

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
            # Capture AgentCore-reported execution time (seconds) for cost tracking.
            execution_time_seconds = structured.get("executionTime", execution_time_seconds)

            # Skip collecting output once we have timed out or been cancelled —
            # we only continue iterating to drain the stream and get the taskId.
            if timed_out or cancelled:
                continue

            for block in result.get("content", []):
                if block.get("type") == "text":
                    text_content += block.get("text", "") + "\n"

            if result.get("isError"):
                stderr = structured.get("stderr", "")
                logger.error("Code execution error in session %s: %s", session_id, stderr)
                execution_error = stderr

    except Exception as e:
        logger.error("Exception while consuming AgentCore event stream: %s", e)
        return {"success": False, "error": f"Stream processing error: {e}"}

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
        logger.warning("Could not determine sandbox file listing for session %s", session_id)

    for path in discovered_paths:
        file_result = _fetch_and_upload_sandbox_file(path, session_id, current_user)
        if file_result.get("success"):
            output_files.append(file_result["data"])
            logger.info("Sandbox file uploaded: %s -> %s", path, file_result["data"].get("type"))
        else:
            logger.warning("Failed to fetch sandbox file %s: %s", path, file_result.get("error"))

    record_execution_charge({
        "current_user": current_user,
        "account_id": account_id,
        "request_id": request_id,
        "record_id": record_id,
        "session_id": session_id,
        "execution_time_seconds": execution_time_seconds,
    })

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


def record_execution_charge(info):
    """Record a per-execution charge for a single AgentCore executeCode call.

    Uses the executionTime (seconds) AgentCore reports in structuredContent when
    available to estimate the cost; falls back to a flat estimate otherwise.
    """
    from pycommon.api.accounting import record_additional_charge
    from datetime import datetime, timezone

    execution_time_seconds = info.get("execution_time_seconds")
    cost = _estimate_execution_cost(execution_time_seconds)
    logger.debug(
        "Recording execution charge: $%.6f (execution_time_seconds=%s)",
        cost, execution_time_seconds,
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    try:
        record_additional_charge(
            account={"user": info["current_user"], "account_id": info.get("account_id", "")},
            model_id=AGENTCORE_MODEL_ID,
            token_count=0,
            item_type="agentCoreCodeInterpreterExecution",
            request_id=info.get("request_id"),
            details={
                "execution_timestamp": timestamp,
                "record_id": info.get("record_id"),
                "session_id": info.get("session_id"),
                "execution_time_seconds": execution_time_seconds,
                "estimated": execution_time_seconds is None,
            },
            ttl_days=None,
            flat_cost=cost,
        )
        logger.debug("Execution charge recorded")
    except Exception as e:
        logger.error("Failed to record execution charge: %s", e)


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


def create_agentcore_session(user_id, file_keys, file_names=None):
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
            load_files_for_session(session_id, file_keys, file_names=file_names)

        return {"success": True, "data": {"sessionId": session_id}}
    except Exception as e:
        logger.error("Failed to create AgentCore session: %s", e)
        return {"success": False, "error": f"Failed to create AgentCore session: {e}"}


def create_new_session(user_id, file_keys, account_id="", request_id="", file_names=None):
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ASSISTANT_CODE_INTERPRETER_DYNAMODB_TABLE"])
    timestamp = int(time.time() * 1000)

    for file_key in file_keys:
        if not owns_file_key(user_id, file_key):
            logger.warning(
                "create_new_session: authorization FAILED for user_id=%r against file_key=%r",
                user_id, file_key,
            )
            return {"success": False, "error": "You are not authorized to access the referenced files"}

    session_info = create_agentcore_session(user_id, file_keys, file_names=file_names)
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
        "data": {
            "codeInterpreterRecordId": record_id,
        },
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
