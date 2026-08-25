# M1 OWNER and STAFF acceptance runbook

This checklist is evidence collection, not evidence that acceptance has happened. Leave every field
blank until a human performs the step against a live deployment. Never record a cookie, token,
pairing code, password, or connector credential.

## Release-gate live e2e specs

Before a release, run the four Playwright specs in `tests/e2e/specs/` against a live
`https://app.localhost` stack. They are the highest-value checks in the repository:

- `owner-login-project.spec.ts`
- `file-quarantine.spec.ts`
- `device-import.spec.ts`
- `staff-denial.spec.ts`

Do not substitute `npm run test:contracts`, unit tests, or skipped specs for this gate.

## Secure Playwright preparation

Use two distinct local accounts created by the interactive acceptance seed. Put credentials only in
the current private PowerShell process, never in a script or `.env`. The harness creates fresh
browser contexts and keeps screenshots, trace, and video disabled so passwords and cookies are not
retained in Playwright artifacts.

```powershell
$env:E2E_BASE_URL='https://app.localhost'
$env:E2E_ALLOW_LOCAL_SELF_SIGNED='true'
$env:E2E_OWNER_USERNAME='<OWNER_USERNAME>'
$env:E2E_OWNER_PASSWORD='<OWNER_PASSWORD>'
$env:E2E_STAFF_USERNAME='<STAFF_USERNAME>'
$env:E2E_STAFF_PASSWORD='<STAFF_PASSWORD>'
$env:E2E_CONNECTOR_COMMAND_JSON='["<ABSOLUTE_CONNECTOR_EXE>"]'
$env:E2E_TARGET='local'

Push-Location tests/e2e
try {
    npm ci
    npx playwright install chromium
    npm test
} finally {
    Remove-Item Env:E2E_OWNER_USERNAME,Env:E2E_OWNER_PASSWORD `
      -ErrorAction SilentlyContinue
    Remove-Item Env:E2E_STAFF_USERNAME,Env:E2E_STAFF_PASSWORD `
      -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path (Get-Location) 'output') -Recurse -Force -ErrorAction SilentlyContinue
    Pop-Location
}
```

For the documented local HTTPS loopback deployment only, set
`E2E_ALLOW_LOCAL_SELF_SIGNED=true`. The harness rejects this opt-in for production, HTTP, and every
non-loopback host. Production must use a trusted certificate.

If `E2E_BASE_URL`, any local credential, the connector command, or the connector fixture is missing,
`npm test` fails before running tests. A failed or not-run live test remains FAIL or NOT RUN; do not
replace it with `npm run test:contracts`, `npm run test:list`, mocks, or skipped tests.

## Manual clean and EICAR upload

Create local fixtures in a disposable directory. EICAR is a non-malicious antivirus test signature.

```powershell
$AcceptanceDir=Join-Path $env:TEMP 'superboss-m1-acceptance'
New-Item -ItemType Directory -Force -Path $AcceptanceDir | Out-Null
Set-Content -LiteralPath (Join-Path $AcceptanceDir 'clean.txt') -Value 'SuperBoss M1 clean acceptance file' -Encoding ascii
Set-Content -LiteralPath (Join-Path $AcceptanceDir 'eicar.com.txt') -Value 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' -Encoding ascii -NoNewline
```

With the OWNER account:

1. Open `https://app.localhost/owner/drive` and select `验收测试`.
2. Upload `clean.txt`; record the file ID while it says `扫描中`.
3. Try download before CLEAN and record the denial. Retry until the UI offers the download; download
   and compare the bytes.
4. Upload `eicar.com.txt`; record the file ID and verify it never becomes downloadable after the
   scanner finishes.
5. Inspect safe service logs without copying secrets:

```powershell
docker compose --env-file .env -f docker-compose.dev.yml logs --tail 200 api file-scan-worker clamav
```

## Connector pairing, import, and revocation

Follow `kimi-connector-installation.md`. In the OWNER account, open
`https://app.localhost/owner/devices`, select `验收测试`, generate a one-time code, and enter it only in
the local connector command. Submit one fixture package and poll its job until the server reports
the literal status `RECEIVED`. Record the device and job IDs, but not the pairing code. Verify the
OWNER import response has no M2 document/version fields. Then select **撤销设备**, confirm, and
verify the device is shown as revoked and its next authenticated connector request is denied.

