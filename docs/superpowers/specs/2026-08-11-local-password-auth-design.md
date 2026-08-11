# Local Password Authentication Design

## Status and scope

This design replaces WeCom browser OAuth with local username and password authentication. It is
approved for the current private development phase, where only one OWNER uses SuperBoss on the
home development machine through `app.localhost`.

The implementation must preserve the existing authorization, project membership, device,
connector, file, import, audit, cookie, CSRF, and rotating-session boundaries. It must not deploy to
Tencent Cloud, expose a public endpoint, configure the company network, create real STAFF accounts,
or build the future `night-forest.com` project portal.

The current development database may be cleared. The future company deployment will create fresh
accounts after ICP filing is complete.

This design supersedes `2026-08-11-anonymous-refresh-boundary-design.md`. A CSRF cookie will also be
issued to anonymous login pages, so CSRF-cookie presence cannot safely be used as evidence that a
refresh session exists.

## Deferred deployment design

The later company deployment will use three independently isolated hosts:

- `night-forest.com`: a static, unauthenticated internal project portal;
- `moonbox.night-forest.com`: Moonbox with its own authentication;
- `superboss.night-forest.com`: SuperBoss with local authentication.

They will resolve only through company-private DNS and will use host-only cookies. Portal work,
internal DNS, certificates, company Docker hosting, VPN connectivity, Tencent Cloud private-VPC
hosting, and employee onboarding are explicitly deferred until filing is complete.

## Identity model

`User.wecom_userid` is replaced by `User.username`. `OAuthState` and all WeCom provider persistence,
configuration, routes, infrastructure, and runtime state are removed.

The `users` table contains:

- the existing UUID primary key, role, status, display name, timestamps, and last-login time;
- `username`, unique and constrained to `[a-z][a-z0-9._-]{2,31}`;
- a self-describing Argon2id `password_hash` that is never returned by an API;
- `must_change_password`;
- `password_changed_at`;
- `failed_login_count` and `locked_until` for bounded credential failures.

There remains exactly one OWNER. STAFF project memberships and ACTIVE/DISABLED semantics remain
unchanged. Disabling a user and resetting a password revoke that user's live sessions in the same
business transaction.

The new Alembic revision must support a fresh database and an empty current database. Because the
identity change is intentionally destructive, downgrade or upgrade paths that would discard
nonempty user, OAuth-state, or auth-session data must fail before their first persistent mutation.
The development reset procedure clears data explicitly; the migration must never silently erase it.

## Password policy and hashing

Passwords are accepted as exact user input. The server does not trim, normalize, case-fold, or
silently transform them.

- Minimum: 12 Unicode code points.
- Maximum: 128 Unicode code points and a separate bounded UTF-8 byte limit.
- NUL, control characters, malformed Unicode, and invalid encodings are rejected.
- Spaces and non-ASCII printable characters are allowed.
- Composition rules such as mandatory uppercase, digits, or symbols are not imposed.

Argon2id is a production dependency. Its parameters are explicitly configured and covered by tests;
encoded hashes retain their parameters, and a successful login may rehash an older valid hash when
the configured policy changes. An unknown username executes a fixed dummy Argon2id verification so
the ordinary invalid-user and invalid-password paths do not have a trivial timing distinction.

Passwords and temporary passwords must never appear in logs, audit metadata, exception messages,
URLs, command-line arguments, environment variables, browser storage, analytics, or API errors.

## OWNER bootstrap and recovery

There is no registration endpoint and no default credential.

A server-local administrative command creates the first OWNER. It:

1. refuses to run when an OWNER or any user already exists;
2. reads username and display name interactively;
3. reads the password twice using a non-echoing prompt;
4. validates and hashes the password locally;
5. creates an ACTIVE OWNER with `must_change_password=false` in one transaction;
6. records a bounded `auth.owner.bootstrap` audit event without credential data;
7. prints only the created user UUID and username.

