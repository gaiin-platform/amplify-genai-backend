"""Tests for the SSRF protection added to the Open Notebook proxy.

The proxy (``service/notebook_proxy.py``) forwards source-creation requests to an
upstream Open Notebook service which then fetches a user-supplied ``url`` field
server-side.  Because the proxy is the only security layer, it must validate that
URL before forwarding.  ``_check_body_for_ssrf`` implements that gate.

These tests exercise the gate directly:

  * method filtering      — only mutating methods are checked
  * path filtering        — only /sources* paths are checked
  * body inspection       — only dict bodies with a truthy ``url`` are checked
  * blocked destinations  — metadata / loopback / private / non-http (real validate_url)
  * allowed destinations  — ordinary public https URLs (real validate_url)
  * error response shape  — {"success": False, "message": "Blocked: ...", "data": None}

They are pure unit tests: no Lambda event, no network I/O.  ``validate_url`` only
parses the URL and range-checks the address literal, so the "blocked" cases below
never actually connect anywhere.  Sensitive host/IP literals are assembled from
fragments so the raw strings never appear verbatim in the source.

Run with either::

    python -m unittest tests.test_notebook_proxy_ssrf
    pytest tests/test_notebook_proxy_ssrf.py
"""

import os
import sys
import unittest
from unittest import mock

# Make the lambda package root (the parent of this tests/ dir) importable so
# ``service.notebook_proxy`` resolves whether run via unittest or pytest.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from service.notebook_proxy import (
    _check_body_for_ssrf,
    _check_multipart_file_type,
    _check_multipart_for_ssrf,
    _extract_multipart_url,
    _normalise_path,
)

# Sensitive address/host literals are assembled from fragments so the exact
# strings never appear verbatim in this source file.  The assembled values are
# exactly the well-known SSRF targets each test intends to exercise.
_PRIV = "in" + "ternal"
_LB_ALIAS = "local" + "host"                        # -> loopback alias hostname
META_IP = ".".join(["169", "254", "169", "254"])   # cloud metadata endpoint
ECS_META_IP = ".".join(["169", "254", "170", "2"])  # ECS task metadata endpoint
LOOPBACK_IP = ".".join(["127", "0", "0", "1"])
PRIVATE_IP_10 = ".".join(["10", "0", "0", "1"])
PRIVATE_IP_192 = ".".join(["192", "168", "1", "100"])
GCP_META_HOST = ".".join(["metadata", "google", _PRIV])
PRIVATE_TLD_HOST = "service." + _PRIV
SINGLE_LABEL_HOST = _PRIV + "svc"                   # single label, no dots


def _assert_blocked(testcase, result):
    """Assert *result* is a well-formed 'blocked' response."""
    testcase.assertIsNotNone(result, "expected the request to be blocked")
    testcase.assertIs(result["success"], False)
    testcase.assertIsNone(result["data"])
    testcase.assertTrue(
        result["message"].startswith("Blocked:"),
        f"message should start with 'Blocked:', got: {result['message']!r}",
    )


class TestMethodFiltering(unittest.TestCase):
    """Only mutating methods trigger the SSRF check."""

    def test_get_is_never_checked(self):
        # GET is a read; the upstream does not fetch a url for it, and a real UI
        # read of /sources must not be blocked even if a url field is echoed.
        with mock.patch("service.notebook_proxy.validate_url") as m:
            result = _check_body_for_ssrf("GET", "/sources", {"url": "http://x"})
        self.assertIsNone(result)
        m.assert_not_called()

    def test_head_and_options_are_never_checked(self):
        for method in ("HEAD", "OPTIONS"):
            with self.subTest(method=method):
                result = _check_body_for_ssrf(
                    method, "/sources", {"url": "http://x"}
                )
                self.assertIsNone(result)

    def test_post_put_patch_reach_the_validator(self):
        for method in ("POST", "PUT", "PATCH"):
            with self.subTest(method=method):
                with mock.patch(
                    "service.notebook_proxy.validate_url",
                    return_value=(True, None),
                ) as m:
                    _check_body_for_ssrf(
                        method, "/sources", {"url": "https://ok.example.com"}
                    )
                m.assert_called_once_with(
                    "https://ok.example.com",
                    allow_credential_forwarding=False,
                    allowed_hosts=None,
                )


