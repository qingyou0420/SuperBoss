---
name: kimi-superboss
description: Synchronize completed Kimi Work K3 document results to SuperBoss through the secure local connector.
---

# Kimi to SuperBoss

Communicate with the OWNER in the language they use. Use this workflow when the OWNER wants a
completed Kimi Work document result synchronized to SuperBoss.

## Workflow

1. Finish the document work before offering synchronization.
2. Create the strict local manifest and generate one idempotency key for this submission.
3. Present the final OWNER preview with every required field and count.
4. Ask for fresh explicit confirmation after that preview.
5. Run the approved submit command only after confirmation.
6. Report the connector job ID and status as the evidence of the action.
7. Follow the recovery table when the connector returns a nonzero exit code.

## Manifest

Create a UTF-8 JSON file with exactly these root fields:

- `idempotency_key`
- `project_id`
- `local_task_id`
- `external_document_reference`
- `base_sha256`
- `k3_result`
- `attachments`

Create `k3_result` with exactly `model_label`, timezone-aware `processed_at`,
`modification_details`, `knowledge_points`, `risks`, `suggested_title`, and `suggested_tags`.

Create one to three attachment entries. Give each entry exactly `kind`, `path`, and
`content_type`. Use unique kinds and exactly one `K3_RAW`. Use a safe relative attachment path
from the manifest directory: keep it inside that directory and omit drive letters, leading
slashes, and `..` components. Let the connector derive filename, size, and SHA-256.

When `base_sha256` is not null, include exactly one attachment with kind `ORIGINAL`. When it is
null, `ORIGINAL` is optional.

Generate `idempotency_key` once as `kimi-` followed by a fresh UUIDv7 or UUIDv4. Keep that exact
key stable only for this submission and an exit 6 submission retry whose manifest content is
unchanged. For exit 4 or any new submission, create a new manifest and a fresh UUID key because the
connector has discarded the old recovery state.

## Preview

Show a final preview containing:

- project ID;
- attachment kind, relative path, and content type for every attachment;
- modification count;
- knowledge-point count;
- risk count and the risks;
- proposed idempotency key.

Keep this preview concrete enough for the OWNER to decide exactly what will leave the workstation.

## Confirmation

After showing the final preview, ask for explicit OWNER confirmation immediately before submit.
Silence or no response does not count as confirmation. Earlier or prior general approval does not
count for the final preview. If the manifest, attachments, project, or key changes, show the updated
preview and obtain fresh confirmation.

## CLI surface

Use only these connector commands and option shapes:

```powershell
superboss pair --server <ORIGIN> --code <ONE_TIME_CODE> --name "OWNER-PC"
superboss submit --server <ORIGIN> --manifest <MANIFEST_PATH>
superboss status --server <ORIGIN> --job-id <JOB_ID>
superboss retry --server <ORIGIN>
```

For pairing, direct the OWNER to enter the one-time code locally in the exact pair command. Kimi
does not request, read, repeat, store, or echo that code.

## Status

Use connector output as evidence. Report the returned job ID and status without adding an inferred
business outcome. Treat `SCANNING` as a pending safety scan; it is not proof of archive completion.
Report completion or archive state only when later SuperBoss evidence explicitly provides it.

## Recovery

### Pairing failure

If `superboss pair` returns any nonzero exit or pairing remains incomplete, direct the OWNER to
enter a current valid one-time code locally in this exact command:

```powershell
superboss pair --server <ORIGIN> --code <ONE_TIME_CODE> --name "OWNER-PC"
```

Kimi does not request, read, receive, repeat, store, or echo the actual code.
Never use the `superboss retry` command to recover pairing.

| Exit | Action |
|---:|---|
| 2 | Correct the local input, manifest, or recovery-state ambiguity, then show the resulting preview again. |
| 3 | Pair the device again or inspect device revocation with the OWNER. |
| 4 | Create a new manifest and fresh UUID idempotency key, then repeat the preview and confirmation. |
| 5 | Inspect and resolve the stable server rejection before proposing another action. |
| 6 | For submission or recovery state only, keep the existing manifest and key, then offer `superboss retry --server <ORIGIN>`. |

## Security

Keep API access and refresh credentials in the connector-managed credential store. Keep those
credentials out of the manifest, chat, command arguments, ordinary files, and logs. The one-time
pairing code is the only command-argument exception: the OWNER enters its actual value directly in
the local terminal, and Kimi does not receive or echo it. Use only connector output as action
evidence; state that a submission occurred only after the connector returns a job ID and status.

## Example

Save this compact example as `manifest.json` beside the relative `exports` directory:

```json
{
  "idempotency_key": "kimi-0198d7f3-bd92-7a31-9f42-3e6a76b9f810",
  "project_id": "0198d7f2-4f60-7cf2-a629-1e93242c93f1",
  "local_task_id": "proposal-revision-001",
  "external_document_reference": "customer-proposal-001",
  "base_sha256": null,
  "k3_result": {
    "model_label": "K3",
    "processed_at": "2026-08-10T09:54:00Z",
    "modification_details": [
      "Updated the staged payment milestones."
    ],
    "knowledge_points": [
      "The customer requires staged acceptance."
    ],
    "risks": [
      "Final legal review is pending."
    ],
    "suggested_title": "Customer proposal revision",
    "suggested_tags": [
      "customer",
      "proposal"
    ]
  },
  "attachments": [
    {
      "kind": "K3_RAW",
      "path": "exports/k3-result.json",
      "content_type": "application/json"
    }
  ]
}
```

Preview project `0198…93f1`, the `K3_RAW` attachment, modification count 1, knowledge-point count
1, risk count 1 with its risk, and key `kimi-0198d7f3-bd92-7a31-9f42-3e6a76b9f810`. After fresh
confirmation, run:

```powershell
superboss submit --server https://nightforest.example --manifest manifest.json
```
