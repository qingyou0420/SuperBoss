from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

GOVERNANCE_PREFIX = ".governance/"
LIFECYCLE_FIELDS = {"status", "bootstrap", "candidate", "review_round", "approval_ids"}
@dataclass(frozen=True)
class CheckResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: Mapping[str, int]
class ConfigurationError(Exception):
    pass
def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
def load_strict_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as source:
        value = json.load(source, object_pairs_hook=_no_duplicates)
    if not isinstance(value, dict):
        raise TypeError("top-level JSON value must be an object")
    return value
def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
def immutable_contract(card: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in card.items() if key not in LIFECYCLE_FIELDS}
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    ).stdout
def _git_exists(repo: Path, revision_path: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", revision_path],
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0
def collect_diff(repo: Path, base: str, head: str) -> Mapping[str, object]:
    try:
        names = _git(repo, "diff", "-z", "--name-status", "--find-renames", base, head).split("\0")
        stats = _git(repo, "diff", "-z", "--numstat", base, head).split("\0")
    except subprocess.CalledProcessError as error:
        raise ConfigurationError("GIT_DIFF: unable to collect change") from error
    paths: list[str] = []
    index = 0
    while index < len(names) and names[index]:
        status = names[index]
        index += 1
        path = names[index]
        if status.startswith(("R", "C")):
            index += 1
            path = names[index]
        paths.append(path)
        index += 1
    additions: dict[str, int] = {path: 0 for path in paths}
    binary_paths: list[str] = []
    index = 0
    while index < len(stats) and stats[index]:
        fields = stats[index].split("\t", 2)
        if len(fields) != 3:
            raise ConfigurationError(f"invalid git numstat output: {stats[index]}")
        added, _deleted, path = fields
        if not path:
            index += 1
            path = stats[index + 1]
            index += 1
        if added == "-":
            additions[path] = 0
            binary_paths.append(path)
        else:
            additions[path] = int(added)
        index += 1
    return {"paths": paths, "additions": additions, "binary_paths": tuple(binary_paths)}
def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"INVALID_METADATA: {label} must be an object")
    return value
def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ConfigurationError(f"INVALID_METADATA: {label} must be an array")
    return value
def _validate_path_pattern(pattern: object) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise ConfigurationError(f"INVALID_PATH_PATTERN: {pattern}")
    pure = Path(pattern)
    if pure.is_absolute() or pattern.startswith(("/", "\\")) or ".." in pure.parts or pattern in {"*", "**"}:
        raise ConfigurationError(f"INVALID_PATH_PATTERN: {pattern}")
    return pattern
def _load_metadata(repo: Path) -> tuple[dict[str, object], dict[str, object], dict[str, object], str]:
    def load(relative: str) -> dict[str, object]:
        try:
            return load_strict_json(repo / relative)
        except (OSError, TypeError, ValueError) as error:
            raise ConfigurationError(f"INVALID_JSON: {relative}: {error}") from error
    try:
        policy = load(".governance/policy.json")
        baseline = load(".governance/baseline.json")
        task_paths = sorted((repo / ".governance/tasks").glob("*.json"))
        cards = [(path, load(path.relative_to(repo).as_posix())) for path in task_paths]
    except OSError as error:
        raise ConfigurationError(f"INVALID_JSON: metadata: {error}") from error
    active = [(path, card) for path, card in cards if card.get("status") == "active"]
    if len(active) != 1:
        raise ConfigurationError(f"ACTIVE_TASKS: expected 1, found {len(active)}")
    path, card = active[0]
    _validate_metadata(policy, baseline, card)
    return policy, baseline, card, path.relative_to(repo).as_posix()
