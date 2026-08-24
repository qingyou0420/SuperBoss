"""GitHub Release checks and one-click replacement for the packaged connector."""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import __version__
from .errors import ConnectorError

GITHUB_LATEST_URL: Final = "https://api.github.com/repos/qingyou0420/SuperBoss/releases/latest"
PACKAGE_ASSET = "superboss.exe"
DIGEST_ASSET = "superboss.exe.sha256"
USER_AGENT = "SuperBoss-Connector"
API_VERSION = "2022-11-28"
API_MAX_BYTES = 1_000_000
DIGEST_MAX_BYTES = 4_096
PACKAGE_MAX_BYTES = 80 * 1024 * 1024
REDIRECT_LIMIT = 5
UPDATE_TEMPORARY = "The update check could not finish; retry later."
UPDATE_REJECTED = "The published release is missing a usable connector package."
UPDATE_NOT_PACKAGED = "Update applies to the packaged Windows executable."
UPDATE_TOO_LARGE = "The update package is too large."
UPDATE_DIGEST_INVALID = "The update digest is invalid."
UPDATE_DIGEST_MISMATCH = "The downloaded package did not match its published digest."
UPDATE_REPLACE_FAILED = "The packaged executable could not be replaced."
_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)
_STRICT_IGNORE = ConfigDict(extra="ignore")


class _Asset(BaseModel):
    model_config = _STRICT_IGNORE

    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1, le=PACKAGE_MAX_BYTES)
    browser_download_url: str = Field(min_length=1, max_length=2_048)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("unsafe asset name")
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("unsafe asset name")
        return value


class _Release(BaseModel):
    model_config = _STRICT_IGNORE

    tag_name: str = Field(min_length=1, max_length=64)
    html_url: str = Field(min_length=1, max_length=2_048)
    prerelease: bool = False
    assets: list[_Asset] = Field(min_length=1, max_length=50)


@dataclass(frozen=True)
class UpdateOffer:
    current_version: str
    latest_version: str
    available: bool
    html_url: str
    package_url: str
    digest_url: str


def packaged_executable() -> Path:
    if not getattr(sys, "frozen", False):
        raise ConnectorError(2, UPDATE_NOT_PACKAGED)
    path = Path(sys.executable).resolve()
    if path.name.casefold() != PACKAGE_ASSET:
        raise ConnectorError(2, UPDATE_NOT_PACKAGED)
    return path


def parse_semver(value: str) -> tuple[int, int, int]:
    raw = value.strip()
    if raw[:1] in {"v", "V"}:
        raw = raw[1:]
    parts = raw.split(".")
    if len(parts) != 3 or any(not part.isdecimal() for part in parts):
        raise ValueError("invalid version")
    numbers = (int(parts[0]), int(parts[1]), int(parts[2]))
    if any(part < 0 for part in numbers):
        raise ValueError("invalid version")
    return numbers


def is_newer(latest: str, current: str) -> bool:
    return parse_semver(latest) > parse_semver(current)


def parse_digest(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ConnectorError(2, UPDATE_DIGEST_INVALID)
    token = lines[0].split()[0]
    if _SHA256_HEX.fullmatch(token) is None:
        raise ConnectorError(2, UPDATE_DIGEST_INVALID)
    return token.casefold()


def github_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(30.0, read=120.0),
        follow_redirects=False,
        trust_env=False,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        },
    )


def _assert_https_allowed(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise ConnectorError(5, UPDATE_REJECTED) from error
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not host
    ):
        raise ConnectorError(5, UPDATE_REJECTED)
    if host not in _ALLOWED_HOSTS and not host.endswith(".githubusercontent.com"):
        raise ConnectorError(5, UPDATE_REJECTED)


def _read_limited(response: httpx.Response, maximum: int) -> bytes:
    length = response.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError as error:
            raise ConnectorError(5, UPDATE_REJECTED) from error
        if declared < 0 or declared > maximum:
            raise ConnectorError(2, UPDATE_TOO_LARGE)
    payload = bytearray()
    for chunk in response.iter_bytes():
        payload.extend(chunk)
        if len(payload) > maximum:
            raise ConnectorError(2, UPDATE_TOO_LARGE)
    return bytes(payload)


