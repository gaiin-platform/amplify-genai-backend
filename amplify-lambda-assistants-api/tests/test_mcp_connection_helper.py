"""Tests for `_test_mcp_connection_by_url` in integrations/mcp_servers.py.

Covers two behaviours of the shared handshake helper (used by the test / refresh / OAuth-retest paths):

  * stateful servers: a server that issues an ``Mcp-Session-Id`` at ``initialize`` rejects later calls
    that omit it, so the id must be forwarded to the ``initialized`` notification and ``tools/list``;
  * a slow response body surfaces from ``requests`` as a ConnectionError, which must be reported as a
    timeout rather than "could not connect".

Platform imports (pycommon, schemata, boto3) are stubbed, so this runs standalone:

    python -m unittest tests.test_mcp_connection_helper
"""

import json
import os
import sys
import types
import unittest
from unittest import mock

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)


def _identity_decorator(*_a, **_k):
    return lambda fn: fn


def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules.setdefault(name, module)
    return module


_stub("boto3")
_stub("pycommon")
_stub("pycommon.decorators", required_env_vars=_identity_decorator)
_stub("pycommon.dal")
_stub("pycommon.dal.providers")
_stub("pycommon.dal.providers.aws")
_stub("pycommon.dal.providers.aws.resource_perms", DynamoDBOperation=mock.MagicMock())
_stub("pycommon.authz", validated=_identity_decorator, setup_validated=lambda *a, **k: None)
_stub("pycommon.api")
_stub("pycommon.api.ops", api_tool=_identity_decorator, set_permissions_by_state=lambda *a, **k: None)
_stub("pycommon.logger", getLogger=lambda *_a, **_k: mock.MagicMock())
_stub("schemata", permissions=types.SimpleNamespace(get_permission_checker=lambda *a, **k: True))
_stub("schemata.schema_validation_rules", rules={})

from integrations import mcp_servers as m  # noqa: E402


class _Resp:
    """Just enough of requests.Response for the helper."""

    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _server(*, issues_session, requires_session):
    """A fake MCP server. Records the headers of every request it receives."""
    seen = []

    def post(url, json=None, headers=None, **_kw):  # noqa: A002 - mirrors requests.post
        method = json.get("method")
        seen.append((method, dict(headers or {})))
        if method == "initialize":
            extra = {"Mcp-Session-Id": "sess-123"} if issues_session else {}
            return _Resp(200, {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "s"}}}, extra)
        if method == "notifications/initialized":
            return _Resp(202)
        if method == "tools/list":
            if requires_session and (headers or {}).get("Mcp-Session-Id") != "sess-123":
                return _Resp(400, {"error": {"code": -32000, "message": "Bad Request: Mcp-Session-Id header is required"}})
            return _Resp(200, {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "a"}, {"name": "b"}]}})
        raise AssertionError(f"unexpected method {method}")

    return post, seen


class SessionId(unittest.TestCase):
    def _run(self, post):
        with mock.patch("requests.post", side_effect=post), mock.patch.object(m, "_validate_url_for_ssrf", return_value=None):
            return m._test_mcp_connection_by_url("https://example.com/mcp", {"Authorization": "Bearer t"})

    def test_forwards_the_session_id_to_the_notification_and_tools_list(self):
        post, seen = _server(issues_session=True, requires_session=True)
        result = self._run(post)
        self.assertTrue(result["success"])
        self.assertEqual([t["name"] for t in result["data"]["tools"]], ["a", "b"])
        by_method = dict(seen)
        self.assertNotIn("Mcp-Session-Id", by_method["initialize"])
        self.assertEqual(by_method["notifications/initialized"]["Mcp-Session-Id"], "sess-123")
        self.assertEqual(by_method["tools/list"]["Mcp-Session-Id"], "sess-123")
        # The caller's Authorization header still rides along.
        self.assertEqual(by_method["tools/list"]["Authorization"], "Bearer t")

    def test_stateless_servers_never_get_a_session_header(self):
        post, seen = _server(issues_session=False, requires_session=False)
        self.assertTrue(self._run(post)["success"])
        self.assertTrue(all("Mcp-Session-Id" not in headers for _method, headers in seen))

    def test_a_server_that_issues_but_does_not_need_a_session_still_works(self):
        post, _seen = _server(issues_session=True, requires_session=False)
        self.assertTrue(self._run(post)["success"])

    def test_a_failed_tools_list_is_still_tolerated_as_no_tools(self):
        # Unchanged legacy behaviour for the refresh paths: connected, zero tools.
        post, _seen = _server(issues_session=False, requires_session=True)  # tools/list -> 400
        result = self._run(post)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["tools"], [])


class Timeouts(unittest.TestCase):
    def _run(self, exc):
        def post(*_a, **_k):
            raise exc

        with mock.patch("requests.post", side_effect=post), mock.patch.object(m, "_validate_url_for_ssrf", return_value=None):
            return m._test_mcp_connection_by_url("https://example.com/mcp", {})

    def test_a_slow_response_body_is_a_timeout_not_unreachable(self):
        import requests

        slow = requests.exceptions.ConnectionError("HTTPSConnectionPool(host='x', port=443): Read timed out.")
        self.assertEqual(self._run(slow)["error"], "Connection timed out")

    def test_a_refused_connection_is_still_reported_as_unreachable(self):
        import requests

        refused = requests.exceptions.ConnectionError("Connection refused")
        self.assertTrue(self._run(refused)["error"].startswith("Connection failed"))


if __name__ == "__main__":
    unittest.main()
