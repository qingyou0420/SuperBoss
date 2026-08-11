# Local Password Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace WeCom OAuth with secure local username/password authentication while preserving the existing authorization, rotating browser sessions, audit, device, file, import, and connector contracts.

**Architecture:** PostgreSQL remains authoritative for users and server-side sessions. A focused Argon2id password module supplies hashing and verification; FastAPI exposes same-origin CSRF-protected login and password-change routes; Vue uses the existing narrow HTTP facade and cookie sessions. The current phase creates one local OWNER on `app.localhost`; company deployment, the Night Forest portal, formal domains, and real STAFF onboarding stay deferred.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 async, PostgreSQL 17/18, Alembic, Argon2id, PyJWT, Vue 3, Pinia, Vue Router, TypeScript, Vitest, Playwright, Docker Compose.

## Global Constraints

- Use TDD for every behavior: permanent RED, narrow GREEN, focused gate, then commit.
- The development database may be reset, but migration 0018 must fail before mutation if identity/session state is nonempty.
- Username is exact lowercase ASCII matching `[a-z][a-z0-9._-]{2,31}`.
- Password is exact input: 12–128 Unicode code points, bounded UTF-8 bytes, no trim or normalization, and no NUL/control characters.
- Passwords use Argon2id and never enter logs, audit metadata, URLs, environment variables, browser storage, or error responses.
- Browser access remains exactly two hours; refresh remains exactly fourteen days with rotation and server-side session validation.
- Login, password change, refresh, logout, and OWNER credential operations retain cookie-only browser auth and exact CSRF enforcement.
- Device pairing and device-token authentication must not change.
- Current delivery uses only `app.localhost`, one OWNER, and synthetic data; no public endpoint or formal-domain claim.
- Do not implement the future `night-forest.com` portal, company DNS, VPN, Tencent Cloud deployment, or real employee onboarding.

---

## File map

**Backend identity and migration**

- Create `server/migrations/versions/0018_local_password_auth.py`: guarded identity schema replacement.
- Create `server/src/superboss/modules/auth/passwords.py`: password validation, Argon2id hash/verify/rehash, generated temporary credentials.
- Create `server/scripts/manage_local_owner.py`: interactive bootstrap and OWNER recovery.
- Modify `server/src/superboss/modules/users/models.py`: local credential columns and username constraints.
- Modify `server/src/superboss/modules/users/repository.py`: username lookup, row locks, session revocation.
- Modify `server/src/superboss/modules/auth/models.py`: remove `OAuthState`.
- Modify `server/src/superboss/modules/auth/repository.py`: remove OAuth state methods and add session queries needed by password rotation.
- Modify `server/src/superboss/modules/auth/schemas.py`: strict login/change/me DTOs.
- Modify `server/src/superboss/modules/auth/service.py`: login, throttle, refresh hint, password change, atomic sessions.
- Modify `server/src/superboss/modules/auth/router.py`: local auth HTTP boundary and cookies.
- Modify `server/src/superboss/core/actors.py`: deny business actors while password change is required.

**OWNER account management**

- Modify `server/src/superboss/modules/users/schemas.py`, `service.py`, and `router.py`: username-based STAFF create/reset.

**Runtime cleanup**

- Modify `server/src/superboss/main.py` and `core/config.py`: remove provider construction/config.
- Delete `server/src/superboss/infrastructure/wecom.py`.
- Modify `.env.example`, `docker-compose.yml`, and `docker-compose.dev.yml`: remove WeCom variables.

**Frontend**

- Rewrite `web/src/api/auth.ts`: local login, CSRF bootstrap, me, password change.
- Modify `web/src/api/http.ts`: refresh only on the exact server refreshable header.
- Modify `web/src/stores/auth.ts`: local login and password-change state.
- Rewrite `web/src/pages/LoginPage.vue`.
- Create `web/src/pages/PasswordChangePage.vue`.
- Delete `web/src/pages/AuthCallbackPage.vue`.
- Modify `web/src/app/router.ts`: remove callback route and enforce password change.
- Modify `web/src/api/users.ts` and `web/src/pages/owner/UsersPage.vue`: usernames and one-time temporary credentials.

**Tests and docs**