def _request(client: httpx.Client, url: str, *, maximum: int) -> bytes:
    current = url
    for _ in range(REDIRECT_LIMIT):
        _assert_https_allowed(current)
        try:
            with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ConnectorError(6, UPDATE_TEMPORARY)
                    current = urljoin(current, location)
                    continue
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise ConnectorError(6, UPDATE_TEMPORARY)
                if response.status_code == 404:
                    raise ConnectorError(5, "No published SuperBoss connector release was found.")
                if response.status_code >= 400:
                    raise ConnectorError(5, UPDATE_REJECTED)
                return _read_limited(response, maximum)
        except httpx.HTTPError as error:
            raise ConnectorError(6, UPDATE_TEMPORARY) from error
    raise ConnectorError(5, UPDATE_REJECTED)


def _select_assets(release: _Release) -> tuple[_Asset, _Asset]:
    package = next((asset for asset in release.assets if asset.name == PACKAGE_ASSET), None)
    digest = next((asset for asset in release.assets if asset.name == DIGEST_ASSET), None)
    if package is None or digest is None:
        raise ConnectorError(5, UPDATE_REJECTED)
    _assert_https_allowed(package.browser_download_url)
    _assert_https_allowed(digest.browser_download_url)
    return package, digest


def parse_latest_release(payload: bytes, current_version: str) -> UpdateOffer:
    try:
        release = _Release.model_validate_json(payload)
        latest_version = ".".join(str(part) for part in parse_semver(release.tag_name))
        parse_semver(current_version)
    except (ValidationError, ValueError) as error:
        raise ConnectorError(5, UPDATE_REJECTED) from error
    if release.prerelease:
        raise ConnectorError(5, UPDATE_REJECTED)
    _assert_https_allowed(release.html_url)
    package, digest = _select_assets(release)
    return UpdateOffer(
        current_version=current_version,
        latest_version=latest_version,
        available=is_newer(release.tag_name, current_version),
        html_url=release.html_url,
        package_url=package.browser_download_url,
        digest_url=digest.browser_download_url,
    )


def check_for_update(
    current_version: str = __version__,
    *,
    client: httpx.Client | None = None,
    latest_url: str = GITHUB_LATEST_URL,
) -> UpdateOffer:
    owned = client is None
    session = client if client is not None else github_client()
    try:
        payload = _request(session, latest_url, maximum=API_MAX_BYTES)
        return parse_latest_release(payload, current_version)
    finally:
        if owned:
            session.close()


def replace_executable(target: Path, payload: bytes) -> None:
    incoming = target.with_name(f"{target.name}.incoming")
    previous = target.with_name(f"{target.name}.previous")
    try:
        incoming.write_bytes(payload)
        if previous.exists():
            previous.unlink()
        if target.exists():
            target.replace(previous)
        incoming.replace(target)
    except OSError as error:
        incoming.unlink(missing_ok=True)
        raise ConnectorError(2, UPDATE_REPLACE_FAILED) from error
    try:
        previous.unlink(missing_ok=True)
    except OSError:
        pass


def install_update(
    offer: UpdateOffer,
    target: Path,
    *,
    client: httpx.Client | None = None,
) -> None:
    if not offer.available:
        return
    owned = client is None
    session = client if client is not None else github_client()
    try:
        digest = parse_digest(
            _request(session, offer.digest_url, maximum=DIGEST_MAX_BYTES).decode("ascii")
        )
        payload = _request(session, offer.package_url, maximum=PACKAGE_MAX_BYTES)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != digest:
            raise ConnectorError(2, UPDATE_DIGEST_MISMATCH)
        replace_executable(target, payload)
    except UnicodeError as error:
        raise ConnectorError(2, UPDATE_DIGEST_INVALID) from error
    finally:
        if owned:
            session.close()