def _validate_metadata(
    policy: Mapping[str, object], baseline: Mapping[str, object], card: Mapping[str, object]
) -> None:
    policy_required = {"schema_version", "levels", "path_classification", "gates", "limits", "forbidden_artifacts", "approval"}
    baseline_required = {"schema_version", "baseline_commit", "baseline_tree", "historical_debt"}
    card_required = {
        "schema_version", "task_id", "status", "base_commit", "bootstrap", "candidate", "review_round", "level",
        "budgets", "allowed_paths", "conditional_allowed_paths", "gate_ids", "historical_debt_ids",
        "approval_ids",
    }
    for name, value, required in (
        ("policy", policy, policy_required), ("baseline", baseline, baseline_required),
        ("task", card, card_required),
    ):
        missing = required - set(value)
        if missing:
            raise ConfigurationError(f"INVALID_METADATA: {name} missing {min(missing)}")
    allowed = {
        "policy": policy_required, "baseline": baseline_required,
        "task": card_required | {"problem", "non_goals", "threat_model", "success_criteria", "stop_conditions"},
    }
    for name, value in (("policy", policy), ("baseline", baseline), ("task", card)):
        unknown = set(value) - allowed[name]
        if unknown:
            raise ConfigurationError(f"INVALID_METADATA: {name} unknown {min(unknown)}")
    if policy["schema_version"] != 1 or baseline["schema_version"] != 1 or card["schema_version"] != 1:
        raise ConfigurationError("INVALID_METADATA: schema_version")
    for field in ("task_id", "base_commit", "level"):
        if not isinstance(card[field], str):
            raise ConfigurationError(f"INVALID_METADATA: {field}")
    for field in ("bootstrap", "candidate"):
        if not isinstance(card[field], bool):
            raise ConfigurationError(f"INVALID_METADATA: {field}")
    if not isinstance(card["review_round"], int) or isinstance(card["review_round"], bool):
        raise ConfigurationError("INVALID_METADATA: review_round")
    for debt in _list(baseline["historical_debt"], "historical_debt"):
        debt_map = _mapping(debt, "historical debt")
        if set(debt_map) != {"id", "path", "description"} or not all(isinstance(value, str) for value in debt_map.values()):
            raise ConfigurationError("INVALID_METADATA: historical debt")
    classification = _mapping(policy["path_classification"], "path_classification")
    if set(classification) != {"tests", "documentation", "unknown"} or classification["unknown"] != "production":
        raise ConfigurationError("INVALID_METADATA: path_classification")
    for field in ("tests", "documentation"):
        if not all(isinstance(pattern, str) for pattern in _list(classification[field], field)):
            raise ConfigurationError(f"INVALID_METADATA: {field}")
    limits = _mapping(policy["limits"], "limits")
    if set(limits) != {"single_file_lines", "test_to_production_ratio", "max_review_round", "max_active_tasks"} or not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in limits.values()):
        raise ConfigurationError("INVALID_METADATA: limits")
    levels = _mapping(policy["levels"], "levels")
    if card["level"] not in levels:
        raise ConfigurationError(f"INVALID_LEVEL: {card['level']}")
    for pattern in [*_list(card["allowed_paths"], "allowed_paths"), *_list(card["conditional_allowed_paths"], "conditional_allowed_paths")]:
        _validate_path_pattern(pattern)
    gates = _mapping(policy["gates"], "gates")
    for gate_id in _list(card["gate_ids"], "gate_ids"):
        if not isinstance(gate_id, str):
            raise ConfigurationError("INVALID_METADATA: gate_ids item must be a string")
        if gate_id not in gates:
            raise ConfigurationError(f"UNKNOWN_GATE: {gate_id}")
    for gate_id, gate in gates.items():
        gate_map = _mapping(gate, f"gate {gate_id}")
        if (
            set(gate_map) != {"cwd", "steps", "timeout_seconds", "kind"}
            or not isinstance(gate_map["cwd"], str)
            or not isinstance(gate_map["kind"], str)
            or not isinstance(gate_map["steps"], list)
            or not gate_map["steps"]
            or not all(isinstance(step, str) and step for step in gate_map["steps"])
            or not isinstance(gate_map["timeout_seconds"], int)
            or isinstance(gate_map["timeout_seconds"], bool)
            or gate_map["timeout_seconds"] <= 0
        ):
            raise ConfigurationError(f"INVALID_METADATA: gate {gate_id}")
    for level_id, level in levels.items():
        level_map = _mapping(level, f"level {level_id}")
        if set(level_map) - {"budgets", "verification", "timeout_seconds", "requires_approval", "triggers"}:
            raise ConfigurationError(f"INVALID_METADATA: level {level_id}")
        if "budgets" in level_map:
            level_budgets = _mapping(level_map["budgets"], "level budgets")
            if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in level_budgets.values()):
                raise ConfigurationError("INVALID_METADATA: level budgets")
    card_budgets = _mapping(card["budgets"], "budgets")
    required_budgets = {
        "files", "production_lines", "test_lines", "documentation_lines", "migrations",
        "dependencies", "services", "containers", "routes", "auth_sources",
        "persistent_state", "network_boundaries",
    }
    if set(card_budgets) != required_budgets:
        raise ConfigurationError("INVALID_METADATA: budgets")
    for budget_name, budget in card_budgets.items():
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
            raise ConfigurationError(f"INVALID_METADATA: budget {budget_name}")
        ceiling = _mapping(_mapping(levels[card["level"]], "level").get("budgets", {}), "level budgets").get(budget_name, 0)
        if card["level"] != "L3" and budget > int(ceiling):
            raise ConfigurationError(f"BUDGET_CEILING: {budget_name}")
