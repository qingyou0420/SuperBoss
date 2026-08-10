# Kimi connector installation and operation

The connector stores its rotating refresh credential in the operating-system keyring. It never
belongs in a manifest, command transcript, chat, or ordinary file.

## Verify and build on Windows

```powershell
Push-Location integrations/kimi-superboss/connector
uv sync --locked --group dev
uv run pytest -v
uv run ruff check .
uv run mypy src
Pop-Location
powershell -ExecutionPolicy Bypass -File integrations/kimi-superboss/scripts/build-windows.ps1
& 'integrations/kimi-superboss/connector/dist/superboss.exe' --help
```

Copy the verified executable and `integrations/kimi-superboss/SKILL.md` to the OWNER workstation by
the approved internal distribution channel. Record the build commit and checksum; do not send a
pre-paired executable or credential store.

## Pair and submit

In the OWNER browser, create a pairing code for the intended projects. The OWNER enters the actual
one-time code locally; nobody pastes it into chat or a runbook.

```powershell
superboss pair --server https://<APP_HOST> --code <ONE_TIME_CODE> --name "<DEVICE_NAME>"
superboss submit --server https://<APP_HOST> --manifest <MANIFEST_PATH>
superboss status --server https://<APP_HOST> --job-id <IMPORT_JOB_ID>
```

The manifest must follow `integrations/kimi-superboss/SKILL.md`, use relative attachment paths, and
contain exactly one `K3_RAW` attachment. Preview the project, attachment kinds, counts, risks, and
idempotency key, then obtain fresh OWNER confirmation immediately before `submit`.

Treat `SCANNING` as pending. Record a final server response only when the connector reports
`RECEIVED`, `REJECTED`, or `CONFLICT`. A `RECEIVED` M1 import must not be described as an M2 document
version.

## Recovery and revocation

Use the existing unchanged manifest/key only for the connector's temporary-failure recovery:

```powershell
superboss retry --server https://<APP_HOST>
superboss status --server https://<APP_HOST> --job-id <IMPORT_JOB_ID>
```

For exit 2, correct input; exit 3, re-pair; exit 4, create a new manifest and idempotency key; exit 5,
resolve the stable server rejection; exit 6, retry the unchanged operation. Do not use `retry` for a
pairing failure.

To revoke, use the OWNER browser at `https://<APP_HOST>/owner/devices`, select the named device,
choose **撤销设备**, and confirm. Verify a subsequent connector request fails. Never delete keyring
material as a substitute for server-side revocation.