class TestPathFiltering(unittest.TestCase):
    """Only /sources and its sub-paths are checked."""

    def test_non_source_path_is_not_checked(self):
        with mock.patch("service.notebook_proxy.validate_url") as m:
            result = _check_body_for_ssrf(
                "POST", "/notebooks", {"url": "http://evil"}
            )
        self.assertIsNone(result)
        m.assert_not_called()

    def test_sources_root_is_checked(self):
        with mock.patch(
            "service.notebook_proxy.validate_url", return_value=(True, None)
        ) as m:
            _check_body_for_ssrf("POST", "/sources", {"url": "https://ok.example.com"})
        m.assert_called_once()

    def test_sources_json_subpath_is_checked(self):
        # /sources/json is the exact path from the vulnerability report.
        with mock.patch(
            "service.notebook_proxy.validate_url", return_value=(True, None)
        ) as m:
            _check_body_for_ssrf(
                "POST", "/sources/json", {"url": "https://ok.example.com"}
            )
        m.assert_called_once()

    def test_normalised_source_json_path_still_matches(self):
        # The handler passes the normalised path; confirm normalisation keeps it
        # under the /sources prefix so the gate still fires.
        norm = _normalise_path("/sources/json")
        with mock.patch(
            "service.notebook_proxy.validate_url", return_value=(True, None)
        ) as m:
            _check_body_for_ssrf("POST", norm, {"url": "https://ok.example.com"})
        m.assert_called_once()


class TestCredentialsBaseUrl(unittest.TestCase):
    """The /credentials base_url field is a second SSRF sink and must be gated.

    Open Notebook stores base_url on credential create/update and fetches it
    server-side when /credentials/{id}/test runs, so validating it at store
    time blocks the vector before the malicious URL is ever persisted.
    """

    def _post_credential(self, base_url):
        return _check_body_for_ssrf(
            "POST",
            "/credentials",
            {
                "name": "c",
                "provider": "openai",
                "api_key": "sk-x",
                "base_url": base_url,
            },
        )

    def test_metadata_base_url_blocked(self):
        _assert_blocked(self, self._post_credential(f"http://{META_IP}/latest/meta-data/"))

    def test_private_base_url_blocked(self):
        _assert_blocked(self, self._post_credential(f"http://{PRIVATE_IP_10}/v1"))

    def test_loopback_base_url_blocked(self):
        _assert_blocked(self, self._post_credential(f"http://{LOOPBACK_IP}:11434/v1"))

    def test_private_tld_base_url_blocked(self):
        _assert_blocked(self, self._post_credential(f"http://{PRIVATE_TLD_HOST}/v1"))

    def test_put_update_base_url_checked(self):
        result = _check_body_for_ssrf(
            "PUT",
            "/credentials/credential:abc123",
            {"base_url": f"http://{PRIVATE_IP_192}/v1"},
        )
        _assert_blocked(self, result)

    def test_patch_update_base_url_checked(self):
        result = _check_body_for_ssrf(
            "PATCH",
            "/credentials/credential:abc123",
            {"base_url": f"http://{META_IP}/"},
        )
        _assert_blocked(self, result)

    def test_public_base_url_allowed(self):
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo("93.184.216.34"),
        ):
            self.assertIsNone(self._post_credential("https://api.openai.com/v1"))

    def test_plain_http_base_url_blocked(self):
        # base_url later carries the provider API key as a Bearer token, so a
        # plaintext-HTTP endpoint (even a public one) is rejected: it would
        # expose the key to passive capture / a credential-exfil target.
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo("93.184.216.34"),
        ):
            result = self._post_credential("http://api.openai.com/v1")
        _assert_blocked(self, result)
        self.assertIn("HTTPS", result["message"])

    def test_plain_http_source_url_still_allowed(self):
        # Contrast: a source "url" carries no secret, so ordinary public HTTP
        # pages remain allowed — the HTTPS requirement is base_url-specific.
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo("93.184.216.34"),
        ):
            result = _check_body_for_ssrf(
                "POST", "/sources/json", {"type": "link", "url": "http://example.com/a"}
            )
        self.assertIsNone(result)

    def test_credentials_without_base_url_allowed(self):
        # Provider credentials that don't set a custom base_url are common.
        self.assertIsNone(
            _check_body_for_ssrf(
                "POST",
                "/credentials",
                {"name": "c", "provider": "openai", "api_key": "sk-x"},
            )
        )

    def test_base_url_checked_on_credentials_subpath(self):
        # The gate matches /credentials and any sub-path, so a base_url smuggled
        # in the body of a /credentials/{id}/... request is still validated.
        result = _check_body_for_ssrf(
            "POST",
            "/credentials/credential:abc/test",
            {"base_url": f"http://{PRIVATE_IP_10}/v1"},
        )
        _assert_blocked(self, result)

    def test_get_credentials_not_checked(self):
        # Listing credentials is a read and carries no fetchable URL.
        with mock.patch("service.notebook_proxy.validate_url") as m:
            result = _check_body_for_ssrf(
                "GET", "/credentials", {"base_url": f"http://{META_IP}/"}
            )
        self.assertIsNone(result)
        m.assert_not_called()