def _classify(policy: Mapping[str, object], path: str) -> str:
    classification = _mapping(policy["path_classification"], "path_classification")
    for kind in ("tests", "documentation"):
        patterns = _list(classification.get(kind, []), kind)
        if any(fnmatch.fnmatchcase(path, str(pattern)) for pattern in patterns):
            return kind
    return "production"
def _is_allowed(card: Mapping[str, object], path: str) -> bool:
    patterns = [*_list(card["allowed_paths"], "allowed_paths"), *_list(card["conditional_allowed_paths"], "conditional_allowed_paths")]
    return any(fnmatch.fnmatchcase(path, str(pattern)) for pattern in patterns)
def _direct_dependencies(repo: Path, revision: str, path: str) -> set[str]:
    if not _git_exists(repo, f"{revision}:{path}"):
        return set()
    try:
        content = _git(repo, "show", f"{revision}:{path}")
    except subprocess.CalledProcessError as error:
        raise ConfigurationError(f"DEPENDENCY_PARSE: {path}") from error
    try:
        if path.endswith("pyproject.toml"):
            document = tomllib.loads(content)
            project = document.get("project", {})
            dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
            optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
            groups = document.get("dependency-groups", {})
            values = list(dependencies)
            values.extend(item for group in optional.values() for item in group)
            values.extend(item for group in groups.values() for item in group)
            return {str(item).split()[0].split("[")[0].split("=")[0] for item in values}
        document = json.loads(content)
        dependencies = document.get("dependencies", {})
        dev_dependencies = document.get("devDependencies", {})
        return set(dependencies if isinstance(dependencies, dict) else ()) | set(dev_dependencies if isinstance(dev_dependencies, dict) else ())
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"DEPENDENCY_PARSE: {path}") from error
def _l3_triggers(repo: Path, base: str, head: str, paths: Sequence[str]) -> set[str]:
    triggers: set[str] = set()
    for path in paths:
        lowered = path.lower()
        if path.endswith(("pyproject.toml", "package.json")) and (
            _direct_dependencies(repo, head, path) - _direct_dependencies(repo, base, path)
        ):
            triggers.add("dependency")
        if any(word in lowered for word in ("alembic", "migration", "/models/", "models.py")):
            triggers.add("migration")
        if any(word in lowered for word in ("docker", "compose", "nginx", "deploy")):
            triggers.add("deployment_boundary")
        if any(word in lowered for word in ("auth", "actor", "security")):
            triggers.add("auth_source")
        if any(word in lowered for word in ("external", "webhook", "transport", "network", "io_")):
            triggers.add("network_boundary")
    return triggers