## Direct STAFF denial and audit evidence

Run the Playwright STAFF spec with the distinct account. It sends real direct requests to project
creation, pairing-code creation, OWNER import listing, and a foreign-project file download; each must
return 403. UI hiding alone is not evidence.

The successful STAFF spec prints exactly one safe labeled line through the list reporter:
`ACCEPTANCE_FOREIGN_FILE_ID=<UUID>`. Copy that line from the captured terminal transcript into the
checklist. It remains visible after the `finally` block deletes ignored output; do not depend on the
annotation, attachment, or HTML report. After the operations, set the OWNER, STAFF, and DEVICE actor
record IDs plus the exact UTC start/end of the live acceptance window, then inspect only the
required bounded audit rows. The three OWNER-only STAFF denials intentionally have a null
`object_id`; the foreign-file denial must carry the recorded file ID.

```powershell
$OwnerActorId='<OWNER_USER_RECORD_ID>'
$StaffActorId='<STAFF_USER_RECORD_ID>'
$DeviceActorId='<DEVICE_ID>'
$AuditStart='<UTC_START_ISO8601>'
$AuditEnd='<UTC_END_ISO8601>'
$AuditSql=@"
\set ON_ERROR_STOP on
SELECT created_at, actor_kind, actor_id, action, outcome, object_type, object_id, project_id, request_id
FROM audit_logs
WHERE actor_id IN ('$OwnerActorId'::uuid, '$StaffActorId'::uuid, '$DeviceActorId'::uuid)
  AND created_at >= '$AuditStart'::timestamptz
  AND created_at <= '$AuditEnd'::timestamptz
  AND action IN (
    'auth.login', 'project.create', 'file.upload.complete', 'file.download',
    'device.pairing_code.create', 'device.pair', 'device.revoke', 'import.list', 'import.submit'
  )
  AND outcome IN ('SUCCESS', 'DENIED')
ORDER BY created_at, id;
"@
$AuditSql | docker compose --env-file .env -f docker-compose.dev.yml exec -T postgres `
  sh -ceu 'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"'
```

Confirm OWNER/STAFF `auth.login` success, upload, pre-CLEAN denied and CLEAN successful download,
pairing, revocation, import submission, and all four STAFF denials. Pairing requires the DEVICE
actor's `device.pair` row; import submission requires the same DEVICE actor's `import.submit` row.
The expected STAFF denial rows are `project.create`, `device.pairing_code.create`, and `import.list`
with `object_id IS NULL`, plus `file.download` with the recorded foreign-file ID. A missing row is
FAIL, not a documentation exception.

## Blank sign-off checklist

- Date/time and timezone: ______________________________
- Browser and version (OWNER): _________________________
- Browser and version (STAFF): _________________________
- OWNER role/user record ID: ___________________________
- STAFF role/user record ID: ___________________________
- Normal project ID: ___________________________________
- `验收测试` project ID: _______________________________
- Clean file ID: _______________________________________
- EICAR file ID: _______________________________________
- STAFF foreign file denial ID: ________________________
- Device ID: ___________________________________________
- Import job ID: _______________________________________
- OWNER local login: PASS / FAIL / NOT RUN ______________
- OWNER creates test project: PASS / FAIL / NOT RUN _____
- CLEAN-before-download denial: PASS / FAIL / NOT RUN ___
- Clean upload downloadable: PASS / FAIL / NOT RUN ______
- EICAR remains unavailable: PASS / FAIL / NOT RUN ______
- Connector pair/import `RECEIVED`: PASS / FAIL / NOT RUN
- Device revocation enforced: PASS / FAIL / NOT RUN _____
- STAFF direct API denials: PASS / FAIL / NOT RUN _______
- Required audit rows present: PASS / FAIL / NOT RUN ____
- OWNER name/signature: _________________________________
- Reviewer name/signature: ______________________________
- Notes/incident IDs (no secrets): ______________________

The command's `finally` block removes credential environment variables and every file under the
ignored `tests/e2e/output/` tree whether the run succeeds or fails. Confirm that cleanup completed.
Do not mark M1 accepted while any required line is FAIL or NOT RUN.
