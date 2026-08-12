"""Repository contracts for the bootstrap governance metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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
    card = _load(".governance/tasks/development-governance-guardrails.json")

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
    assert len([card]) == 1 and card["status"] == "active"
