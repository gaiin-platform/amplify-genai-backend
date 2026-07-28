"""
Regression tests for notebook_proxy.py authorization logic.

Covers the SSRF finding: "Unvalidated Path Allows Access to Internal API
Endpoints" (pen-test report, 2026-07-23, risk score 9.9).

Specifically tests:
  - All originally-reported sensitive endpoints are now admin-gated
  - Legitimate per-user paths remain accessible to regular users
  - Path-traversal variants are rejected before auth evaluation
  - HTTP-method escalation on shared-config endpoints is enforced
  - Encoding/bypass techniques demonstrated in the pen-test report

These tests exercise only the pure-logic helper functions (_normalise_path,
_has_traversal, _required_level, _reject_if_unauthorized) so they run
offline with no network I/O and no pycommon layer installed.  The module
is imported via a lightweight mock of its external dependencies.
"""
from __future__ import annotations

import sys
import types
import unittest
import pathlib


# ---------------------------------------------------------------------------
# Minimal mocks for external imports not present outside the Lambda env
# ---------------------------------------------------------------------------

def _install_mocks():
    """Inject stub modules for pycommon and schemata so notebook_proxy imports."""
    if "pycommon" in sys.modules:
        return  # idempotent

    pycommon = types.ModuleType("pycommon")

    authz = types.ModuleType("pycommon.authz")
    authz.validated = lambda *a, **kw: (lambda f: f)
    authz.setup_validated = lambda *a, **kw: None
    authz.add_api_access_types = lambda *a, **kw: None
    pycommon.authz = authz

    api = types.ModuleType("pycommon.api")
    auth_admin = types.ModuleType("pycommon.api.auth_admin")
    # Default: non-admin caller — tests that need admin override this per-test
    auth_admin.verify_user_as_admin = lambda *a, **kw: False
    api.auth_admin = auth_admin
    pycommon.api = api

    const = types.ModuleType("pycommon.const")

    class _APIAccessType:
        API_KEY = type("X", (), {"value": "api_key"})()

    const.APIAccessType = _APIAccessType
    pycommon.const = const

    decorators = types.ModuleType("pycommon.decorators")
    decorators.required_env_vars = lambda *a, **kw: (lambda f: f)
    pycommon.decorators = decorators

    logger_mod = types.ModuleType("pycommon.logger")

    class _Logger:
        def info(self, *a, **kw): pass
        def warning(self, *a, **kw): pass
        def error(self, *a, **kw): pass
        def exception(self, *a, **kw): pass

    logger_mod.getLogger = lambda *a, **kw: _Logger()
    pycommon.logger = logger_mod

    for name, mod in [
        ("pycommon", pycommon),
        ("pycommon.authz", authz),
        ("pycommon.api", api),
        ("pycommon.api.auth_admin", auth_admin),
        ("pycommon.const", const),
        ("pycommon.decorators", decorators),
        ("pycommon.logger", logger_mod),
    ]:
        sys.modules[name] = mod

    schemata = types.ModuleType("schemata")
    rules_mod = types.ModuleType("schemata.schema_validation_rules")
    rules_mod.rules = {}
    schemata.schema_validation_rules = rules_mod
    perms_mod = types.ModuleType("schemata.permissions")
    perms_mod.get_permission_checker = lambda: None
    schemata.permissions = perms_mod

    for name, mod in [
        ("schemata", schemata),
        ("schemata.schema_validation_rules", rules_mod),
        ("schemata.permissions", perms_mod),
    ]:
        sys.modules[name] = mod


_install_mocks()

_SVC_DIR = pathlib.Path(__file__).parent.parent / "service"
if str(_SVC_DIR) not in sys.path:
    sys.path.insert(0, str(_SVC_DIR))

import notebook_proxy as _nb  # noqa: E402