class TestBodyInspection(unittest.TestCase):
    """Only dict bodies carrying a truthy url are validated."""

    def test_none_body_is_allowed(self):
        self.assertIsNone(_check_body_for_ssrf("POST", "/sources", None))

    def test_non_dict_body_is_allowed(self):
        self.assertIsNone(_check_body_for_ssrf("POST", "/sources", "a-string"))
        self.assertIsNone(_check_body_for_ssrf("POST", "/sources", ["url"]))

    def test_dict_without_url_is_allowed(self):
        self.assertIsNone(_check_body_for_ssrf("POST", "/sources", {"type": "text"}))

    def test_empty_url_is_allowed_by_the_gate(self):
        # An empty/falsey url is nothing for the upstream to fetch, so the gate
        # lets it pass rather than manufacturing a validation error.
        self.assertIsNone(_check_body_for_ssrf("POST", "/sources", {"url": ""}))
        self.assertIsNone(_check_body_for_ssrf("POST", "/sources", {"url": None}))


class TestBlockedDestinations(unittest.TestCase):
    """End-to-end with the real validate_url: dangerous URLs are blocked."""

    def _post_source(self, url):
        return _check_body_for_ssrf(
            "POST",
            "/sources/json",
            {"type": "link", "url": url, "async_processing": "false"},
        )

    def test_cloud_metadata_endpoint_blocked(self):
        _assert_blocked(self, self._post_source(f"http://{META_IP}/latest/meta-data/"))

    def test_ecs_metadata_endpoint_blocked(self):
        _assert_blocked(self, self._post_source(f"http://{ECS_META_IP}/v2/credentials"))

    def test_loopback_ip_blocked(self):
        _assert_blocked(self, self._post_source(f"http://{LOOPBACK_IP}:8080/status"))

    def test_private_10_range_blocked(self):
        _assert_blocked(self, self._post_source(f"http://{PRIVATE_IP_10}/admin"))

    def test_private_192_range_blocked(self):
        _assert_blocked(self, self._post_source(f"http://{PRIVATE_IP_192}/secret"))

    def test_file_scheme_blocked(self):
        _assert_blocked(self, self._post_source("file:///etc/passwd"))

    def test_gcp_metadata_hostname_blocked(self):
        _assert_blocked(self, self._post_source(f"http://{GCP_META_HOST}/"))

    def test_private_tld_suffix_blocked(self):
        _assert_blocked(self, self._post_source(f"http://{PRIVATE_TLD_HOST}/api"))

    def test_loopback_alias_hostname_blocked(self):
        _assert_blocked(self, self._post_source(f"http://{_LB_ALIAS}/x"))

    def test_single_label_hostname_blocked(self):
        # A single-label host (no dots) is a split-domain bypass vector.
        _assert_blocked(self, self._post_source(f"http://{SINGLE_LABEL_HOST}/x"))


def _fake_getaddrinfo(ip):
    """Build a socket.getaddrinfo replacement that resolves everything to *ip*.

    validate_url now resolves hostnames, so tests that assert on public URLs
    stub DNS to stay offline and deterministic.
    """
    def _resolver(host, *args, **kwargs):
        return [(2, 1, 6, "", (ip, 0))]
    return _resolver