A separate server-local recovery command resets the OWNER password. It requires an interactive
password entry, revokes every OWNER session atomically with the hash update, records a bounded audit
event, and prints no credential. Web-based self-service recovery, email, SMS, recovery questions,
and public reset tokens are out of scope.

## Browser authentication API

The browser API becomes:

- `GET /api/v1/auth/csrf` -> exact `204`, sets or rotates the readable `XSRF-TOKEN` cookie;
- `POST /api/v1/auth/login` -> exact body `{username, password}`, exact `204` on success;
- `GET /api/v1/auth/me` -> exact user summary including `username`, `role`, and
  `must_change_password`;
- `POST /api/v1/auth/password/change` -> exact body `{current_password, new_password}`, exact `204`;
- existing `POST /api/v1/auth/refresh` -> exact `204`;
- existing `POST /api/v1/auth/logout` -> exact `204`.

Login and every credential-changing POST require the existing exact double-submit CSRF cookie and
header. The login page obtains the CSRF cookie through the same-origin GET before submitting. The
login request accepts no alternate content types, query credentials, Authorization header, or
provider callback data.

Successful login updates `last_login_at`, creates the existing server-side `AuthSession`, and sets
the existing Secure, HttpOnly, SameSite cookies. Browser access remains exactly two hours; refresh
remains exactly fourteen days with one-time rotation. Every access-token request continues loading
and validating its `AuthSession` and current `User`, so revocation, disablement, and role changes take
effect without waiting for JWT expiry.

`GET /auth/me` returns a bounded non-secret response header only when the server observes a refresh
cookie that could be attempted. The frontend attempts refresh only when this exact header is present.
This replaces CSRF-cookie inference: an anonymous login page has CSRF state but no refresh state.
The refresh endpoint still requires CSRF whenever a refresh credential exists; missing or invalid
credentials return the uniform authentication failure and never create a session.

## Login failures and throttling

Invalid usernames, invalid passwords, disabled users, and locked users return the same bounded login
failure shape. Audit metadata contains only fixed reason codes and server-known identifiers; an
unknown submitted username is never echoed into audit data.

Credential failures are bounded per normalized username and request source. A real user row is
locked before counters are changed. Reaching the configured threshold sets a temporary lock; a
successful login resets the counter. Unknown usernames are subject to the same source throttle and
dummy hash cost. Raw proxy headers are not trusted unless the request came through the configured
production proxy boundary.

Audit failure is fail closed for login: session cookies are not issued unless the SUCCESS audit is
durable. A DENIED-audit failure also returns a safe server error and never falls through to a
successful login.

## First-password change and session behavior

The interactive OWNER bootstrap uses a final password and therefore does not require a first change.
Future server-generated STAFF credentials set `must_change_password=true`.

A user in that state may only call `me`, `password/change`, and `logout`. All business routes deny the
session even when its JWT and role are otherwise valid. The frontend always redirects such a user to
the password-change page.

A successful password change:

1. verifies the current password;
2. validates and hashes the new password;
3. rejects reuse of the current password;
4. updates password timestamps and clears `must_change_password`;
5. revokes all existing sessions;
6. issues one new session for the current browser;
7. records the SUCCESS audit before returning cookies.

Any failure leaves the old hash, user flags, sessions, and cookies unchanged.

## OWNER-managed STAFF credentials

The existing OWNER users API changes from WeCom IDs to local usernames.

- Creating a STAFF user accepts username, display name, and project IDs.
- The server generates a high-entropy temporary password and returns it exactly once with the newly
  created user summary.
- The hash and user are committed only when the SUCCESS audit is durable.
- The temporary plaintext is not recoverable after the response.
- Resetting a STAFF password generates a new one-time value, sets `must_change_password=true`, and
  revokes all sessions atomically.
- OWNER cannot reset the OWNER password through the web API; OWNER changes it using the authenticated
  change flow or the server-local recovery command.
- Enable, disable, role, membership, audit-denial, and concurrency protections remain as currently
  frozen.

The current phase creates no real STAFF users, but these contracts are implemented and tested now so
the post-filing rollout needs only account creation, not another schema redesign.

