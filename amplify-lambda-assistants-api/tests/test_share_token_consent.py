"""Tests for token sharing (microsoft_outlook -> microsoft_exchange) in integrations/oauth.py.

Regression for the Oct 29 2026 shared-mailbox outage.  Microsoft returns the generic OAuth error
``invalid_grant`` both when a refresh token is genuinely dead *and* when the grant is healthy but the
broader ``Mail.*.Shared`` scopes are not consented to (``AADSTS65001``).  A change in 3286fadf treated
every ``invalid_grant`` during the scope-upgrade step as "source grant revoked", which marked
microsoft_exchange as disconnected for every user lacking Shared-scope consent.

Expected behaviour (asserted below):
  * consent / scope errors during the scope upgrade  -> keep the copied token, Exchange stays connected;
  * genuinely dead grants (expired / revoked)         -> roll back the copy, Exchange is disconnected.

The error bodies are copied verbatim (minus trace ids) from the prod CloudWatch logs.
Platform imports (pycommon, schemata, ...) are stubbed so this runs standalone:

    python -m unittest tests.test_share_token_consent
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


def _stub_if_missing(name, **attrs):
    try:
        __import__(name)
    except Exception:
        _stub(name, **attrs)


_stub_if_missing("boto3")
_stub_if_missing("botocore")
_stub_if_missing("botocore.exceptions", ClientError=Exception)
_stub_if_missing("requests")
_stub_if_missing("msal", ConfidentialClientApplication=mock.MagicMock())
_stub_if_missing("google_auth_oauthlib")
_stub_if_missing("google_auth_oauthlib.flow", Flow=mock.MagicMock())
_stub("integrations.oauth_encryption",
      decrypt_oauth_data=lambda x: x, encrypt_oauth_data=lambda x: x,
      verify_oauth_encryption_parameter=lambda *a, **k: None)
_stub("pycommon")
_stub("pycommon.decorators", required_env_vars=_identity_decorator)
_stub("pycommon.dal")
_stub("pycommon.dal.providers")
_stub("pycommon.dal.providers.aws")
_stub("pycommon.dal.providers.aws.resource_perms",
      DynamoDBOperation=mock.MagicMock(), SSMOperation=mock.MagicMock())
_stub("pycommon.authz", validated=_identity_decorator, setup_validated=lambda *a, **k: None)
_stub("pycommon.api")
_stub("pycommon.api.ops", api_tool=_identity_decorator, set_permissions_by_state=lambda *a, **k: None)
_stub("pycommon.api.auth_admin", verify_user_as_admin=lambda *a, **k: True)
_stub("pycommon.api.secrets", store_secret_parameter=lambda *a, **k: None)
_stub("pycommon.api.critical_logging", log_critical_error=lambda *a, **k: None, SEVERITY_HIGH="HIGH")
_stub("pycommon.logger", getLogger=lambda *_a, **_k: mock.MagicMock())
_stub("schemata", permissions=types.SimpleNamespace(get_permission_checker=lambda *a, **k: True))
_stub("schemata.schema_validation_rules", rules={})

from integrations import oauth as m  # noqa: E402

USER = "christine.m.fogg@vanderbilt.edu"
ITEM_KEY = f"{USER}/microsoft"

# --- Verbatim token-endpoint bodies seen in prod (trace/correlation ids elided) ---
CONSENT_65001 = {
    "error": "invalid_grant",
    "error_description": "AADSTS65001: The user or administrator has not consented to use the application "
                         "with ID '3d271b3f-b370-4a85-ad2c-53c19d688ddb' named 'Amplify'. Send an interactive "
                         "authorization request for this user and resource.",
}
EXPIRED_700082 = {
    "error": "invalid_grant",
    "error_description": "AADSTS700082: The refresh token has expired due to inactivity. The token was issued "
                         "on 2025-11-14T15:52:43.2309830Z and was inactive for 90.00:00:00.",
}
REVOKED_50173 = {
    "error": "invalid_grant",
    "error_description": "AADSTS50173: The provided grant has expired due to it being revoked, a fresh auth "
                         "token is needed.",
}
# Same digits prefix as 65001 but a different code: must NOT be treated as a consent error.
LOOKALIKE_650011 = {
    "error": "invalid_grant",
    "error_description": "AADSTS650011: some other problem entirely.",
}


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _Table:
    """Minimal DynamoDB table: stores the last item written per key."""

    def __init__(self, item):
        self.items = {ITEM_KEY: item}
        self.writes = 0

    def get_item(self, Key):
        item = self.items.get(Key["user_integration"])
        return {"Item": json.loads(json.dumps(item))} if item else {}

    def put_item(self, Item):
        self.writes += 1
        self.items[Item["user_integration"]] = json.loads(json.dumps(Item))


def _record(outlook_expires=0, exchange_expires=0):
    # decrypt/encrypt are identity in this test, so "encrypted" creds are plain dicts.
    return {
        "user_integration": ITEM_KEY,
        "integrations": {
            "microsoft_outlook": {"token": "OUTLOOK", "refresh_token": "RT", "expires_at": outlook_expires},
            "microsoft_exchange": {"token": "OLD-EXCHANGE", "refresh_token": "RT-OLD", "expires_at": exchange_expires},
        },
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.table = _Table(_record())
        patches = [
            mock.patch.object(m, "get_oauth_user_table", lambda: self.table),
            mock.patch.object(m, "decrypt_oauth_data", lambda x: x),
            mock.patch.object(m, "encrypt_oauth_data", lambda x: x),
            mock.patch.object(
                m, "get_oauth_client_credentials",
                lambda integration: ("cid", "secret", "tenant", "https://login.example/token"),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def exchange_creds(self):
        return self.table.items[ITEM_KEY]["integrations"].get("microsoft_exchange")


class ShareTokenScopeUpgradeTests(_Base):
    def _share(self, status, body):
        with mock.patch.object(m.requests, "post", return_value=_Resp(status, body)):
            return m._share_token(USER, "microsoft_exchange", "microsoft_outlook")

    def test_consent_error_keeps_copied_token(self):
        """AADSTS65001 on the Shared-scope upgrade is NOT a dead source: Exchange stays connected."""
        result = self._share(400, CONSENT_65001)
        self.assertIsNotNone(result)
        self.assertTrue(result["body"]["token_shared"])
        # the copied Outlook token is what remains in the exchange slot
        self.assertEqual(self.exchange_creds()["token"], "OUTLOOK")

    def test_expired_refresh_token_is_fatal_and_rolls_back(self):
        result = self._share(400, EXPIRED_700082)
        self.assertIsNone(result)
        self.assertEqual(self.exchange_creds()["token"], "OLD-EXCHANGE")  # copy undone

    def test_revoked_grant_is_fatal_and_rolls_back(self):
        result = self._share(400, REVOKED_50173)
        self.assertIsNone(result)
        self.assertEqual(self.exchange_creds()["token"], "OLD-EXCHANGE")

    def test_lookalike_code_is_not_mistaken_for_consent(self):
        result = self._share(400, LOOKALIKE_650011)
        self.assertIsNone(result)

    def test_missing_previous_exchange_creds_are_removed_on_fatal(self):
        del self.table.items[ITEM_KEY]["integrations"]["microsoft_exchange"]
        self.assertIsNone(self._share(400, EXPIRED_700082))
        self.assertIsNone(self.exchange_creds())

    def test_non_json_error_body_falls_back_to_copied_token(self):
        result = self._share(502, None)
        self.assertIsNotNone(result)
        self.assertEqual(self.exchange_creds()["token"], "OUTLOOK")


class ScopeConsentClassifierTests(unittest.TestCase):
    def test_classification(self):
        cases = [
            (CONSENT_65001, True),
            ({"error": "consent_required"}, True),
            ({"error": "invalid_scope"}, True),
            ({"error": "invalid_grant", "error_description": "AADSTS70011: scope invalid"}, True),
            ({"error": "invalid_grant", "error_description": "AADSTS90094: admin consent"}, True),
            (EXPIRED_700082, False),
            (REVOKED_50173, False),
            (LOOKALIKE_650011, False),
            ({"error": "invalid_grant"}, False),
        ]
        for body, expected in cases:
            self.assertEqual(m._is_scope_consent_failure(_Resp(400, body)), expected, body)
        self.assertFalse(m._is_scope_consent_failure(_Resp(500, None)))


class ListUserIntegrationsEndToEndTests(_Base):
    """Drives list_user_integrations the way the prod request on Oct 29 20:59 UTC did."""

    SUPPORTED = {"microsoft": [{"id": "microsoft_outlook"}, {"id": "microsoft_exchange"}]}

    def _run(self, upgrade_body):
        def fake_refresh(current_user, integration, credentials):
            if integration == "microsoft_outlook":  # healthy source: refresh succeeds (as in prod logs)
                return {"success": True}
            # exchange's own refresh with the Shared scopes -> consent error, like prod
            return {"success": False, "reauth_required": True, "error_code": "invalid_grant"}

        disconnected = {}
        with mock.patch.object(m, "refresh_credentials", fake_refresh), \
                mock.patch.object(m.requests, "post", return_value=_Resp(400, upgrade_body)):
            connected = m.list_user_integrations(self.SUPPORTED, USER, disconnected=disconnected)
        return connected, disconnected

    def test_consent_gap_keeps_exchange_connected(self):
        connected, disconnected = self._run(CONSENT_65001)
        self.assertIn("microsoft_exchange", connected)
        self.assertIn("microsoft_outlook", connected)
        self.assertEqual(disconnected, {})

    def test_dead_source_marks_exchange_disconnected(self):
        connected, disconnected = self._run(EXPIRED_700082)
        self.assertNotIn("microsoft_exchange", connected)
        self.assertIn("microsoft_exchange", disconnected)


if __name__ == "__main__":
    unittest.main()
