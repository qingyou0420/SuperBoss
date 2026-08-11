"""Executable contract that keeps the current runtime free of legacy WeCom auth."""

from pathlib import Path

from superboss.core.config import Settings
from superboss.main import create_app

ROOT = Path(__file__).resolve().parents[3]
SERVER_SOURCE = ROOT / "server" / "src" / "superboss"


def test_settings_and_openapi_expose_only_local_browser_auth() -> None:
    assert not any("wecom" in name.lower() for name in Settings.model_fields)

    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql+asyncpg://unit:unit@127.0.0.1:1/unit",
            jwt_secret="test-only-signing-secret-with-at-least-thirty-two-bytes",
            lifecycle_reconcile_interval_seconds=0,
        ),
        object_storage=object(),  # type: ignore[arg-type]
        enqueue_file_scan=lambda _file_id, _delivery_key: None,
    )
    paths = set(app.openapi()["paths"])
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/password/change" in paths
    assert not any("wecom" in path.lower() or "callback" in path.lower() for path in paths)


def test_server_runtime_has_no_legacy_identity_provider_or_import() -> None:
    assert not (SERVER_SOURCE / "infrastructure" / "wecom.py").exists()
    matches: list[str] = []
    for path in SERVER_SOURCE.rglob("*.py"):
        source = path.read_text(encoding="utf-8").lower()
        if "wecom" in source:
            matches.append(str(path.relative_to(ROOT)))
    assert matches == []


def test_current_deployment_e2e_and_operator_docs_have_no_wecom_instructions() -> None:
    current_files = [
        ROOT / ".env.example",
        ROOT / "docker-compose.yml",
        ROOT / "docker-compose.dev.yml",
        ROOT / "README.md",
        ROOT / "docs" / "01-需求定稿.md",
        ROOT / "docs" / "02-架构设计.md",
        *sorted((ROOT / "docs" / "runbooks").glob("*.md")),
        *sorted((ROOT / "tests" / "e2e" / "specs").rglob("*.ts")),
    ]
    matches: list[str] = []
    for path in current_files:
        source = path.read_text(encoding="utf-8").lower()
        if any(marker in source for marker in ("wecom", "企业微信", "企微", "wecom_userid")):
            matches.append(str(path.relative_to(ROOT)))
    assert matches == []