## Frontend behavior

The WeCom start/callback API, callback page, callback route, OAuth state parsing, and provider
navigation are removed.

The login page contains only username and password fields. It:

- initializes CSRF before enabling submission;
- uses correct browser autocomplete attributes;
- never persists, logs, reflects, or places the password in a URL;
- disables duplicate submission;
- displays only fixed safe errors;
- consumes the existing sanitized internal redirect after successful login.

A password-change-required page is added. Router guards send affected sessions there before any
OWNER or STAFF page. The auth store gains explicit login and password-change operations while
retaining bootstrap, refresh resynchronization, logout, and authentication-loss handling.

The OWNER users page displays `username` instead of `wecom_userid`. A newly created or reset STAFF
temporary password is displayed in a single in-memory dialog and cleared on close, navigation, and
component unmount. It is never automatically copied or stored.

## Error and response contracts

All new request models forbid extra fields and have bounded strings. All success responses have
exact status, body, and key sets. Error responses continue using the existing safe envelope and do
not expose hashes, password length, submitted username, lock counters, SQL details, internal paths,
or Argon2 parameters.

The frontend continues using the frozen narrow browser HTTP facade. It does not expose Axios,
arbitrary headers, alternate transports, Authorization, or raw response objects.

## Removal boundary

The following are removed rather than retained as dormant alternatives:

- WeCom settings and production environment keys;
- the WeCom provider and fake provider;
- OAuth state cookies and database table;
- `/auth/wecom/start` and `/auth/wecom/callback`;
- the callback page and route;
- WeCom-specific tests, runbook steps, seed fields, and acceptance fixtures.

Device pairing and device-token authentication are unrelated and must not change.

## Verification strategy

Permanent backend tests cover:

- username, password, schema, migration, and database constraints;
- Argon2id hashing, dummy verification, rehash, and secret-safe failures;
- first OWNER bootstrap, duplicate bootstrap, and local recovery;
- login success, invalid/unknown/disabled/locked users, failure counters, and concurrency;
- CSRF ordering, exact cookies, two-hour access, fourteen-day refresh, rotation, and logout;
- refresh-hint behavior for anonymous, expired-access, missing-CSRF, and invalid-refresh cases;
- first-change route denial and atomic new-session issuance;
- STAFF creation/reset one-time credentials, session revocation, role and project boundaries;
- audit SUCCESS/DENIED/fault behavior without credential leakage;
- removal of WeCom imports, routes, configuration, network calls, and migration residue.

Permanent frontend tests cover:

- strict local auth API models;
- CSRF initialization and login form behavior;
- no refresh attempt for an ordinary anonymous response;
- single-flight refresh only with the exact server hint;
- first-password-change routing and business-route denial;
- safe return paths, logout, re-login, role resynchronization, and authentication loss;
- username-based OWNER user management and one-time temporary-password cleanup;
- absence of credentials from DOM after cleanup, browser storage, URLs, console output, bundles, and
  retained test artifacts.

Final gates include the full backend, web, connector, migration, lint, typecheck, build, secret scan,
no-network import, database-residue, and local Docker suites that are available. Manual acceptance
uses only `app.localhost`, one OWNER, and synthetic data. It does not claim company-network, employee,
Tencent Cloud, public-domain, WeChat, or Moonbox-portal acceptance.

## Delivery sequence

1. Add permanent RED tests for schema, migration, password service, API, frontend, and removal.
2. Implement the migration and password primitive.
3. Implement local login, CSRF bootstrap, refresh hint, password change, audit, and CLI bootstrap.
4. Adapt OWNER user management and STAFF temporary-password reset.
5. Replace the frontend OAuth flow and add forced password change.
6. Remove all WeCom runtime/configuration/docs/test remnants in scope.
7. Run focused and full gates, rebuild local Docker, create one test OWNER, and perform local manual
   feature acceptance.
8. Stop before company deployment, formal domains, portal work, or real employee onboarding.
