"""
URL validation utilities to prevent Server-Side Request Forgery (SSRF) attacks.

Validates URLs against private/internal network ranges, cloud metadata endpoints,
and optionally enforces HTTPS + allowlist when credentials are being forwarded.
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

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


def validate_url(url, allow_credential_forwarding=False, allowed_hosts=None):
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

    return True, None


def _is_ip_literal(hostname):
    """Return True if hostname is already a literal IP address (v4 or v6)."""
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _is_private_ip(hostname):
    """Check if a hostname is a private/reserved IP address."""
    try:
        addr = ipaddress.ip_address(hostname)
        return (
            addr.is_private
            or addr.is_reserved
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
        )
    except ValueError:
        # Not a valid IP address (it's a hostname), not blocked by this check
        return False


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
