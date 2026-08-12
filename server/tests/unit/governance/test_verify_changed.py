from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
VERIFY_CHANGED = REPO / "scripts" / "verify_changed.py"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("verify_changed", VERIFY_CHANGED)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


def _policy(log: Path) -> dict[str, object]:
    gates: dict[str, object] = {}
    for gate_id in (
        "governance", "governance-static", "web-focused", "web-lint", "web-static", "backend-full", "web-full",
        "connector-full", "compose", "e2e-contract", "windows-packaging",
    ):
        gates[gate_id] = {
            "cwd": ".",
            "steps": [
                sys.executable,
                "-c",
                "from pathlib import Path; Path(r'" + str(log) + "').open('a').write('"
                + gate_id + "\\n')",
            ],
            "timeout_seconds": 10,
            "kind": gate_id,
        }
    return {
        "levels": {
            "L1": {"timeout_seconds": 1},
            "L2": {"timeout_seconds": 2},
            "L3": {"timeout_seconds": 3},
        },
        "limits": {"max_review_round": 2},
        "gates": gates,
    }


def _card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "level": "L2",
        "candidate": True,
        "review_round": 1,
        "gate_ids": [
            "governance", "governance-static", "web-focused", "web-lint", "web-static", "backend-full", "web-full",
            "connector-full", "compose", "e2e-contract", "windows-packaging",
        ],
    }
    card.update(overrides)
    return card


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Governance tests")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_selects_only_the_proportionate_declared_gates(tmp_path: Path) -> None:
    module = _module()
    policy = _policy(tmp_path / "gates.log")
    card = _card()

    assert module.selected_gates(policy, card, ("docs/runbook.md",), "affected") == ("governance",)
    assert module.selected_gates(policy, card, (
        ".governance/policy.json", "scripts/verify_changed.py",
        "server/tests/unit/governance/test_verify_changed.py", ".github/workflows/governance.yml",
    ), "affected") == ("governance", "governance-static")
    assert module.selected_gates(policy, card, ("web/src/app.ts",), "affected") == (
        "web-focused", "web-lint", "web-static",
    )
    assert module.selected_gates(policy, card, ("docker-compose.yml",), "affected") == ("compose",)
    assert module.selected_gates(policy, card, ("server/api.py",), "candidate") == (
        "backend-full", "web-full", "connector-full",
    )


def test_rejects_web_changes_when_only_a_full_web_gate_is_declared(tmp_path: Path) -> None:
    module = _module()
    policy = _policy(tmp_path / "gates.log")
    policy["gates"] = {"web": policy["gates"]["web-full"]}

    with pytest.raises(module.VerificationError, match="focused/static"):
        module.selected_gates(policy, _card(gate_ids=["web"]), ("web/src/app.ts",), "affected")


def test_reuses_green_evidence_without_capturing_command_output(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path)
    log = tmp_path / "gates.log"
    policy = _policy(log)
    card = _card()

    missing = module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "c" * 40, "d" * 64, dry_run=True)
    assert [(r.status, r.evidence_key) for r in missing] == [("MISSING", module.evidence_key("c" * 40, "d" * 64, gate)) for gate in ("web-focused", "web-lint", "web-static")] and not log.exists()
    first = module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "a" * 40, "b" * 64)
    second = module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "a" * 40, "b" * 64)

    assert [result.status for result in first] == ["PASS", "PASS", "PASS"]
    assert [result.status for result in second] == ["REUSED", "REUSED", "REUSED"]
    assert log.read_text(encoding="utf-8").splitlines() == ["web-focused", "web-lint", "web-static"]
    evidence_dir = repo / _git(repo, "rev-parse", "--git-path", "governance-evidence")
    assert all(set(json.loads(path.read_text(encoding="utf-8"))) == {
        "gate_id", "tree_sha", "card_sha", "argv_digest", "timestamp", "duration_ms", "returncode", "status"
    } for path in evidence_dir.glob("*.json"))

