"""Behavioral contract for production Compose smoke orchestration."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL_SMOKE = ROOT / "tests" / "compose" / "smoke.ps1"
SHELL_SMOKE = ROOT / "tests" / "compose" / "smoke.sh"


def _fake_docker_cmd(directory: Path) -> Path:
    shim = directory / "docker.cmd"
    shim.write_text(
        """@echo off
echo %*>>\"%FAKE_DOCKER_LOG%\"
set args=%*
echo %args% | findstr /C:"config --format json" >nul
if not errorlevel 1 (
  echo {"services":{"nginx":{"ports":[{"published":"443","target":8443}]},"web":{},"api":{},"worker":{},"scheduler":{},"postgres":{},"redis":{},"minio":{},"minio-init":{},"clamav":{}}}
  exit /b 0
)
echo %args% | findstr /C:" up -d" >nul
if not errorlevel 1 if "%FAKE_FAIL_UP%"=="1" exit /b 42
echo %args% | findstr /C:"exec -T nginx" >nul
if not errorlevel 1 exit /b 0
exit /b 0
""",
        encoding="utf-8",
    )
    return shim


def _run_powershell_smoke(
    tmp_path: Path, timeout_seconds: int = 5, *, fail_up: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    assert POWERSHELL_SMOKE.is_file()
    _fake_docker_cmd(tmp_path)
    log_path = tmp_path / "docker.log"
    env_path = tmp_path / "production.env"
    env_path.write_text(
        "SUPERBOSS_APP_HOST=nightforest.com\nSUPERBOSS_OBJECTS_HOST=objects.nightforest.com\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path}{os.pathsep}{environment['PATH']}"
    environment["FAKE_DOCKER_LOG"] = str(log_path)
    environment["FAKE_FAIL_UP"] = "1" if fail_up else "0"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(POWERSHELL_SMOKE),
            "-ComposeFile",
            str(ROOT / "docker-compose.yml"),
            "-EnvFile",
            str(env_path),
            "-ReadinessTimeoutSeconds",
            "1",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    calls = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    return result, calls


def _assert_smoke_call_contract(result: subprocess.CompletedProcess[str], calls: list[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert "M1_COMPOSE_SMOKE_PASSED" in result.stdout
    rendered = "\n".join(calls)
    expected_in_order = (
        "config --quiet",
        "build",
        "up -d",
        "exec -T api alembic upgrade head",
        "exec -T nginx",
        "config --format json",
        "down",
    )
    positions = [rendered.index(fragment) for fragment in expected_in_order]
    assert positions == sorted(positions)
    down_calls = [call for call in calls if call.rstrip().endswith(" down")]
    assert len(down_calls) == 1
    assert " -v" not in down_calls[0]
    assert "--volumes" not in down_calls[0]


def test_powershell_smoke_executes_the_full_bounded_sequence(tmp_path: Path) -> None:
    result, calls = _run_powershell_smoke(tmp_path)
    _assert_smoke_call_contract(result, calls)


def test_powershell_smoke_rejects_a_poll_window_above_ten_minutes(tmp_path: Path) -> None:
    assert POWERSHELL_SMOKE.is_file()
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(POWERSHELL_SMOKE),
            "-ReadinessTimeoutSeconds",
            "601",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode != 0
    assert "M1_COMPOSE_SMOKE_PASSED" not in result.stdout


def test_powershell_smoke_cleans_up_when_up_partially_fails(tmp_path: Path) -> None:
    result, calls = _run_powershell_smoke(tmp_path, fail_up=True)

    assert result.returncode != 0
    assert any(" up -d" in call for call in calls)
    assert any(call.rstrip().endswith(" down") for call in calls)
    assert "M1_COMPOSE_SMOKE_PASSED" not in result.stdout


def test_posix_smoke_source_retains_the_port_and_cleanup_contract_without_bash() -> None:
    assert SHELL_SMOKE.is_file()
    source = SHELL_SMOKE.read_text(encoding="utf-8")

    for fragment in (
        "command -v python3",
        "config --quiet",
        "build",
        "up -d",
        "exec -T api alembic upgrade head",
        "/api/v1/health/ready",
        "config --format json",
        "trap cleanup EXIT HUP INT TERM",
        "down",
        "M1_COMPOSE_SMOKE_PASSED",
    ):
        assert fragment in source
    assert "down -v" not in source
    assert "down --volumes" not in source
    assert source.index("started=1") < source.index("up -d")


def test_posix_smoke_executes_the_same_contract_when_bash_is_available(tmp_path: Path) -> None:
    assert SHELL_SMOKE.is_file()
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable on this Windows host")

    docker = tmp_path / "docker"
    docker.write_text(
        """#!/usr/bin/env sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *"config --format json"*)
    printf '%s\\n' '{"services":{"nginx":{"ports":[{"published":"443","target":8443}]},"web":{},"api":{},"worker":{},"scheduler":{},"postgres":{},"redis":{},"minio":{},"minio-init":{},"clamav":{}}}'
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    log_path = tmp_path / "docker.log"
    env_path = tmp_path / "production.env"
    env_path.write_text(
        "SUPERBOSS_APP_HOST=nightforest.com\nSUPERBOSS_OBJECTS_HOST=objects.nightforest.com\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path}{os.pathsep}{environment['PATH']}"
    environment["FAKE_DOCKER_LOG"] = str(log_path)
    environment["COMPOSE_FILE"] = str(ROOT / "docker-compose.yml")
    environment["ENV_FILE"] = str(env_path)
    environment["READINESS_TIMEOUT_SECONDS"] = "1"
    result = subprocess.run(
        [bash, str(SHELL_SMOKE)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    calls = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []

    _assert_smoke_call_contract(result, calls)
