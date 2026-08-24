from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
import respx
from conftest import load_app
from typer.testing import CliRunner

from superboss_connector.errors import ConnectorError
from superboss_connector.updater import (
    DIGEST_ASSET,
    GITHUB_LATEST_URL,
    PACKAGE_ASSET,
    UPDATE_DIGEST_MISMATCH,
    UPDATE_REJECTED,
    check_for_update,
    install_update,
    is_newer,
    parse_digest,
    parse_latest_release,
    parse_semver,
    replace_executable,
)

PACKAGE_URL = "https://github.com/qingyou0420/SuperBoss/releases/download/v0.2.0/superboss.exe"
DIGEST_URL = "https://github.com/qingyou0420/SuperBoss/releases/download/v0.2.0/superboss.exe.sha256"
HTML_URL = "https://github.com/qingyou0420/SuperBoss/releases/tag/v0.2.0"
NEW_PACKAGE = b"superboss-package-v0.2.0"


def _release_payload(
    *,
    tag: str = "v0.2.0",
    html_url: str = HTML_URL,
    package_url: str = PACKAGE_URL,
    digest_url: str = DIGEST_URL,
    prerelease: bool = False,
    extra_asset: dict[str, object] | None = None,
) -> dict[str, object]:
    assets: list[dict[str, object]] = [
        {"name": PACKAGE_ASSET, "size": len(NEW_PACKAGE), "browser_download_url": package_url},
        {"name": DIGEST_ASSET, "size": 80, "browser_download_url": digest_url},
    ]
    if extra_asset is not None:
        assets.append(extra_asset)
    return {
        "tag_name": tag,
        "html_url": html_url,
        "prerelease": prerelease,
        "assets": assets,
        "name": "ignored extra field",
    }


def test_semver_and_digest_parsing() -> None:
    assert parse_semver("v0.2.0") == (0, 2, 0)
    assert is_newer("v0.2.0", "0.1.0")
    assert not is_newer("0.1.0", "0.1.0")
    assert parse_digest(f"{hashlib.sha256(NEW_PACKAGE).hexdigest()}  superboss.exe\n") == hashlib.sha256(
        NEW_PACKAGE
    ).hexdigest()
    with pytest.raises(ConnectorError) as error:
        parse_digest("not-a-digest")
    assert error.value.exit_code == 2


def test_parse_latest_release_offers_a_newer_package() -> None:
    offer = parse_latest_release(json.dumps(_release_payload()).encode(), "0.1.0")
    assert offer.available
    assert offer.latest_version == "0.2.0"
    assert offer.package_url == PACKAGE_URL
    assert offer.digest_url == DIGEST_URL
    assert offer.html_url == HTML_URL


def test_parse_latest_release_rejects_prerelease_and_foreign_hosts() -> None:
    with pytest.raises(ConnectorError) as prerelease:
        parse_latest_release(
            json.dumps(_release_payload(prerelease=True)).encode(),
            "0.1.0",
        )
    assert prerelease.value.exit_code == 5
    with pytest.raises(ConnectorError) as foreign:
        parse_latest_release(
            json.dumps(_release_payload(package_url="https://evil.example/superboss.exe")).encode(),
            "0.1.0",
        )
    assert foreign.value.message == UPDATE_REJECTED


@respx.mock
def test_check_for_update_uses_github_latest_release() -> None:
    respx.get(GITHUB_LATEST_URL).mock(return_value=httpx.Response(200, json=_release_payload()))
    offer = check_for_update("0.1.0")
    assert offer.available is True
    assert offer.latest_version == "0.2.0"


