# WeCom setup for M1

Perform this in the WeCom administrator console and the private deployment `.env`. Use placeholders
in tickets and screenshots; never record the real corp ID, secret, userid, cookie, or OAuth code.

## Application and trusted domain

1. Create or select the internal SuperBoss application.
2. Set its trusted OAuth domain to `<APP_HOST>` with no scheme, path, or private address.
3. Set the OAuth redirect URI to `https://<APP_HOST>/auth/callback`.
4. Ensure the trusted domain serves the production TLS certificate and resolves only through the
   approved ingress/allowlist.
5. Copy the private values into these existing `.env.example` variable names:
   `SUPERBOSS_WECOM_CORP_ID`, `SUPERBOSS_WECOM_AGENT_ID`,
   `SUPERBOSS_WECOM_CORP_SECRET`, `SUPERBOSS_WECOM_REDIRECT_URI`, and
   `SUPERBOSS_OWNER_WECOM_USERID`.

Validate configuration without printing interpolated secrets:

```powershell
docker compose --env-file .env -f docker-compose.yml config --quiet
docker compose --env-file .env -f docker-compose.yml up -d
docker compose --env-file .env -f docker-compose.yml exec -T api alembic upgrade head
```

## OWNER whitelist bootstrap and STAFF account

The OWNER userid in `.env` is the sole protected OWNER. Seed the records only after confirming the
intended account. The STAFF userid must be different and is entered as transient seed input:

```powershell
$env:SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID='<STAFF_WECOM_USERID>'
docker compose --env-file .env -f docker-compose.yml run --rm --no-deps `
  -e SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID `
  -v "${PWD}/server/scripts:/app/scripts:ro" `
  api python scripts/seed_acceptance.py --confirm-production-seed
Remove-Item Env:SUPERBOSS_ACCEPTANCE_STAFF_WECOM_USERID
```

The seed creates no password and prints record IDs only. It never changes an existing OWNER; a
different existing OWNER makes it fail closed. OWNER and STAFF then log in through separate WeCom
browser profiles. Verify `/api/v1/auth/me` reports the expected role through the application UI;
do not copy the response cookies or tokens.

## Troubleshooting

```powershell
docker compose --env-file .env -f docker-compose.yml ps
docker compose --env-file .env -f docker-compose.yml logs --tail 200 nginx web api
```

A redirect mismatch, untrusted domain, unapproved account, or untrusted certificate is FAIL. Do not
enable fake WeCom outside `SUPERBOSS_ENVIRONMENT=test`, and do not bypass login for acceptance.
