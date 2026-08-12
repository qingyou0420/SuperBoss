"""Check a declared governance task against a Git change."""

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
    """The repository cannot provide a valid governance contract."""


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_strict_json(path: Path) -> dict[str, object]:
    """Load a JSON object while refusing ambiguous duplicate keys."""
    with path.open(encoding="utf-8") as source:
        value = json.load(source, object_pairs_hook=_no_duplicates)
    if not isinstance(value, dict):
        raise TypeError("top-level JSON value must be an object")
    return value


def canonical_sha256(value: object) -> str:
    """Return the stable JSON digest used to bind an approval to a contract."""
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def immutable_contract(card: Mapping[str, object]) -> dict[str, object]:
    """Drop state-machine fields that must not invalidate an approval."""
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


def _path_from_name_status(line: str) -> str:
    fields = line.split("\t")
    if len(fields) < 2:
        raise ConfigurationError(f"invalid git name-status output: {line}")
    return fields[-1]


def collect_diff(repo: Path, base: str, head: str) -> Mapping[str, object]:
    """Collect changed paths and added-line counts from the two required Git views."""
    try:
        names = _git(repo, "diff", "--name-status", "--find-renames", base, head).splitlines()
        stats = _git(repo, "diff", "--numstat", base, head).splitlines()
    except subprocess.CalledProcessError as error:
        raise ConfigurationError("GIT_DIFF: unable to collect change") from error

    paths = tuple(_path_from_name_status(line) for line in names if line)
    additions: dict[str, int] = {path: 0 for path in paths}
    binary_paths: list[str] = []
    for line in stats:
        fields = line.split("\t")
        if len(fields) != 3:
            raise ConfigurationError(f"invalid git numstat output: {line}")
        added, _deleted, path = fields
        if added == "-":
            additions[path] = 0
            binary_paths.append(path)
        else:
            additions[path] = int(added)
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
    if pure.is_absolute() or pattern.startswith(("/", "\\")) or ".." in pure.parts or pattern == "*":
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
    policy_required = {"levels", "path_classification", "gates", "limits", "forbidden_artifacts", "approval"}
    baseline_required = {"historical_debt"}
    card_required = {
        "task_id", "status", "base_commit", "bootstrap", "candidate", "review_round", "level",
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
    levels = _mapping(policy["levels"], "levels")
    if card["level"] not in levels:
        raise ConfigurationError(f"INVALID_LEVEL: {card['level']}")
    for pattern in [*_list(card["allowed_paths"], "allowed_paths"), *_list(card["conditional_allowed_paths"], "conditional_allowed_paths")]:
        _validate_path_pattern(pattern)
    gates = _mapping(policy["gates"], "gates")
    for gate_id in _list(card["gate_ids"], "gate_ids"):
        if gate_id not in gates:
            raise ConfigurationError(f"UNKNOWN_GATE: {gate_id}")


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
            return {str(item).split()[0].split("[")[0].split("=")[0] for item in dependencies}
        document = json.loads(content)
        dependencies = document.get("dependencies", {})
        return set(dependencies) if isinstance(dependencies, dict) else set()
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


def _approval_status(repo: Path, card: Mapping[str, object]) -> tuple[bool, str | None]:
    approval_ids = _list(card["approval_ids"], "approval_ids")
    if not approval_ids:
        return False, None
    expected = canonical_sha256(immutable_contract(card))
    for approval_id in approval_ids:
        if not isinstance(approval_id, str):
            return False, str(approval_id)
        try:
            approval = load_strict_json(repo / ".governance/approvals" / f"{approval_id}.json")
        except (OSError, ValueError):
            return False, approval_id
        if approval.get("task_id") == card["task_id"] and approval.get("contract_sha256") == expected:
            return True, approval_id
        return False, approval_id
    return False, None


def check(repo: Path, base: str, head: str) -> CheckResult:
    """Return all local governance findings for the requested Git change."""
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
    if metrics["production_lines"] and metrics["test_lines"] < metrics["production_lines"] * ratio:
        warnings.append(f"WARNING_TEST_RATIO: {metrics['test_lines']} < {metrics['production_lines'] * ratio}")

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
    approval_matches, approval_id = _approval_status(repo, card)
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
