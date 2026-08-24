# 本机 HTTPS 部署

从空仓库走到 `https://app.localhost/login`。证书与 allowlist 只放在本机 `ops/local-tls/`，不要提交。

## 1. Hosts

以管理员身份把下面两行加入 `C:\Windows\System32\drivers\etc\hosts`：

```
127.0.0.1 app.localhost
127.0.0.1 objects.localhost
```

## 2. 证书与 allowlist

在仓库根目录用 PowerShell：

```powershell
New-Item -ItemType Directory -Force -Path ops\local-tls | Out-Null
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes `
  -keyout ops\local-tls\tls.key `
  -out ops\local-tls\tls.crt `
  -subj "/CN=app.localhost" `
  -addext "subjectAltName=DNS:app.localhost,DNS:objects.localhost,IP:127.0.0.1"
Set-Content -Encoding ascii ops\local-tls\allowlist.conf @"
allow 127.0.0.1;
allow ::1;
deny all;
"@
```

把 `ops\local-tls\tls.crt` 导入「受信任的根证书颁发机构」（当前用户即可），否则浏览器会拦截自签证书。

## 3. 环境与启动

开发栈（API、Web、Nginx、扫描）：

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f docker-compose.dev.yml up -d --build
docker compose --env-file .env -f docker-compose.dev.yml exec -T api alembic upgrade head

If `alembic upgrade head` fails because `alembic_version` still names a retired
revision, recreate the local volume (`docker compose ... down -v`) and start
again. The current schema is a single `0001_baseline` and is not a linear
upgrade from the old 18-revision chain.
```

浏览器打开 `https://app.localhost/login`。OWNER 账号按 [local-auth-setup.md](local-auth-setup.md) 交互式创建，不要把密码写进 `.env` 或命令行。

## 4. 生产栈本机试跑

生产 compose 只发布 `127.0.0.1:443`。补齐 `.env` 里的密码、JWT、S3 密钥，并把证书/allowlist 路径指到 `ops/local-tls/`：

```
SUPERBOSS_APP_HOST=app.localhost
SUPERBOSS_OBJECTS_HOST=objects.localhost
SUPERBOSS_TLS_CERT_PATH=./ops/local-tls/tls.crt
SUPERBOSS_TLS_KEY_PATH=./ops/local-tls/tls.key
SUPERBOSS_ALLOWLIST_PATH=./ops/local-tls/allowlist.conf
```

```powershell
docker compose --env-file .env up -d --build
```
