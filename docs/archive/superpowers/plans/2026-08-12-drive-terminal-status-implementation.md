# Drive Terminal Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accurately render infected and failed uploads in the current-only Drive UI while preserving the CLEAN-only download boundary, then prove current PostgreSQL and MinIO data can be restored in isolated temporary containers.

**Architecture:** Keep the existing download route and split its safe 409 error code by authoritative file state. The browser API converts only exact terminal error envelopes into a fixed typed state, and Drive renders all five states explicitly. Backup validation runs outside application code with uniquely named Docker resources and never mounts or replaces the current Compose volumes.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, Vue 3, TypeScript, Vitest, Docker Compose, PostgreSQL 18, MinIO/mc.

## Global Constraints

- Only `CLEAN` files may receive a presigned download URL.
- Do not add a file list endpoint, file status endpoint, polling timer, persistence, or dependency.
- Error output must not include virus signatures, object keys, URLs, credentials, or raw exceptions.
- Backup restore resources must use a generated `superboss-restore-<hex>` prefix and publish no ports.
- Never delete, replace, or mount `m1-foundation_postgres-data` or `m1-foundation_minio-data` as restore targets.

---

### Task 1: Lock the backend download-state contract

**Files:**
- Modify: `server/tests/api/test_file_uploads.py`
- Modify: `server/src/superboss/modules/files/service.py`

**Interfaces:**
- Consumes: `FileService.presign_download(actor, file_id)` and `FileState`.
- Produces: 409 codes `FILE_NOT_READY`, `FILE_INFECTED`, and `FILE_SCAN_FAILED` without changing route shape or audit behavior.

- [ ] **Step 1: Write the failing API expectations**

Change the existing five-state parameterization to literal pairs:

```python
@pytest.mark.parametrize(
    ("state", "expected_code"),
    [
        ("UPLOADING", "FILE_NOT_READY"),
        ("QUARANTINED", "FILE_NOT_READY"),
        ("SCANNING", "FILE_NOT_READY"),
        ("INFECTED", "FILE_INFECTED"),
        ("FAILED", "FILE_SCAN_FAILED"),
    ],
)
```

Keep the existing assertions for HTTP 409, zero storage expiry, one DENIED audit, exact actor/object/project/request IDs, and absence of URL/object key.

- [ ] **Step 2: Run RED**

Run from `server`:

```powershell
C:\Users\Administrator\.local\bin\uv.exe run pytest -q tests/api/test_file_uploads.py::test_download_rejects_non_clean_file_with_denied_audit
```

Expected: the `INFECTED` and `FAILED` cases fail because both still return `FILE_NOT_READY`.

- [ ] **Step 3: Implement the minimal state-specific errors**

Add fixed `ConflictError` subclasses for infected and scan-failed states, then make `ensure_downloadable()` branch only on `FileState.INFECTED` and `FileState.FAILED` before the existing non-CLEAN fallback. Do not change route or audit code.

- [ ] **Step 4: Run GREEN**

Repeat the focused command. Expected: 5 passed.

### Task 2: Lock the browser API terminal-state boundary

**Files:**
- Modify: `web/tests/files-api.test.ts`
- Modify: `web/src/api/files.ts`

**Interfaces:**
- Consumes: frozen `HttpClientError(status, data)` from `api/http.ts`.
- Produces: `FileDownloadUnavailableError` with readonly state `'INFECTED' | 'FAILED'`; all malformed or unrelated errors remain untrusted.

- [ ] **Step 1: Write failing API tests**

Add literal 409 envelopes for `FILE_INFECTED` and `FILE_SCAN_FAILED` and assert `download()` rejects with the matching fixed state error. Add malformed/extra-key/wrong-status cases and assert they are not converted to terminal state.

- [ ] **Step 2: Run RED**

Run from `web`:

```powershell
npm test -- --run tests/files-api.test.ts
```

Expected: terminal cases expose only generic `HttpClientError` because the typed boundary does not exist.

