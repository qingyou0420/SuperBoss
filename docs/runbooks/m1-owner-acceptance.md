# M1 OWNER and STAFF acceptance runbook

This checklist is evidence collection, not evidence that acceptance has happened. Leave every field
blank until a human performs the step against a live deployment. Never record a cookie, token,
pairing code, WeCom authorization code, password, or connector credential.

## Secure Playwright preparation

Use two different WeCom accounts and two different browser profiles. The external storage-state
files may contain account cookies: keep them outside the repository, or under the ignored
`tests/e2e/output/` directory, restrict their filesystem permissions, and delete them after the run.

```powershell
$env:E2E_BASE_URL='https://<APP_HOST>'
$env:E2E_OWNER_STORAGE_STATE_PATH='<ABSOLUTE_OWNER_STATE_PATH>'
$env:E2E_STAFF_STORAGE_STATE_PATH='<ABSOLUTE_STAFF_STATE_PATH>'
$env:E2E_CONNECTOR_COMMAND_JSON='["<ABSOLUTE_CONNECTOR_EXE>"]'
$env:E2E_TARGET='production'

Push-Location tests/e2e
npm ci
npx playwright install chromium
npx playwright codegen --save-storage "$env:E2E_OWNER_STORAGE_STATE_PATH" "$env:E2E_BASE_URL/login"
# Complete OWNER WeCom login in the opened profile, then close it.
npx playwright codegen --save-storage "$env:E2E_STAFF_STORAGE_STATE_PATH" "$env:E2E_BASE_URL/login"
# Complete the distinct STAFF WeCom login in the opened profile, then close it.
npm test
Pop-Location
```

For the documented local HTTPS loopback deployment only, set
`E2E_ALLOW_LOCAL_SELF_SIGNED=true`. The harness rejects this opt-in for production, HTTP, and every
non-loopback host. Production must use a trusted certificate.

If `E2E_BASE_URL`, either account state, the connector command, or the connector fixture is missing,
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

With the OWNER profile:

1. Open `https://<APP_HOST>/owner/drive` and select `验收测试`.
2. Upload `clean.txt`; record the file ID while it says `扫描中`.
3. Try download before CLEAN and record the denial. Retry until the UI offers the download; download
   and compare the bytes.
4. Upload `eicar.com.txt`; record the file ID and verify it never becomes downloadable after the
   scanner finishes.
5. Inspect safe service logs without copying secrets:

```powershell
docker compose --env-file .env -f docker-compose.yml logs --tail 200 api worker clamav
```

## Connector pairing, import, and revocation

Follow `kimi-connector-installation.md`. In the OWNER profile, open
`https://<APP_HOST>/owner/devices`, select `验收测试`, generate a one-time code, and enter it only in
the local connector command. Submit one fixture package and poll its job until the server reports
the literal status `RECEIVED`. Record the device and job IDs, but not the pairing code. Verify the
OWNER import response has no M2 document/version fields. Then select **撤销设备**, confirm, and
verify the device is shown as revoked and its next authenticated connector request is denied.

## Direct STAFF denial and audit evidence

Run the Playwright STAFF spec with the distinct account. It sends real direct requests to project
creation, pairing-code creation, OWNER import listing, and a foreign-project file download; each must
return 403. UI hiding alone is not evidence.

After the operations, inspect bounded audit rows by the recorded IDs:

```powershell
docker compose --env-file .env -f docker-compose.yml exec -T postgres psql `
  --username "$env:SUPERBOSS_POSTGRES_USER" `
  --dbname "$env:SUPERBOSS_POSTGRES_DB" `
  --command "SELECT created_at,action,outcome,object_type,object_id FROM audit_logs WHERE object_id IN ('<PROJECT_ID>','<FILE_ID>','<DEVICE_ID>','<IMPORT_JOB_ID>') ORDER BY created_at;"
```

Confirm evidence for login, denied access, upload, denied and successful download, pairing,
revocation, and import submission. A missing row is FAIL, not a documentation exception.

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
- Device ID: ___________________________________________
- Import job ID: _______________________________________
- OWNER login callback: PASS / FAIL / NOT RUN __________
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

Delete the external storage-state files after recording results. Do not mark M1 accepted while any
required line is FAIL or NOT RUN.
