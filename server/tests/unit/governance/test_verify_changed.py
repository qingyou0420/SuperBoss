"""Behavioral tests for proportional verification-gate selection."""

from __future__ import annotations

import hashlib
import importlib.util
import json
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
        "governance", "web-focused", "web-static", "backend-full", "web-full",
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
            "governance", "web-focused", "web-static", "backend-full", "web-full",
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
    """A routing regression must not run unrelated boundary or platform gates."""
    module = _module()
    policy = _policy(tmp_path / "gates.log")
    card = _card()

    assert module.selected_gates(policy, card, ("docs/runbook.md",), "affected") == ("governance",)
    assert module.selected_gates(policy, card, ("web/src/app.ts",), "affected") == (
        "web-focused", "web-static",
    )
    assert module.selected_gates(policy, card, ("docker-compose.yml",), "affected") == ("compose",)
    assert module.selected_gates(policy, card, ("server/api.py",), "candidate") == (
        "backend-full", "web-full", "connector-full",
    )


def test_rejects_web_changes_when_only_a_full_web_gate_is_declared(tmp_path: Path) -> None:
    """A Web routing regression must not silently expand an L2 run to the full suite."""
    module = _module()
    policy = _policy(tmp_path / "gates.log")
    policy["gates"] = {"web": policy["gates"]["web-full"]}

    with pytest.raises(module.VerificationError, match="focused/static"):
        module.selected_gates(policy, _card(gate_ids=["web"]), ("web/src/app.ts",), "affected")


def test_reuses_green_evidence_without_capturing_command_output(tmp_path: Path) -> None:
    """A cache regression must not execute an already-green identical gate."""
    module = _module()
    repo = _repo(tmp_path)
    log = tmp_path / "gates.log"
    policy = _policy(log)
    card = _card()

    first = module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "a" * 40, "b" * 64)
    second = module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "a" * 40, "b" * 64)

    assert [result.status for result in first] == ["PASS", "PASS"]
    assert [result.status for result in second] == ["REUSED", "REUSED"]
    assert log.read_text(encoding="utf-8").splitlines() == ["web-focused", "web-static"]
    evidence_dir = repo / _git(repo, "rev-parse", "--git-path", "governance-evidence")
    assert all(set(json.loads(path.read_text(encoding="utf-8"))) == {
        "gate_id", "tree_sha", "card_sha", "argv_digest", "timestamp", "duration_ms", "returncode", "status"
    } for path in evidence_dir.glob("*.json"))


def test_does_not_reuse_evidence_for_a_changed_policy_argv(tmp_path: Path) -> None:
    """A policy-command change must invalidate otherwise matching green evidence."""
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
    """Changing any identity input must prevent a stale green result from matching."""
    module = _module()
    key = module.evidence_key(tree_sha="a" * 40, card_sha="b" * 64, gate="web-full")

    assert key == hashlib.sha256(f"{'a' * 40}\0{'b' * 64}\0web-full".encode()).hexdigest()
    assert key != module.evidence_key("c" * 40, "b" * 64, "web-full")
    assert key != module.evidence_key("a" * 40, "d" * 64, "web-full")


def test_rejects_card_commands_and_ineligible_candidate_runs(tmp_path: Path) -> None:
    """A task card cannot inject commands or bypass candidate review controls."""
    module = _module()
    policy = _policy(tmp_path / "gates.log")

    with pytest.raises(module.VerificationError, match="gate_ids"):
        module.selected_gates(policy, _card(gate_ids=[{"id": "web-full", "steps": ["bad"]}]), (), "affected")
    with pytest.raises(module.VerificationError, match="candidate"):
        module.selected_gates(policy, _card(candidate=False), (), "candidate")
    with pytest.raises(module.VerificationError, match="review round"):
        module.selected_gates(policy, _card(review_round=3), (), "candidate")


def test_timeout_does_not_create_green_evidence(tmp_path: Path) -> None:
    """A timed-out command must remain eligible for a future rerun."""
    module = _module()
    repo = _repo(tmp_path)
    gate = {
        "cwd": ".",
        "steps": [sys.executable, "-c", "import time; time.sleep(1)"],
        "timeout_seconds": 1,
        "kind": "web-focused",
    }
    policy = _policy(tmp_path / "gates.log")
    policy["gates"] = {"web-focused": gate}
    card = _card(gate_ids=["web-focused"])

    result = module.verify(repo, policy, card, ("web/src/app.ts",), "affected", "a" * 40, "b" * 64)

    assert result[0].status == "TIMEOUT"
    evidence_dir = repo / _git(repo, "rev-parse", "--git-path", "governance-evidence")
    assert not list(evidence_dir.glob("*.json"))