# Force the non-admin stub onto the imported module's binding. notebook_proxy
# does ``from pycommon.api.auth_admin import verify_user_as_admin``, so it holds
# its OWN module-level reference; patching sys.modules is not enough. This also
# makes the suite import-order independent: if another test file imported the
# REAL pycommon first, _install_mocks() above early-returns and the real
# verify_user_as_admin (which reads os.environ['API_BASE_URL'] and raises
# KeyError offline) would otherwise leak in. Every test here expects a
# non-admin caller, so binding False unconditionally is correct.
_nb.verify_user_as_admin = lambda *a, **kw: False

_normalise = _nb._normalise_path
_traversal = _nb._has_traversal
_level = _nb._required_level
_reject = _nb._reject_if_unauthorized


# ---------------------------------------------------------------------------
# 1. Paths confirmed open in the pen-test report must now be admin-gated
# ---------------------------------------------------------------------------

class TestConfirmedOpenPathsNowAdminGated(unittest.TestCase):
    """Every endpoint the pen-test report confirmed as 'PASS' must require admin."""

    def _assert_admin(self, method: str, path: str):
        norm = _normalise(path)
        lvl = _level(method, norm)
        self.assertEqual(
            lvl, "admin",
            f"Expected admin for {method} {path!r} (norm={norm!r}), got {lvl!r}",
        )

    # --- Service-level endpoints (the new additions in this patch) ----------

    def test_openapi_json_get(self):
        """Full OpenAPI spec dump — exposes all 80 internal endpoint paths."""
        self._assert_admin("GET", "/openapi.json")

    def test_openapi_json_subpath(self):
        """Sub-paths of /openapi.json must not fall through."""
        self._assert_admin("GET", "/openapi.json/anything")

    def test_commands_registry_debug_get(self):
        """Command registry debug — reveals all internal command names."""
        self._assert_admin("GET", "/commands/registry/debug")

    def test_commands_embed_note_post(self):
        """Command execution endpoint — triggers expensive operations."""
        self._assert_admin("POST", "/commands/embed_note")

    def test_commands_root_get(self):
        self._assert_admin("GET", "/commands")

    def test_docs_get(self):
        """Interactive Swagger UI — exposes full API surface."""
        self._assert_admin("GET", "/docs")

    def test_docs_oauth2_redirect(self):
        """Sub-path of /docs must also be gated."""
        self._assert_admin("GET", "/docs/oauth2-redirect")

    def test_redoc_get(self):
        """ReDoc UI — alternative full API documentation browser."""
        self._assert_admin("GET", "/redoc")

    # --- Credential / config endpoints (gated in the prior commit) ----------

    def test_credentials_get(self):
        self._assert_admin("GET", "/credentials")

    def test_credentials_env_status_get(self):
        self._assert_admin("GET", "/credentials/env-status")

    def test_credentials_post(self):
        self._assert_admin("POST", "/credentials")

    def test_config_get(self):
        self._assert_admin("GET", "/config")

    def test_auth_get(self):
        self._assert_admin("GET", "/auth")

    def test_settings_get(self):
        """/settings reads expose internal service config -> admin-only.
        (This closes pen-test script Test 3, which counted any 200 as exposure.)"""
        self._assert_admin("GET", "/settings")

    # --- Shared-config write mutations (admin only) -------------------------

    def test_models_put(self):
        self._assert_admin("PUT", "/models/some-id")

    def test_models_post(self):
        self._assert_admin("POST", "/models")

    def test_models_delete(self):
        self._assert_admin("DELETE", "/models/some-id")

    def test_settings_put(self):
        self._assert_admin("PUT", "/settings")

    def test_settings_post(self):
        self._assert_admin("POST", "/settings")


# ---------------------------------------------------------------------------
# 2. Legitimate per-user paths must remain accessible (no over-blocking)
# ---------------------------------------------------------------------------