def _approval_status(
    repo: Path, card: Mapping[str, object], triggers: set[str], trigger_paths: Sequence[str]
) -> tuple[bool, str | None]:
    approval_ids = _list(card["approval_ids"], "approval_ids")
    if not approval_ids:
        return False, None
    expected = canonical_sha256(immutable_contract(card))
    for approval_id in approval_ids:
        if not isinstance(approval_id, str):
            return False, str(approval_id)
        try:
            approval = load_strict_json(repo / ".governance/approvals" / f"{approval_id}.json")
        except (OSError, TypeError, ValueError):
            return False, approval_id
        required = {"task_id", "contract_sha256", "rules", "reason", "paths", "expires"}
        if set(approval) - (required | {"approved_by"}) or required - set(approval):
            return False, approval_id
        rules = approval["rules"]
        paths = approval["paths"]
        if (
            not isinstance(approval["task_id"], str)
            or not isinstance(approval["contract_sha256"], str)
            or not isinstance(approval["reason"], str)
            or approval["expires"] != "merge"
            or not isinstance(rules, list)
            or not all(isinstance(rule, str) for rule in rules)
            or not isinstance(paths, list)
            or not all(isinstance(path, str) and _validate_path_pattern(path) for path in paths)
        ):
            return False, approval_id
        required_rules = triggers or {"L3"}
        covers_paths = all(any(fnmatch.fnmatchcase(path, pattern) for pattern in paths) for path in trigger_paths)
        if (
            approval["task_id"] == card["task_id"]
            and approval["contract_sha256"] == expected
            and required_rules <= set(rules)
            and covers_paths
        ):
            return True, approval_id
        return False, approval_id
    return False, None
