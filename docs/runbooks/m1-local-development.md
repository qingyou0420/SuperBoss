# M1 local development runbook

This runbook starts and verifies the development stack. It does not prove production or manual
acceptance. Run every command from the repository root unless a section changes directory.

## Prerequisites and private environment

Install Docker Desktop, Node.js/npm, Python 3.13, and `uv`. Keep `.env` private.

```powershell
Copy-Item .env.example .env
```

Fill only the variables already named in `.env.example`. Use private values in `.env`; do not put
credentials, corp IDs, userids, private hosts, cookies, or tokens in a command transcript.

## Start, migrate, and seed

```powershell
docker compose --env-file .env -f docker-compose.dev.yml up -d --build
docker compose --env-file .env -f docker-compose.dev.yml ps
docker compose --env-file .env -f docker-compose.dev.yml exec -T api alembic upgrade head
```

The seed takes the OWNER userid from `SUPERBOSS_OWNER_WECOM_USERID` and a distinct, transient STAFF
userid from `SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID`. The latter is seed input, not a password or
application runtime setting. Enter it only in the current private shell. The bind mount is needed
because production images intentionally exclude operator scripts.

```powershell
$env:SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID='<STAFF_WECOM_USERID>'
docker compose --env-file .env -f docker-compose.dev.yml run --rm --no-deps `
  -e SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID `
  -v "${PWD}/server/scripts:/app/scripts:ro" `
  api python scripts/seed_acceptance.py
Remove-Item Env:SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID
```

Save the four printed record IDs in the blank acceptance checklist. Repeating the same command is
idempotent. If an existing OWNER has another userid, or an acceptance name conflicts with existing
data, the command fails closed and changes nothing. In production it additionally requires the
literal `--confirm-production-seed` flag after an explicit operator decision.

## Automated verification

```powershell
Push-Location server
uv run alembic upgrade head
uv run pytest -v
uv run ruff check .
uv run mypy src
Pop-Location

Push-Location web
npm ci
npm run test -- --run
npm run typecheck
npm run build
Pop-Location

Push-Location integrations/kimi-superboss/connector
uv run pytest -v
uv run ruff check .
uv run mypy src
Pop-Location

npm --prefix tests/e2e ci
npm --prefix tests/e2e run test:contracts
npm --prefix tests/e2e run typecheck
npm --prefix tests/e2e run lint
docker compose --env-file .env -f docker-compose.dev.yml config --quiet
```

The live Playwright command and required secure account-state preparation are in
`m1-owner-acceptance.md`. `test:contracts`, type checking, linting, and `--list` are not live E2E.

## Logs and safe shutdown

```powershell
docker compose --env-file .env -f docker-compose.dev.yml logs --tail 200 api file-scan-worker file-maintenance-worker celery-beat clamav minio postgres redis
docker compose --env-file .env -f docker-compose.dev.yml down
```

Do not add `--volumes` to normal shutdown: that deletes local database, object, queue, and ClamAV
state. Confirm `docker compose ... ps` is empty after shutdown.
