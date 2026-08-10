# SuperBoss

SuperBoss M1 provides the OWNER/STAFF identity boundary, project and quarantined-file flow, OWNER
device management, and the least-privilege Kimi connector import boundary. M1 `RECEIVED` imports do
not create M2 document versions.

## Operator map

- [Local start, migrate, seed, verify, logs, and stop](docs/runbooks/m1-local-development.md)
- [OWNER/STAFF live acceptance and blank sign-off](docs/runbooks/m1-owner-acceptance.md)
- [Kimi connector build, pair, submit, retry, and revoke](docs/runbooks/kimi-connector-installation.md)
- [WeCom trusted-domain and whitelist setup](docs/runbooks/wecom-setup.md)
- [Pre-pilot PostgreSQL and object backup/restore](docs/runbooks/backup-before-m1-pilot.md)

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

Stop safely without deleting volumes:

```powershell
docker compose --env-file .env -f docker-compose.dev.yml down
```

## Verification truthfulness

The live E2E suite requires a deployed origin, separate external OWNER/STAFF WeCom browser state,
and a runnable connector. Missing prerequisites fail fast. Static contracts, type checks, lint,
`playwright test --list`, and mocked tests are not live E2E evidence.

Docker production compose/smoke, live ClamAV clean/EICAR, live WeCom, a real connector/keyring, two
manual browser profiles, backup restore, and signed OWNER acceptance must be run and recorded by the
operator. This repository does not claim those checks have been completed.