- Create `server/tests/unit/auth/test_passwords.py`.
- Create `server/tests/api/test_local_auth.py`.
- Create `server/tests/integration/test_local_identity_schema.py`.
- Replace `server/tests/unit/auth/test_wecom_protocol.py` with local-password tests, then delete it when equivalent coverage exists.
- Adapt user fixtures throughout `server/tests` from `wecom_userid` to `username` plus password hashes.
- Rewrite affected `web/tests/auth-*.test.ts` and `web/tests/users-page.test.ts`.
- Adapt `server/scripts/seed_acceptance.py`, `server/tests/integration/test_acceptance_seed.py`, and `tests/e2e` auth support.
- Replace `docs/runbooks/wecom-setup.md` with `docs/runbooks/local-auth-setup.md` and update current requirements/architecture/runbooks/README.

---

### Task 1: Lock the local identity schema in PostgreSQL

**Files:**
- Create: `server/tests/integration/test_local_identity_schema.py`
- Create: `server/migrations/versions/0018_local_password_auth.py`
- Modify: `server/src/superboss/modules/users/models.py`
- Modify: `server/src/superboss/modules/auth/models.py`
- Modify: `server/tests/conftest.py`

**Interfaces:**
- Produces: `User.username`, `User.password_hash`, `User.must_change_password`, `User.password_changed_at`, `User.failed_login_count`, and `User.locked_until`.
- Removes: `User.wecom_userid` and `OAuthState`.

- [ ] **Step 1: Write migration and ORM RED tests**

Add real-PostgreSQL tests that assert the exact columns, unique/check constraints, timezone-aware columns, no `oauth_states` table, and rejection of invalid usernames. Add a guarded migration test that inserts one user/session or OAuth state at 0017 and proves upgrade aborts before any catalog mutation.

```python
@pytest.mark.parametrize("username", ["Aaa", "ab", "1owner", "owner ", "用户"])
async def test_users_reject_invalid_local_username(db_session, username):
    db_session.add(User(username=username, password_hash=VALID_HASH,
                        role=Role.STAFF, status=UserStatus.ACTIVE))
    with pytest.raises(IntegrityError):
        await db_session.flush()
```

- [ ] **Step 2: Run the schema RED**

Run: `cd server && uv run pytest -q tests/integration/test_local_identity_schema.py`

Expected: failures for missing revision/columns and surviving WeCom/OAuth schema, with no fixture or connection failures.

- [ ] **Step 3: Implement revision 0018 and matching ORM models**

The revision must guard nonempty `users`, `auth_sessions`, and `oauth_states` before its first mutation; on an empty schema it drops OAuth state, replaces the identifier, adds credential columns, and installs explicitly named checks. Downgrade must likewise refuse when local identity/session rows exist.

```python
op.add_column("users", sa.Column("username", sa.String(32), nullable=False))
op.create_check_constraint(
    "ck_users_username",
    "users",
    "username ~ '^[a-z][a-z0-9._-]{2,31}$'",
)
```

- [ ] **Step 4: Update test factories to build valid local users**

Centralize a valid test-only Argon2id hash constant/factory in `server/tests/conftest.py`; do not weaken production non-null constraints or compute an expensive hash in every fixture.

- [ ] **Step 5: Run migration gates**

Run:

```powershell
cd server
uv run pytest -q tests/integration/test_local_identity_schema.py tests/integration/test_identity_schema.py
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Expected: all tests pass; current/heads are the single `0018_local_password_auth` head; no drift.

- [ ] **Step 6: Commit the schema unit**

```powershell
git add server/migrations/versions/0018_local_password_auth.py server/src/superboss/modules/users/models.py server/src/superboss/modules/auth/models.py server/tests/conftest.py server/tests/integration/test_local_identity_schema.py server/tests/integration/test_identity_schema.py
git commit -m "feat(auth): add local identity schema"
```

### Task 2: Add the password primitive and server-local OWNER management

**Files:**
- Create: `server/src/superboss/modules/auth/passwords.py`
- Create: `server/tests/unit/auth/test_passwords.py`
- Create: `server/scripts/manage_local_owner.py`
- Create: `server/tests/integration/test_manage_local_owner.py`
- Modify: `server/pyproject.toml`
- Modify: `server/uv.lock`

**Interfaces:**
- Produces: `validate_password(raw: str) -> None`, `hash_password(raw: str) -> str`, `verify_password(hash_value: str, raw: str) -> PasswordVerification`, and `new_temporary_password() -> str`.
- Produces CLI commands: `bootstrap` and `reset`.

- [ ] **Step 1: Write password and CLI RED tests**

Cover exact Unicode/code-point/byte boundaries, controls, no normalization, Argon2id encoded output, valid/invalid/dummy verification, rehash signaling, generated-password entropy/grammar, non-echo prompts, duplicate OWNER refusal, reset session revocation, audit, and output secret scans.

```python
def test_password_is_not_trimmed_or_normalized():
    encoded = hash_password("correct horse battery staple")
    assert verify_password(encoded, "correct horse battery staple").valid
    assert not verify_password(encoded, "correct horse battery staple ").valid
```

- [ ] **Step 2: Run focused RED**

Run: `cd server && uv run pytest -q tests/unit/auth/test_passwords.py tests/integration/test_manage_local_owner.py`

Expected: import/file-not-found failures only.

- [ ] **Step 3: Add and lock Argon2id**

Add `argon2-cffi` as a production dependency and update the uv lock. Implement a small immutable result type:

```python
@dataclass(frozen=True)
class PasswordVerification:
    valid: bool
    needs_rehash: bool
