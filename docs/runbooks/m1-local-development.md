# M1 local development runbook

This runbook starts and verifies the private development stack on the owner's Windows computer. It
does not prove public, company-network, or production acceptance. Run commands from the repository
root unless a section changes directory.

## Prerequisites and private environment

Install Docker Desktop, Node.js/npm, Python 3.13, and `uv`. Keep `.env` private.

```powershell
Copy-Item .env.example .env
```

The production-shaped `.env.example` uses reserved `example.invalid` hostnames. It is not the local
browser origin and must not be treated as a deployment instruction. Do not add passwords, private
hosts, cookies, or tokens to a command transcript.

## Start and migrate

```powershell
docker compose --env-file .env -f docker-compose.dev.yml up -d --build
docker compose --env-file .env -f docker-compose.dev.yml ps
docker compose --env-file .env -f docker-compose.dev.yml exec -T api alembic upgrade head
```

Bootstrap or recover the unique OWNER by following `local-auth-setup.md`. The interactive prompt is
the only approved password input path.

For synthetic OWNER/STAFF acceptance data, provide only usernames through temporary environment
variables; the seed reads both passwords interactively and prints only four UUIDs:

```powershell
$env:SUPERBOSS_OWNER_USERNAME='owner-acceptance'
$env:SUPERBOSS_ACCEPTANCE_STAFF_USERNAME='staff-acceptance'
try {
  docker compose --env-file .env -f docker-compose.dev.yml run --rm --no-deps `
    -e SUPERBOSS_OWNER_USERNAME -e SUPERBOSS_ACCEPTANCE_STAFF_USERNAME `
    -v "${PWD}/server/scripts:/app/scripts:ro" `
    api python scripts/seed_acceptance.py
} finally {
  Remove-Item Env:SUPERBOSS_OWNER_USERNAME -ErrorAction SilentlyContinue
  Remove-Item Env:SUPERBOSS_ACCEPTANCE_STAFF_USERNAME -ErrorAction SilentlyContinue
}
```

Repeating matching input is idempotent. An OWNER mismatch or conflicting acceptance record fails
closed with no partial changes.

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

The live Playwright command and required local credentials are in `m1-owner-acceptance.md`.
Contract tests, type checking, linting, and `--list` are not live E2E evidence.

## Logs and safe shutdown

```powershell
docker compose --env-file .env -f docker-compose.dev.yml logs --tail 200 api file-scan-worker file-maintenance-worker celery-beat clamav minio postgres redis
docker compose --env-file .env -f docker-compose.dev.yml down
```

Do not add `--volumes` to normal shutdown: that deletes local database, object, queue, and antivirus
state. Confirm the Compose service list is empty after shutdown.
