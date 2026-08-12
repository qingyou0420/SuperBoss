from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast


class VerificationError(Exception):
    pass
@dataclass(frozen=True)
class GateResult:
    gate_id: str
    evidence_key: str
    status: str
    returncode: int
    duration_ms: int
def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise VerificationError(f"{label} must be an object")
    return cast(Mapping[str, object], value)
def _declared_gate_ids(card: Mapping[str, object], policy: Mapping[str, object]) -> tuple[str, ...]:
    gate_ids = card.get("gate_ids")
    gates = _mapping(policy.get("gates"), "policy gates")
    if not isinstance(gate_ids, list) or not all(isinstance(gate_id, str) for gate_id in gate_ids):
        raise VerificationError("gate_ids must contain only policy gate identifiers")
    if len(gate_ids) != len(set(gate_ids)):
        raise VerificationError("gate_ids must not repeat a gate")
    if any(gate_id not in gates for gate_id in gate_ids):
        raise VerificationError("gate_ids includes an unknown policy gate")
    return tuple(gate_ids)
def _first_declared(declared: tuple[str, ...], *choices: str) -> tuple[str, ...]:
    for choice in choices:
        if choice in declared:
            return (choice,)
    return ()
def _web_gates(declared: tuple[str, ...]) -> tuple[str, ...]:
    focused = tuple(gate for gate in ("web-focused", "web-static") if gate in declared)
    if not focused:
        raise VerificationError("web changes require declared focused/static gates")
    return focused
def selected_gates(
    policy: Mapping[str, object],
    card: Mapping[str, object],
    paths: tuple[str, ...],
    mode: str,
) -> tuple[str, ...]:
    declared = _declared_gate_ids(card, policy)
    if mode == "auto":
        mode = "candidate" if card.get("candidate") is True else "affected"
    if mode not in {"affected", "candidate"}:
        raise VerificationError(f"unknown verification mode: {mode}")
    if mode == "candidate":
        limits = _mapping(policy.get("limits"), "policy limits")
        if card.get("candidate") is not True:
            raise VerificationError("candidate verification requires candidate: true")
        review_round = card.get("review_round")
        max_round = limits.get("max_review_round")
        if not isinstance(review_round, int) or isinstance(review_round, bool) or not isinstance(max_round, int):
            raise VerificationError("candidate review round is invalid")
        if review_round > max_round:
            raise VerificationError("candidate review round exceeds the policy limit")
        selected = (
            _first_declared(declared, "backend-full", "backend")
            + _first_declared(declared, "web-full", "web")
            + _first_declared(declared, "connector-full", "connector")
        )
        return tuple(dict.fromkeys(selected))
    governance_files = {"scripts/governance_check.py", "scripts/verify_changed.py", ".github/workflows/governance.yml", "docs/runbooks/development-governance.md", "README.md"}
    if paths and all(
        path.startswith((".governance/", "server/tests/unit/governance/"))
        or path in governance_files for path in paths
    ):
        return tuple(gate for gate in ("governance", "governance-static") if gate in declared)
    if paths and all(path.startswith("docs/") or path.endswith(".md") for path in paths):
        return _first_declared(declared, "governance")
    if any(
        path.startswith("ops/") or "compose" in path.lower() or "deploy" in path.lower()
        for path in paths
    ):
        return _first_declared(declared, "compose")
    if any(path.startswith("web/") for path in paths):
        return _web_gates(declared)
    if any(path.startswith("integrations/") for path in paths):
        focused = tuple(gate for gate in ("connector-focused", "connector-static") if gate in declared)
        return focused or _first_declared(declared, "connector")
    focused = tuple(gate for gate in ("backend-focused", "backend-static") if gate in declared)
    return focused or _first_declared(declared, "backend", "governance")
def evidence_key(tree_sha: str, card_sha: str, gate: str) -> str:
    return hashlib.sha256(f"{tree_sha}\0{card_sha}\0{gate}".encode()).hexdigest()
def _gate_argv(gate: Mapping[str, object]) -> tuple[str, ...]:
    steps = gate.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(step, str) and step for step in steps):
        raise VerificationError("policy gate steps must be a non-empty argv array")
    return tuple(steps)
def _gate_cwd(repo: Path, gate: Mapping[str, object]) -> Path:
    cwd = gate.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise VerificationError("policy gate cwd must be a relative path")
    candidate = (repo / cwd).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as error:
        raise VerificationError("policy gate cwd escapes repository") from error
    return candidate
def run_gate(repo: Path, gate_id: str, gate: Mapping[str, object], timeout: int, key: str) -> GateResult:
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise VerificationError("gate timeout must be positive")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            _gate_argv(gate), cwd=_gate_cwd(repo, gate), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return GateResult(gate_id, key, "TIMEOUT", 124, int((time.monotonic() - started) * 1000))
    except OSError:
        return GateResult(gate_id, key, "INTERRUPTED", 125, int((time.monotonic() - started) * 1000))
    duration_ms = int((time.monotonic() - started) * 1000)
    return GateResult(gate_id, key, "PASS" if completed.returncode == 0 else "FAILED", completed.returncode, duration_ms)
