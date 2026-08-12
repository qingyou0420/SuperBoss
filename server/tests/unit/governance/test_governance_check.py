"""Repository contracts for the bootstrap governance metadata."""

from __future__ import annotations

import json
import re
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