def check(repo: Path, base: str, head: str) -> CheckResult:
    repo = repo.resolve()
    try:
        ancestor = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, head],
            text=True, capture_output=True, check=False,
        )
    except OSError as error:
        raise ConfigurationError("GIT_REPOSITORY: unavailable") from error
    if ancestor.returncode != 0:
        raise ConfigurationError(f"BASE_NOT_ANCESTOR: {base}")
    policy, baseline, card, _card_path = _load_metadata(repo)
    if card["base_commit"] != base:
        raise ConfigurationError(f"TASK_BASE_MISMATCH: {card['base_commit']}")
    baseline_commit = str(baseline["baseline_commit"])
    if not _git_exists(repo, baseline_commit):
        raise ConfigurationError("BASELINE_COMMIT_MISSING")
    try:
        baseline_tree = _git(repo, "rev-parse", f"{baseline_commit}^{{tree}}").strip()
    except subprocess.CalledProcessError as error:
        raise ConfigurationError("BASELINE_COMMIT_MISSING") from error
    if baseline["baseline_tree"] != baseline_tree:
        raise ConfigurationError("BASELINE_TREE_MISMATCH")
    base_has_policy = _git_exists(repo, f"{base}:.governance/policy.json")
    base_has_baseline = _git_exists(repo, f"{base}:.governance/baseline.json")
    if card["bootstrap"] is True and (base_has_policy or base_has_baseline):
        raise ConfigurationError("BOOTSTRAP_REUSED: policy exists at base")
    if card["bootstrap"] is not True and (not base_has_policy or not base_has_baseline):
        raise ConfigurationError("BOOTSTRAP_REQUIRED: policy absent at base")
    diff = collect_diff(repo, base, head)
    paths = [str(path) for path in diff["paths"]]
    additions = _mapping(diff["additions"], "additions")
    errors: list[str] = []
    warnings: list[str] = [f"BINARY_FILE: {path}" for path in diff["binary_paths"]]
    for path in paths:
        if any(fnmatch.fnmatchcase(path, str(pattern)) for pattern in _list(policy["forbidden_artifacts"], "forbidden_artifacts")):
            errors.append(f"FORBIDDEN_ARTIFACT: {path}")
        if not _is_allowed(card, path):
            errors.append(f"SCOPE_PATH: {path}")
    non_governance = [path for path in paths if not path.startswith(GOVERNANCE_PREFIX)]
    metrics = {"files": len(non_governance), "production_lines": 0, "test_lines": 0, "documentation_lines": 0}
    for path in non_governance:
        added = int(additions.get(path, 0))
        metric_key = {"tests": "test_lines", "documentation": "documentation_lines"}.get(
            _classify(policy, path), "production_lines"
        )
        metrics[metric_key] += added
        if added > int(_mapping(policy["limits"], "limits")["single_file_lines"]):
            warnings.append(f"WARNING_SINGLE_FILE_LINES: {path} has {added} lines")
    budgets = _mapping(card["budgets"], "budgets")
    for key in ("files", "production_lines", "test_lines", "documentation_lines"):
        if metrics[key] > int(budgets[key]):
            errors.append(f"BUDGET_{key.upper()}: {metrics[key]} > {budgets[key]}")
    ratio = int(_mapping(policy["limits"], "limits")["test_to_production_ratio"])
    if metrics["test_lines"] > metrics["production_lines"] * ratio:
        warnings.append(f"WARNING_TEST_RATIO: {metrics['test_lines']} > {metrics['production_lines'] * ratio}")
    limits = _mapping(policy["limits"], "limits")
    if int(card["review_round"]) > int(limits["max_review_round"]):
        raise ConfigurationError(f"REVIEW_ROUND: {card['review_round']} > {limits['max_review_round']}")
    debts = _list(baseline["historical_debt"], "historical_debt")
    declared_debts = {str(item) for item in _list(card["historical_debt_ids"], "historical_debt_ids")}
    for debt in debts:
        debt_map = _mapping(debt, "historical debt")
        debt_path = str(debt_map["path"]).rstrip("/")
        if (
            any(path == debt_path or path.startswith(f"{debt_path}/") for path in paths)
            and str(debt_map["id"]) not in declared_debts
        ):
            errors.append(f"HISTORICAL_DEBT: {debt_map['id']} requires disposition")
    triggers = _l3_triggers(repo, base, head, paths)
    trigger_paths = [
        path for path in paths
        if any(word in path.lower() for word in ("alembic", "migration", "models", "docker", "compose", "nginx", "deploy", "auth", "actor", "security", "external", "webhook", "transport", "network", "io_"))
    ]
    approval_matches, approval_id = _approval_status(repo, card, triggers, trigger_paths)
    needs_l3 = bool(triggers) or card["level"] == "L3"
    if triggers and card["level"] != "L3":
        errors.extend(f"L3_TRIGGER: {trigger}" for trigger in sorted(triggers))
    if needs_l3 and card["level"] == "L3" and not approval_matches:
        errors.append(f"APPROVAL_MISMATCH: {approval_id}" if approval_id else "APPROVAL_REQUIRED")
    if not errors and needs_l3 and approval_matches:
        warnings.append(str(_mapping(policy["approval"], "approval")["local_status"]))
    return CheckResult(tuple(errors), tuple(warnings), metrics)
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--platform-owner-enforced", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = check(args.repo, args.base, args.head)
    except ConfigurationError as error:
        print(json.dumps({"errors": [str(error)], "warnings": [], "metrics": {}}))
        return 3
    print(json.dumps({"errors": list(result.errors), "warnings": list(result.warnings), "metrics": result.metrics}, sort_keys=True))
    if result.errors:
        return 2
    if "WAITING_FOR_OWNER_VERIFICATION" in result.warnings and not args.platform_owner_enforced:
        return 4
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
