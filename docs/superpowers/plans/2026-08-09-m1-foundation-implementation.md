# SuperBoss M1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-shaped M1 foundation that lets the unique OWNER sign in through WeCom, manage projects, upload and safely download project files, audit all sensitive actions, pair a Kimi PC device, and receive a minimal K3 result package.

**Architecture:** Keep one FastAPI codebase and run it as API, Celery worker, and scheduler processes. Vue is a separate SPA; PostgreSQL is the source of truth, MinIO stores objects, Redis carries jobs and short-lived state, ClamAV scans every uploaded object, and Nginx is the only production ingress. M1 stores K3 packages without performing the M2 model/OCR/version-chain processing.

**Tech Stack:** Python 3.13, uv, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, PostgreSQL 18, Redis 8, Celery, boto3 S3 client, ClamAV 1.5.3, Vue 3, TypeScript, Vite, Element Plus, Pinia, Vue Router, Axios, Vitest, Testing Library, Playwright, Docker Compose, Nginx.

## Global Constraints

- Preserve the frozen design in `docs/superpowers/specs/2026-08-09-technical-foundation-design.md`.
- Keep a single backend codebase; do not introduce business microservices or Kubernetes.
- Only one `OWNER` may exist; ordinary application APIs cannot disable, delete, downgrade, or replace it.
- Browser authentication uses WeCom OAuth only, with no public registration or password login.
- Access Token lifetime is exactly 2 hours; Refresh Token lifetime is exactly 14 days with rotation and immediate revocation checks.
- `STAFF` receives no Agent, finance, revenue, account-management, or Kimi-device APIs.
- The OWNER's STAFF acceptance account uses a distinct WeCom userid and no testing bypass.
- Test projects set `is_test=true`; production reports must exclude them by default and by server-side policy.
- Kimi device credentials have only `imports:create`, `imports:upload`, and `imports:read-own` scopes.
- Browser and device credentials are stored as Secure, HttpOnly cookies or Windows Credential Manager entries; never use browser local storage or checked-in files for secrets.
- Browser state-changing requests use a non-secret `XSRF-TOKEN` cookie plus matching `X-CSRF-Token` header; the authentication cookies remain HttpOnly.
- All timestamps are stored in UTC and rendered in `Asia/Shanghai`.
- M1 accepts files up to 100 MiB, uses 8 MiB multipart parts, 24-hour upload sessions, 15-minute upload-part URLs, and 60-second download URLs.
- Files are not downloadable until their state is `CLEAN`.
- M1 does not call Kimi, DeepSeek, LangGraph, or Tencent OCR; it only receives and preserves K3 result packages for M2.
- Python dependencies are locked in `server/uv.lock`; frontend dependencies are locked in `web/package-lock.json`.
- Production containers are pinned to immutable image digests when the implementation task runs; the checked-in compose file also records the human-readable version tag.
- Every implementation task follows red-green-refactor, runs the focused test, runs the affected suite, and ends with one focused commit.

## Planned File Structure

```text
SuperBoss/
├── .editorconfig
├── .env.example
├── .gitignore
├── README.md
├── docker-compose.yml
├── docker-compose.dev.yml
├── server/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── migrations/
│   ├── src/superboss/
│   │   ├── main.py
│   │   ├── api/router.py
│   │   ├── core/{config,db,errors,security,actors}.py
│   │   ├── infrastructure/{redis,s3,clamav,wecom}.py
│   │   ├── modules/
│   │   │   ├── health/router.py
│   │   │   ├── auth/{models,schemas,repository,service,router}.py
│   │   │   ├── users/{models,schemas,repository,service,router}.py
│   │   │   ├── projects/{models,schemas,repository,service,router}.py
│   │   │   ├── audit/{models,schemas,service}.py
│   │   │   ├── files/{models,schemas,storage,service,router,tasks}.py
│   │   │   ├── devices/{models,schemas,service,router}.py
│   │   │   └── imports/{models,schemas,service,router}.py
│   │   └── workers/{celery_app,schedules}.py
│   └── tests/{unit,integration,api}/
├── web/
│   ├── package.json
│   ├── package-lock.json
│   ├── Dockerfile
│   ├── src/{api,app,components,layouts,pages,stores}/
│   └── tests/
├── integrations/kimi-superboss/
│   ├── SKILL.md
│   ├── connector/{pyproject.toml,uv.lock,src,tests}/
│   └── scripts/build-windows.ps1
├── ops/nginx/{nginx.conf,conf.d/superboss.conf,allowlist.conf.example}/
└── tests/e2e/
```

### Task 1: Bootstrap the FastAPI service and backend quality gate

**Files:**
- Create: `.editorconfig`
- Create: `.gitignore`
- Create: `server/pyproject.toml`
- Create: `server/src/superboss/__init__.py`
- Create: `server/src/superboss/main.py`
- Create: `server/src/superboss/api/router.py`
- Create: `server/src/superboss/core/config.py`
- Create: `server/src/superboss/modules/health/router.py`
- Create: `server/tests/api/test_health.py`
- Create: `server/tests/conftest.py`

**Interfaces:**
- Produces: `create_app(settings: Settings | None = None) -> FastAPI`
- Produces: `GET /api/v1/health/live -> {"status": "ok"}`
- Produces: cached `get_settings() -> Settings`

- [ ] **Step 1: Add the backend manifest and failing health test**

Use Python `>=3.13,<3.14`. Add runtime dependencies `fastapi`, `uvicorn[standard]`, `pydantic-settings`; add development dependencies `httpx`, `pytest`, `pytest-asyncio`, `ruff`, and `mypy`. Configure Ruff for 100-character lines and mypy strict mode.

```python
# server/tests/api/test_health.py
from fastapi.testclient import TestClient

from superboss.main import create_app


def test_liveness_does_not_require_external_services() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `cd server && uv lock && uv run pytest tests/api/test_health.py -v`

Expected: FAIL because `superboss.main` or `create_app` does not exist.

- [ ] **Step 3: Implement the minimal application factory and configuration**

```python
# server/src/superboss/main.py
from fastapi import FastAPI

from superboss.api.router import api_router
from superboss.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    app = FastAPI(title="SuperBoss API", version="1.0.0")
    app.state.settings = active_settings
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
```

```python
# server/src/superboss/modules/health/router.py
from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}
```

`Settings` must use the `SUPERBOSS_` environment prefix, reject unknown fields, default `environment` to `development`, and never define real secret defaults.

- [ ] **Step 4: Run the focused test and backend checks**

Run: `cd server && uv run pytest tests/api/test_health.py -v`

Expected: PASS, 1 test passed.

Run: `cd server && uv run ruff check . && uv run mypy src`

Expected: both commands exit 0.

- [ ] **Step 5: Commit the backend bootstrap**

```bash
git add .editorconfig .gitignore server
git commit -m "chore(server): bootstrap FastAPI service"
```

### Task 2: Bootstrap the Vue owner shell and frontend quality gate

**Files:**
- Create: `web/package.json`
- Create: `web/package-lock.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/vitest.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.ts`
- Create: `web/src/App.vue`
- Create: `web/src/app/router.ts`
- Create: `web/src/pages/HealthPage.vue`
- Create: `web/tests/setup.ts`
- Create: `web/tests/App.test.ts`

**Interfaces:**
- Produces: Vue application mounted at `#app`
- Produces: route `/health` with visible text `SuperBoss is ready`
- Produces: scripts `dev`, `build`, `test`, `typecheck`, and `lint`

- [ ] **Step 1: Add the frontend manifest and failing shell test**

Use Vue 3, TypeScript, Vite, Element Plus, Pinia, Vue Router, Axios, Vitest, Vue Testing Library, jsdom, ESLint, and Prettier. Require Node `>=24<25`.

