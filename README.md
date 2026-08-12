# SuperBoss

SuperBoss M1 provides a local OWNER/STAFF identity boundary, project and quarantined-file flow,
OWNER device management, and the least-privilege Kimi connector import boundary. M1 `RECEIVED`
imports do not create M2 document versions.

The current supported acceptance target is private single-owner testing on the owner's Windows
computer at `https://app.localhost`. Public domains, Tencent Cloud, company-network multi-user
access, and the Moonbox entry portal are deferred until filing and a separate deployment review.

## Operator map

- [Local account bootstrap and recovery](docs/runbooks/local-auth-setup.md)
- [Local start, migrate, verify, logs, and stop](docs/runbooks/m1-local-development.md)
- [OWNER/STAFF live acceptance and blank sign-off](docs/runbooks/m1-owner-acceptance.md)
- [Kimi connector build, pair, submit, retry, and revoke](docs/runbooks/kimi-connector-installation.md)
- [Pre-pilot PostgreSQL and object backup/restore](docs/runbooks/backup-before-m1-pilot.md)
- [Development governance and remote-enforcement activation](docs/runbooks/development-governance.md)

## Local stack

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f docker-compose.dev.yml up -d --build
docker compose --env-file .env -f docker-compose.dev.yml exec -T api alembic upgrade head
docker compose --env-file .env -f docker-compose.dev.yml ps
```

The development stack includes API, PostgreSQL, Redis, MinIO, ClamAV, dedicated scan and maintenance
workers, and Celery beat. ClamAV is private to the Compose network; never publish its unauthenticated
protocol. Initial signature loading can take several minutes and roughly 4 GiB of memory.

Bootstrap the OWNER through the interactive command in `local-auth-setup.md`, then sign in at
`https://app.localhost/login`. Never put a password in `.env` or a command argument.

Stop safely without deleting volumes:

```powershell
docker compose --env-file .env -f docker-compose.dev.yml down
```

## Verification truthfulness

Live E2E requires a running local origin, external OWNER/STAFF credentials, and a runnable connector.
Missing prerequisites fail fast. Static contracts, type checks, lint, `playwright test --list`, and
mocked tests are not live E2E evidence.

Docker production smoke, live ClamAV clean/EICAR, a real connector/keyring, manual browser profiles,
backup restore, public-domain deployment, company-network access, and signed OWNER acceptance remain
NOT RUN until an operator actually performs and records them.