```

Bind the password hasher and parameters privately at module load; map malformed stored hashes to invalid credentials without leaking parser detail.

- [ ] **Step 4: Implement the interactive management command**

Use `getpass.getpass`, explicit subcommands, a single async transaction, and the repository/service boundaries. Neither subcommand accepts a password CLI option or environment variable. A SUCCESS audit must commit with the user/password operation.

- [ ] **Step 5: Run password, CLI, lint, and type gates**

Run:

```powershell
cd server
uv lock --check
uv run pytest -q tests/unit/auth/test_passwords.py tests/integration/test_manage_local_owner.py
uv run ruff check src scripts tests/unit/auth/test_passwords.py tests/integration/test_manage_local_owner.py
uv run mypy src
```

- [ ] **Step 6: Commit the password unit**

```powershell
git add server/pyproject.toml server/uv.lock server/src/superboss/modules/auth/passwords.py server/scripts/manage_local_owner.py server/tests/unit/auth/test_passwords.py server/tests/integration/test_manage_local_owner.py
git commit -m "feat(auth): add local password primitives"
```

### Task 3: Replace WeCom login with local cookie authentication

**Files:**
- Create: `server/tests/api/test_local_auth.py`
- Modify: `server/src/superboss/modules/auth/schemas.py`
- Modify: `server/src/superboss/modules/auth/repository.py`
- Modify: `server/src/superboss/modules/auth/service.py`
- Modify: `server/src/superboss/modules/auth/router.py`
- Modify: `server/src/superboss/main.py`
- Modify: `server/tests/unit/auth/test_token_rotation.py`
- Modify: `server/tests/api/test_audit_events.py`

**Interfaces:**
- Produces: `AuthService.login(username: str, password: str) -> CompletedLogin`.
- Produces routes: `GET /auth/csrf`, `POST /auth/login`, existing `refresh/logout/me` with strict local shapes.
- Produces response header: `X-SuperBoss-Refreshable: 1` only when `/auth/me` sees a refresh credential worth attempting.

- [ ] **Step 1: Write local login HTTP RED tests**

Cover OpenAPI route exactness; CSRF bootstrap; missing/mismatched CSRF; valid OWNER/STAFF; unknown/wrong/disabled/locked credentials; exact cookies; 2h/14d lifetimes; audit SUCCESS/DENIED/fault; duplicate request; no Authorization acceptance; exact `me`; refresh hint present/absent; and anonymous refresh ordering.

```python
response = await client.post(
    "/api/v1/auth/login",
    json={"username": "owner", "password": VALID_PASSWORD},
    headers={"X-CSRF-Token": csrf},
    cookies={"XSRF-TOKEN": csrf},
)
assert response.status_code == 204
assert response.content == b""
assert set(response.cookies) >= {"access_token", "refresh_token", "XSRF-TOKEN"}
```

- [ ] **Step 2: Run the auth RED**

Run: `cd server && uv run pytest -q tests/api/test_local_auth.py tests/unit/auth/test_token_rotation.py`

Expected: missing local routes/service methods and old WeCom response fields.

- [ ] **Step 3: Implement login and bounded failure state**

Lock a real user row before changing counters. Verify a dummy hash for unknown users. Use the same public failure for wrong, unknown, disabled, and locked identities. Reset counters only on success. Do not set cookies until the login SUCCESS audit is durable.

- [ ] **Step 4: Implement CSRF bootstrap and refresh hint**

`GET /auth/csrf` rotates only the readable CSRF cookie. `/auth/me` includes the exact refreshable header on its `401` only when a refresh cookie is present. A refresh credential continues requiring matching CSRF; an anonymous request does not create or rotate a session.

- [ ] **Step 5: Run focused auth and audit gates**

Run:

```powershell
cd server
uv run pytest -q tests/api/test_local_auth.py tests/unit/auth/test_token_rotation.py tests/unit/auth/test_token_lifetimes.py tests/api/test_audit_events.py
uv run ruff check src tests/api/test_local_auth.py
uv run mypy src
```

- [ ] **Step 6: Commit the login unit**

```powershell
git add server/src/superboss/modules/auth server/src/superboss/main.py server/tests/api/test_local_auth.py server/tests/unit/auth/test_token_rotation.py server/tests/api/test_audit_events.py
git commit -m "feat(auth): add local browser login"
```

### Task 4: Enforce first password change and atomic session replacement

**Files:**
- Modify: `server/tests/api/test_local_auth.py`
- Modify: `server/src/superboss/modules/auth/service.py`
- Modify: `server/src/superboss/modules/auth/router.py`
- Modify: `server/src/superboss/core/actors.py`
- Modify: `server/tests/unit/core/test_authorization.py`

**Interfaces:**
- Produces: `AuthService.change_password(user, current_password, new_password) -> SessionPair`.
- Produces route: `POST /auth/password/change` -> `204` and replacement cookies.
- Enforces: password-change-required sessions can call only `me`, `password/change`, and `logout`.

- [ ] **Step 1: Add RED for forced-change state**

Test current-password verification, password reuse, invalid new password, all business routes denied, concurrency, rollback on hash/audit/session failure, all old sessions revoked, and exactly one replacement session/cookie pair.

- [ ] **Step 2: Run the forced-change RED**

Run: `cd server && uv run pytest -q tests/api/test_local_auth.py -k "password_change or must_change"`

- [ ] **Step 3: Implement the transaction and actor gate**

Lock the user, verify/hash before mutation, revoke sessions in deterministic order, issue the replacement pair, and commit the audit with the credential update. Extend actor resolution with a dedicated safe error such as `PASSWORD_CHANGE_REQUIRED` before business authorization.

- [ ] **Step 4: Run auth plus protected-route regression**

Run:

```powershell
cd server
uv run pytest -q tests/api/test_local_auth.py tests/unit/core/test_authorization.py tests/api/test_projects.py tests/api/test_file_uploads.py tests/api/test_devices.py tests/api/test_device_imports.py
```

- [ ] **Step 5: Commit the password-change unit**

```powershell
git add server/src/superboss/modules/auth server/src/superboss/core/actors.py server/tests/api/test_local_auth.py server/tests/unit/core/test_authorization.py
git commit -m "feat(auth): require initial password change"
```

### Task 5: Convert OWNER user administration to local credentials

**Files:**
- Modify: `server/src/superboss/modules/users/repository.py`
- Modify: `server/src/superboss/modules/users/schemas.py`
- Modify: `server/src/superboss/modules/users/service.py`
- Modify: `server/src/superboss/modules/users/router.py`
- Modify: `server/tests/unit/users/test_user_service.py`
- Modify: `server/tests/api/test_owner_users.py`

**Interfaces:**
- `POST /owner/users` consumes `{username, display_name, project_ids}` and returns exact `{user, temporary_password}` once.
- `POST /owner/users/{user_id}/password-reset` returns exact `{temporary_password}` once.
- Existing list/update/project routes return `username`, never credential fields.

- [ ] **Step 1: Write STAFF local-credential RED tests**

Cover username uniqueness/grammar, generated temporary password, first-change flag, exact response, response-loss/reset recovery, no OWNER web reset, disable/session revocation, audit fault rollback, project atomicity, concurrency, and output/metadata secret scans.

- [ ] **Step 2: Run the OWNER users RED**

Run: `cd server && uv run pytest -q tests/unit/users/test_user_service.py tests/api/test_owner_users.py`

- [ ] **Step 3: Implement create/reset with one-time plaintext boundaries**

Generate the temporary password in the service, hash immediately, retain plaintext only in the immutable command result, and never persist it. Commit user/hash/membership/session revocation and SUCCESS audit according to the established transaction boundary.

- [ ] **Step 4: Run users/auth/project regressions**

Run:

```powershell
cd server
uv run pytest -q tests/unit/users/test_user_service.py tests/api/test_owner_users.py tests/api/test_local_auth.py tests/api/test_projects.py
uv run ruff check src tests/unit/users tests/api/test_owner_users.py
uv run mypy src
```

- [ ] **Step 5: Commit the OWNER users unit**

```powershell
git add server/src/superboss/modules/users server/tests/unit/users/test_user_service.py server/tests/api/test_owner_users.py
git commit -m "feat(users): manage local staff credentials"
```

### Task 6: Replace the Vue OAuth flow with local login and password change

**Files:**
- Modify: `web/src/api/http.ts`
- Rewrite: `web/src/api/auth.ts`
- Modify: `web/src/stores/auth.ts`
- Rewrite: `web/src/pages/LoginPage.vue`
- Create: `web/src/pages/PasswordChangePage.vue`
- Modify: `web/src/app/router.ts`
- Delete: `web/src/pages/AuthCallbackPage.vue`
- Rewrite: `web/tests/auth-api.test.ts`
- Rewrite: `web/tests/auth-flow.test.ts`
- Rewrite: `web/tests/auth-pages.test.ts`
- Modify: `web/tests/auth-store.test.ts`
- Modify: `web/tests/auth-routing.test.ts`
- Modify: `web/tests/auth-role-refresh.test.ts`

**Interfaces:**
- `authApi.prepareCsrf()`, `authApi.login(credentials)`, `authApi.changePassword(command)`, `authApi.me()`, and `authApi.logout()`.
- `AuthUser` exposes exact `username`, `role`, and `must_change_password`.

- [ ] **Step 1: Rewrite auth frontend tests to RED**

Delete OAuth assumptions and add strict API/form/router tests for CSRF-before-login, no password persistence/URL/DOM echo, duplicate-submit suppression, safe redirect, anonymous no-refresh, hinted single-flight refresh, first-change redirect, change failure, replacement session, logout, and re-login.

```ts
await user.type(screen.getByLabelText('用户名'), 'owner')
await user.type(screen.getByLabelText('密码'), 'correct horse battery staple')
await user.click(screen.getByRole('button', { name: '登录' }))
expect(authApi.login).toHaveBeenCalledWith({
    username: 'owner',
    password: 'correct horse battery staple',
})
```

- [ ] **Step 2: Run frontend auth RED**

Run: `cd web && npm test -- --run tests/auth-api.test.ts tests/auth-flow.test.ts tests/auth-pages.test.ts tests/auth-store.test.ts tests/auth-routing.test.ts tests/auth-role-refresh.test.ts`

- [ ] **Step 3: Implement strict local auth API and refresh hint**

Keep the frozen narrow HTTP facade. The response interceptor refreshes only for `401` plus exact `X-SuperBoss-Refreshable: 1`; unrelated `401` responses notify auth loss without POSTing refresh. Login first obtains CSRF and then sends only the exact body.

- [ ] **Step 4: Implement pages/store/router**

Use native password input semantics and `autocomplete="current-password"` / `new-password`. Remove callback/session-state OAuth handling. Preserve the existing sanitized internal redirect. Make password-change-required routing higher priority than role routing.

- [ ] **Step 5: Run auth frontend gates**

Run:

```powershell
cd web
npm test -- --run tests/auth-api.test.ts tests/auth-flow.test.ts tests/auth-pages.test.ts tests/auth-store.test.ts tests/auth-routing.test.ts tests/auth-role-refresh.test.ts tests/http.test.ts tests/http-facade.test.ts
npm run lint
npm run typecheck
npm run build
```

- [ ] **Step 6: Commit the frontend auth unit**

```powershell
git add web/src/api/http.ts web/src/api/auth.ts web/src/stores/auth.ts web/src/pages/LoginPage.vue web/src/pages/PasswordChangePage.vue web/src/app/router.ts web/tests/auth-api.test.ts web/tests/auth-flow.test.ts web/tests/auth-pages.test.ts web/tests/auth-store.test.ts web/tests/auth-routing.test.ts web/tests/auth-role-refresh.test.ts
git rm web/src/pages/AuthCallbackPage.vue
git commit -m "feat(web): add local password login"
```

### Task 7: Adapt the OWNER users page to local accounts

**Files:**
- Modify: `web/src/api/users.ts`
- Modify: `web/src/pages/owner/UsersPage.vue`
- Modify: `web/tests/users-page.test.ts`

**Interfaces:**
- Uses exact username-based OWNER API from Task 5.
- Displays one-time temporary passwords only in an in-memory modal.

- [ ] **Step 1: Write local user-management RED tests**

Cover username fields, create/reset, one-time modal, close/navigation/unmount cleanup, no automatic clipboard, no password in list state/storage/logs, disable confirmation by username, project assignments, and safe errors.

- [ ] **Step 2: Run users-page RED**

Run: `cd web && npm test -- --run tests/users-page.test.ts`

- [ ] **Step 3: Implement API decoder and page behavior**

Decode create/reset envelopes exactly. Keep the temporary password in one `ref<string>` only; clear it from state in the modal close handler and `onBeforeUnmount`.

- [ ] **Step 4: Run page and full frontend gates**

Run:

```powershell
cd web
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