```ts
// web/tests/App.test.ts
import { render, screen } from '@testing-library/vue'
import App from '../src/App.vue'

test('renders the SuperBoss shell', () => {
  render(App)
  expect(screen.getByText('SuperBoss')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `cd web && npm install && npm run test -- --run tests/App.test.ts`

Expected: FAIL because `src/App.vue` does not exist.

- [ ] **Step 3: Implement the minimal Vue shell**

```vue
<!-- web/src/App.vue -->
<template>
  <el-config-provider>
    <header>SuperBoss</header>
    <router-view />
  </el-config-provider>
</template>
```

Create `/health` as the default route and render `SuperBoss is ready`. Register Element Plus, Pinia, and the router in `main.ts`.

- [ ] **Step 4: Run frontend tests and build checks**

Run: `cd web && npm run test -- --run && npm run typecheck && npm run build`

Expected: tests pass, TypeScript exits 0, and Vite produces `web/dist`.

- [ ] **Step 5: Commit the frontend bootstrap**

```bash
git add web
git commit -m "chore(web): bootstrap Vue owner shell"
```

### Task 3: Add local infrastructure, database sessions, and the identity/project schema

**Files:**
- Create: `.env.example`
- Create: `docker-compose.dev.yml`
- Modify: `server/pyproject.toml`
- Create: `server/alembic.ini`
- Create: `server/migrations/env.py`
- Create: `server/migrations/versions/0001_identity_projects_audit.py`
- Create: `server/src/superboss/core/db.py`
- Create: `server/src/superboss/modules/users/models.py`
- Create: `server/src/superboss/modules/projects/models.py`
- Create: `server/src/superboss/modules/audit/models.py`
- Create: `server/tests/integration/test_identity_schema.py`

**Interfaces:**
- Produces: `async_session_factory() -> async_sessionmaker[AsyncSession]`
- Produces: enums `Role.OWNER`, `Role.STAFF`, `UserStatus.ACTIVE`, `UserStatus.DISABLED`
- Produces: models `User`, `Project`, `ProjectMember`, `AuditLog`
- Produces: database constraint allowing exactly one protected OWNER

- [ ] **Step 1: Add database dependencies and a failing schema test**

Add `sqlalchemy[asyncio]`, `asyncpg`, and `alembic`; add `testcontainers[postgres]` to the development group.

```python
# server/tests/integration/test_identity_schema.py
import pytest
from sqlalchemy.exc import IntegrityError

from superboss.modules.users.models import Role, User, UserStatus


@pytest.mark.asyncio
async def test_database_rejects_a_second_owner(db_session) -> None:
    db_session.add(User(wecom_userid="owner-1", role=Role.OWNER, status=UserStatus.ACTIVE))
    await db_session.commit()
    db_session.add(User(wecom_userid="owner-2", role=Role.OWNER, status=UserStatus.ACTIVE))
    with pytest.raises(IntegrityError):
        await db_session.commit()
```

Add a second test proving `(project_id, user_id)` membership is unique.

- [ ] **Step 2: Start PostgreSQL and verify the migration/test failure**

Run: `docker compose -f docker-compose.dev.yml up -d postgres`

Run: `cd server && uv lock && uv run alembic upgrade head && uv run pytest tests/integration/test_identity_schema.py -v`

Expected: FAIL because the models and migration do not exist.

- [ ] **Step 3: Implement the models and initial migration**

Use UUID primary keys, `created_at`/`updated_at` UTC timestamps, and these required fields:

```python
class User(Base):
    id: Mapped[UUID]
    wecom_userid: Mapped[str]  # unique
    display_name: Mapped[str]
    role: Mapped[Role]
    status: Mapped[UserStatus]
    last_login_at: Mapped[datetime | None]


class Project(Base):
    id: Mapped[UUID]
    name: Mapped[str]
    is_test: Mapped[bool]
    status: Mapped[ProjectStatus]  # ACTIVE or ARCHIVED


class ProjectMember(Base):
    project_id: Mapped[UUID]
    user_id: Mapped[UUID]
```

The migration must create a PostgreSQL partial unique index equivalent to:

```sql
CREATE UNIQUE INDEX uq_users_single_owner
ON users ((role))
WHERE role = 'OWNER';
```

`AuditLog` must store `actor_kind`, `actor_id`, `action`, `object_type`, `object_id`, `project_id`, `outcome`, `metadata_json`, `created_at`, and an optional `request_id`.

- [ ] **Step 4: Run migrations and schema tests**

Run: `cd server && uv run alembic downgrade base && uv run alembic upgrade head && uv run pytest tests/integration/test_identity_schema.py -v`

Expected: migration round-trip succeeds and both constraint tests pass.

- [ ] **Step 5: Run affected backend checks**

Run: `cd server && uv run pytest tests/api tests/integration/test_identity_schema.py -v && uv run ruff check . && uv run mypy src`

Expected: all tests and checks pass.

- [ ] **Step 6: Commit the infrastructure and schema**

```bash
git add .env.example docker-compose.dev.yml server
git commit -m "feat(core): add identity and project data foundation"
```

### Task 4: Implement WeCom OAuth, protected OWNER bootstrap, and rotating sessions

**Files:**
- Modify: `server/pyproject.toml`
- Create: `server/migrations/versions/0002_auth_sessions.py`
- Create: `server/src/superboss/infrastructure/wecom.py`
- Create: `server/src/superboss/core/security.py`
- Create: `server/src/superboss/modules/auth/models.py`
- Create: `server/src/superboss/modules/auth/schemas.py`
- Create: `server/src/superboss/modules/auth/repository.py`
- Create: `server/src/superboss/modules/auth/service.py`
- Create: `server/src/superboss/modules/auth/router.py`
- Create: `server/src/superboss/modules/users/repository.py`
- Modify: `server/src/superboss/api/router.py`
- Create: `server/tests/unit/auth/test_token_rotation.py`
- Create: `server/tests/api/test_wecom_auth.py`

**Interfaces:**
- Produces: `WeComIdentityProvider.authorization_url(state: str) -> str`
- Produces: `await WeComIdentityProvider.exchange_code(code: str) -> WeComIdentity`
- Produces: `AuthService.complete_wecom_login(code: str, state: str) -> SessionPair`
- Produces: `AuthService.rotate_refresh_token(raw_token: str) -> SessionPair`
- Produces: `/api/v1/auth/wecom/start`, `/callback`, `/refresh`, `/logout`, `/me`

- [ ] **Step 1: Write failing session and OAuth tests**

```python
@pytest.mark.asyncio
async def test_refresh_token_is_single_use(auth_service, active_owner) -> None:
    pair = await auth_service.issue_session(active_owner)
    rotated = await auth_service.rotate_refresh_token(pair.refresh_token)
    assert rotated.refresh_token != pair.refresh_token
    with pytest.raises(InvalidSession):
        await auth_service.rotate_refresh_token(pair.refresh_token)
```

API tests must also prove:

- the configured OWNER userid bootstraps the sole OWNER on first successful callback;
- an unknown userid receives 403 and is not inserted;
- a disabled whitelist user receives 403;
- logout revokes refresh and access sessions;
- the callback rejects missing or mismatched OAuth `state`.
- POST, PUT, PATCH, and DELETE browser requests reject a missing or mismatched CSRF header.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd server && uv run pytest tests/unit/auth/test_token_rotation.py tests/api/test_wecom_auth.py -v`

Expected: FAIL because auth models and services do not exist.

- [ ] **Step 3: Implement token storage and WeCom adapter**

Add `pyjwt`, `cryptography`, and `httpx`. Store only SHA-256 hashes of opaque refresh tokens. Access JWT claims are exactly `sub`, `role`, `session_id`, `iat`, `exp`, and `jti`.

