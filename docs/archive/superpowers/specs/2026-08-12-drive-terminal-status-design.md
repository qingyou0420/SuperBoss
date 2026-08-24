# Drive 终态呈现设计

## 目标

让 OWNER 在当前上传结果进入 `INFECTED` 或 `FAILED` 后看到准确、固定且安全的终态提示，不再被“扫描中”与无效重试按钮误导；同时保持 CLEAN-only 下载、无历史文件列表、无持久化下载链接的既有边界。

## 现状与根因

`DrivePage.vue` 将除 `CLEAN` 外的所有状态统一显示为“扫描中”，并对任何下载拒绝统一显示“文件仍在扫描中”。后端下载接口也对 `UPLOADING`、`QUARANTINED`、`SCANNING`、`INFECTED`、`FAILED` 都返回同一个 `FILE_NOT_READY`，因此正常上传后异步扫描为感染或失败时，浏览器无法区分终态。

## 方案

沿用现有 `GET /api/v1/files/{file_id}/download`，不新增状态接口：

- `UPLOADING`、`QUARANTINED`、`SCANNING`：409 `FILE_NOT_READY`。
- `INFECTED`：409 `FILE_INFECTED`。
- `FAILED`：409 `FILE_SCAN_FAILED`。
- `CLEAN`：行为不变，返回短期 HTTPS 下载 URL。

前端 `filesApi.download()` 只接受上述精确、受限错误包络，并把两种终态转换为只携带固定状态枚举的 `FileDownloadUnavailableError`。未知、畸形或越权错误继续走通用安全错误路径，不展示服务端原文。

`DrivePage` 显式映射五种状态：

- `QUARANTINED` / `SCANNING`：显示“扫描中”，允许“检查并获取下载”。
- `CLEAN`：显示“处理完成”，只在取得 URL 后显示本次下载链接。
- `INFECTED`：显示“检测到风险，文件不可下载”，隐藏检查按钮与链接。
- `FAILED`：显示“扫描失败，文件不可下载，请重新上传”，隐藏检查按钮与链接。

下载探测返回 `FILE_INFECTED` 或 `FILE_SCAN_FAILED` 时，页面把当前内存结果更新为对应终态；不写入 LocalStorage、SessionStorage 或 IndexedDB。

## 安全与兼容性

- CLEAN-only 服务端授权检查与审计顺序不变；任何非 CLEAN 状态都不会调用对象存储预签名。
- 新错误码只暴露已授权文件的粗粒度安全状态，不返回病毒签名、对象键、URL 或内部异常。
- 项目权限、匿名拒绝、文件不存在与审计契约不变。
- 不增加文件历史、状态轮询、后台定时器或新依赖。

## 测试

先写永久 RED：

1. 真实后端 API 对五种非 CLEAN 状态返回精确安全码且零预签名。
2. files API 只把精确终态包络映射为固定状态错误，畸形包络不被信任。
3. Drive 从直接 completion 以及下载探测两条路径正确呈现 `INFECTED` / `FAILED`，且隐藏重试与链接；扫描中与 CLEAN 回归不变。

完成后运行后端文件 API、前端 focused/full、Ruff、mypy、ESLint、Prettier、vue-tsc 与 Vite build。

## 独立备份恢复演练

代码验证完成后，读取当前 PostgreSQL 与 MinIO 形成临时备份。恢复使用带唯一前缀的新容器、新网络、新卷，不发布端口、不复用当前 Compose 卷。PostgreSQL 通过 schema head 与业务表计数校验；MinIO 通过递归对象路径、大小和 SHA-256 校验。finally 只删除已验证唯一前缀的临时资源与临时备份目录，当前 `m1-foundation` 栈与卷不删除、不覆盖。