- [ ] **Step 5: Commit the users UI unit**

```powershell
git add web/src/api/users.ts web/src/pages/owner/UsersPage.vue web/tests/users-page.test.ts
git commit -m "feat(web): manage local staff accounts"
```

### Task 8: Remove WeCom runtime and update deployment/acceptance contracts

**Files:**
- Delete: `server/src/superboss/infrastructure/wecom.py`
- Delete: `server/tests/unit/auth/test_wecom_protocol.py`
- Modify: `server/src/superboss/main.py`
- Modify: `server/src/superboss/core/config.py`
- Modify: `server/tests/core/test_auth_configuration.py`
- Modify: `server/tests/unit/test_main_lifecycle.py`
- Modify: `server/tests/unit/test_deployment_contract.py`
- Modify: `.env.example`, `docker-compose.yml`, `docker-compose.dev.yml`
- Modify: `tests/compose/test_production_contract.py`
- Modify: `server/scripts/seed_acceptance.py`
- Modify: `server/tests/integration/test_acceptance_seed.py`
- Modify: `tests/e2e/specs/support/auth.ts`, affected E2E specs, contracts, and environment schema.
- Delete: `docs/runbooks/wecom-setup.md`
- Create: `docs/runbooks/local-auth-setup.md`
- Modify: `README.md`, `docs/01-需求定稿.md`, `docs/02-架构设计.md`, `docs/runbooks/m1-local-development.md`, and `docs/runbooks/m1-owner-acceptance.md`.