```python
@dataclass(frozen=True)
class SessionPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
```

The real WeCom adapter obtains an application access token server-side and exchanges the OAuth code for `UserId`; it must never accept a userid supplied by the browser. Keep `WeComIdentityProvider` injectable so API tests use a fake provider.

Support a deterministic fake provider only when `SUPERBOSS_ENVIRONMENT=test`. Application startup must fail if fake WeCom mode is configured for staging or production. Automated E2E tests use fake authorization codes; manual OWNER/STAFF acceptance uses real WeCom OAuth.

Set cookies as `Secure`, `HttpOnly`, `SameSite=Lax`; use path `/` for access and `/api/v1/auth` for refresh. Store OAuth state in a separate signed, 10-minute, `SameSite=Lax` cookie.

Issue a random, non-secret `XSRF-TOKEN` cookie after login and refresh. Require the same value in `X-CSRF-Token` for browser-authenticated POST, PUT, PATCH, and DELETE requests. Device bearer requests do not use the browser CSRF mechanism.

- [ ] **Step 4: Enforce OWNER immutability in service methods**

`UserRepository.disable`, `change_role`, and `delete` must raise `ProtectedOwnerError` when the target role is OWNER. Do not rely solely on absent UI routes.

- [ ] **Step 5: Run auth tests and migration checks**

Run: `cd server && uv run alembic upgrade head && uv run pytest tests/unit/auth/test_token_rotation.py tests/api/test_wecom_auth.py -v`

Expected: all auth tests pass.

Run: `cd server && uv run ruff check . && uv run mypy src`

Expected: both commands exit 0.

- [ ] **Step 6: Commit authentication**

```bash
git add server
git commit -m "feat(auth): add WeCom OAuth and rotating sessions"
```

### Task 5: Add actor resolution, project authorization, and project APIs

**Files:**
- Create: `server/src/superboss/core/actors.py`
- Create: `server/src/superboss/core/errors.py`
- Create: `server/src/superboss/modules/projects/schemas.py`
- Create: `server/src/superboss/modules/projects/repository.py`
- Create: `server/src/superboss/modules/projects/service.py`
- Create: `server/src/superboss/modules/projects/router.py`
- Modify: `server/src/superboss/api/router.py`
- Create: `server/tests/unit/core/test_authorization.py`
- Create: `server/tests/api/test_projects.py`

**Interfaces:**
- Produces: `Actor(kind, subject_id, role, project_ids, scopes)`
- Produces: `get_actor() -> Actor`
- Produces: `require_owner(actor: Actor) -> None`
- Produces: `require_project_access(actor: Actor, project_id: UUID) -> None`
- Produces: `POST /api/v1/projects`, `GET /api/v1/projects`, `GET /api/v1/projects/{project_id}`

- [ ] **Step 1: Write failing policy tests**

```python
def test_staff_cannot_use_owner_policy(staff_actor: Actor) -> None:
    with pytest.raises(ForbiddenError):
        require_owner(staff_actor)


def test_staff_can_only_access_assigned_projects(staff_actor: Actor, assigned_project_id: UUID) -> None:
    require_project_access(staff_actor, assigned_project_id)
    with pytest.raises(ForbiddenError):
        require_project_access(staff_actor, uuid4())
```

API tests must prove OWNER sees all projects, STAFF sees only memberships, STAFF cannot create projects, and a project with `is_test=true` preserves that flag.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd server && uv run pytest tests/unit/core/test_authorization.py tests/api/test_projects.py -v`

Expected: FAIL because actor policies and project routes do not exist.

- [ ] **Step 3: Implement actor and project boundaries**

```python
@dataclass(frozen=True)
class Actor:
    kind: Literal["user", "device", "system"]
    subject_id: UUID
    role: Role | None
    project_ids: frozenset[UUID]
    scopes: frozenset[str]
```

`get_actor` must resolve browser JWTs against current database status on every request. OWNER gets all project access through `role`, not by materializing membership rows. STAFF queries must join through `project_members` in the repository; do not load all projects and filter in Python.

- [ ] **Step 4: Implement explicit error mapping**

Map unauthenticated requests to 401, forbidden requests to 403, missing objects to 404, duplicate names to 409, and validation errors to FastAPI's 422 response. Error bodies use:

```json
{"error":{"code":"PROJECT_FORBIDDEN","message":"You cannot access this project","request_id":"..."}}
```

- [ ] **Step 5: Run policy, API, and quality checks**

Run: `cd server && uv run pytest tests/unit/core/test_authorization.py tests/api/test_projects.py -v && uv run ruff check . && uv run mypy src`

Expected: all tests and checks pass.

- [ ] **Step 6: Commit authorization and projects**

```bash
git add server
git commit -m "feat(projects): enforce project-scoped access"
```

### Task 6: Add append-only audit recording, including denied actions

**Files:**
- Create: `server/src/superboss/modules/audit/schemas.py`
- Create: `server/src/superboss/modules/audit/service.py`
- Modify: `server/src/superboss/main.py`
- Modify: `server/src/superboss/core/errors.py`
- Modify: `server/src/superboss/modules/projects/service.py`
- Create: `server/tests/unit/audit/test_audit_service.py`
- Create: `server/tests/api/test_audit_events.py`

**Interfaces:**
- Produces: `AuditService.record(event: AuditEventInput) -> UUID`
- Produces: request-scoped `request_id`
- Consumes: `Actor` from Task 5 and `AuditLog` from Task 3

- [ ] **Step 1: Write failing audit tests**

```python
@pytest.mark.asyncio
async def test_records_denied_project_access(client, staff_cookie, foreign_project, db_session) -> None:
    response = await client.get(f"/api/v1/projects/{foreign_project.id}", cookies=staff_cookie)
    assert response.status_code == 403
    event = await latest_audit_event(db_session)
    assert event.action == "project.read"
    assert event.outcome == "DENIED"
    assert event.project_id == foreign_project.id
```

Add a test proving audit metadata rejects keys named `access_token`, `refresh_token`, `authorization`, `cookie`, `file_content`, and `model_input`.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd server && uv run pytest tests/unit/audit/test_audit_service.py tests/api/test_audit_events.py -v`

Expected: FAIL because audit service and request IDs do not exist.

- [ ] **Step 3: Implement request IDs and audit redaction**

Generate or validate an incoming UUID `X-Request-ID`, return it in every response, and pass it to the audit service. `AuditEventInput` must accept only structured metadata and recursively redact forbidden keys before persistence.

Record successful and denied actions explicitly in the application service or exception boundary. Successful events are written after the business transaction commits; denied events use a separate short audit transaction so the rejection does not roll back its own evidence. Do not audit health checks or static assets.

- [ ] **Step 4: Make audit rows append-only at the application boundary**

Do not create update or delete repository methods for `AuditLog`. Add an integration test that the API has no route capable of mutating audit rows.

- [ ] **Step 5: Run audit and regression suites**

Run: `cd server && uv run pytest tests/unit/audit tests/api/test_audit_events.py tests/api/test_projects.py -v && uv run ruff check . && uv run mypy src`

Expected: all tests and checks pass.

- [ ] **Step 6: Commit audit support**

```bash
git add server
git commit -m "feat(audit): record sensitive and denied actions"
```

### Task 7: Implement multipart file upload and clean-only download authorization

**Files:**
- Modify: `server/pyproject.toml`
- Create: `server/migrations/versions/0003_files_and_uploads.py`
- Create: `server/src/superboss/infrastructure/s3.py`
- Create: `server/src/superboss/modules/files/models.py`
- Create: `server/src/superboss/modules/files/schemas.py`
- Create: `server/src/superboss/modules/files/storage.py`
- Create: `server/src/superboss/modules/files/service.py`
- Create: `server/src/superboss/modules/files/router.py`
- Modify: `server/src/superboss/api/router.py`
- Create: `server/tests/unit/files/test_file_service.py`
- Create: `server/tests/api/test_file_uploads.py`

