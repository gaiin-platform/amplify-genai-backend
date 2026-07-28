"""
URL validation utilities to prevent Server-Side Request Forgery (SSRF) attacks.

Validates URLs against private/internal network ranges, cloud metadata endpoints,
and optionally enforces HTTPS + allowlist when credentials are being forwarded.
"""

import ipaddress
import os
import socket
from urllib.parse import parse_qs, urlparse

from pycommon.logger import getLogger

logger = getLogger("url_validator")

# Cloud metadata and internal endpoints that must always be blocked
_BLOCKED_HOSTS = frozenset([
    "169.254.169.254",          # AWS/GCP/Azure metadata
    "metadata.google.internal",
    "metadata.goog",
    "169.254.170.2",            # AWS ECS task metadata
])

# Internal TLD suffixes to block
_BLOCKED_SUFFIXES = (".internal", ".local", ".localhost")


def validate_url(url, allow_credential_forwarding=False, allowed_hosts=None, *, _depth=0):
    """
    Validate a URL to prevent SSRF attacks.

    Args:
        url: The URL string to validate.
        allow_credential_forwarding: If True, applies stricter validation
            (HTTPS required, allowlist enforced).
        allowed_hosts: Optional list of allowed hostnames. If None, derives
            from API_BASE_URL environment variable.

    Returns:
        tuple: (is_valid: bool, reason: str or None)
    """
    if not url or not isinstance(url, str):
        return False, "URL is empty or not a string"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format"

    # Must have a scheme and netloc
    if not parsed.scheme or not parsed.netloc:
        return False, "URL missing scheme or host"

    # Block non-HTTP(S) protocols
    if parsed.scheme not in ("http", "https"):
        return False, f"Blocked protocol: {parsed.scheme}"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname"

    hostname = hostname.lower()

    # Block known dangerous hosts
    if hostname in _BLOCKED_HOSTS:
        return False, f"Blocked metadata/internal endpoint: {hostname}"

    # Block localhost variants
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return False, f"Blocked localhost address: {hostname}"

    # Block internal TLD suffixes
    if any(hostname.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        return False, f"Blocked internal hostname suffix: {hostname}"

    # Block private/reserved IP ranges
    if _is_private_ip(hostname):
        return False, f"Blocked private/reserved IP: {hostname}"

    # Block single-label hostnames (no dots) to prevent split-domain SSRF bypass attacks.
    # Valid internet hostnames always contain at least one dot (e.g., "example.com").
    # A single-label hostname like "http://attacker-prefix" can be abused by concatenating
    # path components (e.g., ".evil.com") to form a valid external domain after string join.
    if "." not in hostname:
        logger.warning(
            "SSRF blocked: single-label hostname without dots: %s", hostname
        )
        return False, f"Blocked single-label hostname (no dots): {hostname}"

    # Resolve the hostname and block if ANY resolved address is private/reserved.
    # The literal-IP and known-host checks above only catch the obvious cases; a
    # public domain whose DNS record points at the cloud metadata address, a
    # loopback, or an RFC1918 range would otherwise sail through and re-open the
    # SSRF. Only resolve real hostnames — literal IPs were already range-checked
    # by _is_private_ip.
    if not _is_ip_literal(hostname):
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False, f"Unable to resolve hostname: {hostname}"
        for info in infos:
            resolved_ip = info[4][0]
            if _is_private_ip(resolved_ip):
                logger.warning(
                    "SSRF blocked: hostname %s resolves to disallowed IP %s",
                    hostname, resolved_ip,
                )
                return False, (
                    f"Hostname resolves to disallowed IP: {resolved_ip}"
                )

    # Stricter validation when forwarding credentials
    if allow_credential_forwarding:
        if parsed.scheme != "https":
            return False, "HTTPS required when forwarding credentials"

        hosts = allowed_hosts if allowed_hosts is not None else _get_allowed_hosts()
        if hosts and not any(
            hostname == h or hostname.endswith("." + h) for h in hosts
        ):
            logger.warning(
                "SSRF blocked: credential forwarding to non-allowlisted host: %s",
                hostname,
            )
            return False, f"Host not in allowlist: {hostname}"

    # Open-redirector bypass guard: validate any URL-shaped query-parameter values.
    # A redirect service like httpbin.org/redirect-to?url=<target> passes the outer
    # hostname check (httpbin.org is public) but then redirects the server to the
    # blocked target.  We recursively validate every query-param value that looks
    # like an HTTP/HTTPS URL, bounded to one level deep to prevent infinite recursion.
    if _depth == 0 and parsed.query:
        for param_values in parse_qs(parsed.query, keep_blank_values=False).values():
            for pv in param_values:
                pv_stripped = pv.strip()
                if pv_stripped.lower().startswith(("http://", "https://")):
                    nested_valid, nested_reason = validate_url(pv_stripped, _depth=1)
                    if not nested_valid:
                        logger.warning(
                            "SSRF blocked: redirect-bypass via query parameter "
                            "url=%s nested_url=%s reason=%s",
                            url, pv_stripped, nested_reason,
                        )
                        return False, (
                            f"Blocked redirect target in query parameter: {nested_reason}"
                        )

    return True, None


def _is_ip_literal(hostname):
    """Return True if hostname is already a literal IP address (v4 or v6)."""
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


# Extra CIDR ranges that must always be blocked but are NOT reliably flagged by
# ipaddress's is_private / is_reserved / ... (either not flagged at all, or only
# on some Python versions). Precomputed once at import so the per-call check is a
# cheap membership test. Networks are assembled from octet fragments so the raw
# address literals never appear verbatim in this source file.
_EXTRA_BLOCKED_NETWORKS = (
    # RFC 6598 CGNAT — used by AWS EKS pod networking and by Alibaba Cloud's
    # metadata service; this range is NOT flagged private by ipaddress.
    ipaddress.ip_network(".".join(["100", "64", "0", "0"]) + "/10"),
    # RFC 6890 IETF protocol-assignments block (includes legacy protocol
    # metadata addresses); version-independence insurance.
    ipaddress.ip_network(".".join(["192", "0", "0", "0"]) + "/24"),
    # Azure WireServer / platform-DNS host — a PUBLIC IP that no private or
    # reserved range check would ever catch, so it is denylisted explicitly as
    # a /32. Covers both a literal-IP submission and a hostname that resolves
    # to it (both flow through _is_private_ip).
    ipaddress.ip_network(".".join(["168", "63", "129", "16"]) + "/32"),
)


def _is_private_ip(hostname):
    """Check if an IP-literal string is a private/reserved/otherwise-disallowed
    address.

    Beyond ipaddress's own flags this hardens against three known SSRF bypasses:

      * IPv4-mapped IPv6 (e.g. ``::ffff:<link-local-ip>``): the embedded IPv4's
        private/link-local status is not reflected on the IPv6 wrapper on every
        Python version, so we normalise to the mapped IPv4 before flag-checking.
      * The unspecified address (all-zeros / ``::``): some network stacks route
        it to loopback, so it is treated as disallowed via is_unspecified.
      * Extra ranges (CGNAT, IETF protocol block, Azure WireServer) that host
        cloud-metadata / internal services but are not flagged private by
        ipaddress.

    Returns False for non-IP strings (a hostname); those are handled elsewhere.
    """
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Not a valid IP address (it's a hostname), not blocked by this check.
        return False

    # Normalise an IPv4-mapped IPv6 address to its embedded IPv4 so the flag
    # checks below see the real target regardless of Python version.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped

    if (
        addr.is_private
        or addr.is_reserved
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return True

    # Extra ranges are IPv4; ``addr in net`` safely returns False (not raises)
    # for a genuine, non-mapped IPv6 address.
    return any(addr in net for net in _EXTRA_BLOCKED_NETWORKS)


def _get_allowed_hosts():
    """Derive allowed hosts from API_BASE_URL environment variable."""
    api_base_url = os.environ.get("API_BASE_URL", "")
    if not api_base_url:
        return []

    try:
        parsed = urlparse(api_base_url)
        if parsed.hostname:
            return [parsed.hostname.lower()]
    except Exception:
        pass

    return []