@respx.mock
def test_check_for_update_maps_missing_release_and_outages() -> None:
    respx.get(GITHUB_LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(ConnectorError) as missing:
        check_for_update("0.1.0")
    assert missing.value.exit_code == 5
    respx.get(GITHUB_LATEST_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    with pytest.raises(ConnectorError) as outage:
        check_for_update("0.1.0")
    assert outage.value.exit_code == 6


@respx.mock
def test_check_for_update_refuses_redirects_off_github() -> None:
    respx.get(GITHUB_LATEST_URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example/latest"})
    )
    with pytest.raises(ConnectorError) as error:
        check_for_update("0.1.0")
    assert error.value.exit_code == 5


def test_replace_executable_swaps_bytes(tmp_path: Path) -> None:
    target = tmp_path / PACKAGE_ASSET
    target.write_bytes(b"old")
    replace_executable(target, NEW_PACKAGE)
    assert target.read_bytes() == NEW_PACKAGE
    assert not (tmp_path / f"{PACKAGE_ASSET}.incoming").exists()
    assert not (tmp_path / f"{PACKAGE_ASSET}.previous").exists()


@respx.mock
def test_install_update_verifies_digest_then_replaces(tmp_path: Path) -> None:
    target = tmp_path / PACKAGE_ASSET
    target.write_bytes(b"old")
    digest = hashlib.sha256(NEW_PACKAGE).hexdigest()
    respx.get(GITHUB_LATEST_URL).mock(return_value=httpx.Response(200, json=_release_payload()))
    respx.get(DIGEST_URL).mock(return_value=httpx.Response(200, text=f"{digest}  superboss.exe\n"))
    respx.get(PACKAGE_URL).mock(return_value=httpx.Response(200, content=NEW_PACKAGE))
    offer = check_for_update("0.1.0")
    install_update(offer, target)
    assert target.read_bytes() == NEW_PACKAGE


@respx.mock
def test_install_update_leaves_original_when_digest_mismatches(tmp_path: Path) -> None:
    target = tmp_path / PACKAGE_ASSET
    target.write_bytes(b"old")
    respx.get(DIGEST_URL).mock(
        return_value=httpx.Response(200, text=f"{'0' * 64}  superboss.exe\n")
    )
    respx.get(PACKAGE_URL).mock(return_value=httpx.Response(200, content=NEW_PACKAGE))
    offer = parse_latest_release(json.dumps(_release_payload()).encode(), "0.1.0")
    with pytest.raises(ConnectorError) as error:
        install_update(offer, target)
    assert error.value.message == UPDATE_DIGEST_MISMATCH
    assert target.read_bytes() == b"old"


@respx.mock
def test_cli_check_does_not_download_the_package(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = load_app(monkeypatch, tmp_path)
    respx.get(GITHUB_LATEST_URL).mock(return_value=httpx.Response(200, json=_release_payload()))
    package_route = respx.get(PACKAGE_URL).mock(return_value=httpx.Response(200, content=NEW_PACKAGE))
    result = runner.invoke(app, ["--check-update"])
    assert result.exit_code == 0
    assert "0.1.0 -> 0.2.0" in result.stdout
    assert HTML_URL in result.stdout
    assert package_route.call_count == 0


@respx.mock
def test_cli_update_downloads_and_replaces_the_packaged_exe(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = load_app(monkeypatch, tmp_path)
    target = tmp_path / PACKAGE_ASSET
    target.write_bytes(b"old")
    digest = hashlib.sha256(NEW_PACKAGE).hexdigest()
    respx.get(GITHUB_LATEST_URL).mock(return_value=httpx.Response(200, json=_release_payload()))
    respx.get(DIGEST_URL).mock(return_value=httpx.Response(200, text=f"{digest}  superboss.exe\n"))
    respx.get(PACKAGE_URL).mock(return_value=httpx.Response(200, content=NEW_PACKAGE))
    monkeypatch.setattr("superboss_connector.cli.packaged_executable", lambda: target)
    result = runner.invoke(app, ["--update"])
    assert result.exit_code == 0, result.output
    assert "Updated to 0.2.0." in result.stdout
    assert target.read_bytes() == NEW_PACKAGE


def test_cli_version_flag(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = load_app(monkeypatch, tmp_path)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"