**Interfaces:**
- Produces: `ObjectStorage` protocol
- Produces: `FileService.start_upload`, `presign_part`, `complete_upload`, `presign_download`
- Produces: `POST /api/v1/files/uploads`
- Produces: `POST /api/v1/files/uploads/{upload_id}/parts/{part_number}`
- Produces: `POST /api/v1/files/uploads/{upload_id}/complete`
- Produces: `GET /api/v1/files/{file_id}/download`

- [ ] **Step 1: Write failing file-state and permission tests**

```python
@pytest.mark.asyncio
async def test_download_requires_clean_state(file_service, owner_actor, quarantined_file) -> None:
    with pytest.raises(FileNotReadyError):
        await file_service.presign_download(owner_actor, quarantined_file.id)


@pytest.mark.asyncio
async def test_staff_cannot_presign_foreign_project_part(client, staff_cookie, foreign_project) -> None:
    response = await client.post(
        "/api/v1/files/uploads",
        cookies=staff_cookie,
        json={"project_id": str(foreign_project.id), "filename": "x.pdf", "size_bytes": 12, "sha256": "0" * 64, "category": "资料", "file_date": "2026-08-09"},
    )
    assert response.status_code == 403
```

Also test the 100 MiB maximum, 64-character lowercase SHA-256 validation, part numbers starting at 1, and a 409 response when the same idempotency key is reused with different file metadata.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd server && uv run pytest tests/unit/files/test_file_service.py tests/api/test_file_uploads.py -v`

Expected: FAIL because file models and storage protocol do not exist.

- [ ] **Step 3: Implement file and upload models**

Use exact states `UPLOADING`, `QUARANTINED`, `SCANNING`, `CLEAN`, `INFECTED`, and `FAILED`. Store `project_id`, `filename`, `category`, `file_date`, `object_key`, `size_bytes`, `sha256`, uploader actor fields, scan result, and timestamps.

Object keys use:

```text
projects/{project_id}/{category}/{YYYY-MM-DD}/{file_id}/{sanitized_filename}
```

Keep the original display filename in PostgreSQL; never trust it as a raw filesystem or object-key path.

- [ ] **Step 4: Implement the S3 protocol and completion checks**

Add `boto3`. The protocol must expose:

```python
class ObjectStorage(Protocol):
    async def create_multipart(self, object_key: str, content_type: str) -> str: ...
    async def presign_upload_part(self, object_key: str, multipart_id: str, part_number: int, expires_seconds: int) -> str: ...
    async def complete_multipart(self, object_key: str, multipart_id: str, parts: list[CompletedPart]) -> ObjectMetadata: ...
    async def abort_multipart(self, object_key: str, multipart_id: str) -> None: ...
    async def presign_get(self, object_key: str, expires_seconds: int) -> str: ...
    async def stream(self, object_key: str) -> AsyncIterator[bytes]: ...
```

On completion, compare the declared size with S3 metadata, set the file to `QUARANTINED`, enqueue scanning, and write an audit event. Do not treat S3 multipart ETag as a SHA-256 hash.

- [ ] **Step 5: Run file tests and migration round-trip**

Run: `cd server && uv run alembic upgrade head && uv run pytest tests/unit/files/test_file_service.py tests/api/test_file_uploads.py -v`

Expected: file and API tests pass.

- [ ] **Step 6: Run backend regression checks and commit**

Run: `cd server && uv run pytest tests/unit tests/api -v && uv run ruff check . && uv run mypy src`

Expected: all tests and checks pass.

```bash
git add server
git commit -m "feat(files): add resumable uploads and guarded downloads"
```

### Task 8: Scan every completed object with ClamAV before release

**Files:**
- Modify: `server/pyproject.toml`
- Modify: `docker-compose.dev.yml`
- Create: `server/src/superboss/infrastructure/clamav.py`
- Create: `server/src/superboss/workers/celery_app.py`
- Create: `server/src/superboss/workers/schedules.py`
- Create: `server/src/superboss/modules/files/tasks.py`
- Modify: `server/src/superboss/modules/files/service.py`
- Create: `server/tests/unit/files/test_scan_task.py`
- Create: `server/tests/integration/test_clamav_scan.py`

**Interfaces:**
- Produces: `ClamAVScanner.scan(chunks: AsyncIterator[bytes]) -> ScanVerdict`
- Produces: Celery task `superboss.files.scan(file_id: str)`
- Consumes: `ObjectStorage.stream` and file state machine from Task 7

- [ ] **Step 1: Write failing scan state-machine tests**

```python
@pytest.mark.asyncio
async def test_clean_verdict_releases_file(scan_service, quarantined_file, clean_scanner) -> None:
    await scan_service.scan_file(quarantined_file.id)
    assert await file_state(quarantined_file.id) == FileState.CLEAN


@pytest.mark.asyncio
async def test_infected_verdict_never_releases_file(scan_service, quarantined_file, eicar_scanner) -> None:
    await scan_service.scan_file(quarantined_file.id)
    assert await file_state(quarantined_file.id) == FileState.INFECTED
```

Add tests for scanner timeout setting `FAILED`, retries not scanning an already `CLEAN` file, and concurrent tasks acquiring a row lock so only one scan runs.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd server && uv run pytest tests/unit/files/test_scan_task.py -v`

Expected: FAIL because scan service and Celery task do not exist.

- [ ] **Step 3: Implement streaming ClamAV scanning and Celery wiring**

Send the MinIO object to `clamd` through the INSTREAM protocol in bounded chunks; do not mount untrusted objects into the API container. Set the task to `acks_late=True`, `worker_prefetch_multiplier=1`, scan queue concurrency 1, three retries with exponential backoff, and a hard time limit below the upload-session expiry.

Compute SHA-256 while streaming the object. A mismatch with the declared hash sets the file to `FAILED`, records `HASH_MISMATCH`, and prevents download or import completion.

State transitions are atomic:

```text
QUARANTINED -> SCANNING -> CLEAN
QUARANTINED -> SCANNING -> INFECTED
QUARANTINED -> SCANNING -> FAILED
```

Record signature name for infected files without including file content in logs.

Add an hourly scheduler task that finds upload sessions older than 24 hours, aborts the S3 multipart upload, marks the session and file `FAILED`, and records an audit event. The task must skip completed sessions and be idempotent across retries.

- [ ] **Step 4: Verify against a real ClamAV container**

Run: `docker compose -f docker-compose.dev.yml up -d redis minio clamav`

Run: `cd server && uv run pytest tests/integration/test_clamav_scan.py -v`

Expected: a plain text object becomes `CLEAN`; the standard EICAR test object becomes `INFECTED`; neither test exposes file content in logs.

- [ ] **Step 5: Run affected suites and commit**

Run: `cd server && uv run pytest tests/unit/files tests/api/test_file_uploads.py tests/integration/test_clamav_scan.py -v && uv run ruff check . && uv run mypy src`

Expected: all tests and checks pass.

```bash
git add server docker-compose.dev.yml
git commit -m "feat(files): quarantine and scan uploaded objects"
```

### Task 9: Implement least-privilege Kimi device pairing and revocation

**Files:**
- Create: `server/migrations/versions/0004_device_connections.py`
- Create: `server/src/superboss/modules/devices/models.py`
- Create: `server/src/superboss/modules/devices/schemas.py`
- Create: `server/src/superboss/modules/devices/service.py`
- Create: `server/src/superboss/modules/devices/router.py`
- Modify: `server/src/superboss/core/actors.py`
- Modify: `server/src/superboss/api/router.py`
- Create: `server/tests/unit/devices/test_pairing.py`
- Create: `server/tests/api/test_devices.py`

