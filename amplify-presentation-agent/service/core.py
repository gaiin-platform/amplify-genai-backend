"""Presentation agent API: starts AgentCore Runtime jobs and reports their status.

The heavy lifting runs in the AgentCore Runtime container (../agent). These
handlers authenticate the user, authorize the template, create a job record,
invoke the runtime asynchronously, and serve status with presigned download
URLs once the deck is ready.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote

import boto3
from botocore.config import Config

from pycommon.api.amplify_groups import verify_user_in_amp_group
from pycommon.api.auth_admin import verify_user_as_admin
from pycommon.authz import setup_validated, validated
from pycommon.dal.providers.aws.resource_perms import DynamoDBOperation, S3Operation
from pycommon.decorators import required_env_vars
from pycommon.logger import getLogger
from schemata.permissions import get_permission_checker
from schemata.schema_validation_rules import rules

logger = getLogger("presentation_agent")
setup_validated(rules, get_permission_checker)

PPTX_TEMPLATES_CONFIG = "powerPointTemplates"
PRESENTATION_AGENT_CONFIG = "presentationAgent"
DEFAULT_AGENT_CONFIG = {"modelId": "us.anthropic.claude-opus-5", "visionModelId": "", "imageModelId": "", "maxReviewPasses": 2}
JOB_TTL_DAYS = 7
PRESIGNED_URL_SECONDS = 8 * 3600
MAX_CONTENT_CHARS = 400_000
INDUCTION_JOB_PREFIX = "template-analysis:"

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3", config=Config(signature_version="s3v4"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jobs_table():
    return dynamodb.Table(os.environ["PRESENTATION_JOBS_TABLE"])


def _admin_config(config_id: str):
    item = dynamodb.Table(os.environ["AMPLIFY_ADMIN_DYNAMODB_TABLE"]).get_item(Key={"config_id": config_id}).get("Item")
    return item.get("data") if item else None


def _agent_config() -> dict:
    config = dict(DEFAULT_AGENT_CONFIG)
    config.update({k: v for k, v in (_admin_config(PRESENTATION_AGENT_CONFIG) or {}).items() if v not in (None, "")})
    return config


def _invoke_runtime(job_id: str, payload: dict) -> None:
    runtime_arn = os.environ.get("PRESENTATION_AGENT_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("The presentation agent runtime is not deployed")
    client = boto3.client("bedrock-agentcore", config=Config(read_timeout=60, retries={"max_attempts": 3}))
    # One runtime session per job keeps jobs isolated in their own microVM.
    # AgentCore requires session ids of at least 33 characters.
    session_id = f"presentation-job-{job_id}"
    if len(session_id) < 33:
        session_id = f"{session_id}-{uuid.uuid4().hex}"
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode(),
        contentType="application/json",
        accept="application/json",
    )
    body = response["response"].read()
    result = json.loads(body) if body else {}
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "The presentation agent rejected the job")


def _can_use_template(template_name: str, access_token: str) -> bool:
    templates = _admin_config(PPTX_TEMPLATES_CONFIG) or []
    template = next((t for t in templates if t.get("name") == template_name), None)
    if template is None:
        return False
    if template.get("isAvailable", False):
        return True
    groups = template.get("amplifyGroups", [])
    return bool(groups) and verify_user_in_amp_group(access_token, groups)


def _presign(key: str, filename: str = None) -> str:
    params = {"Bucket": os.environ["S3_CONSOLIDATION_BUCKET_NAME"], "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return s3.generate_presigned_url("get_object", Params=params, ExpiresIn=PRESIGNED_URL_SECONDS)


def _public_job(item: dict) -> dict:
    job = {
        "jobId": item["jobId"],
        "status": item.get("status"),
        "stage": item.get("stage"),
        "progress": int(item.get("progress", 0)),
        "message": item.get("message"),
        "templateName": item.get("templateName"),
        "title": item.get("title"),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
    }
    if item.get("status") == "failed":
        job["error"] = item.get("error")
    result = item.get("result")
    if item.get("status") == "completed" and result and result.get("pptxKey"):
        filename = result["pptxKey"].rsplit("/", 1)[-1]
        job["result"] = {
            "downloadUrl": _presign(result["pptxKey"], filename),
            "fileName": filename,
            "slideCount": int(result.get("slideCount", 0)),
            "slides": [_presign(k) for k in result.get("slideKeys", [])],
            "reviewScores": [int(s) for s in result.get("reviewScores", [])],
        }
    return job


@required_env_vars({
    "PRESENTATION_JOBS_TABLE": [DynamoDBOperation.PUT_ITEM, DynamoDBOperation.UPDATE_ITEM],
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("start")
def start_presentation(event, context, current_user, name, data):
    token_account = data.get("account") if isinstance(data.get("account"), str) else ""
    return create_presentation_job(unquote(current_user), data["data"], data["access_token"], token_account)


def create_presentation_job(user: str, body: dict, access_token: str, token_account: str = "") -> dict:
    template_name = body["templateName"]
    content = body["content"]
    if len(content) > MAX_CONTENT_CHARS:
        return {"success": False, "message": "The conversation is too long to turn into a presentation."}
    if not _can_use_template(template_name, access_token):
        return {"success": False, "message": f"You do not have access to the template '{template_name}'."}

    config = _agent_config()
    job_id = str(uuid.uuid4())
    now = _now()
    _jobs_table().put_item(
        Item={
            "jobId": job_id,
            "user": user,
            "createdAt": now,
            "updatedAt": now,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "Starting presentation agent",
            "templateName": template_name,
            "title": body.get("title") or "",
            "conversationId": body.get("conversationId") or "",
            "ttl": int(time.time()) + JOB_TTL_DAYS * 86400,
        }
    )
    payload = {
        "action": "generate",
        "jobId": job_id,
        "user": user,
        # Same precedence as the code interpreter: the caller-selected account, else the token's.
        "accountId": body.get("accountId") or token_account or "general_account",
        "templateName": template_name,
        "content": content,
        "title": body.get("title"),
        "instructions": body.get("instructions"),
        "modelId": config.get("modelId"),
        "visionModelId": config.get("visionModelId"),
        "imageModelId": config.get("imageModelId"),
        "maxReviewPasses": int(config.get("maxReviewPasses", 2)),
    }
    try:
        _invoke_runtime(job_id, payload)
    except Exception as e:
        logger.error("Failed to start presentation job %s: %s", job_id, e)
        _jobs_table().update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, stage = :s, message = :m, #e = :e, updatedAt = :u",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": "failed", ":m": "Could not start the presentation agent", ":e": str(e)[:500], ":u": _now()},
        )
        return {"success": False, "message": "Could not start the presentation agent. Please try again later."}

    logger.info("Started presentation job %s for %s", job_id, user)
    return {"success": True, "data": {"jobId": job_id, "status": "queued"}}


@required_env_vars({
    "PRESENTATION_JOBS_TABLE": [DynamoDBOperation.GET_ITEM],
    "S3_CONSOLIDATION_BUCKET_NAME": [S3Operation.GET_OBJECT],
})
@validated("status")
def get_presentation_status(event, context, current_user, name, data):
    job_id = (event.get("queryStringParameters") or {}).get("jobId")
    return presentation_status(unquote(current_user), job_id)


def presentation_status(user: str, job_id: str) -> dict:
    if not job_id:
        return {"success": False, "message": "jobId is required"}
    item = _jobs_table().get_item(Key={"jobId": job_id}).get("Item")
    # Report other users' jobs as missing rather than forbidden.
    if not item or item.get("user") != user:
        return {"success": False, "message": "Presentation job not found"}
    return {"success": True, "data": _public_job(item)}


def _start_induction(template_name: str) -> str:
    job_id = INDUCTION_JOB_PREFIX + template_name
    now = _now()
    _jobs_table().put_item(
        Item={
            "jobId": job_id,
            "user": "system",
            "createdAt": now,
            "updatedAt": now,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "Queued for layout analysis",
            "templateName": template_name,
        }
    )
    config = _agent_config()
    # A fresh id per run: re-analysis must not reuse a finished runtime session.
    _invoke_runtime(
        str(uuid.uuid4()),
        {"action": "induct", "jobId": job_id, "templateName": template_name, "modelId": config.get("visionModelId") or config.get("modelId")},
    )
    return job_id


@required_env_vars({
    "PRESENTATION_JOBS_TABLE": [DynamoDBOperation.PUT_ITEM],
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("analyze")
def analyze_template(event, context, current_user, name, data):
    if not verify_user_as_admin(data["access_token"], "Analyze PowerPoint template"):
        return {"success": False, "message": "User is not an authorized admin"}
    template_name = data["data"]["templateName"]
    try:
        job_id = _start_induction(template_name)
    except Exception as e:
        logger.error("Template analysis failed to start for %s: %s", template_name, e)
        return {"success": False, "message": f"Could not start template analysis: {e}"}
    return {"success": True, "data": {"jobId": job_id}}


@required_env_vars({
    "PRESENTATION_JOBS_TABLE": [DynamoDBOperation.GET_ITEM],
    "AMPLIFY_ADMIN_DYNAMODB_TABLE": [DynamoDBOperation.GET_ITEM],
})
@validated("read")
def get_template_analysis_status(event, context, current_user, name, data):
    if not verify_user_as_admin(data["access_token"], "View PowerPoint template analysis"):
        return {"success": False, "message": "User is not an authorized admin"}
    names = [t.get("name") for t in (_admin_config(PPTX_TEMPLATES_CONFIG) or []) if t.get("name")]
    if not names:
        return {"success": True, "data": {}}
    keys = [{"jobId": INDUCTION_JOB_PREFIX + n} for n in names]
    table = os.environ["PRESENTATION_JOBS_TABLE"]
    statuses = {}
    for start in range(0, len(keys), 100):
        response = dynamodb.batch_get_item(RequestItems={table: {"Keys": keys[start : start + 100]}})
        for item in response.get("Responses", {}).get(table, []):
            statuses[item["templateName"]] = {
                "status": item.get("status"),
                "message": item.get("message"),
                "updatedAt": item.get("updatedAt"),
            }
    return {"success": True, "data": {n: statuses.get(n, {"status": "not_analyzed"}) for n in names}}


def analyze_uploaded_template(event, context):
    """Invoked asynchronously by amplify-lambda's handle_pptx_upload after a template upload."""
    template_name = event.get("templateName")
    if not template_name:
        return {"success": False, "message": "templateName is required"}
    try:
        job_id = _start_induction(template_name)
        logger.info("Started layout analysis %s", job_id)
        return {"success": True, "data": {"jobId": job_id}}
    except Exception as e:
        logger.error("Template analysis failed to start for %s: %s", template_name, e)
        return {"success": False, "message": str(e)}
