"""Connector constants and trusted-origin validation."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

from .errors import INVALID_INPUT, ConnectorError

PART_SIZE = 8 * 1024 * 1024
ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024
MANIFEST_MAX_UTF8_BYTES = 65_536
MANIFEST_INPUT_MAX_BYTES = 256 * 1024
ETAG_MAX_CHARS = 1_024
HTTP_TIMEOUT_SECONDS = 30.0
LOCK_TIMEOUT_SECONDS = 0.25
RESPONSE_MAX_BYTES = 128 * 1024
TOKEN_MAX_CHARS = 4_096

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _canonical_host(hostname: str) -> tuple[str, bool]:
    candidate = hostname.removesuffix(".")
    if not candidate or candidate.endswith("."):
        raise ValueError("invalid host")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        if re.fullmatch(r"[0-9.]+", candidate) is not None:
            raise ValueError("noncanonical numeric host")
        try:
            ascii_host = candidate.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise ValueError("invalid host") from error
        labels = ascii_host.split(".")
        if len(ascii_host) > 253 or any(_DNS_LABEL.fullmatch(label) is None for label in labels):
            raise ValueError("invalid host")
        return ascii_host, False
    return address.compressed.lower(), address.version == 6


def normalize_origin(value: str) -> str:
    """Return a canonical HTTPS origin, permitting HTTP only for loopback."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ConnectorError(2, INVALID_INPUT) from error
    if (
        not value
        or any(
            character.isspace() or ord(character) < 33 or ord(character) == 127
            for character in value
        )
        or "%" in value
        or "\\" in value
    ):
        raise ConnectorError(2, INVALID_INPUT)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
        if hostname is None or port == 0:
            raise ValueError("invalid authority")
        normalized_host, is_ipv6 = _canonical_host(hostname)
    except ValueError as error:
        raise ConnectorError(2, INVALID_INPUT) from error
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ConnectorError(2, INVALID_INPUT)
    scheme = parsed.scheme.lower()
    if scheme == "http" and not _is_loopback(normalized_host):
        raise ConnectorError(2, INVALID_INPUT)
    if is_ipv6:
        normalized_host = f"[{normalized_host}]"
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = normalized_host if port is None or default_port else f"{normalized_host}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))