class TestLegitUserPathsPassThrough(unittest.TestCase):
    """Regular-user calls for per-user data must NOT require admin."""

    def _assert_user(self, method: str, path: str):
        norm = _normalise(path)
        lvl = _level(method, norm)
        self.assertEqual(
            lvl, "user",
            f"Expected user for {method} {path!r} (norm={norm!r}), got {lvl!r}",
        )

    def test_notebooks_get(self): self._assert_user("GET", "/notebooks")
    def test_notebooks_post(self): self._assert_user("POST", "/notebooks")
    def test_notebooks_id_get(self): self._assert_user("GET", "/notebooks/abc123")
    def test_notebooks_id_delete(self): self._assert_user("DELETE", "/notebooks/abc123")
    def test_sources_get(self): self._assert_user("GET", "/sources")
    def test_sources_id_get(self): self._assert_user("GET", "/sources/src001")
    def test_notes_get(self): self._assert_user("GET", "/notes")
    def test_notes_post(self): self._assert_user("POST", "/notes")
    def test_podcasts_episodes_audio(self):
        """Used by the raw-proxy endpoint for audio retrieval."""
        self._assert_user("GET", "/podcasts/episodes/ep001/audio")
    def test_chat_post(self): self._assert_user("POST", "/chat")
    def test_insights_get(self): self._assert_user("GET", "/insights")

    # Shared-config reads are fine for regular users (NOTE: /settings is NOT
    # here — it is admin-only for reads too; see TestConfirmedOpenPathsNowAdminGated)
    def test_models_get(self): self._assert_user("GET", "/models")
    def test_models_id_get(self): self._assert_user("GET", "/models/gpt-4o")
    def test_transformations_get(self): self._assert_user("GET", "/transformations")
    def test_episode_profiles_get(self): self._assert_user("GET", "/episode-profiles")
    def test_speaker_profiles_get(self): self._assert_user("GET", "/speaker-profiles")


# ---------------------------------------------------------------------------
# 3. Path traversal must be rejected before auth evaluation
# ---------------------------------------------------------------------------

class TestPathTraversalBlocked(unittest.TestCase):
    """_has_traversal must catch all traversal variants, encoded or not."""

    def _assert_traversal(self, path: str):
        self.assertTrue(_traversal(path), f"Expected traversal detected in {path!r}")

    def _assert_no_traversal(self, path: str):
        self.assertFalse(_traversal(path), f"Unexpected traversal detected in {path!r}")

    def test_literal_dotdot(self):
        self._assert_traversal("/notebooks/../credentials")

    def test_single_percent_encoded(self):
        """Single-encoded %2e%2e — classic bypass."""
        self._assert_traversal("/notebooks/%2e%2e/credentials")

    def test_double_percent_encoded(self):
        """%252e%252e decodes in two passes to '..'."""
        self._assert_traversal("/notebooks/%252e%252e/credentials")

    def test_triple_percent_encoded(self):
        """Triple encoding still resolves to '..' after multiple decode passes."""
        self._assert_traversal("/%25%32%65%25%32%65/credentials")

    def test_lfi_report_evidence5_exact_payload(self):
        """LFI report (Version 9) EVIDENCE 5 claimed '/%2e%2e/api/notebooks'
        bypassed the filter. It must now be detected as traversal."""
        self._assert_traversal("/%2e%2e/api/notebooks")

    def test_encoded_slash_traversal(self):
        """'..%2f' (encoded slash) must also decode and be caught."""
        self._assert_traversal("/%2e%2e%2fcredentials")

    def test_double_slash(self):
        self._assert_traversal("/notebooks//credentials")

    def test_double_slash_at_root(self):
        self._assert_traversal("//credentials")

    def test_normal_path_no_traversal(self):
        self._assert_no_traversal("/notebooks/abc123")

    def test_dotfile_in_segment_no_traversal(self):
        """A filename containing '.' is not a traversal."""
        self._assert_no_traversal("/sources/my.file.txt")

    def test_deep_path_no_traversal(self):
        self._assert_no_traversal("/notes/n001/content")


# ---------------------------------------------------------------------------
# 4. _reject_if_unauthorized — end-to-end auth decisions
# ---------------------------------------------------------------------------

