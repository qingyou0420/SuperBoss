# SuperBoss 审查与迭代方案：修复后记分卡（Fable 5.1，第六版）

日期：2026-09-07。基于 **`feature/p0-drop-devices-imports@22e94dc`**（提交 "feat: land Fable v5 E3 and U1-c recall, drive confirm, and visual gate"，相对上一 tip `e2309ce`（= 第五版基线 `79e2dd3` + 第五版文档合并 PR #7）只有这一个提交，改动 20 个文件，+234 / −59 行，另删除六张 PNG）的逐文件核查与本机实测。

**本文是第五版（[`审查与迭代方案-Fable51-UI-第五版-79e2dd3.md`](./审查与迭代方案-Fable51-UI-第五版-79e2dd3.md)）的后续，只回答一个问题：第五版列出的 N27–N36、E3 / U1-c / G 三张清单与决策点 R / T / U / V–Z，在 `22e94dc` 里哪些修好了、哪些修了一半、哪些没动，又引入了什么。** 第二版第三章的 UI 规格仍是唯一规格。

标注约定同前：**（已验证）** 附文件与行号，行号以 `22e94dc` 为准；**（实测）** 指在本机 PostgreSQL 16（`pg_trgm`）+ 真实 alembic 迁移上跑过，或在 vitest / jsdom 里渲染过；**（推断）** 指只读源码、未上浏览器或真实 LLM。

---

## 一、摘要

给老板的一页纸：

1. **第五版点名的两个中等级问题都修到底了。** N27（网盘"删除"一点即删）：菜单项改为只置 `pendingRemove`，`el-dialog`（360px、`close-on-click-modal=false`）里"确定"才调 `filesApi.remove`，复用 `driveCopy.removeConfirm`（"删除后不可恢复。"），新用例断言"点菜单 → `remove` 未调用 → 点确定 → 调用"。形态与成员页"禁用"对话框逐行一致（决策 W 落地，N30 一并关闭）。N29（召回上限先截后算）：`recall_needles` 去掉 8 上限、整句 token 仅在 `len ≤ 8` 时入候选，`recall` 对全部候选计命中后再取前 8；实测"把上季度的复盘纪要发给星野的对接人"和"帮我看看星野合作项目这个月的交付节点怎么样了"都召回"星野合作"（决策 V 落地）。

2. **第五版 E3 / U1-c 十条里九条落地。** N32（摘要首条 `[:4000]`）、N35（`files/service.py` 五处 `rollback()` 全删，改 `begin_nested()` 保存点；`audit/service.py`、`agent/router.py` 顺手一并改，`rg 'session.rollback\(\)' server/src/superboss/modules` 归零，**决策 T 三个服务全落地**）、N31（`downloadFile` 三条 INFECTED / FAILED / 其它 行为测试补回）、N34（表格下方虚线 `el-upload` 框删除，页头"上传"文字动作 + 整页拖放，决策 Z 落地）、N36（面包屑最后一级 `<span>`）、死文案（`CARD_STATUS_LABEL` 删除、`CARD_ERROR_LABEL` 补三个 code、`removeConfirm` 有了引用）。**只剩 N33（`ruff format --check` 进 CI）没动**，本机 30 文件待格式化（第五版 31）。