**Interfaces:**
- Produces: `POST /api/v1/owner/devices/pairing-codes`
- Produces: `GET /api/v1/owner/devices`
- Produces: `DELETE /api/v1/owner/devices/{device_id}`
- Produces: `POST /api/v1/device-auth/pair`
- Produces: `POST /api/v1/device-auth/refresh`
- Produces: `GET /api/v1/device-auth/me` with import-target project IDs and names only
- Produces: `DeviceActor` through the shared `Actor` type

- [ ] **Step 1: Write failing pairing and scope tests**

```python
@pytest.mark.asyncio
async def test_pairing_code_is_single_use(device_service, owner) -> None:
    code = await device_service.create_pairing_code(owner.id)
    await device_service.pair(code.raw_code, "Owner-PC")
    with pytest.raises(InvalidPairingCode):
        await device_service.pair(code.raw_code, "Second-PC")


@pytest.mark.asyncio
async def test_revoked_device_fails_immediately(client, paired_device_cookie, device) -> None:
    await revoke_device(device.id)
    response = await client.get("/api/v1/device-auth/me", cookies=paired_device_cookie)
    assert response.status_code == 401
```

API tests must prove STAFF cannot create pairing codes, raw pairing codes are never stored, codes expire after 10 minutes, and device tokens cannot call `/api/v1/projects` or OWNER routes. Pairing-code creation must include at least one allowed import-target project, and `/device-auth/me` must return only the explicitly granted project IDs and names.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd server && uv run pytest tests/unit/devices/test_pairing.py tests/api/test_devices.py -v`

Expected: FAIL because device records and routes do not exist.

- [ ] **Step 3: Implement device credentials and actor resolution**

Store hashed pairing codes, hashed rotating device refresh tokens, and `device_project_grants` rows. Device access tokens contain `sub`, `device_id`, `owner_id`, `scopes`, `session_id`, `iat`, `exp`, and `jti`, but no OWNER role claim. Import-target grants are loaded from the database on every request and are not trusted from token claims.

Device Access Tokens live for 2 hours and rotating device Refresh Tokens live for 14 days. Successful use may rotate the refresh credential without extending a revoked device.

Every device-authenticated request must load `device_connections.revoked_at` and the owning OWNER's current status before authorizing scopes. Audit pair, refresh failure, successful use, and revocation events.

- [ ] **Step 4: Run migration, device tests, and policy regression tests**

Run: `cd server && uv run alembic upgrade head && uv run pytest tests/unit/devices/test_pairing.py tests/api/test_devices.py tests/unit/core/test_authorization.py -v`

Expected: all tests pass.

- [ ] **Step 5: Run backend checks and commit**

Run: `cd server && uv run ruff check . && uv run mypy src`

Expected: both commands exit 0.

```bash
git add server
git commit -m "feat(devices): add scoped Kimi device pairing"
```

### Task 10: Receive minimal K3 result packages through device-scoped APIs

**Files:**
- Create: `server/migrations/versions/0005_import_jobs.py`
- Create: `server/src/superboss/modules/imports/models.py`
- Create: `server/src/superboss/modules/imports/schemas.py`
- Create: `server/src/superboss/modules/imports/service.py`
- Create: `server/src/superboss/modules/imports/router.py`
- Modify: `server/src/superboss/api/router.py`
- Modify: `server/src/superboss/modules/files/service.py`
- Modify: `server/src/superboss/modules/files/tasks.py`
- Create: `server/tests/unit/imports/test_import_service.py`
- Create: `server/tests/api/test_device_imports.py`

**Interfaces:**
- Produces: import states `UPLOADING`, `SCANNING`, `RECEIVED`, `REJECTED`, `CONFLICT`
- Produces: `POST /api/v1/device/import-jobs`
- Produces: `POST /api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/parts/{part_number}`
- Produces: `POST /api/v1/device/import-jobs/{job_id}/attachments/{attachment_id}/complete`
- Produces: `POST /api/v1/device/import-jobs/{job_id}/submit`
- Produces: `GET /api/v1/device/import-jobs/{job_id}`
- Produces: `GET /api/v1/owner/import-jobs`

- [ ] **Step 1: Write failing import contract tests**

```python
@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_same_job(import_service, device_actor, manifest) -> None:
    first = await import_service.create(device_actor, manifest, "kimi-task-001")
    second = await import_service.create(device_actor, manifest, "kimi-task-001")
    assert second.id == first.id


@pytest.mark.asyncio
async def test_submit_waits_for_all_attachments_to_be_clean(import_service, device_actor, job_with_scanning_file) -> None:
    result = await import_service.submit(device_actor, job_with_scanning_file.id)
    assert result.status == ImportStatus.SCANNING
```

API tests must prove a device can read only jobs it created, a device cannot select a project missing from its `device_project_grants`, a changed payload with the same idempotency key returns 409, and rejected/infected attachments prevent `RECEIVED`.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd server && uv run pytest tests/unit/imports/test_import_service.py tests/api/test_device_imports.py -v`

Expected: FAIL because import models and APIs do not exist.

- [ ] **Step 3: Implement the exact K3 manifest schema**

```python
from pydantic import BaseModel, Field


class K3Result(BaseModel):
    model_label: str
    processed_at: datetime
    modification_details: list[str]
    knowledge_points: list[str]
    risks: list[str]
    suggested_title: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)


class ImportJobCreate(BaseModel):
    project_id: UUID
    local_task_id: str
    external_document_reference: str | None = None
    base_sha256: str | None = None
    k3_result: K3Result
    attachments: list[AttachmentDeclaration]
```

Allowed attachment kinds are exactly `ORIGINAL`, `REVISED`, and `K3_RAW`. Require at least one attachment and exactly one `K3_RAW`. Store the raw K3 attachment unchanged. M1 does not turn the package into a document version or knowledge card.

- [ ] **Step 4: Reuse the file upload and scanning state machine**

Each attachment owns a `File` and upload session from Task 7. The import job becomes `RECEIVED` only when all declared attachments are `CLEAN` and their server-computed SHA-256 values match declarations. Any `INFECTED` attachment makes the job `REJECTED`. When `base_sha256` is present, it must match the clean `ORIGINAL` attachment; a mismatch makes the job `CONFLICT`. `external_document_reference` is stored only for later M2 mapping and is not treated as a document-version foreign key in M1.

- [ ] **Step 5: Run import, file, and device suites**

Run: `cd server && uv run alembic upgrade head && uv run pytest tests/unit/imports tests/api/test_device_imports.py tests/unit/files tests/api/test_devices.py -v`

Expected: all tests pass.

- [ ] **Step 6: Run backend checks and commit**

Run: `cd server && uv run ruff check . && uv run mypy src`

Expected: both commands exit 0.

```bash
git add server
git commit -m "feat(imports): receive auditable K3 result packages"
```

### Task 11: Build the Kimi Skill and Windows-safe local connector

**Files:**
- Create: `integrations/kimi-superboss/SKILL.md`
- Create: `integrations/kimi-superboss/connector/pyproject.toml`
- Create: `integrations/kimi-superboss/connector/src/superboss_connector/__init__.py`
- Create: `integrations/kimi-superboss/connector/src/superboss_connector/config.py`
- Create: `integrations/kimi-superboss/connector/src/superboss_connector/credentials.py`
- Create: `integrations/kimi-superboss/connector/src/superboss_connector/client.py`
- Create: `integrations/kimi-superboss/connector/src/superboss_connector/manifest.py`
- Create: `integrations/kimi-superboss/connector/src/superboss_connector/outbox.py`
- Create: `integrations/kimi-superboss/connector/src/superboss_connector/cli.py`
- Create: `integrations/kimi-superboss/connector/tests/test_pair.py`
- Create: `integrations/kimi-superboss/connector/tests/test_submit_resume.py`
- Create: `integrations/kimi-superboss/scripts/build-windows.ps1`

