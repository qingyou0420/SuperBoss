# SuperBoss

内部运营工作台：一个老板 + 约 10 名员工。同一登录页，三层账号，霜月只出建议卡片，老板确认后才入库。

仓库按私有内部系统维护，不对外发布，不提供设备配对或 Kimi 连接器。

Supported origin for local acceptance: `https://app.localhost`.

## Roles

| Role | After login | Can do |
|---|---|---|
| OWNER（老板） | `/chat` 霜月 | 对话与卡片确认、财务读写、项目读写、网盘全部目录、SOUL/记忆、账号、审计 |
| MANAGER（管理层） | `/projects` | 公司+项目财务只读、项目进度、公司/项目网盘 |
| STAFF（员工） | `/projects` | 仅项目成本、项目进度、`项目` 网盘目录 |

## Operator map

- [Local account bootstrap and recovery](docs/runbooks/local-auth-setup.md)（含创建 MANAGER）
- [LLM setup for 霜月](docs/runbooks/llm-setup.md)
- [Local HTTPS from clone to login](docs/runbooks/local-https.md)
- [Local start, migrate, verify, logs, and stop](docs/runbooks/m1-local-development.md)
- [Live acceptance](docs/runbooks/m1-owner-acceptance.md)
- [PostgreSQL and object backup/restore](docs/runbooks/backup-before-m1-pilot.md)
- [Current iteration plan](SuperBoss-迭代方案-三层账号与霜月.md)

## Local stack

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f docker-compose.dev.yml up -d --build
docker compose --env-file .env -f docker-compose.dev.yml exec -T api alembic upgrade head
docker compose --env-file .env -f docker-compose.dev.yml ps
```

Deploy on the internal host is `git pull` (deploy key) → `docker compose build` → `alembic upgrade head` → restart. There is no public Release or client self-update.

Bootstrap the OWNER through the interactive command in `local-auth-setup.md`, then sign in at `https://app.localhost/login`. Never put a password in `.env` or a command argument.

Optional 霜月: set `SUPERBOSS_LLM_*` as in `llm-setup.md`. Empty values keep the rest of the app working.

Optional ClamAV: `SUPERBOSS_SCAN_ENABLED=false` skips scanning; start ClamAV with `--profile scan` when you want it.

Stop safely without deleting volumes:

```powershell
docker compose --env-file .env -f docker-compose.dev.yml down
```

## Verification

Live E2E needs the local origin and OWNER/STAFF/MANAGER credentials. Static contracts, type checks, lint, and mocked tests are not live evidence.