def test_does_not_reuse_evidence_for_a_changed_policy_argv(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path)
    log = tmp_path / "gates.log"
    policy = _policy(log)
    card = _card()
    module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "a" * 40, "b" * 64)
    policy["gates"]["web-focused"]["steps"][-1] += " # changed"

    rerun = module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "a" * 40, "b" * 64)

    assert rerun[0].status == "PASS"

def test_evidence_key_binds_tree_card_and_gate() -> None:
    module = _module()
    key = module.evidence_key(tree_sha="a" * 40, card_sha="b" * 64, gate="web-full")

    assert key == hashlib.sha256(f"{'a' * 40}\0{'b' * 64}\0web-full".encode()).hexdigest()
    assert key != module.evidence_key("c" * 40, "b" * 64, "web-full")
    assert key != module.evidence_key("a" * 40, "d" * 64, "web-full")


def test_web_focused_appends_only_git_derived_related_paths(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path)
    log = tmp_path / "related.log"
    policy = _policy(tmp_path / "other.log")
    policy["gates"]["web-focused"] = {"cwd": ".", "steps": [sys.executable, "-c", "import sys; from pathlib import Path; Path(sys.argv[1]).write_text('|'.join(sys.argv[2:]))", str(log)], "timeout_seconds": 10, "kind": "web"}
    result = module.verify(repo, policy, _card(), ("web/src/app.ts", "web/--help", "server/api.py"), "affected", "a" * 40, "b" * 64)
    assert result[0].status == "PASS"
    assert log.read_text() == "./src/app.ts|./--help"


def test_run_gate_resolves_policy_executable_from_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _module()
    resolved = tmp_path / "npm.CMD"
    executed: list[tuple[str, ...]] = []
    monkeypatch.setattr(shutil, "which", lambda name: str(resolved) if name == "npm" else None)

    def run(argv: list[str] | tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[object]:
        if argv[0] != str(resolved):
            raise FileNotFoundError(argv[0])
        executed.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    result = module.run_gate(tmp_path, "web", {"cwd": ".", "steps": ["npm", "--version"]}, 10, "key")

    assert result.status == "PASS"
    assert executed == [(str(resolved), "--version")]
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert module.run_gate(tmp_path, "web", {"cwd": ".", "steps": ["missing"]}, 10, "key").status == "INTERRUPTED"


def test_rejects_card_commands_and_ineligible_candidate_runs(tmp_path: Path) -> None:
    module = _module()
    policy = _policy(tmp_path / "gates.log")

    with pytest.raises(module.VerificationError, match="gate_ids"):
        module.selected_gates(policy, _card(gate_ids=[{"id": "web-full", "steps": ["bad"]}]), (), "affected")
    with pytest.raises(module.VerificationError, match="candidate"):
        module.selected_gates(policy, _card(candidate=False), (), "candidate")
    with pytest.raises(module.VerificationError, match="review round"):
        module.selected_gates(policy, _card(review_round=3), (), "candidate")
    with pytest.raises(module.VerificationError, match="backend-full, web-full, connector-full"):
        module.selected_gates(policy, _card(gate_ids=["backend-full", "web-full"]), (), "candidate")


def test_timeout_does_not_create_green_evidence(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path)
    gate = {
        "cwd": ".",
        "steps": [sys.executable, "-c", "import time; time.sleep(1)"],
        "timeout_seconds": 1,
        "kind": "web-focused",
    }
    policy = _policy(tmp_path / "gates.log")
    policy["gates"]["web-focused"] = gate
    card = _card()

    result = module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "a" * 40, "b" * 64)

    assert result[0].status == "TIMEOUT"
    evidence_dir = repo / _git(repo, "rev-parse", "--git-path", "governance-evidence")
    assert all(json.loads(path.read_text())["gate_id"] != "web-focused" for path in evidence_dir.glob("*.json"))
