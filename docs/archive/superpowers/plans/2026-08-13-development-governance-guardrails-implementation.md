# Development Governance Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small, repository-owned governance gate that rejects undeclared scope and boundary expansion, selects proportionate verification, and prevents duplicate full-suite runs.

**Architecture:** Two Python-standard-library CLIs read strict JSON policy/task metadata and inspect Git diffs. `governance_check.py` owns scope, budget, boundary, debt, approval, and review-round decisions; `verify_changed.py` maps the approved change to fixed commands and caches evidence outside the worktree. A single GitHub workflow can enforce the same entry point after a real remote and OWNER identity exist; until then, remote enforcement is explicitly `NOT_CONFIGURED`.

**Tech Stack:** Python 3.13 standard library, Git CLI, JSON Schema 2020-12 as an editor contract, pytest for repository tests, GitHub Actions only after remote activation.

## Global Constraints

- Implementation level is L2: at most 20 files, 700 production lines, 1000 test lines, and 300 documentation lines.
- Add no runtime/development package, service, container, route, migration, table, authentication source, persistent application state, or network boundary.
- Historical baseline is commit `51cd8491fa593eb3095684d7528ecea6d1dc17de`, tree `e84f5e49c2a374c6ac11fb5e74d231d6cfa993cd`; historical debt is non-blocking when untouched.
- JSON parsing rejects duplicate keys and unknown fields; paths are repository-relative POSIX paths with no absolute form or `..`.
- Task cards may reference fixed gate IDs, never shell commands.
- Local approval validation cannot prove OWNER identity. An approval added in the same change is pending, not authoritative.
- Do not create a fake `CODEOWNERS` identity. Remote enforcement remains `NOT_CONFIGURED` until an actual GitHub remote and `GOVERNANCE_OWNER_HANDLE` are verified.
- Run one implementer self-review, one independent review, and one backend/web/connector full gate per immutable merge candidate. Do not add a third review without new reproducible in-scope evidence and OWNER approval.

---

## File Map

- Create four metadata files under `.governance/`: policy, baseline, schema, and this implementation's active task card.
- Create `scripts/governance_check.py` and `scripts/verify_changed.py`: decision engine plus gate/evidence runner.
- Create two matching tests under `server/tests/unit/governance/`: isolated Git-policy tests and fake-command cache tests.
- Create `.github/workflows/governance.yml`; create `.github/CODEOWNERS` only after its real handle passes remote validation.
- Create `docs/runbooks/development-governance.md`; modify `README.md` only to link it.

### Task 1: Bootstrap strict governance metadata

**Files:**
- Create: `.governance/policy.json`
- Create: `.governance/baseline.json`
- Create: `.governance/task-card.schema.json`
- Create: `.governance/tasks/development-governance-guardrails.json`
- Create: `server/tests/unit/governance/test_governance_check.py`

**Interfaces:**
- Produces: schema version 1, levels `L0`–`L3`, fixed gate IDs, baseline debt IDs, and exactly one `status: "active"` task.
- Consumes: the implementation-plan commit as the task card's full 40-character `base_commit`.

- [ ] **Step 1: Record the immutable implementation base**

Run `git rev-parse HEAD` after this plan is committed. Put that exact SHA in the task card; do not use the historical baseline SHA as the task base.

- [ ] **Step 2: Write failing metadata contract tests**

Create pytest tests that load all four JSON files with a duplicate-key rejecting `object_pairs_hook`, then assert:

```python
assert policy["schema_version"] == 1
assert set(policy["levels"]) == {"L0", "L1", "L2", "L3"}
assert baseline["baseline_commit"] == "51cd8491fa593eb3095684d7528ecea6d1dc17de"
assert card["level"] == "L2"
assert card["budgets"] == {
    "files": 20, "production_lines": 700, "test_lines": 1000, "documentation_lines": 300,
    "migrations": 0, "dependencies": 0, "services": 0, "containers": 0, "routes": 0,
    "auth_sources": 0, "persistent_state": 0, "network_boundaries": 0,
}
assert card["approval_ids"] == []
```

Also assert every schema object has `additionalProperties: false`, every success criterion has a stable `SC-<number>` ID, and all referenced gate/debt IDs exist.

- [ ] **Step 3: Run RED**

From `server`, run:

```powershell
C:\Users\Administrator\.local\bin\uv.exe run pytest -q tests/unit/governance/test_governance_check.py -k metadata
```

Expected: FAIL because the governance files do not exist.

- [ ] **Step 4: Add the minimal JSON files**

Use the approved L0/L1/L2 budgets and L3 triggers from the design. Classify tests before docs before production; unknown tracked paths count as production. Store the eight narrow historical debt entries from the design. Define fixed gates for governance, backend, web, connector, E2E-contract, compose, and Windows packaging; each gate contains only `cwd`, an argv-array `steps`, `timeout_seconds`, and `kind`.

The active task card must declare `bootstrap: true`, `candidate: false`, `review_round: 1`, `problem`, `non_goals`, all five `threat_model` arrays, the eleven unconditional paths in the File Map, optional `.github/CODEOWNERS`, success criteria for scope rejection, approval scoping, historical-baseline tolerance, proportional gates, evidence reuse, and no third-party governance dependency.

- [ ] **Step 5: Run GREEN and commit metadata**

Repeat the focused test, run `git diff --check`, then commit with:

```powershell
git add .governance server/tests/unit/governance/test_governance_check.py
git commit -m "chore(governance): define policy metadata"
```

### Task 2: Enforce task scope, budgets, boundaries, and approvals

**Files:**
- Create: `scripts/governance_check.py`
- Modify: `server/tests/unit/governance/test_governance_check.py`

**Interfaces:**
- Produces: `CheckResult(errors, warnings, metrics)` and CLI exit 0 for pass, 2 for policy failure, 3 for invalid configuration/environment, 4 for structurally valid but externally unverified OWNER approval.
- Consumes: `--repo`, `--base`, `--head`, and the one active task discovered under `.governance/tasks`.

- [ ] **Step 1: Add temporary-Git-repository RED tests**

Use `tmp_path` and `subprocess.run(["git", ...], check=True)` to build isolated repositories. Cover exact failures for: two active cards, non-ancestor base, path outside `allowed_paths`, file/line budget overflow, dependency addition, migration/deploy/auth L3 trigger, touched debt without disposition, review round 3, unknown gate, duplicate JSON key, absolute/parent/standalone-wildcard path, forbidden artifact, mismatched approval, and reuse of `bootstrap` after policy exists at base. Cover passes for the one-time bootstrap and untouched debt. A structurally matching approval returns 4 locally and 0 only with `--platform-owner-enforced`. Assert metrics and the ratio/800-line warnings.

```python
result = run_check(repo, base=base_sha, head="HEAD")
assert result.returncode == 2
payload = json.loads(result.stdout)
assert payload["errors"] == ["SCOPE_PATH: outside.txt"]
```

- [ ] **Step 2: Run RED**

```powershell
C:\Users\Administrator\.local\bin\uv.exe run pytest -q tests/unit/governance/test_governance_check.py -k "scope or budget or approval or boundary"
```

Expected: FAIL because the CLI is absent.

- [ ] **Step 3: Implement the minimal checker**

Implement these stable types/functions with only `argparse`, `dataclasses`, `fnmatch`, `hashlib`, `json`, `pathlib`, `subprocess`, and `tomllib`:

```python
@dataclass(frozen=True)
class CheckResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: Mapping[str, int]
def load_strict_json(path: Path) -> dict[str, object]: ...
def canonical_sha256(value: object) -> str: ...
def collect_diff(repo: Path, base: str, head: str) -> Mapping[str, object]: ...
def check(repo: Path, base: str, head: str) -> CheckResult: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Use `git diff --name-status --find-renames` and `git diff --numstat`; count added lines, count a rename once, and count binary files with zero lines plus a declaration warning. Parse direct dependency names from `pyproject.toml` and `package.json`. Treat compose/Docker/Nginx, migrations/models, auth/actor/security, and external-I/O paths as conservative L3 triggers; do not add AST or YAML parsing.

Allow `bootstrap: true` only when policy/baseline are absent at base; governance metadata is always schema-checked but excluded from line budgets. Approval matching uses a canonical hash of immutable task contract fields, not lifecycle fields. Without `--platform-owner-enforced`, a matching approval emits `WAITING_FOR_OWNER_VERIFICATION` and exits 4; the flag is valid only in the CODEOWNERS-protected workflow. Never trust `approved_by` locally.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
C:\Users\Administrator\.local\bin\uv.exe run pytest -q tests/unit/governance/test_governance_check.py
C:\Users\Administrator\.local\bin\uv.exe run ruff check ..\scripts tests/unit/governance
```

