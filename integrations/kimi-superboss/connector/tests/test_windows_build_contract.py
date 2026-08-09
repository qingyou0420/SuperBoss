from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

INTEGRATION_ROOT = Path(__file__).resolve().parents[2]
CONNECTOR_ROOT = INTEGRATION_ROOT / "connector"
BUILD_SCRIPT = INTEGRATION_ROOT / "scripts" / "build-windows.ps1"
LAUNCHER = CONNECTOR_ROOT / "src" / "superboss_connector" / "__main__.py"
PYPROJECT = CONNECTOR_ROOT / "pyproject.toml"
UV_LOCK = CONNECTOR_ROOT / "uv.lock"
GITIGNORE = CONNECTOR_ROOT / ".gitignore"


def _build_script() -> str:
    if not BUILD_SCRIPT.is_file():
        pytest.fail(
            "Task 11 Stage 3 RED: integrations/kimi-superboss/scripts/build-windows.ps1 is missing",
            pytrace=False,
        )
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _without_comments(script: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in script.splitlines())


def _normalized(script: str) -> str:
    uncommented = _without_comments(script).lower()
    punctuation_as_space = re.sub(r"[\"'(),=@]", " ", uncommented)
    return re.sub(r"\s+", " ", punctuation_as_space).strip()


def _checked_invocations(script: str) -> list[str]:
    return re.findall(r"(?im)^\s*Invoke-Checked\b.+$", _without_comments(script))


def test_windows_build_is_rooted_and_fails_closed_for_native_commands() -> None:
    script = _build_script()
    lowered = script.lower()

    assert re.search(r"\$ErrorActionPreference\s*=\s*['\"]Stop['\"]", script)
    assert "$psscriptroot" in lowered
    assert "resolve-path" in lowered and "-literalpath" in lowered
    assert re.search(r"(?im)^function\s+Invoke-Checked\b", script)
    assert "$lastexitcode" in lowered
    assert re.search(r"\$LASTEXITCODE\s+-ne\s+0", script)
    assert "throw" in lowered
    invocations = _checked_invocations(script)
    assert len(invocations) >= 6


def test_windows_build_runs_all_gates_in_the_required_order() -> None:
    normalized = _normalized(_build_script())
    gates = (
        "run --locked --group dev pytest",
        "run --locked --group dev ruff check",
        "run --locked --group dev mypy",
        "run --locked --group dev python -m build --wheel --no-isolation --outdir $distroot",
        "run --locked --group dev pyinstaller",
        "$exe --help",
    )
    positions = [normalized.find(gate) for gate in gates]
    assert all(position >= 0 for position in positions), (
        "every required build gate must be explicit"
    )
    assert positions == sorted(positions)


def test_windows_build_uses_uv_locked_tools_not_global_executables() -> None:
    script = _without_comments(_build_script())
    normalized = _normalized(script)
    assert re.search(r"\$Uv\s*=", script)
    assert "--locked" in normalized
    for tool in ("pytest", "ruff", "mypy", "pyinstaller"):
        assert not re.search(rf"(?im)^\s*&?\s*{tool}(?:\.exe)?\b", script)
    for invocation in _checked_invocations(script):
        lowered = invocation.lower()
        if any(tool in lowered for tool in ("pytest", "ruff", "mypy", "pyinstaller")):
            assert "$uv" in lowered and "--locked" in lowered


def test_windows_build_uses_locked_backend_without_isolation() -> None:
    script = _build_script()
    build_lines = [
        line for line in _checked_invocations(script) if "python -m build" in _normalized(line)
    ]
    assert len(build_lines) == 1
    normalized = _normalized(build_lines[0])
    for contract in (
        "$uv run --locked --group dev python -m build",
        "--wheel",
        "--no-isolation",
        "--outdir $distroot",
    ):
        assert contract in normalized

    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dev_dependencies = pyproject["dependency-groups"]["dev"]
    assert "hatchling>=1,<2" in dev_dependencies

    lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    locked_names = {package["name"] for package in lock["package"]}
    assert "build" in locked_names
    assert "hatchling" in locked_names


def test_pyinstaller_builds_onefile_exe_from_absolute_import_launcher() -> None:
    script = _build_script()
    pyinstaller_lines = [
        line for line in _checked_invocations(script) if "pyinstaller" in line.lower()
    ]
    assert len(pyinstaller_lines) == 1
    invocation = pyinstaller_lines[0].lower()
    for contract in (
        "--onefile",
        "--name",
        "superboss",
        "--paths",
        "src",
        "--collect-submodules",
        "keyring.backends",
        "__main__.py",
        "--distpath",
        "--workpath",
        "--specpath",
    ):
        assert contract in invocation

    if not LAUNCHER.is_file():
        pytest.fail(
            "Task 11 Stage 3 RED: superboss_connector/__main__.py launcher is missing",
            pytrace=False,
        )
    tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"), filename=str(LAUNCHER))
    absolute_main_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module == "superboss_connector.cli"
        and any(alias.name == "main" for alias in node.names)
    ]
    assert len(absolute_main_imports) == 1
    assert not any(isinstance(node, ast.ImportFrom) and node.level > 0 for node in ast.walk(tree))
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "main"
        for node in ast.walk(tree)
    )


def test_windows_build_verifies_the_final_executable_help() -> None:
    script = _build_script()
    lowered = script.lower()
    assert re.search(
        r"\$DistRoot\s*=\s*Join-Path\s+\$ConnectorRoot\s+['\"]dist['\"]",
        script,
    )
    assert re.search(
        r"\$Exe\s*=\s*Join-Path\s+\$DistRoot\s+['\"]superboss\.exe['\"]",
        script,
    )
    assert re.search(r"test-path\s+-literalpath\s+\$exe", lowered)
    help_lines = [line for line in _checked_invocations(script) if "--help" in line.lower()]
    assert len(help_lines) == 1
    assert "$exe" in help_lines[0].lower()


def test_windows_build_cleanup_is_guarded_inside_the_integration_tree() -> None:
    script = _without_comments(_build_script())
    lowered = script.lower()
    assert "$integrationroot" in lowered
    assert re.search(r"(?im)^function\s+Assert-WithinIntegration\b", script)
    assert "startswith" in lowered
    assert "$integrationroot" in lowered
    remove_lines = [line for line in script.splitlines() if "remove-item" in line.lower()]
    assert remove_lines
    for line in remove_lines:
        lowered_line = line.lower()
        assert "-literalpath" in lowered_line
        assert any(target in lowered_line for target in ("$distroot", "$buildroot"))
    for target in ("$DistRoot", "$BuildRoot"):
        assert re.search(rf"Assert-WithinIntegration\s+{re.escape(target)}", script)
    forbidden = (
        "$home",
        "$env:userprofile",
        "git clean",
        "cmd /c",
        "rmdir ",
        " del ",
    )
    assert all(term not in lowered for term in forbidden)


def test_connector_ignores_only_local_build_outputs() -> None:
    if not GITIGNORE.is_file():
        pytest.fail(
            "Task 11 Stage 3 RED: connector-local .gitignore is missing",
            pytrace=False,
        )
    patterns = {
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert patterns == {"/build/", "/dist/"}
