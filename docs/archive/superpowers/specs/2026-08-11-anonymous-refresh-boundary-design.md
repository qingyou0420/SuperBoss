# Anonymous Refresh Boundary Design

> Superseded by `2026-08-11-local-password-auth-design.md`. Local password login requires an
> anonymous CSRF cookie, so CSRF-cookie presence is no longer a valid refresh-session hint. This
> document remains as historical evidence and must not be implemented independently.

## Context

A fresh browser has no authenticated session and no `XSRF-TOKEN` cookie. Its initial
`GET /api/v1/auth/me` correctly returns `401`, but the shared HTTP client currently attempts
`POST /api/v1/auth/refresh` for every `401`. Because browser refresh is a cookie-authenticated
write, the server correctly requires CSRF and rejects this impossible refresh with `403`. The
frontend then surfaces the refresh error as “服务暂时不可用，请稍后重试。” on the login page.

The service and dependency health are not at fault. The client is attempting a refresh that cannot
satisfy the existing CSRF contract.

## Decision

The browser HTTP client will treat the presence of the exact `XSRF-TOKEN` cookie as a prerequisite
for attempting session refresh after a `401`.

When the cookie is absent, the response interceptor will:

1. not call `/auth/refresh`;
2. notify the existing authentication-lost handler through its current single-notification guard;
3. reject with the original `401` error.

The auth store already treats `401` from `/auth/me` as the ordinary anonymous state, so the login
page will render without a service-failure alert.

When the CSRF cookie is present, the existing refresh single-flight, exact `204` response contract,
request replay, role resynchronization, and second-loss notification behavior remain unchanged.

## Security boundaries

- Do not weaken or exempt the server-side CSRF requirement for browser refresh.
- Do not infer the presence of the HttpOnly refresh credential and do not read browser storage.
- Do not add arbitrary request headers or expose the underlying Axios client.
- Do not suppress real non-`401` failures. A refresh attempted with a CSRF cookie keeps the existing
  fail-closed behavior and safe error mapping.
- Do not change device authentication, OAuth callback, logout, or credential lifetimes.

## Verification

Permanent tests will prove:

1. a fresh anonymous `/auth/me` `401` with no CSRF cookie performs zero refresh requests, reports the
   original `401`, and triggers authentication loss at most once;
2. the login bootstrap produced by that path leaves the login page free of a service-failure alert;
3. a session with a valid CSRF cookie still performs the existing one-time refresh and request replay;
4. concurrent authenticated `401` responses continue sharing one refresh request;
5. the full Web test, lint, typecheck, and production build gates remain green.

After rebuilding the local production image, a fresh browser visit to `/login` must issue no
`POST /api/v1/auth/refresh` and must show only the normal login prompt. The local stack remains bound
to `127.0.0.1:443`; this verification does not claim real WeCom or public-domain acceptance.