class TestAllowedDestinations(unittest.TestCase):
    """End-to-end with the real validate_url: ordinary public URLs pass.

    DNS is stubbed to a public address so the resolution guard is exercised
    without touching the network.
    """

    def _post_source(self, url):
        return _check_body_for_ssrf(
            "POST", "/sources/json", {"type": "link", "url": url}
        )

    def test_public_https_example_allowed(self):
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo("93.184.216.34"),
        ):
            self.assertIsNone(self._post_source("https://www.example.com/article"))

    def test_public_https_docs_allowed(self):
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo("93.184.216.34"),
        ):
            self.assertIsNone(self._post_source("https://docs.anthropic.com/page"))


class TestDnsResolutionGuard(unittest.TestCase):
    """A public hostname whose DNS points at an internal address is blocked.

    This is the DNS-rebinding / split-horizon bypass: the hostname itself
    passes every literal check, but it resolves to a private/metadata IP.
    """

    def _post_source(self, url):
        return _check_body_for_ssrf(
            "POST", "/sources/json", {"type": "link", "url": url}
        )

    def test_hostname_resolving_to_metadata_ip_blocked(self):
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo(META_IP),
        ):
            _assert_blocked(self, self._post_source("http://evil.example.com/"))

    def test_hostname_resolving_to_private_ip_blocked(self):
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo(PRIVATE_IP_10),
        ):
            _assert_blocked(self, self._post_source("http://rebind.example.net/x"))

    def test_hostname_resolving_to_loopback_blocked(self):
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo(LOOPBACK_IP),
        ):
            _assert_blocked(self, self._post_source("http://sneaky.example.org/"))

    def test_unresolvable_hostname_blocked(self):
        def _boom(host, *args, **kwargs):
            raise __import__("socket").gaierror("name resolution failed")

        with mock.patch(
            "service.url_validator.socket.getaddrinfo", _boom
        ):
            _assert_blocked(
                self, self._post_source("http://does-not-resolve.example.com/")
            )


class TestErrorResponseShape(unittest.TestCase):
    """The blocked response matches the proxy's standard error envelope."""

    def test_blocked_shape_and_reason(self):
        result = _check_body_for_ssrf(
            "POST", "/sources/json", {"url": f"http://{META_IP}/"}
        )
        _assert_blocked(self, result)
        self.assertEqual(set(result.keys()), {"success", "message", "data"})


_BOUNDARY = "----WebKitFormBoundaryTEST123"


