# SuperBoss

## Local backend stack

Start the development stack from the repository root:

```console
docker compose -f docker-compose.dev.yml up --build
```

The stack contains the API, PostgreSQL, Redis, MinIO, ClamAV, one dedicated
`file-scan` worker, one dedicated `file-maintenance` worker, and Celery beat. The API
is exposed on port 8000. A one-shot, idempotent MinIO initializer creates the
`superboss-files` bucket before the API and scan worker start. ClamAV listens on port
3310 only inside the private Compose network; that protocol has no authentication or
transport encryption and must not be published to an untrusted network.

ClamAV's first startup can take several minutes while its virus database volume is
initialized and the database is loaded. Allow roughly 4 GiB of memory for the ClamAV
container. The scan worker intentionally waits for the ClamAV health check, while API
startup does not: uploads can remain quarantined until scanning capacity is ready.

Copy `.env.example` when running backend processes directly on the host. Compose uses
explicit service-to-service endpoints and development-only credentials declared in
`docker-compose.dev.yml`; replace all example credentials outside local development.
Interactive WeCom login requires setting `SUPERBOSS_WECOM_CORP_ID`,
`SUPERBOSS_WECOM_AGENT_ID`, `SUPERBOSS_WECOM_CORP_SECRET`,
`SUPERBOSS_WECOM_REDIRECT_URI`, and `SUPERBOSS_OWNER_WECOM_USERID` in the host
environment or `.env` before starting Compose. The deterministic fake identity
provider remains restricted to `SUPERBOSS_ENVIRONMENT=test` and is not enabled by the
development stack.
