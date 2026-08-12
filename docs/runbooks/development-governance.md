# Development governance

This runbook operates the repository's change-contract checks. It prepares CI; it does not claim
that GitHub branch protection, Code Owner review, or OWNER identity enforcement is active.

## Run the local gate

Read the active task card's `base_commit`, then run the contract check before proportional
verification:

```powershell
$base = (Get-Content .governance/tasks/development-governance-guardrails.json | ConvertFrom-Json).base_commit
C:\Users\Administrator\.local\bin\uv.exe run --project server python scripts/governance_check.py --repo . --base $base --head HEAD
C:\Users\Administrator\.local\bin\uv.exe run --project server python scripts/verify_changed.py --repo . --base $base --head HEAD --mode affected
```

Use `--mode candidate` only for a frozen task card with `candidate: true` and a permitted review
round. A ready, non-draft pull request selects candidate verification in CI; a card with
`candidate: false` therefore fails rather than being silently treated as an affected change.

## Interpret findings and stop rules

Treat findings as four classes:

1. **Configuration failure**: malformed metadata, an invalid base, or a missing declared gate.
   Correct the contract before continuing.
2. **Policy failure**: scope, budget, boundary, approval, or forbidden-artifact error. Stop and
   narrow the change or obtain the specific required OWNER approval.
3. **Warning**: a binary file, oversized file, excessive test ratio, or local owner-verification
   wait. Record the rationale; a warning is not a machine proof of acceptance.
4. **Review finding**: Critical and Important findings block the task; Minor findings that do not
   block the declared delivery go to the backlog rather than expanding this task.

Stop expansion only when every success criterion passes, no Critical or Important finding remains,
one implementer self-review and one independent review are complete, and candidate verification
meets the task's declared plan. A third review round needs new reproducible in-scope evidence and
OWNER approval.

Historical debt recorded in `.governance/baseline.json` does not block unrelated work. When a
changed path touches a recorded debt item, the task card must declare its disposition: maintain,
reduce, or remove it.

The final task summary records actual and approved file counts; production, test, and documentation
line deltas; boundary/dependency changes; selected gate names and durations; evidence reuse; review
round; open Critical/Important findings; and new backlog items. Semantic coverage remains reviewer
judgment, not a machine-proof claim.

## Evidence reuse

Successful gate evidence is keyed by the code-tree SHA, task-card SHA, and gate name in
`.git/governance-evidence`. Reuse matching green evidence. Rerun only after a tree/card change, a
tool or environment failure, flaky-test investigation, or a recorded OWNER-approved reason.

## Activate remote enforcement

First verify both the GitHub OWNER handle and repository remote:

```powershell
gh api "users/$env:GOVERNANCE_OWNER_HANDLE" --silent
git remote get-url origin
```

If either command fails, or `GOVERNANCE_OWNER_HANDLE` is absent, report
`REMOTE_ENFORCEMENT=NOT_CONFIGURED`. Do not create `.github/CODEOWNERS`, do not pass
`--platform-owner-enforced`, and do not claim branch protection is active in that state.

Only after both commands succeed may the platform owner create `.github/CODEOWNERS` with entries
for `.governance/policy.json`, `.governance/baseline.json`, `.governance/approvals/`, and
.github/workflows/governance.yml using the verified handle. Then enable the `governance` required
check and Code Owner review in GitHub settings. After those settings are active, update the workflow
invocation to include `--platform-owner-enforced`; repository files alone do not prove activation.