3. **CI 这条线第一次有了记录。** `ci.yml` 的 `push.branches` 加 `'feature/**'`（决策 Y），`22e94dc` 直推后 GitHub Actions 跑了 [run 34052228413](https://github.com/qingyou0420/SuperBoss/actions/runs/34052228413)：`server`（ruff + pytest **333 passed**）、`web`（lint + typecheck + vitest **133 passed**）、`e2e-visual` 三个 job 全绿。这是第三、四、五版连续三次 `gh run list` 为空后，特性分支第一次有 CI 绿。本机复跑数字与 CI 完全一致。

4. **但 `e2e-visual` 是一扇"绿色的空门"。** job 里只有 `npm ci` + `playwright install` + `playwright test visual-pages.spec.ts` 三步——**没有 Postgres service、没有 alembic、没有 seed、没有 uvicorn、没有 vite build**，`E2E_BASE_URL=https://127.0.0.1` 后面什么都不在听，`ownere2e / visual-gate-12` 这对账号没有任何脚本创建它。它之所以绿，是因为 spec 整体标了 `test.describe.fixme`，CI 日志实际输出是 **"Running 2 tests … 2 skipped"**。六张过期基线按决策 X 从仓库删掉了（这是诚实的一步），但**没有重抓**，仓库里现在零张基线、零张"对话页有卡片"截图。第五版 G-11 写的六个步骤只做了"job 存在 + 标 fixme"两步。runbook 一边把 `visual-pages.spec.ts` 列为 release gate、一边说 "Do not substitute … skipped specs for this gate"，自相矛盾。**视觉门从第五版的"任何机器上跑都红"变成"任何机器上跑都绿但什么都不看"——诊断更诚实了，保护力仍是零。**

5. **一个由 N29 修法暴露出来的新中低级问题（N37）：召回排序偏向最泛的词。** `recall` 把候选按**命中数降序**排、取前 8，再 `OR` 起来按 `importance` 取 8 条。命中数最多的恰恰是"项目 / 交付 / 节点 / 财务"这类出现在几十条记忆里的泛词，"星野"（只命中 1 条）排在它们后面。实测 60 条"第 N 周项目例会：交付节点、财务成本收入汇总"噪声记忆 + 1 条"星野合作是合作类项目"：问"把星野合作项目的交付节点和财务成本收入汇总一下发给对接人"，噪声 `importance = 3`（与星野同级）时"星野合作"排**第 8**（最后一格，靠插入顺序运气）；噪声 `importance = 4` 时**完全召回不到**。第五版两句验收之所以过，是因为测试数据里星野 `importance = 4` > 复盘 3。修法在第五章 E4-1。

6. **UI 新问题两件，都在新加的页头"上传"上（N38 / N39，均实测）。** `MultipartUploader` 的 `textButton` 模式仍渲染组件内的 `<p role="status">` / `<p role="alert">`，上传完成后 **"扫描中" 三个字永久留在页头右侧**（直到下次上传或刷新），同时列表里那一行已显示"处理中"——这是老板明令禁止的 tip spam；页头上传走组件内进度文字、拖放上传走 `UploadTray`，两套进度通道（N34 的另一半）没合并。另外 `el-upload` 根节点是 `div[role=button]`，里面再套一个 `el-button`，可访问性树里出现两个叫"上传"的按钮（Testing Library `getByRole('button', {name: '上传'})` 直接报 multiple）。

7. **顺序建议**：先修 N37（召回排序改"稀有词优先 + 按记忆计分"，几十行 + 一个 60 条噪声的单测）和 N38 / N39（`textButton` 模式不渲染状态段、走 `UploadTray`；用 `#trigger` 槽或隐藏 `<input type=file>` 去掉按钮套按钮），都是小改；然后**把 `e2e-visual` 做成真的**（`docker compose -f docker-compose.dev.yml up` 起 postgres / api / web / nginx，seed 两个账号，`workflow_dispatch` 一次 `--update-snapshots` 上传 artifact、提交七张基线，再去掉 `fixme`）；最后对一次性库跑 `llm_three_round.py`，报告贴 PR。见第五章。

---

## 二、核查表：第五版旧问题现状

### 2.1 核查方式与实测结果

- 逐文件阅读 `git diff e2309ce..22e94dc`（20 文件 + 6 张 PNG 删除），并通读 `server/src/superboss/modules/agent/{service,router}.py`、`audit/service.py`、`files/service.py`、`core/db.py`、`web/src/pages/owner/DrivePage.vue`、`web/src/pages/owner/UsersPage.vue`、`web/src/components/files/MultipartUploader.vue`、`web/src/components/ui/PageHeader.vue`、`web/src/copy/{glossary,pages/drive}.ts`、`.github/workflows/ci.yml`、`tests/e2e/{playwright.config.ts,specs/visual-pages.spec.ts,specs/support/environment.ts}`、`server/scripts/{llm_three_round,seed_acceptance}.py`、`docs/runbooks/m1-owner-acceptance.md`。
- 本机实测（Linux，本机 PostgreSQL 16 + `pg_trgm`，`alembic upgrade head`）：
  - `server`：`uv run ruff check src tests` 通过；`pytest tests/unit tests/api` **333 passed**（第五版 332，+1 `test_recall.py::test_recall_keeps_late_keywords_in_long_sentences` L90）。`ruff format --check` 报 **30 文件**（第五版 31），CI 仍不查。
  - `web`：`npm ci`、`npm run lint`（eslint + prettier）、`vue-tsc --noEmit` 通过；`vitest --run` **16 文件 133 用例全过**（第五版 129，+1 删除确认 +3 下载文案）；`vite build` 通过。
  - 另写四组临时探针（已删除、未入库）：(a) `recall_needles` 对七句话的输出；(b) 500 条记忆下 `recall` 的耗时；(c) 60 条泛词噪声下"星野"的召回（N37）；(d) 审计 `event_key` 竞态在 `begin_nested()` 下的语义（N35）；(e) jsdom 里渲染 `PageHeader + MultipartUploader(compact text-button)` 完成一次上传后的 DOM（N38 / N39）。结果记入 2.2 与第三章。
  - **CI 状态**：`22e94dc` 直推后触发 run 34052228413（push，`feature/p0-drop-devices-imports`）：`server` 1m03s ✓、`web` 38s ✓、`e2e-visual` 29s ✓。`e2e-visual` 日志：`Running 2 tests using 1 worker` → 两条 `-`（skipped）→ `2 skipped`。
  - 未实测：真实 LLM 端点、浏览器观感（仓库里已无任何基线）、Playwright 对真实栈实跑。

### 2.2 第五版 N27–N36

| 编号 | 第五版结论 | 现状 | 证据（已验证 / 实测） | 残留 |
|---|---|---|---|---|
| **N27** 网盘"删除"无确认 | 中，回归 | **FIXED** | `DrivePage.vue` L43-49 `pendingRemove` + `removeOpen` 双向 computed；L175-177 `requestRemove` 只置状态；L179-189 `confirmRemove` 才调 `filesApi.remove`，成功后清 `pendingRemove`；L391-397 菜单项改调 `requestRemove(row)`；L407-422 `el-dialog`（`:title="driveCopy.remove"`、360px、`close-on-click-modal=false`、正文 `driveCopy.removeConfirm`、"关闭" + `type="primary"` "确定"）。`drive-routing.test.ts` L350-387：点"删除"后 `remove` 未调用，点"确定"后 `toHaveBeenCalledWith(FILE_ID)` | 失败时 `errorMessage` 写在遮罩下的页面上、对话框不关、`pendingRemove` 不清（与第五版 N18 残留同款），见 N41 |
| **N28** 六张基线是改名、CI 未接 | 中 | **PARTIAL（1.5 / 4）** | ✓ 六张 `*.png` `git rm`（决策 X，`ls tests/e2e/specs/visual-pages.spec.ts-snapshots/` → 目录不存在）；✓ `ci.yml` L66-91 新增 `e2e-visual` job、L5 `push.branches: [master, 'feature/**']`（决策 Y），run 34052228413 是该分支**第一条** CI 记录；✓ `visual-pages.spec.ts` L12 `test.describe.fixme`、runbook L15 注明。✗ **job 没有栈**：无 `services:`、无 Python / uv、无 `alembic`、无 seed、无 `uvicorn`、无 `vite build`，`E2E_BASE_URL=https://127.0.0.1`（L69）无人监听；`E2E_OWNER_USERNAME=ownere2e / visual-gate-12`（L72-75）在仓库里除 `ci.yml` 外零引用，没有脚本创建它。✗ 未重抓（仓库里零张基线，`git log -1 -- …-snapshots/` 是本提交的删除）。✗ 第七张"对话页有卡片"未加。CI 绿 = `2 skipped` | 见 N40（假绿）。去掉 `fixme` 的那一刻：`page.goto('/login')` 连接被拒；即便有栈，Playwright 遇到不存在的基线会写下实际截图并判失败（需 `--update-snapshots` 一次） |
| **N29** 召回上限先截后算 | 低→中 | **FIXED（暴露 N37）** | `service.py` L129 `len(token) <= 8` 才把整句 token 入候选；L136-140 去掉 `sorted(..., reverse=True)` 与 `== 8: break`，按出现顺序去重不截断；L748-762 对全部候选 `count` 后 `scored.sort(key=(-hits, -len))`、`[:8]`。**实测** `recall_needles("把上季度的复盘纪要发给星野的对接人")` → 16 个候选含 `星野`；`recall(...)` → `['星野合作是合作类项目', '老板偏好项目按季度复盘']`（顺序正确）；80 字长句 54 个候选、500 条记忆下 `recall` **34 ms**（16 候选 19 ms），N21 不必现在做 | 排序 `-hits` 在前，泛词压过专名，见 **N37** |
| **N30** 两页确认形态不一致 | 低 | **FIXED** | `DrivePage.vue` L407-422 与 `UsersPage.vue` L208-225 结构逐行相同（`el-dialog` 360px / `close-on-click-modal=false` / `<p>` 文案 / footer "关闭" + primary "确定"）。决策 W 采纳 | `rg 'el-popconfirm' web/src` 仍为零，全项目只此一种确认形态 |
| **N31** 网盘下载文案无测试 | 低 | **FIXED** | `drive-routing.test.ts` L440-483 `test.each` 三态：`INFECTED → "检测到风险，文件不可下载"`、`FAILED → "扫描失败，文件不可下载，请重新上传"`、其它异常 → `"文件仍在扫描中。"`，都走真实 `···` → "下载" 菜单路径 | 第四版"QUARANTINED 重试直至可下载"用例不再适用（`downloadFile` L135-149 已不重试），无需补 |
| **N32** 摘要首条不截断 | 极低 | **FIXED** | `service.py` L925 `line = f"…".strip()[:4000]`；L935-939 拼接 `new_text` 时同样 `[:4000]` | — |
| **N33** `ruff format --check` 不在 CI | 极低 | **OPEN** | `ci.yml` L37-39 只有 `ruff check`；本机 `ruff format --check src tests` → **30 files would be reformatted** | 第五版 31 → 30，仍未进 CI |
| **N34** 两套上传入口 | 低 | **FIXED（引入 N38 / N39）** | `DrivePage.vue` 表格下方 `<MultipartUploader>` 删除；L239-246 页头 `compact text-button`；`MultipartUploader.vue` L21-24 新 prop，L103 `:drag="!compact && !textButton"`，L106-108 `<el-button text>` 上传；`rg ':drag=' web/src/pages` 无输出。决策 Z 采纳 | 页头上传仍走组件内 `<p>` 进度、不走 `UploadTray`；见 N38 |
| **N35** `files/service.py` 五处 `rollback()` | 低 | **FIXED** | `files/service.py` L200-203 `begin_nested()` 包 `add + flush`，L204-216 `IntegrityError` 分支不再 `rollback`；L227-228 / L236-237 / L252-253 / L336-337 四处 `raise` 前的 `rollback()` 删除。请求级回滚由 `core/db.py` L39-50 `get_session` 的 `except: rollback` 兜底。`audit/service.py` L118-127 同样改 `begin_nested()`，`IntegrityError` 后在**同一外层事务里**重查 `event_key`；`agent/router.py` L119-127 `chat_stream` 删 `except: rollback`，`finally: close()` 隐式回滚。**实测**（N35 探针 d）：先查不到 → 竞争者提交同 `event_key` → 保存点内 `flush` 抛 `IntegrityError` → 外层事务仍可用，重查命中竞争者、返回其 `id`、`commit` 成功、库里只一行 | `IntegrityError` 竞态分支在审计 / 文件两处都**没有单测**（`test_audit_service.py` L68-91 只测顺序重放，走的是"查到即复用"路径），低 |
| **N36** 面包屑末级可点 | 低 | **FIXED** | `DrivePage.vue` L265-272 `index === breadcrumbs.length - 1` 渲染 `<span>`，其余 `<a>` | — |

### 2.3 第五版 E3 / U1-c / G 清单逐条

| # | 条目 | 现状 | 备注 |
|---|---|---|---|
| E3-1 | N29 不截断 + 计完命中再取 8 + 两句单测 | FIXED | `test_recall.py` L90-113 两句都断言命中"星野合作"；引入 N37 |
| E3-2 | N32 `[:4000]` | FIXED | — |
| E3-3 | N35 五处 `rollback()` 改只 `raise` | FIXED | 用 `begin_nested()` 而非裸 `raise`，比第五版建议更稳（`IntegrityError` 后会话仍可用）；顺手改了审计与 SSE 路由 |
| E3-4 | N33 `ruff format` + `--check` 进 CI | **OPEN** | 未动 |
| U1-c-5 | N27 删除确认 + 三步断言 | FIXED | — |
| U1-c-6 | N30 两页统一形态 | FIXED | 决策 W |
| U1-c-7 | N31 三条下载文案测试 | FIXED | — |
| U1-c-8 | N34 删虚线框 + 页头"上传" | FIXED | 引入 N38 / N39 |
| U1-c-9 | N36 末级 `<span>` | FIXED | — |
| U1-c-10 | 死文案：`CARD_STATUS_LABEL` / `removeConfirm` / `CARD_ERROR_LABEL` 补三 code | FIXED | `glossary.ts` L56-66 九项含 `FINANCE_ENTRY_NOT_FOUND / FOLDER_NOT_FOUND / VALIDATION_ERROR`；`CARD_STATUS_LABEL` 全项目零引用后删除；但 `drive.ts` 新增 `confirmRemove: '确定'` 与既有 `confirm: '确定'` 重复，`driveCopy.failed / newFolder` 两键零引用（N42） |
| G-11 | 视觉：CI job + Postgres/alembic/seed/uvicorn/build + Linux 重抓 + 第七张 + 过渡期 `fixme` | **2 / 6** | job 壳 ✓、`fixme` + runbook ✓；栈 ✗、重抓 ✗、第七张 ✗、基线 ✗。见 N28 / N40 |
| G-12 | LLM：加"看一下星野项目"断言 + 跑一次贴报告 | **1 / 2** | `llm_three_round.py` L26 `RECALLS = ("昨天那个合作项目", "看一下星野项目")`，L170-186 循环两问并各自计 `offline / mentions_xingye`；**未跑**，PR / 仓库无 JSON 报告。断言本身仍弱，见 N43 |
| G-13 | `ci.yml` 对 `feature/**` 直推也跑 | FIXED | `ci.yml` L5；run 34052228413 |

### 2.4 第五版第五章八条 `rg` 验收（对 `22e94dc` 实跑）

| 命令 | 期望 | 本版 |
|---|---|---|
| `pytest tests/unit/agent -q` 含 N29 两句新用例 | 全绿 | **全绿**（333 全量） |
| `rg -n 'session.rollback\(\)' server/src/superboss/modules` | 无 | **0**（全仓只剩 `core/db.py` L45 请求级兜底） |
| `rg -n 'el-popconfirm\|el-dialog' DrivePage.vue UsersPage.vue` | 两页同形态 | **三处 `<el-dialog>`，零 popconfirm** |
| `rg -n 'removeConfirm' web/src --glob '!web/src/copy/**'` | ≥ 1 | **1**（`DrivePage.vue` L413） |
| `rg -n ':drag=' web/src/pages/owner/DrivePage.vue` | 无 | **0**（移入 `MultipartUploader.vue` L103 作条件） |
| `git log -1 --format=%h -- tests/e2e/specs/visual-pages.spec.ts-snapshots/` | 不再是 `79e2dd3` | **`22e94dc`——但那是删除，不是重抓** |
| `rg -n 'e2e-visual\|playwright' .github/workflows/ci.yml` | 有输出 | **有**（L66-91） |
| `gh run list --branch feature/p0-drop-devices-imports --limit 1` | 有记录 | **有**（34052228413，success） |

第二版 U0 十条重跑：`el-alert / el-card / ElMessageBox` 0、`:deep` 只在 `MultipartUploader.vue` scoped 样式（允许）、`type="primary"` 每文件 > 1 仍 3 文件（成员 3 / 财务 2 / 项目详情 2，均在互斥容器内；网盘 1，在对话框内）、`📎` 0、旧网盘文案 0。**前端字面量指标维持达标。**

### 2.5 决策点 R / T / U / V–Z 执行情况

| 编号 | 第五版默认 | 本版 |
|---|---|---|
| R 视觉回归放 CI + 容器重抓 + 过渡期 `fixme` | 下一轮必须 | **半做**：job 与 `fixme` ✓；栈与重抓 ✗ |
| T 服务层不 `rollback()` | 文件服务五处一起改 | **全落地**（项目 / 用户 / 文件 / 审计 / SSE 路由） |
| U 二次确认形态 | 并入 W | **并入 W** |
| V 召回候选不截断、计完命中再取前 8 | 采纳 | **采纳**；实测 54 候选 34 ms，N21 不必做；但暴露 N37 |
| W 两页统一 `el-dialog` | 采纳 | **采纳** |
| X 六张旧基线 `git rm` | 采纳 | **采纳**（未重抓） |
| Y `ci.yml` 跑 `feature/**` | 采纳 | **采纳**，首条 CI 记录 |
| Z 删虚线上传框、页头"上传"文字动作 | 采纳 | **采纳**（引入 N38 / N39） |

---

## 三、新问题 / 回归（按风险排序）

### 3.1 服务端

**N37（实测，低→中）召回排序偏向泛词，专名可被挤出。** `service.py` L759 `scored.sort(key=lambda item: (-item[0], -len(item[1])))` 把**命中最多**的候选排最前、L760 取前 8，再 L763-776 `or_(*matches)` 按 `importance desc limit 8`。命中数最多的必然是"项目 / 交付 / 节点 / 财务 / 成本 / 收入 / 汇总"这类几十条记忆里都有的泛词；"星野"只命中 1 条，排在它们之后。N29 之前候选被截到 8 个、多数泛词根本进不了候选，问题被遮住；N29 修完全部候选都计分，这个反向排序就露出来了。**实测**：60 条 `"第 N 周项目例会：交付节点、财务成本收入汇总要发给老板"`（`importance = 3`）+ 1 条 `"星野合作是合作类项目"`（`importance = 3`），问 `"把星野合作项目的交付节点和财务成本收入汇总一下发给对接人"`：候选 26 个、`星野` 在其中；`ranked` 前 7 是七个泛词（各 60 命中）、`星野` 第 8；`OR` 出 61 条按 `importance` 取 8 → "星野合作"排**最后一格**，靠插入顺序运气进的。把噪声 `importance` 改成 4 → **返回 8 条全是例会，"星野合作"没有**。第五版两句验收能过，是测试数据里星野 `importance = 4` > 复盘 3。**修法**（E4-1）：(a) 候选排序改**命中数升序**（稀有优先），且丢弃命中数 > 活跃记忆总数 30% 的泛词（除非全是泛词）；(b) 更根本的是 `OR` 之后不能只按 `importance`——对每条候选记忆按"命中了几个候选词、每个词命中数的倒数之和"计分，`score desc, importance desc` 取 8。单测：上面这组 60 + 1、噪声 `importance = 4`，必须召回"星野合作"且排第一。

**N43（已验证，低）LLM 门的召回断言不判别，`--dry-run` 仍会写库。** `llm_three_round.py` L181-184：`"星野" not in recall_content and not any("星野" in memory)` ——只要记忆抽取产出过任何含"星野"的条目，回复里有没有"星野"都算过；而第二问 `"看一下星野项目"` 本身含"星野"，LLM 复述一遍就 `mentions_xingye = True`。两问加起来验证的是"抽取过星野记忆"，不是"召回能把它找回来"。另 L137-143 在 `dry_run` 下仍会 `POST /projects {"name": "星野合作"}`（真实写入），与 docstring "send messages without confirming cards" 的读者预期不符。**修法**：断言改为"回复里含'星野'**且**该回复的 `recall` 结果（可从 `/api/v1/agent/memories` 的 `recall_count` 增量或新增一个只读 `GET /agent/recall?q=` 探测）含星野记忆"；`--dry-run` 下项目不存在就直接 `SystemExit("需要先有星野项目")`。

**N35 残留（已验证，低）** 审计 / 文件两处 `IntegrityError` 竞态分支无单测（见 2.2）。本次探针证明语义正确，但没有回归保护。

### 3.2 前端

**N38（实测，低，tip spam）页头"上传"完成后"扫描中"永久留在页头。** `MultipartUploader.vue` L126-127 `<p v-if="status" role="status">` / `<p v-if="errorMessage" role="alert">` 在 `textButton` 模式下照常渲染；L71 完成后 `status = driveCopy.scanning` 且不再清空。组件根 `.uploader` 是 `display: grid; gap: 8px`（L132-135），放进 `PageHeader` 的 `.page-header__actions`（flex 行，`PageHeader.vue` L33-37）后，状态段就挂在"上传"按钮正下方。**实测**（jsdom）：`PageHeader + MultipartUploader(compact text-button)` 触发一次上传并完成后，`.page-header__actions [role=status]` 的文本是 `"扫描中"`，此时 `showCompleted()` 已刷新列表、该文件行显示"处理中"——同一状态两处、其中一处在页头且不消失。上传过程中"上传中 42%"同样出现在页头，而拖放上传走 `UploadTray`——两套进度通道（第五版 N34 原文"前者走组件内进度文字，后者走 `UploadTray`"这半句仍成立）。**修法**：`textButton` 模式下不渲染两个 `<p>`，通过 `emit('selected', file)` 让 `DrivePage` 用已有的 `useMultipartUpload().upload()` 走 `UploadTray`（拖放路径 L218-224 已是这么做的）；或 `DrivePage` 页头直接放隐藏 `<input type=file>` + 文字按钮，不再复用 `MultipartUploader`。

**N39（实测，低，可访问性）按钮套按钮。** `el-upload` 根节点是 `div.el-upload[role="button"][tabindex]`，`textButton` 模式在里面再放 `<el-button text>`（L106-108）。**实测** `getAllByRole('button', { name: '上传' })` 返回 2 个（`DIV.el-upload`、`BUTTON.el-button`），`getByRole` 直接抛 multiple。键盘用户 Tab 两次都停在"上传"，读屏读两遍。也是本提交的网盘测试为何要 `stubs: { MultipartUploader: true }`。**修法**：用 `el-upload` 的 `#trigger` 槽放一个非按钮元素（`<span class="text-action">上传</span>`），或按 N38 第二方案自持 `<input type=file>`。

**N41（已验证，低）两页确认对话框失败时看不到错误。** `DrivePage.vue` L186-188 `catch → errorMessage.value = driveCopy.deleteFailed`，`pendingRemove` 不清、对话框不关；`InlineError` 在 L259 页面正文，被遮罩盖住。成员页同款（第五版 N18 残留，当时以"等决策 W"押后；W 已定，可以做了）。**修法**：失败时先 `pendingRemove = undefined` 再写 `errorMessage`（对话框关、页面显示一句），或对话框内加一行 `InlineError`。

**N42（已验证，极低）死文案与重复键。** `drive.ts` L18 `confirmRemove: '确定'` 与 L17 `confirm: '确定'` 重复；`driveCopy.failed`（"未通过"，已被 `FILE_STATE_LABEL` 取代）、`driveCopy.newFolder`（"新建目录"，已被 `newSubfolder` 取代）零引用。

### 3.3 门

**N40（已验证，低但影响判断）`e2e-visual` 是假绿。** 见 2.2 N28。要点：(1) spec L12 `describe.fixme` → CI 输出 `2 skipped`，job 29 秒绿；(2) job 无任何后端 / 前端 / 数据库步骤，`E2E_BASE_URL` 指向空端口，去掉 `fixme` 立刻连接被拒；(3) `ownere2e / staffe2e` 无人创建——`seed_acceptance.py` L181-205 要 `SUPERBOSS_OWNER_USERNAME / SUPERBOSS_ACCEPTANCE_STAFF_USERNAME` 与 `getpass` 交互读密码，CI 里没有 tty，需要改成可从 stdin / 环境读取或加 `--password-stdin`；(4) 仓库零基线，首跑必须 `--update-snapshots` 并把 `*-snapshots/` 作为 artifact 下载后提交；(5) runbook L15 把这条 `fixme` spec 列为 release gate，L17 又说 "Do not substitute … skipped specs for this gate"。**一个绿色徽章下面是零张截图、零条断言**，比第五版"必红"更容易让人误判"视觉门装好了"。**修法**见第五章 G-11。

**N33（已验证，极低）`ruff format --check`** 30 文件，CI 不查（第五版已列，未动）。

**LLM 门（已验证）**：警示 + `--dry-run` + 两问 + runbook 齐，**仍未跑**；断言弱、`--dry-run` 写项目（N43）。仍是"手工验收的正确起点"，不是门。

### 3.4 未变（对老板两条底线）

- **三层账号 + OWNER-only 霜月 + 卡片先确认再写**：本提交未触碰 `confirm_card / commit_card / cards.py`、路由权限、`_owner` 依赖；333 用例含第四 / 五版全部卡片用例全绿。**无变化、无回归。**
- **一屏一个实心**：网盘页新增的实心在对话框内（互斥容器），页头仍是两个文字动作（"上传"、`···`）。

---

## 四、UI 观感复核

依据：源码 + jsdom 渲染。**仓库里已没有任何截图基线，以下对页面观感的判断均为推断。**

### 4.1 本轮进步（源码层面）

- **网盘页**：页头从"`···`"变为"上传 · `···`"两个文字动作；表格下方那块虚线拖放框没了，右栏现在就是四列表格 + 行尾 `···`，与第二版 3.5 的形态一致；面包屑末级不再是链接；删除有 360px 居中确认，文案"删除后不可恢复。"。**第四版起最扎眼的那页，结构性问题清零。**
- **两页确认形态统一**：成员"禁用"与网盘"删除"是同一个对话框，"关闭 / 确定"位置一致。
- **文案表**：`CARD_ERROR_LABEL` 九项，卡片失败不会再直出英文 code（除 `CARD_COMMIT_FAILED` 之外的 `DomainError.code` 都已覆盖）。

### 4.2 仍显廉价 / 需确认的地方

1. **页头"扫描中"残留**（N38）：上传完成后页头右侧多出一行灰字不消失，与"无 tip spam"直接冲突；上传中"上传中 42%"出现在页头而不是页面底部的 `UploadTray`。
2. **按钮套按钮**（N39）：视觉上看不出，键盘 / 读屏能感到。
3. **对话框失败无反馈**（N41）：点"确定"失败后对话框原地不动，错误写在遮罩下面。
4. **零基线**（N40）：本轮所有 UI 改动没有一张截图证据；第四版以来"网盘页变样了"三次都只能凭源码判定。
5. **小处**：`driveCopy.failed / newFolder` 死键、`confirmRemove` 重复键。

### 4.3 结论

按第二版十条戒律：机械指标继续全部达标；网盘页在源码层面完成了第二版规格的最后一件（页头上传 + 整页拖放）。**观感上只剩 N38 一件违反"无 tip spam"的硬伤，十几行可修。但连续第二版零截图，"高端"与否仍无法用证据回答——下一轮不重抓，本文第四章就还得写"推断"。**

---

## 五、下一步迭代（短、可执行）

每条一个 PR；G-11 完成前不再做任何 UI 改动。

**E4 — 服务端补漏（几十行）**

1. **N37**：`recall` 候选按命中数**升序**排、丢弃命中 > 30% 活跃记忆的泛词（全泛词时保留）；`OR` 之后对每条记忆按"命中候选词数 + Σ 1/hits"计分，`score desc, importance desc` 取 8。单测：60 条例会噪声（`importance = 4`）+ 1 条星野（`importance = 3`），`"把星野合作项目的交付节点和财务成本收入汇总一下发给对接人"` 必须召回"星野合作"且排第一。
2. **N33**：单独 PR `ruff format`（30 文件），`ci.yml` server job 加 `ruff format --check src tests`。
3. **N35 残留**：`test_audit_service.py` 加一条"查不到 → 竞争者提交 → 保存点内 `IntegrityError` → 复用竞争者 id、只一行"的用例（本文探针 (d) 可直接改成测试）；`test_file_uploads.py` 加同键并发 start 的用例。
4. **N43**：`--dry-run` 下星野项目不存在则退出而非创建；召回断言改为回复含"星野"**且**召回结果含星野记忆。

**U1-d — 页头上传收尾 + 对话框反馈**

5. **N38**：`MultipartUploader` `textButton` 模式不渲染 `<p role=status/alert>`；`DrivePage` 页头上传改走 `useMultipartUpload().upload()` + `UploadTray`（与拖放同一通道）。vitest：完成上传后 `.page-header__actions` 内无 `[role=status]`，`UploadTray` 出现过一项。
6. **N39**：去掉按钮套按钮（`#trigger` 槽放 `<span>`，或自持 `<input type=file>`）；vitest `getByRole('button', {name: '上传'})` 唯一；网盘测试可去掉 `stubs: { MultipartUploader: true }`。
7. **N41**：两页对话框失败即关、页面显示一句错误（或对话框内 `InlineError`）；两页各加一条失败用例。
8. **N42**：删 `driveCopy.failed / newFolder / confirmRemove`（后者改用 `confirm`）。

**G — 把视觉门做成真的**

9. **N40 / N28**：`e2e-visual` job 改为：`docker compose -f docker-compose.dev.yml up -d postgres redis minio minio-init api web nginx`（已有的本地栈，nginx 提供 `https://app.localhost`）→ 等 `api` 健康 → `alembic upgrade head` → `seed_acceptance.py`（加 `--password-stdin` 或从 `SUPERBOSS_OWNER_PASSWORD / SUPERBOSS_ACCEPTANCE_STAFF_PASSWORD` 读，CI 无 tty）→ `npx playwright test specs/visual-pages.spec.ts`。加一个 `workflow_dispatch` 输入 `update_snapshots=true` 时跑 `--update-snapshots` 并 `actions/upload-artifact` 上传 `specs/visual-pages.spec.ts-snapshots/`；**在 CI 容器里抓一次、下载、提交七张**（六页 + "对话页有一张待确认卡 + 一条回执"），再去掉 `describe.fixme`。成员页基线必须是正常空态。runbook L15-17 改掉"fixme 也是 gate"的矛盾。
10. **LLM**：E4-1 / E4-4 合并后对一次性库先 `--dry-run` 再正式跑，JSON 报告贴 PR。

**验收（命令输出为准）**

```bash
cd server && pytest tests/unit/agent -q                                     # 全绿，含 N37 60+1 用例
cd server && uv run ruff format --check src tests                           # 0 files would be reformatted
rg -n 'ruff format --check' .github/workflows/ci.yml                        # 有输出
rg -n 'role="status"|role="alert"' web/src/components/files/MultipartUploader.vue  # 不在 textButton 分支
rg -n 'stubs: \{ MultipartUploader: true \}' web/tests/drive-routing.test.ts # 无输出（N39 修完可直渲）
rg -n 'services:|alembic|seed_acceptance|docker compose' .github/workflows/ci.yml  # e2e-visual 段内有输出
ls tests/e2e/specs/visual-pages.spec.ts-snapshots/ | wc -l                  # 7
rg -n 'describe.fixme' tests/e2e/specs/visual-pages.spec.ts                 # 无输出
gh run view --job <e2e-visual job id> --log | rg '7 passed'                 # 不是 "2 skipped"
```

---

## 六、暂不做 / 决策点

**暂不做**（沿用二至五版，全部仍成立）：深色模式、图表、插图空态、导航图标、Webfont、国际化、pgvector、员工侧霜月、目录级 ACL、外部 API 令牌、卡片自动入库、记忆抽取按会话 owner 构造 `Actor`、`CARD_ERROR_LABEL` 改服务端下发、`0011` 的 `downgrade`。

**本轮新增暂不做：**

- **N21（`recall` 合并成一条 SQL）正式关闭**：实测 54 候选 34 ms、16 候选 19 ms（500 条记忆），N37 修完再看一次即可，不单独做。
- **给 `MultipartUploader` 加第三种模式**——N38 / N39 修完后 `textButton` 模式若只剩一个 `<input type=file>`，不如让 `DrivePage` 自持，删掉这个 prop。
- **审计 `IntegrityError` 分支改 `INSERT … ON CONFLICT`**——保存点方案已实测正确，不再改写。

**待老板拍板**（编号接续第五版 V–Z）：

| 编号 | 问题 | 本文默认 |
|---|---|---|
| R | 视觉回归放 CI | job 已有、栈没有；**下一轮必须按 G-9 起真实栈并重抓七张**，在此之前 `e2e-visual` 的绿不作数 |
| AA（新） | 召回排序：稀有词优先 + 按记忆计分 vs 只改升序 | **两者都做**（E4-1）；只改升序解决不了 `OR` 之后按 `importance` 截断的问题（本文实测噪声 `importance = 4` 即失） |
| AB（新） | 页头"上传"的进度去哪 | **走 `UploadTray`**，与拖放同一通道；页头只留一个文字按钮，不出现任何状态字 |
| AC（新） | `e2e-visual` 的栈用什么起 | **`docker-compose.dev.yml` 现有服务**（postgres / api / web / nginx），不在 `ci.yml` 里手写第二套 uvicorn + 静态服务；`seed_acceptance.py` 加非交互读密码 |
| AD（新） | 基线由谁抓 | **CI 容器 `workflow_dispatch --update-snapshots` + artifact**，人只做下载与提交；本机（Windows / macOS）抓的图一律不收 |
| AE（新） | LLM 门断言 | **改为"回复含星野 且 召回结果含星野记忆"**（E4-4）；当前"记忆里有过星野就算过"不判别召回 |
