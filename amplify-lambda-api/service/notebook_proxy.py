"""notebook_proxy.py

Lambda handlers that proxy requests from the Amplify frontend to the Open
Notebook service running inside the ai-pod VPC. Three endpoints:

  POST /notebook/proxy       — JSON requests/responses
  POST /notebook/proxy/raw   — Binary responses (e.g. podcast audio), returned
                               as base64-encoded data in a JSON envelope
  POST /notebook/upload      — Multipart file uploads forwarded to /api/sources

The Lambda is VPC-attached to vpc-0da53fc9ca1356120 so it can reach the Open
Notebook EC2 instance on its private IP. The Cognito access token is forwarded
as-is; Open Notebook's JWTAuthMiddleware validates it.

Environment variables required:
  OPEN_NOTEBOOK_INTERNAL_URL  — e.g. http://<private-ip>  (no trailing slash)
"""

import base64
import os

import httpx

from pycommon.authz import validated
from pycommon.decorators import required_env_vars
from pycommon.logger import getLogger

logger = getLogger("notebook_proxy")

OPEN_NOTEBOOK_INTERNAL_URL = os.environ.get("OPEN_NOTEBOOK_INTERNAL_URL", "")

# httpx client — reused across warm invocations
_client = httpx.Client(timeout=28.0)


def _notebook_url(path: str) -> str:
    """Build the full internal URL for an Open Notebook API path."""
    normalised = path if path.startswith("/") else f"/{path}"
    return f"{OPEN_NOTEBOOK_INTERNAL_URL}/api{normalised}"


def _forward_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# POST /notebook/proxy  — JSON proxy
# ---------------------------------------------------------------------------

@required_env_vars({"OPEN_NOTEBOOK_INTERNAL_URL": []})
@validated("proxy")
def notebook_proxy(event, context, current_user, name, data):
    """Forward a JSON request to Open Notebook and return the JSON response."""
    payload = data.get("data", {})
    method = (payload.get("method") or "GET").upper()
    path = payload.get("path", "")
    query_params = payload.get("query_params") or {}
    body = payload.get("body")
    access_token = data["access_token"]

    if not path:
        return {"success": False, "message": "path is required"}

    url = _notebook_url(path)
    logger.info("notebook_proxy: %s %s user=%s", method, url, current_user)

    try:
        resp = _client.request(
            method=method,
            url=url,
            params=query_params or None,
            json=body if body is not None else None,
            headers=_forward_headers(access_token),
        )
        resp.raise_for_status()
        return {"success": True, "data": resp.json()}
    except httpx.HTTPStatusError as e:
        logger.error("notebook_proxy upstream error: %s %s", e.response.status_code, e.response.text[:500])
        return {"success": False, "message": f"Upstream error {e.response.status_code}", "data": None}
    except Exception as e:
        logger.exception("notebook_proxy unexpected error")
        return {"success": False, "message": str(e), "data": None}


# ---------------------------------------------------------------------------
# POST /notebook/proxy/raw  — Binary proxy (audio blobs)
# ---------------------------------------------------------------------------

@required_env_vars({"OPEN_NOTEBOOK_INTERNAL_URL": []})
@validated("proxy")
def notebook_proxy_raw(event, context, current_user, name, data):
    """Forward a request to Open Notebook and return binary content as base64.

    Response shape:
      { "success": true, "data": { "content_type": "audio/mpeg", "data_b64": "<base64>" } }
    """
    payload = data.get("data", {})
    method = (payload.get("method") or "GET").upper()
    path = payload.get("path", "")
    query_params = payload.get("query_params") or {}
    body = payload.get("body")
    access_token = data["access_token"]

    if not path:
        return {"success": False, "message": "path is required"}

    url = _notebook_url(path)
    logger.info("notebook_proxy_raw: %s %s user=%s", method, url, current_user)

    try:
        resp = _client.request(
            method=method,
            url=url,
            params=query_params or None,
            json=body if body is not None else None,
            headers=_forward_headers(access_token),
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "application/octet-stream")
        data_b64 = base64.b64encode(resp.content).decode("utf-8")
        return {
            "success": True,
            "data": {
                "content_type": content_type,
                "data_b64": data_b64,
            },
        }
    except httpx.HTTPStatusError as e:
        logger.error("notebook_proxy_raw upstream error: %s", e.response.status_code)
        return {"success": False, "message": f"Upstream error {e.response.status_code}", "data": None}
    except Exception as e:
        logger.exception("notebook_proxy_raw unexpected error")
        return {"success": False, "message": str(e), "data": None}


# ---------------------------------------------------------------------------
# POST /notebook/upload  — Multipart upload proxy
# ---------------------------------------------------------------------------

@required_env_vars({"OPEN_NOTEBOOK_INTERNAL_URL": []})
@validated("upload")
def notebook_upload(event, context, current_user, name, data):
    """Forward a multipart file upload to Open Notebook /api/sources.

    The frontend sends the raw multipart body base64-encoded in data.body_b64,
    with the Content-Type (including boundary) in data.content_type.

    Response shape mirrors the Open Notebook /api/sources response.
    """
    payload = data.get("data", {})
    body_b64 = payload.get("body_b64", "")
    content_type = payload.get("content_type", "")
    access_token = data["access_token"]

    if not body_b64 or not content_type:
        return {"success": False, "message": "body_b64 and content_type are required"}

    try:
        raw_body = base64.b64decode(body_b64)
    except Exception:
        return {"success": False, "message": "Invalid base64 in body_b64"}

    url = _notebook_url("/sources")
    logger.info("notebook_upload: POST %s user=%s content_type=%s bytes=%d",
                url, current_user, content_type, len(raw_body))

    try:
        resp = _client.post(
            url=url,
            content=raw_body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": content_type,
            },
        )
        resp.raise_for_status()
        return {"success": True, "data": resp.json()}
    except httpx.HTTPStatusError as e:
        logger.error("notebook_upload upstream error: %s %s", e.response.status_code, e.response.text[:500])
        return {"success": False, "message": f"Upstream error {e.response.status_code}", "data": None}
    except Exception as e:
        logger.exception("notebook_upload unexpected error")
        return {"success": False, "message": str(e), "data": None}