- [ ] **Step 3: Implement minimal exact parsing**

Import `HttpClientError`, add the frozen state-only error class, and catch download failures. Convert only status 409 plus an exact bounded `{error:{code,message,request_id}}` envelope whose code is one of the two fixed terminal codes. Rethrow every other error unchanged.

- [ ] **Step 4: Run GREEN**

Repeat the focused command. Expected: all file API tests pass.

### Task 3: Render all Drive states explicitly

**Files:**
- Modify: `web/tests/drive-routing.test.ts`
- Modify: `web/src/pages/owner/DrivePage.vue`

**Interfaces:**
- Consumes: `FileUploadCompleted.state` and `FileDownloadUnavailableError.state`.
- Produces: fixed safe labels and retry visibility for each state.

- [ ] **Step 1: Write failing component tests**

For both direct completion and download rejection, assert:

```text
INFECTED -> 检测到风险，文件不可下载; no check button; no link
FAILED   -> 扫描失败，文件不可下载，请重新上传; no check button; no link
```

Keep the existing QUARANTINED retry and CLEAN link tests.

- [ ] **Step 2: Run RED**

Run from `web`:

```powershell
npm test -- --run tests/drive-routing.test.ts
```

Expected: terminal cases still display `扫描中` and expose the check button.

- [ ] **Step 3: Implement explicit state mapping**

Use computed status text and `canCheckDownload`. On a typed terminal download error, update the current in-memory state and clear any URL; on an unknown error retain the generic scanning-safe retry message. Do not store the file ID, state, or URL.

- [ ] **Step 4: Run GREEN**

Repeat the focused command. Expected: all Drive routing tests pass.

### Task 4: Verify the code change

**Files:**
- No new files.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: fresh regression evidence.

- [ ] **Step 1: Run backend gates**

From `server`, with the repository PostgreSQL test runtime when required:

```powershell
C:\Users\Administrator\.local\bin\uv.exe run pytest -q tests/api/test_file_uploads.py
C:\Users\Administrator\.local\bin\uv.exe run ruff check .
C:\Users\Administrator\.local\bin\uv.exe run mypy src
```

- [ ] **Step 2: Run frontend gates**

From `web`:

```powershell
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

- [ ] **Step 3: Inspect diff and commit**

Run `git diff --check`, inspect the exact changed files, then commit tests and production together with message `fix(files): show terminal scan results`.

### Task 5: Run an isolated PostgreSQL and MinIO restore drill

**Files:**
- No repository files.

**Interfaces:**
- Consumes: current `m1-foundation-postgres-1` and `m1-foundation-minio-1` read-only backup sources.
- Produces: verified temporary restore evidence and no persistent restore resources.

- [ ] **Step 1: Freeze source evidence**

Record current container IDs, health, named volumes, database table counts, Alembic revision, MinIO object paths/sizes/SHA-256, and confirm the only published port is `127.0.0.1:443`.

- [ ] **Step 2: Create temporary backups**

Create a unique directory under `%TEMP%`, run `pg_dump -Fc` inside the current PostgreSQL container and copy the dump out, then use the pinned `mc` image on the existing backend network to mirror the configured bucket to the temporary directory. Never print credentials.

- [ ] **Step 3: Restore into isolated resources**

Create a generated network, PostgreSQL volume/container, and MinIO volume/container with no published ports. Restore the dump with `pg_restore` and mirror object files into a fresh bucket.

- [ ] **Step 4: Compare restored data**

Assert the restored Alembic revision and all business-table counts equal the source. Mirror the restored bucket back to a second local directory and compare the exact relative path set, byte sizes, and SHA-256 values.

- [ ] **Step 5: Guaranteed cleanup and final verification**

In `finally`, resolve and validate every generated name starts with `superboss-restore-`, then remove only those containers, network, volumes, and the temporary backup directory. Confirm no generated resources remain, the current nine-service stack is healthy, current source counts/hashes are unchanged, `git status --short` is clean, and only `127.0.0.1:443` remains published.