def _evidence_dir(repo: Path) -> Path:
    try:
        output = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "governance-evidence"],
            text=True, capture_output=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise VerificationError("unable to locate governance evidence directory") from error
    path = Path(output)
    path = path if path.is_absolute() else repo / path
    path.mkdir(parents=True, exist_ok=True)
    return path
def _argv_digest(gate: Mapping[str, object]) -> str:
    return hashlib.sha256("\0".join(_gate_argv(gate)).encode()).hexdigest()
def _green_evidence(
    path: Path, gate_id: str, tree_sha: str, card_sha: str, argv_digest: str
) -> bool:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(record, dict)
        and set(record) == {
            "gate_id", "tree_sha", "card_sha", "argv_digest", "timestamp", "duration_ms",
            "returncode", "status",
        }
        and record["gate_id"] == gate_id
        and record["tree_sha"] == tree_sha
        and record["card_sha"] == card_sha
        and record["argv_digest"] == argv_digest
        and isinstance(record["timestamp"], str)
        and isinstance(record["duration_ms"], int)
        and record["returncode"] == 0
        and record["status"] == "PASS"
    )
def _timeout(policy: Mapping[str, object], card: Mapping[str, object], gate: Mapping[str, object]) -> int:
    levels = _mapping(policy.get("levels"), "policy levels")
    level = card.get("level")
    level_config = _mapping(levels.get(level), "policy level")
    level_timeout = level_config.get("timeout_seconds")
    gate_timeout = gate.get("timeout_seconds")
    if not isinstance(level_timeout, int) or isinstance(level_timeout, bool) or level_timeout <= 0:
        raise VerificationError("policy level timeout must be positive")
    if not isinstance(gate_timeout, int) or isinstance(gate_timeout, bool) or gate_timeout <= 0:
        raise VerificationError("policy gate timeout must be positive")
    return min(level_timeout, gate_timeout)
def verify(
    repo: Path,
    policy: Mapping[str, object],
    card: Mapping[str, object],
    paths: tuple[str, ...],
    mode: str,
    tree_sha: str,
    card_sha: str, dry_run: bool = False,
) -> tuple[GateResult, ...]:
    evidence_dir = _evidence_dir(repo)
    gates = _mapping(policy.get("gates"), "policy gates")
    results: list[GateResult] = []
    for gate_id in selected_gates(policy, card, paths, mode):
        gate = _mapping(gates[gate_id], f"gate {gate_id}")
        key = evidence_key(tree_sha, card_sha, gate_id)
        evidence_path = evidence_dir / f"{key}.json"
        argv_digest = _argv_digest(gate)
        if _green_evidence(evidence_path, gate_id, tree_sha, card_sha, argv_digest):
            results.append(GateResult(gate_id, key, "REUSED", 0, 0))
            continue
        if dry_run:
            results.append(GateResult(gate_id, key, "MISSING", 0, 0))
            continue
        result = run_gate(repo, gate_id, gate, _timeout(policy, card, gate), key)
        results.append(result)
        if result.status == "PASS":
            evidence_path.write_text(json.dumps({
                "gate_id": gate_id, "tree_sha": tree_sha, "card_sha": card_sha,
                "argv_digest": argv_digest, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "duration_ms": result.duration_ms, "returncode": result.returncode, "status": result.status,
            }, sort_keys=True) + "\n", encoding="utf-8")
    return tuple(results)
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True).stdout.strip()
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--mode", choices=("auto", "affected", "candidate"), default="auto")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).parent))
    import governance_check
    repo = args.repo.resolve()
    try:
        checked = governance_check.check(repo, args.base, args.head)
        if checked.errors:
            print(json.dumps({"errors": list(checked.errors), "gates": []}, sort_keys=True))
            return 2
        policy, _baseline, card, _card_path = governance_check._load_metadata(repo)
        diff = governance_check.collect_diff(repo, args.base, args.head)
        tree_sha = _git(repo, "rev-parse", f"{args.head}^{{tree}}")
        card_sha = hashlib.sha256(json.dumps(card, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        results = verify(repo, policy, card, tuple(diff["paths"]), args.mode, tree_sha, card_sha, args.dry_run)
    except (VerificationError, governance_check.ConfigurationError, subprocess.CalledProcessError) as error:
        print(json.dumps({"errors": [str(error)], "gates": []}, sort_keys=True))
        return 3
    print(json.dumps({"errors": [], "gates": [result.__dict__ for result in results]}, sort_keys=True))
    return 0 if all(result.status in {"PASS", "REUSED"} for result in results) else 2
if __name__ == "__main__":
    raise SystemExit(main())