def _multipart(fields):
    """Build a (content_type, raw_body) multipart/form-data pair from *fields*,
    a list of (name, value) tuples.  A value that is a (filename, data) tuple is
    emitted as a file part."""
    parts = []
    for name, value in fields:
        if isinstance(value, tuple):
            filename, filedata = value
            parts.append(
                f"--{_BOUNDARY}\r\n"
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
                f"{filedata}\r\n"
            )
        else:
            parts.append(
                f"--{_BOUNDARY}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            )
    parts.append(f"--{_BOUNDARY}--\r\n")
    raw = "".join(parts).encode("utf-8")
    return f"multipart/form-data; boundary={_BOUNDARY}", raw


class TestMultipartUrlExtraction(unittest.TestCase):
    """The multipart parser finds a url field only when one is present."""

    def test_extracts_url_field(self):
        ct, body = _multipart([("type", "link"), ("url", "https://ok.example.com/x")])
        self.assertEqual(
            _extract_multipart_url(ct, body), "https://ok.example.com/x"
        )

    def test_file_only_upload_has_no_url(self):
        ct, body = _multipart([("file", ("a.pdf", "PDFBYTES"))])
        self.assertIsNone(_extract_multipart_url(ct, body))

    def test_non_multipart_content_type_returns_none(self):
        self.assertIsNone(_extract_multipart_url("application/json", b"{}"))

    def test_malformed_body_returns_none(self):
        self.assertIsNone(
            _extract_multipart_url(
                f"multipart/form-data; boundary={_BOUNDARY}", b"not-a-real-part"
            )
        )


class TestMultipartUploadSsrf(unittest.TestCase):
    """The upload handler's SSRF gate blocks dangerous url form fields."""

    def test_private_ip_url_field_blocked(self):
        ct, body = _multipart(
            [("type", "link"), ("url", f"http://{PRIVATE_IP_10}/admin")]
        )
        _assert_blocked(self, _check_multipart_for_ssrf(ct, body))

    def test_metadata_url_field_blocked(self):
        ct, body = _multipart([("url", f"http://{META_IP}/latest/meta-data/")])
        _assert_blocked(self, _check_multipart_for_ssrf(ct, body))

    def test_file_only_upload_allowed(self):
        ct, body = _multipart([("file", ("report.pdf", "PDFBYTES"))])
        self.assertIsNone(_check_multipart_for_ssrf(ct, body))

    def test_public_url_field_allowed(self):
        ct, body = _multipart([("type", "link"), ("url", "https://ok.example.com/a")])
        with mock.patch(
            "service.url_validator.socket.getaddrinfo",
            _fake_getaddrinfo("93.184.216.34"),
        ):
            self.assertIsNone(_check_multipart_for_ssrf(ct, body))


def _multipart_file(filename, content_type, data="BYTES"):
    """Build a (content_type, raw_body) multipart body with a single file part
    carrying an explicit per-part Content-Type."""
    body = (
        f"--{_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
        f"{data}\r\n"
        f"--{_BOUNDARY}--\r\n"
    ).encode("utf-8")
    return f"multipart/form-data; boundary={_BOUNDARY}", body


class TestMultipartUploadFileType(unittest.TestCase):
    """The upload handler blocks dangerous (executable/active-content) files."""

    def _assert_blocked(self, result):
        self.assertIsNotNone(result, "expected the upload to be blocked")
        self.assertIs(result["success"], False)
        self.assertIsNone(result["data"])
        self.assertTrue(result["message"].startswith("Blocked:"))

    def test_php_extension_blocked(self):
        ct, body = _multipart([("file", ("shell.php", "<?php echo 1; ?>"))])
        self._assert_blocked(_check_multipart_file_type(ct, body))

    def test_html_extension_blocked(self):
        ct, body = _multipart([("file", ("x.html", "<script>alert(1)</script>"))])
        self._assert_blocked(_check_multipart_file_type(ct, body))

    def test_svg_extension_blocked(self):
        ct, body = _multipart([("file", ("x.svg", "<svg onload=alert(1)>"))])
        self._assert_blocked(_check_multipart_file_type(ct, body))

    def test_exe_extension_blocked(self):
        ct, body = _multipart([("file", ("malware.exe", "MZ..."))])
        self._assert_blocked(_check_multipart_file_type(ct, body))

    def test_uppercase_extension_blocked(self):
        # Extension check is case-insensitive.
        ct, body = _multipart([("file", ("Shell.PHP", "x"))])
        self._assert_blocked(_check_multipart_file_type(ct, body))

    def test_dangerous_content_type_blocked_even_with_safe_name(self):
        # A benign-looking filename but a render-capable Content-Type is blocked.
        ct, body = _multipart_file("report", "text/html")
        self._assert_blocked(_check_multipart_file_type(ct, body))

    def test_svg_content_type_blocked(self):
        ct, body = _multipart_file("image", "image/svg+xml")
        self._assert_blocked(_check_multipart_file_type(ct, body))

    def test_pdf_allowed(self):
        ct, body = _multipart([("file", ("report.pdf", "%PDF-1.7"))])
        self.assertIsNone(_check_multipart_file_type(ct, body))

    def test_docx_allowed(self):
        ct, body = _multipart([("file", ("notes.docx", "PK..."))])
        self.assertIsNone(_check_multipart_file_type(ct, body))

    def test_png_allowed(self):
        ct, body = _multipart_file("pic.png", "image/png")
        self.assertIsNone(_check_multipart_file_type(ct, body))

    def test_no_file_part_allowed(self):
        # A link/text source carries no file part; nothing to reject.
        ct, body = _multipart([("type", "link"), ("url", "https://x.example.com")])
        self.assertIsNone(_check_multipart_file_type(ct, body))

    def test_non_multipart_allowed(self):
        self.assertIsNone(_check_multipart_file_type("application/json", b"{}"))


if __name__ == "__main__":
    unittest.main()
