"""Connector constants and trusted-origin validation."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit

from .errors import INVALID_INPUT, ConnectorError

PART_SIZE = 8 * 1024 * 1024
ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024
MANIFEST_MAX_UTF8_BYTES = 65_536
ETAG_MAX_CHARS = 1_024
HTTP_TIMEOUT_SECONDS = 30.0
LOCK_TIMEOUT_SECONDS = 0.25


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalize_origin(value: str) -> str:
    """Return a canonical HTTPS origin, permitting HTTP only for loopback."""
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConnectorError(2, INVALID_INPUT)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ConnectorError(2, INVALID_INPUT) from error
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ConnectorError(2, INVALID_INPUT)
    scheme = parsed.scheme.lower()
    if scheme == "http" and not _is_loopback(hostname):
        raise ConnectorError(2, INVALID_INPUT)
    normalized_host = hostname.lower()
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = normalized_host if port is None or default_port else f"{normalized_host}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))
