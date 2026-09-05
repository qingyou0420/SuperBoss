# Local account setup

SuperBoss currently uses local username/password authentication with no public registration. The
current acceptance boundary is the private loopback origin `https://app.localhost`; public domains,
the company network, Tencent Cloud deployment, and the Moonbox entry portal are deferred until a
separate deployment decision after filing is complete.

Passwords are read interactively. Never place one in `.env`, command arguments, shell history,
screenshots, reports, or the repository.

## Bootstrap the unique OWNER

Start the development stack and apply migrations first. From the repository root:

```powershell
docker compose --env-file .env -f docker-compose.dev.yml run --rm --no-deps `
  -v "${PWD}/server/scripts:/app/scripts:ro" `
  api python scripts/manage_local_owner.py bootstrap `
  --username owner --display-name 'Owner'
```

Enter and confirm a unique password when prompted. The command refuses a second OWNER and prints
only the resulting user UUID.

## Recover the OWNER password

Use the same private workstation and an interactive terminal:

```powershell
docker compose --env-file .env -f docker-compose.dev.yml run --rm --no-deps `
  -v "${PWD}/server/scripts:/app/scripts:ro" `
  api python scripts/manage_local_owner.py reset
```

The reset revokes existing browser sessions. Sign in again at `https://app.localhost/login`.

## STAFF and MANAGER accounts

The OWNER creates accounts from **账号**. Choose role **STAFF** or **MANAGER**. The generated
temporary password is displayed once and is never recoverable. Deliver it privately; the user must
change it at first login. The OWNER can later switch STAFF ↔ MANAGER, reset a forgotten password,
disable the account, and replace project membership.

- MANAGER sees company finance with `MANAGEMENT`/`ALL` visibility, all projects, and the company
  drive folder. MANAGER cannot use 霜月, user admin, audit, or SOUL.
- STAFF sees only project costs with `ALL` visibility, all project progress, and the shared `项目`
  drive folder.

## Current access boundary

- Use only the local Docker environment on the owner's Windows computer.
- Do not publish Docker ports through router port forwarding or expose them to the public internet.
- Do not configure `night-forest.com`, Tencent Cloud, company LAN access, or the Moonbox portal yet.
- Keep `.env`, cookies, passwords, and local TLS keys private.