- [ ] **Step 5: Commit the checker**

```powershell
git add scripts/governance_check.py server/tests/unit/governance/test_governance_check.py
git commit -m "feat(governance): enforce declared change scope"
```

### Task 3: Select proportionate gates and reuse evidence

**Files:**
- Create: `scripts/verify_changed.py`
- Create: `server/tests/unit/governance/test_verify_changed.py`

**Interfaces:**
- Produces: `selected_gates(policy, card, changed_paths, mode)` and evidence files under `git rev-parse --git-path governance-evidence`.
- Consumes: policy gate argv arrays; modes `affected`, `candidate`, and `auto`.

- [ ] **Step 1: Write RED tests with harmless fake commands**

Build temporary policies whose gates invoke `python -c` and append gate names to a temp log. Assert docs-only selects governance gates; web selects only web focused/static; deploy selects compose; candidate selects backend/web/connector full exactly once. Assert a second identical run leaves the log unchanged and returns `REUSED`; a changed tree or canonical task-card SHA creates a new key.

```python
key = evidence_key(tree_sha="a" * 40, card_sha="b" * 64, gate="web-full")
assert key == hashlib.sha256(f"{'a' * 40}\0{'b' * 64}\0web-full".encode()).hexdigest()
```

Also assert arbitrary task-card commands are rejected, timeout returns failure without a green record, and `candidate` refuses review round 3 or `candidate: false`.

- [ ] **Step 2: Run RED**

```powershell
C:\Users\Administrator\.local\bin\uv.exe run pytest -q tests/unit/governance/test_verify_changed.py
```

- [ ] **Step 3: Implement minimal selection and execution**

Implement:

```python
@dataclass(frozen=True)
class GateResult:
    gate_id: str
    status: str
    returncode: int
    duration_ms: int
def selected_gates(policy: Mapping[str, object], card: Mapping[str, object], paths: tuple[str, ...], mode: str) -> tuple[str, ...]: ...
def evidence_key(tree_sha: str, card_sha: str, gate: str) -> str: ...
def run_gate(repo: Path, gate_id: str, gate: Mapping[str, object], timeout: int) -> GateResult: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Always run the checker first. Store only gate ID, hashes, argv digest, timestamps, duration, return code, and status—never stdout/stderr or secrets. Reuse an existing green key. Failed/environment-interrupted runs have no green cache, so they may rerun; a green same-key rerun is allowed only with a base-present OWNER approval. Apply L1/L2/L3 time budgets from policy.

- [ ] **Step 4: Run GREEN and commit**

```powershell
C:\Users\Administrator\.local\bin\uv.exe run pytest -q tests/unit/governance
C:\Users\Administrator\.local\bin\uv.exe run ruff check ..\scripts tests/unit/governance
git add scripts/verify_changed.py server/tests/unit/governance/test_verify_changed.py
git commit -m "feat(governance): select and reuse verification gates"
```

### Task 4: Wire documentation and prepared CI without false guarantees

**Files:**
- Create: `.github/workflows/governance.yml`
- Create after verified remote identity only: `.github/CODEOWNERS`
- Create: `docs/runbooks/development-governance.md`
- Modify: `README.md`
- Modify: `server/tests/unit/governance/test_governance_check.py`

**Interfaces:**
- Produces: one `governance` workflow with `concurrency.cancel-in-progress: true`, plus an activation runbook.
- Consumes: both CLIs and a verified `GOVERNANCE_OWNER_HANDLE`.

- [ ] **Step 1: Write failing repository-contract tests**

Assert the workflow runs governance before verification, uses full Git history, cancels stale branch runs, and requires a candidate card for a non-draft PR. Assert the runbook says `REMOTE_ENFORCEMENT=NOT_CONFIGURED` when `git remote get-url origin` or `GOVERNANCE_OWNER_HANDLE` is absent, and forbids creating CODEOWNERS in that state.

- [ ] **Step 2: Run RED, then add the minimal workflow and runbook**

Run the governance tests and confirm failure. Add a workflow using `actions/checkout@v4` with `fetch-depth: 0`, `actions/setup-python@v5` for Python 3.13, `actions/setup-node@v4` for Node 24, and `actions/cache@v4` for the exact `.git/governance-evidence` directory. Install `uv==0.12.3` as a CI tool without changing project manifests. Call the governance unit tests, `governance_check.py`, then `verify_changed.py`; use `affected` for a draft PR/push and `candidate` for a ready PR, so a ready PR with `candidate: false` fails. Pass `--platform-owner-enforced` only after CODEOWNERS/required-review activation. Use one concurrency group per workflow/ref and no matrix. The runbook must include the four finding classes, review stop rules, historical-debt disposition, metrics, one final summary, evidence reuse, and the rule that semantic coverage remains reviewer judgment rather than a claimed machine proof.

The activation procedure must validate the handle with GitHub before writing:

```powershell
gh api "users/$env:GOVERNANCE_OWNER_HANDLE" --silent
git remote get-url origin
```

Only after both succeed, create CODEOWNERS entries for `.governance/policy.json`, `.governance/baseline.json`, `.governance/approvals/`, and `.github/workflows/governance.yml`, then enable the `governance` required check and Code Owner review through GitHub settings. If either fails, leave CODEOWNERS absent and report `NOT_CONFIGURED`.

- [ ] **Step 3: Run GREEN and commit documentation/CI preparation**

```powershell
C:\Users\Administrator\.local\bin\uv.exe run pytest -q tests/unit/governance
C:\Users\Administrator\.local\bin\uv.exe run ruff check ..\scripts tests/unit/governance
git diff --check
git add .github/workflows/governance.yml docs/runbooks/development-governance.md README.md server/tests/unit/governance/test_governance_check.py
git commit -m "ci: add development governance gate"
```

Do not stage `.github/CODEOWNERS` unless the remote/handle checks actually passed.

### Task 5: Exercise the guardrail and freeze one candidate

**Files:**
- Modify only if a test exposes an in-scope defect in files from Tasks 1–4.

**Interfaces:**
- Produces: one reviewed immutable candidate and one evidence record per full subsystem gate.
- Consumes: the active task card's success criteria and stop conditions.

- [ ] **Step 1: Run the implementation's affected gate**

```powershell
$card = Get-Content -Raw .governance/tasks/development-governance-guardrails.json | ConvertFrom-Json
python scripts/verify_changed.py --base $card.base_commit --head HEAD --mode affected
```

Expected: governance tests/static gates pass; backend/web/connector full suites do not run yet.

- [ ] **Step 2: Perform exactly one implementer self-review**

Check every design requirement against a task and inspect actual/approved files and line counts. Confirm no unresolved marker, third-party dependency, arbitrary command field, fake approval, fake remote status, or semantic-analysis claim exists.

- [ ] **Step 3: Request exactly one independent review**

The reviewer may run the governance suite and at most three adversarial probes: outside-path change, same-diff approval, and repeated candidate gate. Fix only reproducible Critical/Important findings within this task; rerun focused and affected gates after a code change. Minor/hardening suggestions go to backlog.

- [ ] **Step 4: Mark and commit the immutable candidate**

Set the active card's `candidate` to `true` and `review_round` to `2`, verify its stop-condition references, then commit:

```powershell
git add .governance/tasks/development-governance-guardrails.json
git commit -m "chore(governance): freeze guardrail candidate"
```

- [ ] **Step 5: Run candidate gates once**

```powershell
$card = Get-Content -Raw .governance/tasks/development-governance-guardrails.json | ConvertFrom-Json
python scripts/verify_changed.py --base $card.base_commit --head HEAD --mode candidate
python scripts/verify_changed.py --base $card.base_commit --head HEAD --mode candidate --dry-run
```
Expected: backend, web, and connector full gates each pass once; the dry run reports the same evidence keys as `REUSED` and executes nothing. Docker, live E2E, Windows packaging, and migrations remain unselected because this L2 task changes none of those boundaries.

- [ ] **Step 6: Final evidence and remote-status handoff**

Run `git diff --check`, confirm tracked status is clean, and report actual metrics, gate durations, one self-review, one independent review, and either `REMOTE_ENFORCEMENT=ACTIVE` with verified repository/handle or `REMOTE_ENFORCEMENT=NOT_CONFIGURED`. Do not claim branch protection is active from repository files alone.
