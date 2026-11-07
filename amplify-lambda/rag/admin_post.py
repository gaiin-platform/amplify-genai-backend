"""Call another Amplify service's admin-only endpoint with the admin's own token (never a stored key)."""

import json
import os
from typing import Any, Dict, Mapping, Optional


def post_as_admin(path: str, body: Dict[str, Any], access_token: str, base_url: Optional[str] = None, session: Any = None) -> Mapping[str, Any]:
    """POST ``{"data": body}`` to ``API_BASE_URL + path``. Raises with the server's message when it refuses."""
    import requests

    http = session or requests
    response = http.post(
        (base_url or os.environ["API_BASE_URL"]) + path,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"},
        data=json.dumps({"data": body}), timeout=25)
    try:
        content = response.json()
    except ValueError:
        content = {}
    if response.status_code != 200 or not content.get("success"):
        raise RuntimeError(content.get("message") or content.get("error") or f"the service answered {response.status_code}")
    return content