**Interfaces:**
- Produces: CLI commands `superboss pair`, `superboss submit`, `superboss status`, `superboss retry`
- Produces: Windows Credential Manager entry `SuperBoss/KimiConnector/<server-origin>`
- Produces: local outbox entries keyed by server origin and idempotency key
- Consumes: device-auth and import APIs from Tasks 9 and 10

- [ ] **Step 1: Write failing connector tests with a mocked server**

Use `typer`, `httpx`, `pydantic`, `keyring`, and `platformdirs`; use `pytest`, `respx`, and an in-memory keyring backend for tests.

```python
def test_pair_stores_refresh_credential_only(runner, mocked_pair_api, memory_keyring) -> None:
    result = runner.invoke(app, ["pair", "--server", "https://nightforest.com", "--code", "123456", "--name", "Owner-PC"])
    assert result.exit_code == 0
    assert memory_keyring.get_password("SuperBoss/KimiConnector/https://nightforest.com", "device_refresh")
    assert "refresh_token" not in result.stdout
```

The resume test must simulate a network failure after part 2, invoke `retry`, verify upload resumes at part 3, and assert the same idempotency key is reused.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd integrations/kimi-superboss/connector && uv lock && uv run pytest -v`

Expected: FAIL because the connector package does not exist.

- [ ] **Step 3: Implement credential and outbox boundaries**

Store only the rotating device refresh credential in Windows Credential Manager. Keep access tokens in process memory. Store non-secret resumable state under `platformdirs.user_state_dir("SuperBossKimiConnector")`; state includes job ID, idempotency key, attachment IDs, completed ETags, file paths, sizes, and SHA-256 hashes.

Before every retry, recompute each source file SHA-256. If a file changed, stop with exit code 4 and require a new submit command; never resume changed content under an old idempotency key.

- [ ] **Step 4: Implement CLI calls and stable exit codes**

Use exit codes: `0` success, `2` invalid manifest, `3` authentication/pairing failure, `4` local file changed, `5` server rejection, `6` temporary network failure. `submit` accepts one manifest path and never accepts credentials as CLI arguments.

```powershell
superboss submit --server https://nightforest.com --manifest D:\KimiWork\exports\job-001.json
superboss status --server https://nightforest.com --job-id 019f...
superboss retry --server https://nightforest.com
```

- [ ] **Step 5: Write the Kimi workflow Skill**

The `SKILL.md` must instruct Kimi to:

1. finish document work before offering synchronization;
2. produce the exact `K3Result` fields from Task 10;
3. show project, attachments, modification count, knowledge-point count, and risks to OWNER;
4. require explicit confirmation before invoking `superboss submit`;
5. report the server job ID and status without claiming archive completion when status is `SCANNING`;
6. never put API credentials into the manifest or chat output.

- [ ] **Step 6: Package and verify on Windows**

`build-windows.ps1` must run tests, build a wheel, and use PyInstaller to produce `dist/superboss.exe`. It must fail if tests fail.

Run: `powershell -ExecutionPolicy Bypass -File integrations/kimi-superboss/scripts/build-windows.ps1`

Expected: tests pass and `integrations/kimi-superboss/connector/dist/superboss.exe` runs `--help` with exit code 0.

- [ ] **Step 7: Commit the connector and Skill**

```bash
git add integrations/kimi-superboss
git commit -m "feat(kimi): add secure result-package connector"
```

### Task 12: Add browser authentication, role-aware routing, and the owner project UI

**Files:**
- Create: `web/src/api/http.ts`
- Create: `web/src/api/auth.ts`
- Create: `web/src/api/projects.ts`
- Create: `web/src/stores/auth.ts`
- Modify: `web/src/app/router.ts`
- Create: `web/src/layouts/AppLayout.vue`
- Create: `web/src/pages/LoginPage.vue`
- Create: `web/src/pages/AuthCallbackPage.vue`
- Create: `web/src/pages/ForbiddenPage.vue`
- Create: `web/src/pages/owner/OwnerHomePage.vue`
- Create: `web/src/pages/owner/ProjectsPage.vue`
- Create: `web/tests/auth-routing.test.ts`
- Create: `web/tests/projects-page.test.ts`

**Interfaces:**
- Consumes: `/auth/wecom/start`, `/auth/callback`, `/auth/refresh`, `/auth/logout`, `/auth/me`, and project APIs
- Produces: `useAuthStore()` with `user`, `bootstrap()`, `refresh()`, and `logout()`
- Produces: route metadata `requiresAuth` and `roles`

- [ ] **Step 1: Write failing routing and project-page tests**

```ts
test('redirects STAFF away from owner routes', async () => {
  mockCurrentUser({ role: 'STAFF' })
  const router = makeRouter('/owner/projects')
  await router.isReady()
  expect(router.currentRoute.value.path).toBe('/forbidden')
})
```

Add tests proving unauthenticated users go to `/login`, OWNER can render the project list, and creating a test project sends `is_test: true` to the API.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd web && npm run test -- --run tests/auth-routing.test.ts tests/projects-page.test.ts`

Expected: FAIL because stores and pages do not exist.

- [ ] **Step 3: Implement cookie-based API access**

Configure Axios with `withCredentials: true`; do not read or write access/refresh tokens in JavaScript. On one 401, call `/auth/refresh` once and retry the original request; prevent multiple simultaneous refresh calls with a shared promise. A second 401 clears the store and routes to `/login`.

Read the non-secret `XSRF-TOKEN` cookie and send it as `X-CSRF-Token` on POST, PUT, PATCH, and DELETE. Never expose the HttpOnly access or refresh cookies to JavaScript.

- [ ] **Step 4: Implement route guards and project UI**

The guard uses `/auth/me` for display routing only. The backend remains authoritative. OWNER project creation collects `name` and `is_test`; label test projects visibly as `验收测试`.

- [ ] **Step 5: Run frontend tests, typecheck, and build**

Run: `cd web && npm run test -- --run && npm run typecheck && npm run build`

Expected: all tests pass, TypeScript exits 0, and build succeeds.

- [ ] **Step 6: Commit browser auth and projects UI**

```bash
git add web
git commit -m "feat(web): add owner authentication and projects"
```

### Task 13: Add owner cloud-drive, Kimi-device, and import-status pages

**Files:**
- Create: `web/src/api/files.ts`
- Create: `web/src/api/devices.ts`
- Create: `web/src/api/imports.ts`
- Create: `web/src/components/files/MultipartUploader.vue`
- Create: `web/src/pages/owner/DrivePage.vue`
- Create: `web/src/pages/owner/DevicesPage.vue`
- Create: `web/src/pages/owner/ImportJobsPage.vue`
- Modify: `web/src/app/router.ts`
- Modify: `web/src/layouts/AppLayout.vue`
- Create: `web/tests/multipart-uploader.test.ts`
- Create: `web/tests/devices-page.test.ts`
- Create: `web/tests/import-jobs-page.test.ts`

**Interfaces:**
- Consumes: file, device, and owner import APIs from Tasks 7, 9, and 10
- Produces: resumable browser upload UI with persisted non-secret progress
- Produces: device pairing/revocation and import-status owner pages

- [ ] **Step 1: Write failing UI tests**

