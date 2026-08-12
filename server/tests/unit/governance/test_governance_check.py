"""Repository contracts for the bootstrap governance metadata."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(relative_path: str) -> dict[str, Any]:
    with (REPO / relative_path).open(encoding="utf-8") as source:
        return json.load(source, object_pairs_hook=_reject_duplicate_keys)


def _load_task_cards() -> list[dict[str, Any]]:
    return [
        _load(path.relative_to(REPO).as_posix())
        for path in sorted((REPO / ".governance/tasks").glob("*.json"))
    ]


def _task_card(cards: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    matches = [card for card in cards if card["task_id"] == task_id]
    if len(matches) != 1:
        raise ValueError(f"expected one task card: {task_id}")
    return matches[0]


def _reject_unknown_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{label} unknown field: {min(unknown)}")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return value


def _validate_schema_node(value: Any) -> None:
    node = _mapping(value, "schema node")
    _reject_unknown_fields(
        node,
        {
            "$schema",
            "type",
            "additionalProperties",
            "required",
            "properties",
            "const",
            "pattern",
            "enum",
            "minLength",
            "items",
            "minimum",
        },
        "schema node",
    )
    for child in _mapping(node.get("properties", {}), "schema properties").values():
        _validate_schema_node(child)
    if "items" in node:
        _validate_schema_node(node["items"])


def _validate_document(name: str, value: dict[str, Any]) -> None:
    allowed_fields = {
        "policy": {
            "schema_version", "levels", "path_classification", "gates", "limits",
            "forbidden_artifacts", "approval",
        },
        "baseline": {"schema_version", "baseline_commit", "baseline_tree", "historical_debt"},
        "schema": {"$schema", "type", "additionalProperties", "required", "properties"},
        "card": {
            "schema_version", "task_id", "status", "base_commit", "bootstrap", "candidate",
            "review_round", "level", "problem", "non_goals", "threat_model", "budgets",
            "allowed_paths", "conditional_allowed_paths", "gate_ids", "historical_debt_ids",
            "approval_ids", "success_criteria", "stop_conditions",
        },
    }
    _reject_unknown_fields(value, allowed_fields[name], name)

    if name == "policy":
        for level in _mapping(value["levels"], "policy levels").values():
            level_mapping = _mapping(level, "policy level")
            _reject_unknown_fields(
                level_mapping,
                {"budgets", "verification", "timeout_seconds", "requires_approval", "triggers"},
                "policy level",
            )
            if "budgets" in level_mapping:
                _reject_unknown_fields(
                    _mapping(level_mapping["budgets"], "policy level budgets"),
                    {"files", "production_lines", "test_lines", "documentation_lines"},
                    "policy level budgets",
                )
        for gate in _mapping(value["gates"], "policy gates").values():
            _reject_unknown_fields(
                _mapping(gate, "policy gate"),
                {"cwd", "steps", "timeout_seconds", "kind"},
                "policy gate",
            )
        _reject_unknown_fields(
            _mapping(value["path_classification"], "path classification"),
            {"tests", "documentation", "unknown"},
            "path classification",
        )
        _reject_unknown_fields(
            _mapping(value["limits"], "limits"),
            {"single_file_lines", "test_to_production_ratio", "max_review_round", "max_active_tasks"},
            "limits",
        )
        _reject_unknown_fields(
            _mapping(value["approval"], "approval"),
            {"local_status", "platform_owner_enforced_only"},
            "approval",
        )
    elif name == "baseline":
        for debt in value["historical_debt"]:
            _reject_unknown_fields(
                _mapping(debt, "historical debt"), {"id", "path", "description"}, "historical debt"
            )
    elif name == "schema":
        _validate_schema_node(value)
    else:
        _reject_unknown_fields(
            _mapping(value["threat_model"], "threat model"),
            {"assets", "attackers", "capabilities", "entry_points", "excluded_capabilities"},
            "threat model",
        )
        _reject_unknown_fields(
            _mapping(value["budgets"], "budgets"),
            {
                "files", "production_lines", "test_lines", "documentation_lines", "migrations",
                "dependencies", "services", "containers", "routes", "auth_sources",
                "persistent_state", "network_boundaries",
            },
            "budgets",
        )
        for criterion in value["success_criteria"]:
            _reject_unknown_fields(
                _mapping(criterion, "success criterion"), {"id", "description"}, "success criterion"
            )


def _schema_objects(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("type") == "object":
            found.append(value)
        for child in value.values():
            found.extend(_schema_objects(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_schema_objects(child))
    return found


def test_metadata_contract() -> None:
    policy = _load(".governance/policy.json")
    baseline = _load(".governance/baseline.json")
    schema = _load(".governance/task-card.schema.json")
    cards = _load_task_cards()
    card = _task_card(cards, "development-governance-guardrails")

    for name, document in (("policy", policy), ("baseline", baseline), ("schema", schema)):
        _validate_document(name, document)
    for task_card in cards:
        _validate_document("card", task_card)

    assert policy["schema_version"] == 1
    assert set(policy["levels"]) == {"L0", "L1", "L2", "L3"}
    assert baseline["baseline_commit"] == "51cd8491fa593eb3095684d7528ecea6d1dc17de"
    assert card["level"] == "L2"
    assert card["budgets"] == {
        "files": 20,
        "production_lines": 700,
        "test_lines": 1000,
        "documentation_lines": 300,
        "migrations": 0,
        "dependencies": 0,
        "services": 0,
        "containers": 0,
        "routes": 0,
        "auth_sources": 0,
        "persistent_state": 0,
        "network_boundaries": 0,
    }
    assert card["approval_ids"] == []
    assert re.fullmatch(r"[0-9a-f]{40}", card["base_commit"])
    assert card["base_commit"] != baseline["baseline_commit"]

    assert all(item.get("additionalProperties") is False for item in _schema_objects(schema))
    assert all(
        re.fullmatch(r"SC-[0-9]+", criterion["id"])
        for criterion in card["success_criteria"]
    )

    gate_ids = set(policy["gates"])
    debt_ids = {entry["id"] for entry in baseline["historical_debt"]}
    assert set(card["gate_ids"]) <= gate_ids
    assert set(card["historical_debt_ids"]) <= debt_ids
    assert len([item for item in cards if item["status"] == "active"]) == 1


def test_metadata_selects_bootstrap_card_by_task_id() -> None:
    bootstrap = _load(".governance/tasks/development-governance-guardrails.json")
    completed = deepcopy(bootstrap)
    completed["task_id"] = "archived-governance-task"
    completed["status"] = "complete"
    completed["level"] = "L0"

    assert _task_card([completed, bootstrap], "development-governance-guardrails") is bootstrap


@pytest.mark.parametrize(
    ("name", "relative_path"),
    [
        ("policy", ".governance/policy.json"),
        ("baseline", ".governance/baseline.json"),
        ("schema", ".governance/task-card.schema.json"),
        ("card", ".governance/tasks/development-governance-guardrails.json"),
    ],
)
def test_metadata_rejects_unknown_top_level_fields(name: str, relative_path: str) -> None:
    document = deepcopy(_load(relative_path))
    document["unexpected"] = True

    with pytest.raises(ValueError, match="unknown field: unexpected"):
        _validate_document(name, document)


@pytest.mark.parametrize(
    ("name", "relative_path", "path"),
    [
        ("policy", ".governance/policy.json", ("gates", "governance")),
        ("policy", ".governance/policy.json", ("levels", "L2", "budgets")),
        ("baseline", ".governance/baseline.json", ("historical_debt", 0)),
        ("schema", ".governance/task-card.schema.json", ("properties", "task_id")),
        ("card", ".governance/tasks/development-governance-guardrails.json", ("budgets",)),
    ],
)
def test_metadata_rejects_unknown_nested_fields(
    name: str, relative_path: str, path: tuple[str | int, ...]
) -> None:
    document = deepcopy(_load(relative_path))
    target: Any = document
    for part in path:
        target = target[part]
    target["unexpected"] = True

    with pytest.raises(ValueError, match="unknown field: unexpected"):
        _validate_document(name, document)


CHECKER = REPO / "scripts/governance_check.py"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "schema_version": 1,
        "task_id": "temporary-task",
        "status": "active",
        "base_commit": "0" * 40,
        "bootstrap": False,
        "candidate": False,
        "review_round": 1,
        "level": "L1",
        "problem": "Exercise the checker.",
        "non_goals": [],
        "threat_model": {
            "assets": [], "attackers": [], "capabilities": [], "entry_points": [],
            "excluded_capabilities": [],
        },
        "budgets": {
            "files": 8, "production_lines": 250, "test_lines": 500,
            "documentation_lines": 300, "migrations": 0, "dependencies": 0,
            "services": 0, "containers": 0, "routes": 0, "auth_sources": 0,
            "persistent_state": 0, "network_boundaries": 0,
        },
        "allowed_paths": ["allowed/**", "server/tests/**", ".governance/**"],
        "conditional_allowed_paths": [],
        "gate_ids": ["focused"],
        "historical_debt_ids": [],
        "approval_ids": [],
        "success_criteria": [{"id": "SC-1", "description": "Works."}],
        "stop_conditions": [],
    }
    card.update(overrides)
    return card


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "levels": {
            "L1": {"budgets": _card()["budgets"]},
            "L2": {"budgets": _card()["budgets"]},
            "L3": {"requires_approval": True},
        },
        "path_classification": {
            "tests": ["server/tests/**"], "documentation": ["docs/**", "**/*.md"],
            "unknown": "production",
        },
        "gates": {"focused": {"cwd": ".", "steps": ["true"], "timeout_seconds": 1, "kind": "focused"}},
        "limits": {
            "single_file_lines": 800, "test_to_production_ratio": 2,
            "max_review_round": 2, "max_active_tasks": 1,
        },
        "forbidden_artifacts": ["**/*.diff", "**/*.patch"],
        "approval": {
            "local_status": "WAITING_FOR_OWNER_VERIFICATION",
            "platform_owner_enforced_only": True,
        },
    }


def _seed_repo(tmp_path: Path, *, bootstrap: bool = True) -> tuple[Path, str]:
    repo = tmp_path / "repository"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Governance tests")
    _change(repo, "README.md", "initial\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    base = _git(repo, "rev-parse", "HEAD")
    _write_json(repo / ".governance/policy.json", _policy())
    _write_json(repo / ".governance/baseline.json", {
        "schema_version": 1, "baseline_commit": base,
        "baseline_tree": _git(repo, "rev-parse", f"{base}^{{tree}}"),
        "historical_debt": [{"id": "HD-1", "path": "legacy", "description": "old"}],
    })
    _write_json(repo / ".governance/tasks/temporary-task.json", _card(
        bootstrap=bootstrap, base_commit=base
    ))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo, base


def _change(repo: Path, relative_path: str, content: str = "changed\n") -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_check(repo: Path, base: str, *extra: str) -> subprocess.CompletedProcess[str]:
    card_path = repo / ".governance/tasks/temporary-task.json"
    if card_path.exists():
        card = json.loads(card_path.read_text())
        if card.get("base_commit") == "0" * 40:
            card["base_commit"] = base
            card["bootstrap"] = True
            _write_json(card_path, card)
    if _git(repo, "status", "--porcelain"):
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "change")
    return subprocess.run(
        [sys.executable, str(CHECKER), "--repo", str(repo), "--base", base, "--head", "HEAD", *extra],
        text=True, capture_output=True, check=False,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


def test_scope_rejects_path_outside_declared_contract(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    _change(repo, "outside.txt")

    result = _run_check(repo, base)

    assert result.returncode == 2
    assert _payload(result)["errors"] == ["SCOPE_PATH: outside.txt"]


def test_requires_one_active_card_and_an_ancestor_base(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    _write_json(repo / ".governance/tasks/second.json", _card(task_id="second"))

    two_cards = _run_check(repo, base)

    assert two_cards.returncode == 3
    assert _payload(two_cards)["errors"] == ["ACTIVE_TASKS: expected 1, found 2"]

    repo, _base = _seed_repo(tmp_path / "ancestor")
    non_ancestor = _run_check(repo, "0" * 40)
    assert non_ancestor.returncode == 3
    assert _payload(non_ancestor)["errors"] == ["BASE_NOT_ANCESTOR: " + "0" * 40]


def test_budget_reports_metrics_and_proportion_warnings(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    _change(repo, "allowed/huge.py", "x\n" * 801)
    _change(repo, "server/tests/test_small.py", "x\n")
    card = _card(budgets={**_card()["budgets"], "production_lines": 250, "test_lines": 1})
    _write_json(repo / ".governance/tasks/temporary-task.json", card)

    result = _run_check(repo, base)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["metrics"] == {
        "files": 2, "production_lines": 801, "test_lines": 1, "documentation_lines": 0,
    }
    assert "BUDGET_PRODUCTION_LINES: 801 > 250" in payload["errors"]
    assert "WARNING_SINGLE_FILE_LINES: allowed/huge.py has 801 lines" in payload["warnings"]
    assert not any(warning.startswith("WARNING_TEST_RATIO") for warning in payload["warnings"])


def test_file_budget_overflow_is_a_policy_failure(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    _write_json(repo / ".governance/tasks/temporary-task.json", _card(
        budgets={**_card()["budgets"], "files": 0}
    ))
    _change(repo, "allowed/one.py")

    result = _run_check(repo, base)

    assert result.returncode == 2
    assert "BUDGET_FILES: 1 > 0" in _payload(result)["errors"]


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("server/alembic/versions/a.py", "L3_TRIGGER: migration"),
        ("docker-compose.yml", "L3_TRIGGER: deployment_boundary"),
        ("server/auth.py", "L3_TRIGGER: auth_source"),
    ],
)
def test_boundary_changes_require_l3_approval(tmp_path: Path, path: str, error: str) -> None:
    repo, base = _seed_repo(tmp_path)
    card = _card(allowed_paths=[path])
    _write_json(repo / ".governance/tasks/temporary-task.json", card)
    _change(repo, path)

    result = _run_check(repo, base)

    assert result.returncode == 2
    assert error in _payload(result)["errors"]


def test_dependency_and_debt_changes_need_declarations(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    card = _card(allowed_paths=["package.json", "legacy/**"])
    _write_json(repo / ".governance/tasks/temporary-task.json", card)
    _change(repo, "package.json", json.dumps({"dependencies": {"new-package": "1.0.0"}}))
    _change(repo, "legacy/file.py")

    result = _run_check(repo, base)

    assert result.returncode == 2
    assert "L3_TRIGGER: dependency" in _payload(result)["errors"]
    assert "HISTORICAL_DEBT: HD-1 requires disposition" in _payload(result)["errors"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("review_round", 3, "REVIEW_ROUND: 3 > 2"),
        ("gate_ids", ["unknown"], "UNKNOWN_GATE: unknown"),
        ("allowed_paths", ["/absolute"], "INVALID_PATH_PATTERN: /absolute"),
        ("allowed_paths", ["../parent"], "INVALID_PATH_PATTERN: ../parent"),
        ("allowed_paths", ["*"], "INVALID_PATH_PATTERN: *"),
        ("allowed_paths", ["**"], "INVALID_PATH_PATTERN: **"),
    ],
)
def test_configuration_boundaries_are_rejected(
    tmp_path: Path, field: str, value: object, error: str
) -> None:
    repo, base = _seed_repo(tmp_path)
    _write_json(repo / ".governance/tasks/temporary-task.json", _card(**{field: value}))

    result = _run_check(repo, base)

    assert result.returncode == 3
    assert _payload(result)["errors"] == [error]


def test_rejects_duplicate_metadata_and_forbidden_artifact(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    _change(repo, ".governance/tasks/temporary-task.json", '{"task_id":"one","task_id":"two"}')

    duplicate = _run_check(repo, base)

    assert duplicate.returncode == 3
    assert _payload(duplicate)["errors"] == [
        "INVALID_JSON: .governance/tasks/temporary-task.json: duplicate JSON key: task_id"
    ]

    repo, base = _seed_repo(tmp_path / "artifact")
    _change(repo, "allowed/change.diff")
    artifact = _run_check(repo, base)
    assert artifact.returncode == 2
    assert _payload(artifact)["errors"] == ["FORBIDDEN_ARTIFACT: allowed/change.diff"]


def test_bootstrap_only_passes_when_policy_was_absent_at_base(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path, bootstrap=True)
    policy_base = _git(repo, "rev-parse", "HEAD")
    card = json.loads((repo / ".governance/tasks/temporary-task.json").read_text())
    card["base_commit"] = policy_base
    _write_json(repo / ".governance/tasks/temporary-task.json", card)
    base = policy_base
    result = _run_check(repo, base)
    assert result.returncode == 3
    assert _payload(result)["errors"] == ["BOOTSTRAP_REUSED: policy exists at base"]

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init")
    _git(fresh, "config", "user.email", "tests@example.invalid")
    _git(fresh, "config", "user.name", "Governance tests")
    _change(fresh, "README.md", "base\n")
    _git(fresh, "add", ".")
    _git(fresh, "commit", "-m", "empty base")
    empty_base = _git(fresh, "rev-parse", "HEAD")
    _write_json(fresh / ".governance/policy.json", _policy())
    _write_json(fresh / ".governance/baseline.json", {
        "schema_version": 1, "baseline_commit": empty_base,
        "baseline_tree": _git(fresh, "rev-parse", f"{empty_base}^{{tree}}"),
        "historical_debt": [],
    })
    _write_json(fresh / ".governance/tasks/temporary-task.json", _card(
        bootstrap=True, base_commit=empty_base
    ))
    assert _run_check(fresh, empty_base).returncode == 0


def test_untouched_historical_debt_does_not_block_an_unrelated_task(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    _change(repo, "allowed/unrelated.py")

    result = _run_check(repo, base)

    assert result.returncode == 0
    assert "HISTORICAL_DEBT: HD-1 requires disposition" not in _payload(result)["errors"]


def test_approval_is_contract_bound_and_waits_for_platform_owner(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    card = _card(level="L3", allowed_paths=["server/security.py", ".governance/**"], approval_ids=["approval-1"])
    _write_json(repo / ".governance/tasks/temporary-task.json", card)
    _change(repo, "server/security.py")
    _write_json(repo / ".governance/approvals/approval-1.json", {
        "task_id": "temporary-task", "contract_sha256": "wrong", "approved_by": "untrusted",
        "rules": ["L3", "auth_source"], "reason": "test", "paths": ["server/security.py"], "expires": "merge",
    })

    mismatch = _run_check(repo, base)
    assert mismatch.returncode == 2
    assert "APPROVAL_MISMATCH: approval-1" in _payload(mismatch)["errors"]

    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("governance_check", CHECKER)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    approval = json.loads((repo / ".governance/approvals/approval-1.json").read_text())
    current_card = json.loads((repo / ".governance/tasks/temporary-task.json").read_text())
    approval["contract_sha256"] = module.canonical_sha256(module.immutable_contract(current_card))
    _write_json(repo / ".governance/approvals/approval-1.json", approval)

    waiting = _run_check(repo, base)
    assert waiting.returncode == 4
    assert "WAITING_FOR_OWNER_VERIFICATION" in _payload(waiting)["warnings"]
    assert _run_check(repo, base, "--platform-owner-enforced").returncode == 0


def test_binds_card_base_and_verifies_independent_baseline_tree(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    card = _card(bootstrap=True, base_commit="f" * 40)
    _write_json(repo / ".governance/tasks/temporary-task.json", card)

    wrong_card_base = _run_check(repo, base)

    assert wrong_card_base.returncode == 3
    assert _payload(wrong_card_base)["errors"] == ["TASK_BASE_MISMATCH: " + "f" * 40]

    card["base_commit"] = base
    _write_json(repo / ".governance/tasks/temporary-task.json", card)
    baseline = json.loads((repo / ".governance/baseline.json").read_text())
    baseline["baseline_tree"] = "0" * 40
    _write_json(repo / ".governance/baseline.json", baseline)
    invalid_tree = _run_check(repo, base)
    assert invalid_tree.returncode == 3
    assert _payload(invalid_tree)["errors"] == ["BASELINE_TREE_MISMATCH"]


def test_rejects_nested_unknown_card_command_and_budget_relaxation(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    card = _card(bootstrap=True, base_commit=base)
    card["budgets"] = {**card["budgets"], "production_lines": 251}
    _write_json(repo / ".governance/tasks/temporary-task.json", card)

    relaxed = _run_check(repo, base)

    assert relaxed.returncode == 3
    assert _payload(relaxed)["errors"] == ["BUDGET_CEILING: production_lines"]

    card["budgets"] = _card()["budgets"]
    card["gate_ids"] = [{"id": "focused", "steps": ["bad"]}]
    _write_json(repo / ".governance/tasks/temporary-task.json", card)
    invalid_gate = _run_check(repo, base)
    assert invalid_gate.returncode == 3
    assert _payload(invalid_gate)["errors"] == ["INVALID_METADATA: gate_ids item must be a string"]


def test_collects_rename_destination_binary_and_direct_optional_dependencies(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    _change(repo, "allowed/old.py", "old\n")
    _run_check(repo, base)
    rename_base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "allowed/old.py", "allowed/new.py")
    _change(repo, "allowed/new.py", "old\nnew\n")
    _change(repo, "allowed/blob.bin", "\x00\x01")
    _change(repo, "pyproject.toml", "[project]\ndependencies = [\"direct>=1\"]\n[project.optional-dependencies]\ntest = [\"optional>=1\"]\n[dependency-groups]\ndev = [\"group>=1\"]\n")
    card = _card(bootstrap=False, base_commit=rename_base, level="L3", allowed_paths=[
        "allowed/**", "pyproject.toml"
    ], approval_ids=["approval-1"])
    _write_json(repo / ".governance/tasks/temporary-task.json", card)

    result = _run_check(repo, rename_base)

    assert result.returncode == 2
    assert "L3_TRIGGER: dependency" not in _payload(result)["errors"]
    assert "APPROVAL_MISMATCH: approval-1" in _payload(result)["errors"]
    assert "BINARY_FILE: allowed/blob.bin" in _payload(result)["warnings"]
    assert _payload(result)["metrics"]["files"] == 3


def test_warns_only_for_excessive_test_ratio_and_narrow_approval_paths(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path)
    card = _card(bootstrap=True, base_commit=base, level="L3", allowed_paths=[
        "server/security.py", "server/tests/**"
    ], approval_ids=["approval-1"])
    _write_json(repo / ".governance/tasks/temporary-task.json", card)
    _change(repo, "server/security.py")
    _change(repo, "server/tests/test_security.py", "x\n" * 3)
    _write_json(repo / ".governance/approvals/approval-1.json", {
        "task_id": "temporary-task", "contract_sha256": "", "rules": ["auth_source"],
        "reason": "test", "paths": ["server/other.py"], "expires": "merge",
    })

    result = _run_check(repo, base)

    assert result.returncode == 2
    assert "APPROVAL_MISMATCH: approval-1" in _payload(result)["errors"]
    assert "WARNING_TEST_RATIO: 3 > 2" in _payload(result)["warnings"]


@pytest.mark.parametrize(
    ("budget", "error"),
    [
        ({**_card()["budgets"], "unknown": 1}, "INVALID_METADATA: budgets"),
        ({key: value for key, value in _card()["budgets"].items() if key != "files"}, "INVALID_METADATA: budgets"),
        ({**_card()["budgets"], "files": "one"}, "INVALID_METADATA: budget files"),
    ],
)
def test_rejects_exact_card_budget_shape(tmp_path: Path, budget: dict[str, object], error: str) -> None:
    repo, base = _seed_repo(tmp_path)
    _write_json(repo / ".governance/tasks/temporary-task.json", _card(
        bootstrap=True, base_commit=base, budgets=budget
    ))

    result = _run_check(repo, base)

    assert result.returncode == 3
    assert _payload(result)["errors"] == [error]


@pytest.mark.parametrize(
    ("gate", "error"),
    [
        ({"cwd": ".", "steps": ["true"], "timeout_seconds": 1, "kind": "focused", "extra": 1}, "INVALID_METADATA: gate focused"),
        ({"cwd": ".", "steps": ["true"], "kind": "focused"}, "INVALID_METADATA: gate focused"),
        ({"cwd": ".", "steps": [], "timeout_seconds": 1, "kind": "focused"}, "INVALID_METADATA: gate focused"),
        ({"cwd": ".", "steps": [""], "timeout_seconds": 1, "kind": "focused"}, "INVALID_METADATA: gate focused"),
        ({"cwd": 1, "steps": ["true"], "timeout_seconds": 1, "kind": "focused"}, "INVALID_METADATA: gate focused"),
        ({"cwd": ".", "steps": ["true"], "timeout_seconds": 0, "kind": "focused"}, "INVALID_METADATA: gate focused"),
    ],
)
def test_rejects_exact_policy_gate_shape(tmp_path: Path, gate: dict[str, object], error: str) -> None:
    repo, base = _seed_repo(tmp_path)
    policy = _policy()
    policy["gates"] = {"focused": gate}
    _write_json(repo / ".governance/policy.json", policy)

    result = _run_check(repo, base)

    assert result.returncode == 3
    assert _payload(result)["errors"] == [error]


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("pyproject.toml", "[project]\ndependencies = [\"project-direct>=1\"]\n"),
        ("pyproject.toml", "[project]\n[project.optional-dependencies]\ntest = [\"optional-direct>=1\"]\n"),
        ("pyproject.toml", "[project]\n[dependency-groups]\ntest = [\"group-direct>=1\"]\n"),
        ("package.json", '{"dependencies":{"npm-direct":"1"}}'),
        ("package.json", '{"devDependencies":{"npm-dev-direct":"1"}}'),
    ],
)
def test_each_direct_dependency_source_triggers_l3(tmp_path: Path, path: str, content: str) -> None:
    repo, base = _seed_repo(tmp_path)
    _write_json(repo / ".governance/tasks/temporary-task.json", _card(
        bootstrap=True, base_commit=base, level="L2", allowed_paths=[".governance/**", path]
    ))
    _change(repo, path, content)

    result = _run_check(repo, base)

    assert result.returncode == 2
    assert _payload(result)["errors"] == ["L3_TRIGGER: dependency"]


def test_prepared_governance_workflow_runs_contract_before_proportional_verification() -> None:
    """A ready PR must use a candidate card only after its contract check succeeds."""
    workflow = (REPO / ".github/workflows/governance.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v4" in workflow
    assert "fetch-depth: 0" in workflow
    assert "actions/setup-python@v5" in workflow
    assert "python-version: '3.13'" in workflow
    assert "actions/setup-node@v4" in workflow
    assert "node-version: '24'" in workflow
    assert "actions/cache@v4" in workflow
    assert "path: .git/governance-evidence" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "${{ github.workflow }}-${{ github.ref }}" in workflow
    assert "uv==0.12.3" in workflow
    assert "pytest -q tests/unit/governance" in workflow

    governance_index = workflow.index("governance_check.py")
    verification_index = workflow.index("verify_changed.py")
    assert governance_index < verification_index
    assert "--mode ${{ github.event_name == 'pull_request' && !github.event.pull_request.draft && 'candidate' || 'affected' }}" in workflow
    assert "--platform-owner-enforced" not in workflow


def test_governance_runbook_keeps_remote_enforcement_unconfigured_without_identity() -> None:
    """Unverified ownership cannot be represented by a placeholder CODEOWNERS file."""
    runbook = (REPO / "docs/runbooks/development-governance.md").read_text(encoding="utf-8")

    assert "REMOTE_ENFORCEMENT=NOT_CONFIGURED" in runbook
    assert "git remote get-url origin" in runbook
    assert "GOVERNANCE_OWNER_HANDLE" in runbook
    assert 'gh api "users/$env:GOVERNANCE_OWNER_HANDLE" --silent' in runbook
    assert "Do not create `.github/CODEOWNERS`" in runbook
    assert ".governance/policy.json" in runbook
    assert ".governance/baseline.json" in runbook
    assert ".governance/approvals/" in runbook
    assert ".github/workflows/governance.yml" in runbook