**Interfaces:**
- Removes all runtime WeCom imports/config/routes.
- Produces local-only bootstrap and acceptance instructions with no checked-in credential.

- [ ] **Step 1: Add removal RED contracts**

Add static and import tests that fail if runtime/config/OpenAPI/bundle still contains WeCom settings, provider imports, OAuth routes, callback routes, or public documentation instructions. Historical plans/reports/specs are excluded from the runtime-removal scan.

- [ ] **Step 2: Run removal RED**

Run:

```powershell
cd server
uv run pytest -q tests/core/test_auth_configuration.py tests/unit/test_main_lifecycle.py tests/unit/test_deployment_contract.py ../tests/compose/test_production_contract.py
```

- [ ] **Step 3: Remove runtime/config and adapt seed/E2E**

The acceptance seed creates local hash-bearing OWNER/STAFF rows without printing passwords. Live E2E credentials are external required inputs; contract tests use synthetic values and ensure reporter artifacts never retain them. Do not add default credentials.

- [ ] **Step 4: Replace current operational documentation**

Document interactive OWNER bootstrap, local login, password recovery, current `app.localhost` boundary, synthetic data reset, and explicit deferral of domains/company/Tencent Cloud. Preserve historical reports as evidence but mark obsolete current instructions as superseded.

- [ ] **Step 5: Run removal and acceptance gates**