The multipart test must prove an interrupted upload resumes with the first incomplete part and displays `扫描中` after completion. The device test must prove the raw pairing code is shown once and revocation requires confirmation. The import test must render `UPLOADING`, `SCANNING`, `RECEIVED`, `REJECTED`, and `CONFLICT` with distinct Chinese labels.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd web && npm run test -- --run tests/multipart-uploader.test.ts tests/devices-page.test.ts tests/import-jobs-page.test.ts`

Expected: FAIL because the API clients and pages do not exist.

- [ ] **Step 3: Implement browser multipart upload**

Compute SHA-256 in a Web Worker, request one part URL at a time, upload 8 MiB parts with at most three concurrent requests, and persist only `upload_id`, file fingerprint, and completed ETags in IndexedDB. Never persist session cookies or signed URLs. Remove progress after server completion.

- [ ] **Step 4: Implement device and import owner views**

Devices show name, first paired time, last used time, granted import-target projects, and revoked status. Pairing-code creation requires selecting at least one target project and displays a 10-minute expiry. Imports show project, local Kimi task ID, model label, attachment scan states, final status, and rejection/conflict reason.

- [ ] **Step 5: Run frontend verification and commit**

Run: `cd web && npm run test -- --run && npm run typecheck && npm run build`

Expected: all tests and checks pass.

```bash
git add web
git commit -m "feat(web): add drive and Kimi connection management"
```

### Task 14: Assemble production containers and the 443-only Nginx boundary

**Files:**
- Create: `server/Dockerfile`
- Create: `web/Dockerfile`
- Create: `docker-compose.yml`
- Create: `ops/nginx/nginx.conf`
- Create: `ops/nginx/conf.d/superboss.conf`
- Create: `ops/nginx/allowlist.conf.example`
- Modify: `.env.example`
- Create: `server/src/superboss/modules/health/readiness.py`
- Modify: `server/src/superboss/modules/health/router.py`
- Create: `server/tests/api/test_readiness.py`
- Create: `tests/compose/smoke.ps1`
- Create: `tests/compose/smoke.sh`

**Interfaces:**
- Produces: `GET /api/v1/health/ready` with dependency status
- Produces: production services `nginx`, `web`, `api`, `worker`, `scheduler`, `postgres`, `redis`, `minio`, and `clamav`
- Produces: public hosts `nightforest.com` and `objects.nightforest.com`, both on port 443

- [ ] **Step 1: Write failing readiness tests**

```python
def test_readiness_reports_failed_dependency(client, broken_redis) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["dependencies"]["redis"] == "failed"
```

Add a passing test that reports `postgres`, `redis`, `minio`, and `clamav` as `ok` without exposing connection strings or credentials.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd server && uv run pytest tests/api/test_readiness.py -v`

Expected: FAIL because readiness probes do not exist.

- [ ] **Step 3: Implement containers with non-root application users**

Use multi-stage builds. The API, worker, scheduler, and web runtime must run as non-root users. Mount PostgreSQL, Redis, MinIO, and ClamAV signature data on named volumes. Set worker scan concurrency to 1 on the 4C8G host.

Use PostgreSQL 18.x, Redis 8.x, MinIO `RELEASE.2025-06-13T11-33-47Z`, and ClamAV 1.5.3 tags. Resolve digests with `docker buildx imagetools inspect <image>:<tag> --format '{{json .Manifest.Digest}}'`, place the resulting `image@sha256:...` value in compose, and retain the readable tag in an adjacent comment.

- [ ] **Step 4: Implement Nginx ingress and object host**

Only Nginx publishes host port 443. PostgreSQL, Redis, MinIO, ClamAV, API, and web stay on internal networks. `nightforest.com` serves the SPA and `/api/`; `objects.nightforest.com` proxies S3 traffic to MinIO. Both include the same IP allowlist and TLS policy. Do not expose the MinIO console in production.

Read both hostnames from `SUPERBOSS_APP_HOST` and `SUPERBOSS_OBJECTS_HOST`. Before备案 approval, the runbook uses local hosts-file entries pointing those temporary hostnames to the server IP plus a locally trusted temporary certificate; business code and object keys do not change when DNS is switched.

Set request limits, security headers, upload timeouts, and trusted proxy headers explicitly. Use `allowlist.conf.example` with documentation-only RFC 5737 example addresses, never real home or office IPs.

- [ ] **Step 5: Implement compose smoke scripts**

The scripts must:

1. validate compose configuration;
2. build images;
3. start the stack;
4. run Alembic migration;
5. poll readiness for at most 600 seconds, using short repeated checks rather than one long sleep;
6. assert that only port 443 is published by production compose;
7. assert PostgreSQL, Redis, MinIO console, and ClamAV ports are not published;
8. stop without deleting named volumes.

Run: `powershell -ExecutionPolicy Bypass -File tests/compose/smoke.ps1`

Expected: exits 0 and prints `M1_COMPOSE_SMOKE_PASSED`.

- [ ] **Step 6: Run readiness and configuration checks**

Run: `cd server && uv run pytest tests/api/test_readiness.py -v`

Run: `docker compose config --quiet`

Expected: tests pass and compose validation exits 0.

- [ ] **Step 7: Commit production composition**

```bash
git add .env.example docker-compose.yml ops server/Dockerfile web/Dockerfile tests/compose
git commit -m "feat(ops): add private production composition"
```

### Task 15: Add OWNER-managed STAFF whitelist and project assignments

**Files:**
- Create: `server/src/superboss/modules/users/schemas.py`
- Create: `server/src/superboss/modules/users/service.py`
- Create: `server/src/superboss/modules/users/router.py`
- Modify: `server/src/superboss/modules/users/repository.py`
- Modify: `server/src/superboss/api/router.py`
- Create: `server/tests/unit/users/test_user_service.py`
- Create: `server/tests/api/test_owner_users.py`
- Create: `web/src/api/users.ts`
- Create: `web/src/pages/owner/UsersPage.vue`
- Modify: `web/src/app/router.ts`
- Modify: `web/src/layouts/AppLayout.vue`
- Create: `web/tests/users-page.test.ts`

**Interfaces:**
- Produces: `GET /api/v1/owner/users`
- Produces: `POST /api/v1/owner/users`
- Produces: `PATCH /api/v1/owner/users/{user_id}`
- Produces: `PUT /api/v1/owner/users/{user_id}/projects`
- Produces: OWNER page `/owner/users`

- [ ] **Step 1: Write failing whitelist and protected-OWNER tests**

```python
@pytest.mark.asyncio
async def test_owner_can_create_staff_whitelist_user(client, owner_cookie, project) -> None:
    response = await client.post(
        "/api/v1/owner/users",
        cookies=owner_cookie,
        json={"wecom_userid": "staff-acceptance", "display_name": "员工验收账号", "project_ids": [str(project.id)]},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "STAFF"


@pytest.mark.asyncio
async def test_owner_record_cannot_be_disabled(client, owner_cookie, owner) -> None:
    response = await client.patch(
        f"/api/v1/owner/users/{owner.id}",
        cookies=owner_cookie,
        json={"status": "DISABLED"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OWNER_PROTECTED"
```

Add tests proving STAFF receives 403 from every `/owner/users` route, duplicate WeCom userid returns 409, user creation cannot accept a role field, disabling STAFF revokes all its sessions, and project assignment replaces memberships atomically.

- [ ] **Step 2: Run backend tests and verify failure**

Run: `cd server && uv run pytest tests/unit/users/test_user_service.py tests/api/test_owner_users.py -v`

Expected: FAIL because whitelist services and routes do not exist.

- [ ] **Step 3: Implement whitelist service and session revocation**

`StaffCreate` accepts only `wecom_userid`, `display_name`, and `project_ids`; the service always assigns `Role.STAFF` and `UserStatus.ACTIVE`. `StaffUpdate` accepts `display_name` and `status`. Disabling a STAFF user revokes all browser sessions in the same database transaction and writes an audit event after commit.

