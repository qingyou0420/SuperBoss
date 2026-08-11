import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[3]
COMPOSE_PATH = ROOT / "docker-compose.dev.yml"
COMPOSE_DEFAULT = re.compile(r"^\$\{([A-Z0-9_]+):-([^}]*)\}$")


def _compose() -> dict[str, Any]:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _command(service: dict[str, Any]) -> str:
    command = service.get("command", "")
    return " ".join(command) if isinstance(command, list) else str(command)


def _isolated_compose_environment(environment: dict[str, object]) -> dict[str, str]:
    isolated = {key: value for key, value in os.environ.items() if not key.startswith("SUPERBOSS_")}
    for key, value in environment.items():
        rendered = str(value)
        match = COMPOSE_DEFAULT.fullmatch(rendered)
        isolated[key] = match.group(2) if match is not None else rendered
    return isolated


def test_compose_defines_backend_dependencies_with_health_contracts() -> None:
    compose = _compose()
    services = compose["services"]

    assert {
        "api",
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "clamav",
        "file-scan-worker",
        "file-maintenance-worker",
        "celery-beat",
    } <= services.keys()
    for dependency in ("postgres", "redis", "minio"):
        assert services[dependency]["healthcheck"]
    assert "healthcheck" not in services["clamav"]


def test_minio_bucket_is_idempotently_created_before_storage_consumers_start() -> None:
    services = _compose()["services"]
    initializer = services["minio-init"]
    command = _command(initializer)

    assert initializer["image"] == "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z"
    assert initializer["depends_on"]["minio"]["condition"] == "service_healthy"
    assert initializer["networks"] == ["backend"]
    assert initializer["restart"] == "no"
    assert "mc alias set" in command
    assert "mc mb --ignore-existing" in command
    assert "$${MINIO_ROOT_USER}" in command
    assert "$${MINIO_ROOT_PASSWORD}" in command
    assert "$${MINIO_BUCKET}" in command
    assert initializer["environment"] == {
        "MINIO_ROOT_USER": "superboss",
        "MINIO_ROOT_PASSWORD": "superboss-dev-only",
        "MINIO_BUCKET": "superboss-files",
    }
    for consumer in ("api", "file-scan-worker"):
        assert (
            services[consumer]["depends_on"]["minio-init"]["condition"]
            == "service_completed_successfully"
        )


def test_clamav_is_pinned_persistent_and_only_reachable_on_backend_network() -> None:
    compose = _compose()
    clamav = compose["services"]["clamav"]

    assert clamav["image"] == "clamav/clamav:1.4"
    assert "ports" not in clamav
    assert clamav["networks"] == ["backend"]
    assert compose["networks"]["backend"].get("internal", False) is False
    assert any(str(volume).endswith(":/var/lib/clamav") for volume in clamav["volumes"])
    assert any(str(volume).endswith(":/etc/clamav/clamd.conf:ro") for volume in clamav["volumes"])

    config_path = ROOT / "server" / "config" / "clamd.conf"
    directives = dict(
        line.split(maxsplit=1)
        for line in config_path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert directives["TCPSocket"] == "3310"
    assert directives["TCPAddr"] == "0.0.0.0"
    assert directives["StreamMaxLength"] == "100M"
    assert directives["DatabaseDirectory"] == "/var/lib/clamav"
    assert directives["User"] == "clamav"


def test_workers_and_beat_use_explicit_queues_and_shared_backend_environment() -> None:
    services = _compose()["services"]
    shared_environment = services["api"]["environment"]
    for service_name in (
        "file-scan-worker",
        "file-maintenance-worker",
        "celery-beat",
    ):
        assert services[service_name]["environment"] == shared_environment
    assert shared_environment["SUPERBOSS_REDIS_URL"] == "redis://redis:6379/0"
    assert shared_environment["SUPERBOSS_CLAMAV_HOST"] == "clamav"

    scan_dependencies = services["file-scan-worker"]["depends_on"]
    for dependency in ("postgres", "redis", "minio", "clamav"):
        assert scan_dependencies[dependency]["condition"] == "service_healthy"

    scan_command = _command(services["file-scan-worker"])
    assert "worker" in scan_command
    assert "--queues=file-scan" in scan_command
    assert "--concurrency=1" in scan_command
    assert "--prefetch-multiplier=1" in scan_command

    maintenance_command = _command(services["file-maintenance-worker"])
    assert "worker" in maintenance_command
    assert "--queues=file-maintenance" in maintenance_command
    assert "--concurrency=1" in maintenance_command
    assert "--prefetch-multiplier=1" in maintenance_command
    assert "beat" in _command(services["celery-beat"])


def test_shared_environment_uses_local_auth_without_legacy_identity_pass_through() -> None:
    shared_environment = _compose()["services"]["api"]["environment"]

    assert shared_environment["SUPERBOSS_ENVIRONMENT"] == "development"
    assert not any("WECOM" in name for name in shared_environment)
    assert shared_environment["SUPERBOSS_JWT_SECRET"] == (
        "${SUPERBOSS_JWT_SECRET:-development-only-jwt-secret-change-before-deploy}"
    )


def test_shared_compose_environment_imports_api_and_workers_without_network() -> None:
    shared_environment = _compose()["services"]["api"]["environment"]
    script = """
import socket

def deny_socket(*_args, **_kwargs):
    raise AssertionError("network connection attempted during import")

socket.socket.connect = deny_socket
socket.socket.connect_ex = deny_socket
socket.create_connection = deny_socket

import superboss.main as main
import superboss.workers.celery_app
import superboss.workers.schedules

assert main.app.state.settings.environment == "development"
assert not any("wecom" in name.lower() for name in main.app.state.settings.model_fields)
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT / "server",
        env=_isolated_compose_environment(shared_environment),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_api_startup_does_not_wait_for_clamav_or_redis() -> None:
    api_dependencies = _compose()["services"]["api"].get("depends_on", {})

    assert "clamav" not in api_dependencies
    assert "redis" not in api_dependencies