class TestRejectUnauthorized(unittest.TestCase):
    """_reject_if_unauthorized with a non-admin token must block admin paths
    and allow user paths."""

    _TOK = "Bearer fake-token"
    _USR = "testuser@example.com"

    def _blocked(self, method: str, path: str):
        r = _reject(method, path, self._TOK, self._USR)
        self.assertIsNotNone(r, f"Expected rejection for {method} {path!r}")
        self.assertFalse(r["success"])
        return r

    def _allowed(self, method: str, path: str):
        r = _reject(method, path, self._TOK, self._USR)
        self.assertIsNone(r, f"Expected allow for {method} {path!r}, got {r!r}")

    def test_openapi_json_blocked_for_user(self):
        self._blocked("GET", "/openapi.json")

    def test_commands_debug_blocked_for_user(self):
        self._blocked("GET", "/commands/registry/debug")

    def test_docs_blocked_for_user(self):
        self._blocked("GET", "/docs")

    def test_credentials_blocked_for_user(self):
        self._blocked("GET", "/credentials")

    def test_models_post_blocked_for_user(self):
        self._blocked("POST", "/models")

    def test_settings_get_blocked_for_user(self):
        """Pen-test script Test 3: GET /settings must now be blocked."""
        self._blocked("GET", "/settings")

    def test_notebooks_allowed_for_user(self):
        self._allowed("GET", "/notebooks")

    def test_sources_allowed_for_user(self):
        self._allowed("GET", "/sources")

    def test_models_get_allowed_for_user(self):
        self._allowed("GET", "/models")

    def test_traversal_returns_invalid_path_message(self):
        """Traversal must be rejected with 'Invalid path', not 'Forbidden'."""
        r = _reject("GET", "/notebooks/../credentials", self._TOK, self._USR)
        self.assertIsNotNone(r)
        self.assertIn("Invalid path", r.get("message", ""))


# ---------------------------------------------------------------------------
# 5. _normalise_path edge-cases
# ---------------------------------------------------------------------------

class TestNormalisePath(unittest.TestCase):

    def test_lowercase(self):
        self.assertEqual(_normalise("/Credentials"), "/credentials")

    def test_trailing_slash_stripped(self):
        self.assertEqual(_normalise("/credentials/"), "/credentials")

    def test_root_slash_preserved(self):
        self.assertEqual(_normalise("/"), "/")

    def test_query_stripped(self):
        self.assertEqual(_normalise("/settings?foo=bar"), "/settings")

    def test_fragment_stripped(self):
        self.assertEqual(_normalise("/docs#section"), "/docs")

    def test_single_percent_decoded(self):
        # %63 == 'c'
        self.assertEqual(_normalise("/%63redentials"), "/credentials")

    def test_double_percent_decoded(self):
        # %25 -> '%', so %2563 -> %63 -> 'c'
        self.assertEqual(_normalise("/%2563redentials"), "/credentials")

    def test_no_leading_slash_added(self):
        self.assertTrue(_normalise("credentials").startswith("/"))


# ---------------------------------------------------------------------------
# 6. Admin-read prefixes are also admin-gated even for safe methods
# ---------------------------------------------------------------------------

class TestAdminReadPrefixes(unittest.TestCase):
    """_ADMIN_READ_PREFIXES: GET is admin-only for provider-level operations."""

    def _assert_admin(self, method: str, path: str):
        norm = _normalise(path)
        lvl = _level(method, norm)
        self.assertEqual(lvl, "admin",
                         f"Expected admin for {method} {path!r}, got {lvl!r}")

    def test_models_discover_get(self):
        self._assert_admin("GET", "/models/discover")

    def test_models_sync_post(self):
        self._assert_admin("POST", "/models/sync")

    def test_models_providers_get(self):
        self._assert_admin("GET", "/models/providers")


if __name__ == "__main__":
    unittest.main(verbosity=2)