`PUT /projects` verifies every project exists, locks the user's membership rows, replaces the set atomically, and returns the resulting project IDs. OWNER cannot be passed to STAFF-only mutation methods.

- [ ] **Step 4: Write the failing OWNER users-page test**

```ts
test('creates and displays a STAFF whitelist account without a role selector', async () => {
  server.use(
    http.post('/api/v1/owner/users', async ({ request }) => {
      const body = await request.json()
      return HttpResponse.json(
        { id: 'staff-1', role: 'STAFF', status: 'ACTIVE', ...body },
        { status: 201 },
      )
    }),
  )
  renderUsersPageWithRealApiClient()
  expect(screen.queryByLabelText('角色')).not.toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('企业微信 UserID'), 'staff-acceptance')
  await userEvent.click(screen.getByRole('button', { name: '添加员工' }))
  expect(await screen.findByText('staff-acceptance')).toBeInTheDocument()
  expect(screen.getByText('STAFF')).toBeInTheDocument()
})
```

- [ ] **Step 5: Implement the OWNER whitelist page**

Show userid, display name, status, assigned projects, and last login time. Provide add, enable/disable, and project-assignment actions. Do not show password controls, OWNER role controls, or a delete action for the OWNER record. Require confirmation before disabling a user.

- [ ] **Step 6: Run backend and frontend verification**

Run: `cd server && uv run pytest tests/unit/users/test_user_service.py tests/api/test_owner_users.py tests/api/test_wecom_auth.py -v && uv run ruff check . && uv run mypy src`

Run: `cd web && npm run test -- --run tests/users-page.test.ts && npm run typecheck && npm run build`

Expected: all tests and checks pass.

- [ ] **Step 7: Commit whitelist management**

```bash
git add server web
git commit -m "feat(users): add owner-managed staff whitelist"
```

### Task 16: Prove the M1 owner/Kimi loop end to end and document operation

**Files:**
- Create: `tests/e2e/playwright.config.ts`
- Create: `tests/e2e/package.json`
- Create: `tests/e2e/package-lock.json`
- Create: `tests/e2e/specs/owner-login-project.spec.ts`
- Create: `tests/e2e/specs/file-quarantine.spec.ts`
- Create: `tests/e2e/specs/device-import.spec.ts`
- Create: `tests/e2e/specs/staff-denial.spec.ts`
- Create: `server/scripts/seed_acceptance.py`
- Create: `docs/runbooks/m1-local-development.md`
- Create: `docs/runbooks/m1-owner-acceptance.md`
- Create: `docs/runbooks/kimi-connector-installation.md`
- Create: `docs/runbooks/wecom-setup.md`
- Create: `docs/runbooks/backup-before-m1-pilot.md`
- Create: `README.md`

**Interfaces:**
- Produces: deterministic acceptance seed with one OWNER, one STAFF test account, one normal project, and one `is_test=true` project
- Produces: reproducible M1 verification command sequence
- Consumes: every M1 API, UI, connector, and compose interface

- [ ] **Step 1: Write failing end-to-end scenarios**

The four Playwright specifications must assert:

- OWNER login callback reaches the owner home and can create an `验收测试` project;
- an uploaded file shows `扫描中`, then `可下载`, and cannot be downloaded before `CLEAN`;
- OWNER pairs a device, the connector submits one fixture package, and the import reaches `RECEIVED` without creating an M2 document version;
- the distinct STAFF test account receives 403 from project creation, device, import-list, and foreign-project file endpoints even when requests are sent directly.

Configure Playwright to ignore certificate errors only when `E2E_BASE_URL` points to the documented local self-signed environment. Production acceptance must use a trusted certificate and must not set `ignoreHTTPSErrors`.

- [ ] **Step 2: Run end-to-end tests and verify initial failure**

Run: `cd tests/e2e && npm install && npm run test`

Expected: FAIL until fixtures, seed command, and any missing integration wiring are complete.

- [ ] **Step 3: Add deterministic acceptance fixtures and seed command**

`seed_acceptance.py` accepts WeCom userids through environment variables, refuses to run when `SUPERBOSS_ENVIRONMENT=production` unless `--confirm-production-seed` is passed, creates no passwords, and prints only created record IDs. It must not change an existing OWNER.

- [ ] **Step 4: Complete integration wiring exposed by E2E failures**

Fix only failures within M1 scope. Each fix receives a focused regression assertion in the relevant backend or frontend suite before rerunning Playwright. Do not add model calls, document version chains, employee workflow pages, financial logic, or one-click deployment.

- [ ] **Step 5: Write operator and OWNER acceptance runbooks**

The runbooks must contain exact commands for local startup, WeCom trusted-domain configuration, OWNER whitelist bootstrap, STAFF test account setup, Kimi connector pairing, clean and EICAR upload tests, device revocation, log locations, and safe shutdown. Use variable names from `.env.example`; never include real credentials, IP addresses, corp IDs, or userids.

- [ ] **Step 6: Run the complete M1 verification suite**

Run:

```powershell
cd D:\SuperBoss\server
uv run alembic upgrade head
uv run pytest -v
uv run ruff check .
uv run mypy src

cd D:\SuperBoss\web
npm ci
npm run test -- --run
npm run typecheck
npm run build

cd D:\SuperBoss\integrations\kimi-superboss\connector
uv run pytest -v

cd D:\SuperBoss\tests\e2e
npm ci
npm run test

cd D:\SuperBoss
docker compose config --quiet
powershell -ExecutionPolicy Bypass -File tests\compose\smoke.ps1
```

Expected: every command exits 0; backend, frontend, connector, E2E, and compose smoke suites report zero failures.

- [ ] **Step 7: Perform manual OWNER and STAFF acceptance**

Using separate browser profiles, verify OWNER and the distinct STAFF account against the checklist in `docs/runbooks/m1-owner-acceptance.md`. Record date, browser version, account role, project IDs, file IDs, import job ID, and pass/fail for every step; do not record cookies or tokens.

- [ ] **Step 8: Commit M1 acceptance assets**

```bash
git add README.md docs/runbooks server/scripts tests/e2e
git commit -m "test(m1): add owner and Kimi acceptance flow"
```

## M1 Definition of Done

M1 is complete only when all of the following are evidenced in fresh command output:

- All migrations apply to an empty PostgreSQL 18 database.
- Backend unit, API, integration, lint, and type checks pass.
- Frontend unit tests, type checks, and production build pass.
- Connector tests and Windows packaging pass.
- Production compose publishes only 443 and reaches ready state.
- A clean upload becomes downloadable; an EICAR upload remains unavailable.
- OWNER can pair and revoke a Kimi device.
- The connector can resume an interrupted K3 package and finish at `RECEIVED` once.
- The separate STAFF acceptance account cannot access OWNER or device APIs.
- Audit records exist for login, denied access, upload, download, pairing, revocation, and import submission.
- The OWNER has completed and signed the M1 manual acceptance checklist.

## References

- Technical baseline: `docs/superpowers/specs/2026-08-09-technical-foundation-design.md`
- Frozen requirements: `docs/01-需求定稿.md`
- Original architecture: `docs/02-架构设计.md`
- FastAPI containers: https://fastapi.tiangolo.com/deployment/docker/
- uv projects: https://docs.astral.sh/uv/guides/projects/
- PostgreSQL 18: https://www.postgresql.org/docs/18/
- MinIO containers: https://min.io/docs/minio/container/index.html
- ClamAV Docker: https://docs.clamav.net/manual/Installing/Docker.html
- Kimi Skills: https://github.com/moonshotai/kimi-cli/blob/main/docs/en/customization/skills.md
- Kimi Work: https://www.kimi.com/zh-cn/products/kimi-work
- WeCom OAuth overview: https://open.work.weixin.qq.com/api/doc/90000/90135/91437