Run:

```powershell
cd server
uv run pytest -q tests/core/test_auth_configuration.py tests/unit/test_main_lifecycle.py tests/unit/test_deployment_contract.py tests/integration/test_acceptance_seed.py ../tests/compose/test_production_contract.py
uv run ruff check .
uv run mypy src
cd ../tests/e2e
npm test -- --config playwright.contract.config.ts
npm run typecheck
npm run lint
npx playwright test --list
```

- [ ] **Step 6: Commit the removal and documentation unit**

```powershell
git add -A server/src/superboss/infrastructure/wecom.py server/tests/unit/auth/test_wecom_protocol.py server/src/superboss/main.py server/src/superboss/core/config.py server/tests .env.example docker-compose.yml docker-compose.dev.yml tests/compose tests/e2e docs README.md
git commit -m "refactor(auth): remove WeCom runtime"
```

### Task 9: Run final gates and perform local single-OWNER acceptance

**Files:**
- Modify only if a permanent regression is discovered: the owning source and test from Tasks 1–8.
- Update: ignored task report under `.superpowers/sdd/2026-08-09-m1-foundation-implementation/`.

**Interfaces:**
- Produces a reviewed local-auth candidate and evidence, not a company/public deployment.

- [ ] **Step 1: Run pristine migration and full backend tests**

Start the repository PostgreSQL runtime on a dedicated test port, create a fresh database, run `alembic upgrade head`, then:

```powershell
cd server
uv run pytest -q
uv run ruff check .
uv run mypy src
uv lock --check
uv run alembic current
uv run alembic heads
uv run alembic check
```

Finally verify all business tables are empty, stop PostgreSQL, and prove no owned listener/process/temp database remains.

- [ ] **Step 2: Run full web, connector, compose-static, and E2E-contract gates**

```powershell
cd web
npm test -- --run
npm run lint
npm run typecheck
npm run build
cd ../connector
uv run pytest -q
uv run ruff check .
uv run mypy src
cd ../tests/e2e
npm test -- --config playwright.contract.config.ts
npm run typecheck
npm run lint
```

- [ ] **Step 3: Run secret and removal scans**

Require zero runtime hits for WeCom configuration/imports/routes and zero bundle/source hits for plaintext password fixtures, access/refresh tokens, Authorization Bearer, or local/session storage credential writes. Review every remaining `wecom` hit as historical documentation or remove it.

- [ ] **Step 4: Rebuild the local Docker stack**

Use the existing localhost TLS and production Compose path. Verify only loopback ports are published, all services are healthy, `/live`, `/ready`, API, object proxy, ClamAV, and no-network import gates pass. Record unavailable Docker boundaries as NOT RUN rather than inferred PASS.

- [ ] **Step 5: Bootstrap and manually accept one OWNER**

Run the interactive OWNER bootstrap without capturing the entered password. In a fresh browser profile verify login, bad-password safe failure, refresh, logout, re-login, password change, project/file/device/import/audit flows, and no OAuth/callback traffic. Use synthetic data only and remove it afterward.

- [ ] **Step 6: Request independent code review and repair only C/I findings**

Review the complete design-to-HEAD diff, authentication threat boundaries, migration, password output paths, and full evidence. Keep company deployment, domains, portal, and STAFF onboarding frozen.

- [ ] **Step 7: Commit any review-approved repair, rerun affected and full gates, and freeze**

Use a narrow repair commit for review findings. Final status must be clean and the handoff must explicitly list local PASS evidence and all external/company/public NOT RUN boundaries.
