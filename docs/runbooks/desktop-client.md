# 桌面客户端

Tauri 2 启动器，窗口先检测本机 Docker 栈，就绪后打开 `https://app.localhost/login`。安装包不含 PostgreSQL、镜像或源码；本机仍要有一份仓库克隆和 Docker Desktop。

## 本机依赖

- Windows 10/11，WebView2（一般已随系统安装）
- Docker Desktop 正在运行
- Git（「更新本机栈」会 `git pull --ff-only`）
- 仓库克隆，路径可通过 `SUPERBOSS_HOME` 或启动页「保存路径」指定
- hosts：启动器「写入 hosts」会提权运行 `ops/write-local-hosts.ps1`，写入

```
127.0.0.1 app.localhost
127.0.0.1 objects.localhost
```

启动器会在缺少 `.env` 时从 `.env.example` 复制，缺少证书时生成 `ops/local-tls/`（已 gitignore）。生成后会尝试 `certutil -user -addstore Root`；失败则按 [local-https.md](local-https.md) 手动把 `tls.crt` 导入当前用户「受信任的根证书颁发机构」。WebView 不信任自签证书时打不开登录页。

OWNER 账号仍按 [local-auth-setup.md](local-auth-setup.md) 交互式创建，不要把密码写进 `.env` 或命令行。

## 开发运行

在仓库根目录：

```powershell
cd desktop
npm install
npm run tauri icon -- src-tauri/icons/icon-source.png
npm run dev
```

窗口 1280×800。按钮：

| 按钮 | 行为 |
|---|---|
| 启动本机栈 | `docker compose --env-file .env -f docker-compose.dev.yml up -d`，并轮询 `GET https://app.localhost/api/v1/health/live` |
| 打开工作台 | 同一窗口转到 `https://app.localhost/login` |
| 更新本机栈 | `git pull --ff-only` 再 `compose up -d --build` |
| 停止本机栈 | `compose down`（不删 volume） |
| 写入 hosts | 管理员写入 `127.0.0.1 app.localhost` 与 `objects.localhost` |
| 检查客户端更新 | 读 GitHub Release 的 `latest.json` |

关掉窗口不会停 Docker 栈。

## 打包

```powershell
cd desktop
$env:TAURI_SIGNING_PRIVATE_KEY = (Resolve-Path .\updater.key).Path
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
npm run build
```

`TAURI_SIGNING_PRIVATE_KEY` 可以是私钥文件路径或文件内容。打包更新签名时不要用 `TAURI_SIGNING_PRIVATE_KEY_PATH`，bundler 不认这个变量。

产物在 `desktop/src-tauri/target/release/bundle/nsis/`。NSIS 装到当前用户，不需要管理员。

首次签名前生成密钥（私钥不要提交）：

```powershell
cd desktop
npx tauri signer generate -w updater.key
```

把打印出的公钥写入 `desktop/src-tauri/tauri.conf.json` 的 `plugins.updater.pubkey`。把 `updater.key` 存进密码管理器，并设为 GitHub secret `TAURI_SIGNING_PRIVATE_KEY`（可选 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`）。丢失私钥后，已安装的客户端无法校验后续更新。

## 发版

1. 改 `desktop/package.json` 与 `desktop/src-tauri/tauri.conf.json`、`Cargo.toml` 的 `version`（三者保持一致）。
2. 打标签 `desktop-v0.1.0` 并推送，工作流 `.github/workflows/desktop-release.yml` 会打 Windows NSIS 并生成 `latest.json`。
3. 启动器的更新地址是 `https://github.com/qingyou0420/SuperBoss/releases/latest/download/latest.json`。

仓库若保持私有，未登录的客户端拉不到该 JSON，检查更新会显示「已是最新」。要一键更新，把 `latest.json` 和安装包放到客户端能匿名 HTTPS 访问的地址，并改 `plugins.updater.endpoints`。人工仍可从私有 Release 下载安装包。

## 这不是什么

- 不是把业务改成 SQLite 单机应用
- 不是外置浏览器壳；登录后仍是同一套 cookie-only SPA
- 不是替代 `docs/runbooks/m1-local-development.md` 里的 compose 流程
