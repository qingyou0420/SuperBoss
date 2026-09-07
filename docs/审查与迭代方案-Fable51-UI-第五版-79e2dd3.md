# SuperBoss 审查与迭代方案：修复后记分卡（Fable 5.1，第五版）

> **后续**：本文所列 N27–N36、E3 / U1-c / G 清单与决策点 R / T / U / V–Z 在 `22e94dc` 上的复核结果见第六版 [`审查与迭代方案-Fable51-UI-第六版-22e94dc.md`](./审查与迭代方案-Fable51-UI-第六版-22e94dc.md)。

日期：2026-09-06。基于 **`feature/p0-drop-devices-imports@79e2dd3`**（提交 "feat: land Fable v4 E2 and U1-b reliability and drive polish"，相对上一 tip `043a7ca`（= 第四版基线 `67e843e` + 第四版文档合并）只有这一个提交，改动 44 个文件，+701 / −480 行）的逐文件核查与本机实测。

**本文是第四版（[`审查与迭代方案-Fable51-UI-第四版-67e843e.md`](./审查与迭代方案-Fable51-UI-第四版-67e843e.md)）的后续，只回答一个问题：第四版列出的 N3 / N5 半成品、N17–N26 新问题、网盘 U1-8 剩下六件、E2 / U1-b / G 三张清单与决策点 K / N / O / R–U，在 `79e2dd3` 里哪些修好了、哪些修了一半、哪些没动，又引入了什么。** 第二版第三章的 UI 规格仍是唯一规格。

标注约定同前：**（已验证）** 附文件与行号，行号以 `79e2dd3` 为准；**（实测）** 指在本机 PostgreSQL 16 + 真实 alembic 迁移（含 `0011`）上跑过；**（推断）** 指只读源码、未上浏览器或真实 LLM。

---

## 一、摘要

给老板的一页纸：

1. **第四版点名的两个半成品这次修到底了。** N5-b（同名项目卡确认 → 500）：`ProjectService.create / update` 改为先查 `lower(name)` 预检、直接 `raise ConflictError`，两处 `session.rollback()` 删掉；实测走 `AgentService.confirm_card` 真实路径，同名 `project_create` 卡返回 `FAILED / PROJECT_NAME_CONFLICT`、接口不再 500、同一事务里其它待写入行不丢、再点"直接改"改名后 `PROPOSED → COMMITTED`。N3-b（奇数位关键词切不出）：`recall_needles` 改成全滑动窗口两字，"给星野加个 10 月 20 日交付节点"和"看一下星野项目"实测都召回"星野合作"，且排在"按季度复盘"之前。**决策 T（服务层不回滚）在项目 / 用户两处落地。**

2. **N17 / N18 / N19 三个新问题全修，且 N17 走的是第四版建议的"服务端把 FAILED 改回 PROPOSED"路线。** 失败卡现在显示失败原因（`CARD_ERROR_LABEL`）、"直接改"真的进编辑表单、"放弃"可用；`reject_card / patch_card` 服务端放行 FAILED；前端 `retries` 计数删了。成员"禁用"有二次确认（但用的是 `el-dialog` 而非决策 U 说的 `el-popconfirm`）。`element.css` 去掉 `:deep`，`.plain-table` 无边框规则在构建产物里终于生效。

3. **网盘页第四版剩下的六件一次做完（7 / 7）。** 文件行改 `el-table`（名称 · 大小 · 日期 · 上传者 · `···`）、下载 / 重命名 / 移动 / 删除进行尾 `···`、重命名行内、移动进 `el-drawer`、"新建子目录"进页头 `···` + 抽屉、上传结果块整块删除（完成只刷新列表、行内显示"处理中 / 未通过"）、面包屑改 `a / span`、左栏目录改左对齐文字按钮。加上三处空态（成员 / 财务 / 审计 `:empty-text`，对话页 `grid-template-rows: 1fr auto` + 居中一句"对霜月说一句……"）、📎 换 SVG、SOUL → "霜月设置"、"创建项目" → "保存"、里程碑"取消完成"、毛利口径改含项目、`/owner/*` 重定向删除、`api/*.ts` 八个错误前缀进 `errorCopy`——**第四版 U1-b 十三条全部落地。**

4. **但网盘页带回来一个和 N18 同款的洞：`···` → "删除"一点即删，没有确认。** 旧版删除按钮包着 `el-popconfirm`，这次改菜单时一并删了；服务端 `delete_file` 是硬删（删行 + 删对象），`driveCopy.removeConfirm`（"删除后不可恢复。"）成了死文案，测试里没有任何一处点"删除"。**这是本提交唯一的中等级新问题（N27），修法十几行。**

5. **两扇门：LLM 门按第四版要求装好了警示与 `--dry-run`，视觉门这次是"换了名字、没换照片"。** `playwright.config.ts` 加了 `snapshotPathTemplate` 去平台后缀，六张 `*-win32.png` 改名为 `*.png`——但 `git diff -M` 显示六张全是 **100% rename，一个像素没变**。而这同一个提交把网盘页重做、对话页加空态、成员 / 财务空态改写、项目详情元信息行改写，六张里至少五张（除登录页）已经与代码对不上；成员页基线仍是第四版指出的那张"操作失败。+ 暂无数据 + 还没有成员"出错页。`ci.yml` 未动，Playwright 仍不在 CI；`gh run list --branch feature/p0-drop-devices-imports` 仍为空。**视觉门现在处于"在任何机器上跑都必失败"的状态，比第四版"平台不对"更糟。**

6. **机械指标：** `ruff check` 过、`pytest tests/unit tests/api` **332 passed**（第四版 329，+3 新用例）、`npm run lint / typecheck` 过、`vitest` **129 passed**（第四版 131，网盘五个旧用例换成三个新用例，净 −2）、`vite build` 过。第四版第五章的七条 `rg` 验收全部清零。`ruff format --check` 从 29 文件涨到 31，CI 不查。

7. **顺序建议**：先补 N27（删除确认）和 N29（召回上限先算命中再截断，实测"把上季度的复盘纪要发给星野的对接人"召回不到"星野"），都是小改；然后**在 Linux 容器里重抓六张基线 + 把 Playwright 接进 CI**——不重抓，本提交对网盘 / 空态的所有改动就没有任何视觉回归保护；最后对着一次性库跑一次 `llm_three_round.py`，报告贴 PR。见第五章。

---

## 二、核查表：第四版旧问题现状

### 2.1 核查方式与实测结果

- 逐文件阅读 `git diff 043a7ca..79e2dd3`（44 文件），并通读 `server/src/superboss/modules/agent/{service,cards}.py`、`projects/service.py`、`users/service.py`、迁移 `0011_backfill_seq.py`、`web/src/pages/owner/DrivePage.vue`、`web/src/pages/owner/UsersPage.vue`、`web/src/pages/ChatPage.vue`、`web/src/components/chat/ProposalCard.vue`、`web/src/styles/element.css`、`tests/e2e/playwright.config.ts`、`server/scripts/llm_three_round.py`、`docs/runbooks/m1-owner-acceptance.md`。
- 本机实测（非 CI）：
  - `server`：`uv run ruff check src tests` 通过；PostgreSQL 16（`pg_trgm`）上经 `alembic upgrade head`（含 `0011`）后 `pytest tests/unit tests/api` **332 passed**。新增三个用例：`test_commit_failed.py::test_same_name_project_create_card_fails_closed`（L99）、`::test_failed_card_can_be_patched_or_rejected`（L134）、`test_recall.py::test_recall_hits_odd_offset_keywords`（L59）。`ruff format --check` 报 31 文件（第四版 29），CI 不查，仅记录。
  - `web`：`npm ci`、`npm run lint`（eslint + prettier）、`vue-tsc --noEmit` 均通过；`vitest --run` **16 文件 129 用例全过**；`vite build` 通过。
  - 另写一组临时探针测试（已删除、未入库）在真实数据库上复现：(a) 走 `AgentService.confirm_card` 的同名项目卡完整链路；(b) 四句话的 `recall` 结果；(c) 迁移 `0011` 的回填 SQL 对乱序数据的效果。结果记入 2.2 与第三章。
  - **CI 状态**：与第四版相同，`ci.yml` 只在 `pull_request` 与 `push: master` 触发，`79e2dd3` 直推特性分支，无任何 CI 运行。上面的绿都是本机的。
  - 未实测：真实 LLM 端点、浏览器观感（六张基线是旧图，见 2.5 / 3.3）、Playwright 实跑。

### 2.2 第四版 N3 / N5 半成品与 N17–N26

| 编号 | 第四版结论 | 现状 | 证据（已验证 / 实测） | 残留 |
|---|---|---|---|---|
| **N3-b** 奇数位关键词切不出 | PARTIAL，中 | **FIXED（引入 N29）** | `service.py` L131-135：`len(token) >= 2` 时对每个偏移 `range(len(token) - 1)` 切两字，删掉"偶数位 + 尾巴"逻辑；停用词表删 `"列一"`（L83-108 现 22 个）；`test_recall.py` L59-85 新用例断言两句都命中"星野合作"且排在"按季度复盘"前。实测 `recall_needles("给星野加个 10 月 20 日交付节点") → ['给星野加个','日交付节点','给星','星野','野加','加个','10','20']`，`recall(...)` 返回 `['星野合作是合作类项目']`；`"看一下星野项目"` 返回 `['星野合作…','老板偏好项目按季度复盘']`（顺序正确） | **上限 8 在计命中之前就截断**（L136-141 按长度降序去重到 8 就 `break`），整句 token 永远占第一格，长句里靠后的关键词进不了候选：实测 `"把上季度的复盘纪要发给星野的对接人" → ['把上季度…对接人','把上','上季','季度','度的','的复','复盘','盘纪']`，**无"星野"**，`recall` 只返回"老板偏好项目按季度复盘"。见 N29 |
| **N5-b** 同名项目卡确认 → 500 | PARTIAL，中 | **FIXED** | `projects/service.py` L98-102 `_name_taken`（`lower(name)` 比对，与 `models.py` L56 `uq_projects_name_ci` 口径一致）；`create` L108-111、`update` L174-177 预检抛 `ConflictError("PROJECT_NAME_CONFLICT")`；L122 / L195 `IntegrityError` 分支删 `session.rollback()`，只留 `raise`；`users/service.py` L116-119 同样加 `username` 预检、L124 删 `rollback`。`test_commit_failed.py` L99-131 走 `commit_card` 断言 `FAILED / PROJECT_NAME_CONFLICT`、项目计数不变。**实测（走 `AgentService.confirm_card` 真实路径）**：返回 `FAILED / PROJECT_NAME_CONFLICT`；同 session 里另一条未提交 `AgentMessage` 存活；对同一张 FAILED 卡再次 `confirm_card` 仍 `FAILED`（不 409、不 500）；`patch_card` 改名 → `PROPOSED / error=None / decided_at=None`；再 `confirm_card` → `COMMITTED`；`"alpha"` vs 已有 `"Alpha"` 也判冲突 | `files/service.py` L205 / L229 / L239 / L256 / L341 仍有五处 `session.rollback()`，全在上传 provisioning / complete 路径（`_provision_upload`、分片完成），不在任何卡片路径上，无 500 风险，但决策 T 只落地了 2 / 3 个服务，见 N35 |
| **N17** FAILED 卡两次重试后死角 | 中 | **FIXED** | `ProposalCard.vue`：L81 / L98 折叠条件改 `FAILED && !editing`；L102-104 显示 `CARD_ERROR_LABEL[card.error] \|\| card.error`；L105 "直接改" → `beginEdit()` → `editing = true` → 落入 `<template v-else>` 渲染编辑表单（L116-162，`actions` 因非 PROPOSED 不显示）；L108 "放弃" → `emit('reject')`；`retries` / `retry()` 删除。服务端 `service.py` L578-585 `reject_card` 放行 FAILED 并清 `error`；L595-597 `patch_card` 把 FAILED 改回 PROPOSED、`decided_at = None`；`test_commit_failed.py` L134-160 覆盖。`glossary.ts` L56-63 `CARD_ERROR_LABEL` 六项 | 决策 O 按第四版修订版采纳（原因 + 直接改 + 放弃，不限次数）。`CARD_ERROR_LABEL` 只有六个 code，`commit_card` 可能写入的其它 `DomainError.code`（如 `FINANCE_ENTRY_NOT_FOUND`、`FOLDER_NOT_FOUND`）会以原始 code 直出（L103 兜底），低 |
| **N18** 禁用成员无确认 | 中 | **FIXED（形态偏离决策 U）** | `UsersPage.vue` L107-109 `requestDisable` 只置 `pendingDisable`；L196 菜单项改调它；L208-225 `el-dialog`（360px，`close-on-click-modal=false`）显示 `membersCopy.disableConfirm`，"确定" → `toggle()`；`users-page.test.ts` L210 断言点菜单后 `update` 未被调用、L212 点"确定"后才调 | 决策 U 默认是 `el-popconfirm` 挂在菜单项上、"不用全屏 MessageBox"；实现是一个居中模态对话框（介于两者之间）。对话框里的"确定"是 `type="primary"`（L220），UsersPage 现在三个实心（页头 / 抽屉 / 对话框，互斥容器）。`toggle` 失败时 `errorMessage` 写在页面上、对话框不关，老板在遮罩下看不到错误（低） |
| **N19** `:deep()` 进全局 CSS | 低 | **FIXED** | `element.css` L46-47 改 `.plain-table .el-table__inner-wrapper::before, .plain-table .el-table__border-left-patch`；`rg ':deep' web/src/styles` 无输出 | — |
| **N20** 摘要含回执、水位越界 | 低 | **FIXED** | `service.py` L918 `older` 加 `role != SYSTEM`；L924-936 按行累计到 4000 字即停、`included` 记实际入摘要的行；L958 水位 `included[-1].seq` | L931 `if included and used + extra > 4000` 意味着第一条永远进（哪怕单条 > 4000 字），`payload` 仍可能超长；极低 |
| **N21** `recall` 逐词 `count` | 低 | **OPEN（按计划暂不做）** | L751-760 未动 | 第四版已列入"暂不做"，一致 |
| **N22** 旧数据 `seq` 无保证 | 低 | **FIXED** | 新迁移 `0011_backfill_seq.py` L18-29：`row_number() OVER (PARTITION BY conversation_id ORDER BY created_at, id)` 回填 + `setval` 归位。**实测**：三行 `seq = 49/50/51` 而 `created_at` 顺序为 `a<b<c`（原 `seq` 顺序 `c,a,b`），跑迁移 SQL 后 `a=1,b=2,c=3`；`ix_agent_messages_conversation_seq` 是非唯一索引（`pg_indexes` 实查），重编号不会撞唯一约束 | `downgrade` 为空（回填不可逆，可接受）。`setval` 用 `MAX(seq)` 会把全局序列**下调**到最长会话的长度，因索引非唯一、`seq` 只需会话内单调，安全 |
| **N23** 上传结果块两个"下载" | 低 | **FIXED** | `DrivePage.vue` 删 `currentResult / downloadUrl / currentStatusMessage / canCheckDownload / prepareDownload / .result`；`showCompleted()` 只 `clearTray()` + `loadFiles()`；行内 `FILE_STATE_LABEL`（"处理中 / 未通过"）；`drive-routing.test.ts` L131-171 断言完成后列表刷新且出现"处理中" | — |
| **N24** 表空态双份 | 低 | **FIXED** | `UsersPage.vue` L148、`FinancePage.vue` L244、`AuditPage.vue` L60、`DrivePage.vue` L275 全部 `:empty-text`；三页 `<EmptyLine>` 删除（`rg '<EmptyLine' web/src` 只剩知识 / 项目 / 项目详情三处非表格场景）；`auditPageCopy.empty` 新增 | — |
| **N25** 对话页空态布局 | 低 | **FIXED** | `ChatPage.vue` L313 `.thread__body` 包裹消息区；L316-318 空态一行 `chatCopy.empty`（"对霜月说一句……"）；L402-406 `.thread { display:grid; grid-template-rows: 1fr auto }`；L481 `.composer { margin-top: 0 }` | 空态一行居中靠 `margin: auto 0` + `.thread__body { align-content: start }`，推断可居中，未上浏览器 |
| **N26** 📎 emoji | 低 | **FIXED** | `MultipartUploader.vue` L105-117 16px 内联 SVG，`fill="currentColor"`；`rg '📎' web/src` 无输出 | — |

### 2.3 第四版 E2 / U1-b / G 清单逐条

| # | 条目 | 现状 | 备注 |
|---|---|---|---|
| E2-1 | N5-b 预检 + 删 `rollback` + 单测 | FIXED | `users/service.py` 一并处理；`files/service.py` 未动（不在卡片路径） |
| E2-2 | N3-b 滑动窗口 + 删 `"列一"` + 两条单测 | FIXED | 引入 N29（上限先截后算） |
| E2-3 | N20 | FIXED | — |
| E2-4 | `reject_card` 放行 FAILED；`patch_card` FAILED → PROPOSED | FIXED | — |
| E2-5 | N22 回填 | FIXED | 单独迁移 `0011`，而非追加进 `0009`（更稳妥） |
| U1-b-6 | N17 | FIXED | — |
| U1-b-7 | N18 | FIXED（`el-dialog`） | 见 N30 |
| U1-b-8 | N19 | FIXED | — |
| U1-b-9 | 网盘六件 | **6 / 6（U1-8 合计 7 / 7）** | 行尾 `···`（L342-375）、重命名行内（L282-291）、移动 `el-drawer`（L400-428）、删 `result`、"新建子目录"页头 `···` + 抽屉（L225-235 / L388-399）、`el-table` 四列（L272-378）、面包屑 `a / span`（L238-250）。引入 N27（删除无确认）、N34（两套上传入口仍在） |
| U1-b-10 | 空态 N24 / N25 | FIXED | — |
| U1-b-11 | N26 | FIXED | — |
| U1-b-12 | 决策 K 删重定向；决策 N 毛利口径 | FIXED | `router.ts` `/users`、`/owner`、`/owner/{projects,drive,users}` 五条全删（文件 293 行，`rg "path: '/owner"` 无输出）；`auth-app.test.ts` 相应改 `/projects` / `/members`。`FinancePage.vue` L83-95 毛利 = (公司收入 + 项目收入) − (公司成本 + 项目成本)；服务端 `finance/service.py` L229-230 / L263 STAFF 的 `income_cents` 早已置 `None`，"服务端置 null"无需再改 |
| U1-b-13 | 小处六件 | FIXED | `soul.ts` "霜月设置"；`projects.ts` `createSubmit: '保存'`、`undone: '取消完成'`（`ProjectDetailPage.vue` L172-176 按 `done_at` 切换）；`http.ts` 引 `errorCopy.unauthorized`；`errors.ts` L8-15 八个前缀 + `projectNameConflict`；`ProjectDetailPage.vue` L142-149 元信息 `filter(Boolean).join(' · ')`，无日期不再出现孤立破折号 |
| G-14 | 视觉：去平台后缀 + CI job + Linux 重抓 + 对话页有卡片基线 | **1 / 4** | `playwright.config.ts` L8-9 `snapshotPathTemplate` 去 `{platform}`；runbook L15 登记 spec。**未接 CI、未重抓、未加卡片基线**——六张 100% rename，见 N28 |
| G-15 | LLM：警示 + `--dry-run` + "看一下星野项目"断言 + runbook + 跑一次贴报告 | **3 / 5** | `llm_three_round.py` L3-5 docstring 警示、L123 / L152 `--dry-run` 不确认卡；runbook L83-103 一次性库流程（含 `--dry-run` 先行）。**未加**"看一下星野项目"召回断言（`RECALL` L26 仍只有"昨天那个合作项目"）；**未跑**，PR 无 JSON 报告 |

### 2.4 第四版第五章七条 `rg` 验收（对 `79e2dd3` 实跑）

| 命令 | 期望 | 第四版 | 本版 |
|---|---|---|---|
| `rg 'session.rollback' server/src/superboss/modules/projects/service.py` | 无 | 2 | **0** |
| `rg ':deep' web/src/styles` | 无 | 1 | **0** |
| `rg '📎' web/src` | 无 | 1 | **0** |
| `rg "redirect: '/members'\|path: '/owner" web/src/app/router.ts` | 无 | 5 | **0** |
| `rg '<EmptyLine' UsersPage / FinancePage / AuditPage` | 无 | 3 | **0** |
| `ls visual-pages.spec.ts-snapshots/` 无平台后缀 | 七张 | 六张 `win32` | **六张无后缀**（缺"对话页有卡片"一张，且六张是旧图） |
| `pytest tests/unit/agent -q` 含 N5-b / N3-b 新用例 | 全绿 | — | **全绿**（332 全量） |

第二版 U0 十条重跑：写死色值 1（同一处打印样式）、`el-alert / el-card / ElMessageBox` 0、枚举 / `_id` 直出 0、`type="primary"` 每文件 > 1 仍 3 文件（财务 / 项目详情 / 成员，成员从 2 涨到 3，均在互斥容器内）、`暂时无法 | 请稍后重试`（copy 之外）0、原生控件 0、`isRecord` 1、旧网盘文案 0、`is_test | project_ids` 0。**`api/*.ts` 里第四版剩的 8 个字面量前缀本轮清零，前端字面量指标彻底达标。**

### 2.5 决策点 K / N / O / R–U 执行情况

| 编号 | 第四版默认 | 本版 |
|---|---|---|
| K 删 `/users` 与 `/owner/*` 重定向 | 下一轮删 | **已删** |
| N 财务"毛利"口径 | 改为含项目 | **已改**（服务端 STAFF 置 `null` 早已存在） |
| O FAILED 卡去向 | 原因 + 直接改（回 PROPOSED）+ 放弃，不限次数 | **已采纳，完全按修订版** |
| R 视觉回归放 CI，去平台后缀，删 `win32` 基线 | 放 CI | **半做**：去后缀 ✓；CI ✗；"删 `win32` 基线"被理解成"改名"，旧图原样保留 |
| S `llm_three_round.py` 禁对生产库 | 加警示 + `--dry-run`，runbook 只写一次性库 | **已采纳** |
| T 服务层不 `rollback()` | 只 `raise` | **项目 / 用户已采纳；文件服务未动**（非卡片路径） |
| U 禁用二次确认形态 | `el-popconfirm` 挂菜单项 | **改用 `el-dialog`**（非 MessageBox，但也非 popconfirm） |

---

## 三、新问题 / 回归（按风险排序）

### 3.1 前端

**N27（已验证，中，回归）网盘"删除"一点即删、无确认。** `DrivePage.vue` L366-371 `<el-dropdown-item @click="removeFile(row)">`，`removeFile` L167-174 直接 `filesApi.remove(file.id)`。`67e843e` 的删除按钮包在 `<el-popconfirm :title="driveCopy.removeConfirm">` 里，这次改成菜单时把 popconfirm 一起删了；`rg 'el-popconfirm' web/src` 现在全项目为零。服务端 `files/service.py` L448-457 `delete_file` 是 `session.delete(file)` + `storage.delete_object`，**不可恢复**。`driveCopy.removeConfirm`（"删除后不可恢复。"）成死文案（0 引用）；`drive-routing.test.ts` L248 用例名叫 "…rename, move, or delete files"，但全文没有一处点"删除"（L392 只是 STAFF 看不到菜单项的负向断言）。这和第四版 N18 是同一个模式，且比 N18 后果重（禁用可恢复，删文件不可）。**修法**：与成员页同款——菜单项只置 `pendingRemove`，`el-dialog`（或按决策 U 的 popconfirm）里 "确定" 才调 `removeFile`；复用 `driveCopy.removeConfirm`；测试加"点菜单 → `remove` 未调用 → 点确定 → 调用"三步。

**N30（已验证，低）N18 的修法与决策 U 不一致，两页确认形态应统一。** 成员页用 `el-dialog`（L208-225），若 N27 也用 dialog 则两页一致；若老板坚持 popconfirm，两页一起改。附带：对话框内"确定"为 `type="primary"`，与"一屏一个实心"戒律的关系需要老板确认（遮罩下页头的实心被盖住，可解释为满足）。

**N31（已验证，低）网盘测试覆盖回退。** 第四版有五个用例覆盖"下载探测 → `FileDownloadUnavailableError(INFECTED/FAILED)` → 终态文案"和"QUARANTINED 重试直至可下载"；本次删掉后 `downloadFile` L128-142 的 `FileDownloadUnavailableError` 分支（"检测到风险…" / "扫描失败…" / "文件仍在扫描中。"）**没有任何行为测试**（`rg FileDownloadUnavailableError web/tests/drive-routing.test.ts` 只剩 mock 类定义）。删除也无测试（见 N27）。web 用例数 131 → 129。

**N34（已验证，低，N9 残留）网盘一页两套上传入口。** 表格下方 `MultipartUploader`（L379-385，非 `compact`，即整块虚线 `el-upload` 拖放框，内文"上传"）与整页 `DropZone` 并存；前者走组件内进度文字，后者走 `UploadTray`。第四版 N9 残留原文照旧。第二版 3.5 的网盘目标是"页头一个'上传'文字动作 + 整页拖放"，虚线框应删。

**N36（推断，低）面包屑最后一级也是链接。** `DrivePage.vue` L238-250 每一级都渲染 `<a href="#">`，当前目录点了等于刷新自己；应把最后一级渲染成 `<span>`。

### 3.2 服务端

**N29（实测，低→中）召回候选上限在计命中之前截断，长句靠后关键词丢失。** `service.py` L136-141：候选按长度降序去重，到 8 个即停，之后 L751-762 才按命中数排序。整句 token 永远占第一格（L129-130），剩 7 格给前七个两字片。实测 `"把上季度的复盘纪要发给星野的对接人"` 的候选无"星野"，`recall` 只回"老板偏好项目按季度复盘"（错的那条）；`"帮我看看星野合作项目这个月的交付节点怎么样了"` 的候选无"交付 / 节点"。第四版 N3-b 原文"上限 8 保留"指的是**排序后**取 8。**修法**：`recall_needles` 不截断（或上限放宽到 24），`recall` 里对全部候选计命中后再取前 8；整句 token 只在长度 ≤ 8 时才作为候选。新单测：上面两句必须命中"星野合作"。

**N35（已验证，低）`files/service.py` 五处 `session.rollback()`。** L205 / L229 / L239 / L256 / L341，全在上传 provisioning 与分片完成路径，抛 `FileProvisioningPendingError / FileCompletionPendingError / ConflictError`。不在卡片路径，无 N5-b 那种 500；但决策 T 说"服务层不回滚"，这三分之一没做。可与下一轮文件模块改动一起处理。

**N32（已验证，极低）`maybe_summarize` 首条不截断。** L931 `if included and used + extra > 4000` 让第一条无论多长都进，单条超 4000 字的消息会整条送入 LLM。加 `line[:4000]` 即可。

**N33（已验证，极低）`ruff format --check` 29 → 31 文件。** CI 不查，但持续变差；建议一次性 `ruff format` 并把 `--check` 加进 CI（另开 PR，只改格式）。

### 3.3 门

**N28（已验证，中）六张视觉基线是 100% 改名，与代码脱节。** `git diff 043a7ca 79e2dd3 -M --summary` 六行全是 `rename … (100%)`；`git show --stat` 六张 `Bin` 无字节变化。逐张查看：`drive.png` 仍是"左栏三个居中文字按钮 + 常驻新建子目录表单 + 四文字按钮"的旧网盘；`members.png` 仍是"操作失败。+ 暂无数据 + 还没有成员"的出错页；`chat.png` 输入框仍悬在页面上部无空态提示。而本提交改了：网盘整页、对话页空态与网格、成员 / 财务 / 审计空态文案位置、项目详情元信息行——**除 `login.png` 外五张必与当前代码 diff 超过 `maxDiffPixelRatio 0.005`**。加上 `ci.yml` 未动，这扇门现在是"任何机器上跑都红"的状态。"删 `win32` 基线"（决策 R）的本意是重抓，不是改名。**修法**：见第五章 G。

**LLM 门（已验证）**：`--dry-run` 与警示到位，runbook 到位；未加"看一下星野项目"断言；未跑。仍是"手工验收的正确起点"。

---

## 四、UI 观感复核

依据：源码 + 第四版六张基线（本提交未重抓，**新网盘页 / 新空态没有截图**，以下对新页面的判断均为**推断**）。

### 4.1 本轮进步（源码层面）

- **网盘页**：结构上已到第二版 3.5 的形态——左栏左对齐文字目录（`.folder-link` L445-452，`aside` L439-444 `justify-items: start`），右侧 `el-table` 四列 + 行尾 `···`，面包屑 `13px` 灰字 `a / span`，页头右侧 `···`（新建子目录），重命名行内、移动与新建子目录都在 400px 抽屉里，删了结果块。`.plain-table` 无边框规则生效后表格四周不再有 Element 默认边框。**这是从第二版以来网盘页第一次真的变样。**
- **成员 / 财务 / 审计空态**：表内一行 `:empty-text`，表下不再多一行。
- **对话页空态**：右列两行网格，输入框贴底，空会话居中一行"对霜月说一句……"。
- **卡片失败态**：一行红底灰字 "入库失败 · 新建项目 星野合作 · 同名项目已存在  直接改  放弃"，比"重试 / 重试 / 直接改（无效）"诚实。
- **📎**：16px `currentColor` SVG，随文字色。
- **文案**：SOUL → 霜月设置；"保存"统一；"取消完成"；毛利含项目；`api/*.ts` 前缀归零。

### 4.2 仍显廉价 / 需确认的地方

1. **网盘页两套上传入口**（N34）：表格下面那块虚线 `el-upload` 框（"上传"二字居中）仍在，与整页拖放重复；应删框、页头加"上传"文字动作触发文件选择。
2. **网盘"删除"无确认**（N27）：不是观感问题，但是高端产品最不该有的一处。
3. **成员 / 网盘确认形态**：`el-dialog` 居中模态 vs 决策 U 的 popconfirm；两页需一致。
4. **视觉基线是旧图**（N28）：本轮所有 UI 改动没有一张截图证据，第四版 4.2 的"网盘最扎眼"只能凭源码判定为"应已解决"。
5. **小处**：面包屑最后一级可点（N36）；`CARD_ERROR_LABEL` 覆盖不全时直出英文 code；`CARD_STATUS_LABEL`、`driveCopy.removeConfirm` 两组死文案。

### 4.3 结论

按第二版十条戒律：机械指标全部达标（第二版 U0 十条 + 第三版 / 第四版追加项全部清零，只剩一处可接受的打印色）。**观感上，六页里最后一页（网盘）在源码层面进入了规格形态，三处空态修好，结构性问题只剩"两套上传入口"一件；但本轮零截图，下一轮必须先重抓再谈观感。**

---

## 五、下一步迭代（短、可执行）

每条一个 PR；G-1 完成前不再做任何 UI 改动（否则又是没有基线的改动）。

**E3 — 服务端补漏（几十行）**

1. **N29**：`recall_needles` 去掉 8 上限（或放宽到 24）、整句 token 仅在 `len ≤ 8` 时入候选；`recall` 计完命中再取前 8。单测：`"把上季度的复盘纪要发给星野的对接人"`、`"帮我看看星野合作项目这个月的交付节点怎么样了"` 必须命中"星野合作"。
2. **N32**：`maybe_summarize` 首条 `line[:4000]`。
3. **N35**：`files/service.py` 五处 `rollback()` 改为只 `raise`（与决策 T 对齐），跑 `tests/api/files` 确认 provisioning 重试语义不变。
4. **N33**：单独一个 PR `ruff format`，并把 `ruff format --check` 加进 `ci.yml` 的 server job。

**U1-c — 前端两处确认 + 网盘收尾**

5. **N27**：网盘"删除"走 `pendingRemove` + 确认（形态按决策 W）；复用 `driveCopy.removeConfirm`；`drive-routing.test.ts` 加三步断言。
6. **N30**：成员"禁用"与网盘"删除"统一形态（决策 W）。
7. **N31**：补 `downloadFile` 的 `FileDownloadUnavailableError(INFECTED / FAILED / 其它)` 三条行为测试。
8. **N34**：删表格下方虚线 `el-upload` 框；页头加"上传"文字动作（`compact` 模式的 `MultipartUploader` 或隐藏 `<input type=file>`），保留整页 `DropZone` + `UploadTray`。
9. **N36**：面包屑最后一级 `<span>`。
10. 死文案清理：`CARD_STATUS_LABEL`（若无计划使用）、`driveCopy.removeConfirm`（N27 修完即有引用）；`CARD_ERROR_LABEL` 补 `FINANCE_ENTRY_NOT_FOUND / FOLDER_NOT_FOUND / VALIDATION_ERROR`。

**G — 把两扇门真装上**

11. **视觉**：在 `ci.yml` 加 `e2e-visual` job（Postgres service + `alembic upgrade head` + `seed_acceptance` + `uvicorn` + `vite build` 静态服务 + `npx playwright test visual-pages.spec.ts`，`ubuntu-latest` 固定字体包）；**在该容器里重抓六张基线并提交**（成员页必须是正常空态：无红字、只一行"还没有成员"）；补第七张"对话页有一张待确认卡 + 一条回执"（种子会话或 `ChatPage` mock）。在此之前，把 `visual-pages.spec.ts` 标 `test.fixme` 并在 runbook 注明"基线待重抓"，避免任何人拿旧图对照。
12. **LLM**：`llm_three_round.py` 加 `"看一下星野项目"` 第二条召回断言；E3-1 合并后对一次性库跑 `--dry-run` 再跑正式，JSON 报告贴 PR。
13. **CI 触发**：`ci.yml` 的 `push.branches` 加 `feature/**`（或改为所有分支），否则特性分支直推永远没有 CI 记录（第三、四、五版连续三次 `gh run list` 为空）。

**验收（命令输出为准）**

```bash
cd server && pytest tests/unit/agent -q                                     # 全绿，含 N29 两句新用例
rg -n 'session.rollback\(\)' server/src/superboss/modules                   # 无输出（决策 T 全落地）
rg -n 'el-popconfirm|el-dialog' web/src/pages/owner/DrivePage.vue web/src/pages/owner/UsersPage.vue  # 两页同一形态
rg -n 'removeConfirm' web/src --glob '!web/src/copy/**'                     # ≥ 1
rg -n ':drag=' web/src/pages/owner/DrivePage.vue                            # 无输出（虚线框已删）
git log -1 --format=%h -- tests/e2e/specs/visual-pages.spec.ts-snapshots/   # 不再是 79e2dd3（已重抓）
rg -n 'e2e-visual|playwright' .github/workflows/ci.yml                      # 有输出
gh run list --branch feature/p0-drop-devices-imports --limit 1              # 有记录
```

---

## 六、暂不做 / 决策点

**暂不做**（沿用二至四版，全部仍成立）：深色模式、图表、插图空态、导航图标、Webfont、国际化、pgvector、员工侧霜月、目录级 ACL、外部 API 令牌、卡片自动入库、N21（`recall` 合并成一条 SQL——N29 修完后再看是否需要）、记忆抽取按会话 owner 构造 `Actor`。

**本轮新增暂不做：**

- `CARD_ERROR_LABEL` 改为服务端下发中文 `error_message`——前端映射表够用，只补三个 code。
- `0011` 的 `downgrade` 写回填逆操作——回填不可逆，空 `downgrade` 可接受。
- 成员页对话框内错误提示——等确认形态（决策 W）定了再一并处理。

**待老板拍板**（编号接续第四版 R–U）：

| 编号 | 问题 | 本文默认 |
|---|---|---|
| R | 视觉回归放 CI | 第四版说放、本轮只去了后缀；**下一轮必须接 CI 并在容器里重抓**，在此之前 spec 标 `fixme` |
| T | 服务层不 `rollback()` | 项目 / 用户已改；**文件服务五处一起改**（E3-3） |
| U | 二次确认形态 | 实现用了 `el-dialog`；**并入 W** |
| V（新） | 召回候选上限：不截断 vs 放宽到 24 | **不截断、计完命中再取前 8**；召回句最长 80 字，候选最多约 80 个 `count`，N21 若因此变慢再合并 SQL |
| W（新） | 成员"禁用"与网盘"删除"的确认形态 | **两页统一用现有 `el-dialog`（360px、`close-on-click-modal=false`）**，不再引入 popconfirm；理由：删文件不可恢复，居中模态比气泡更难误点 |
| X（新） | 六张旧基线的去向 | **立即从仓库删除**（`git rm`），由 CI 容器重抓后再提交；不保留"改了名的旧图" |
| Y（新） | `ci.yml` 是否对 `feature/**` 直推也跑 | **跑**：连续三版 tip 都没有 CI 记录，本机绿不等于 CI 绿 |
| Z（新） | 网盘虚线上传框去留 | **删**，页头"上传"文字动作 + 整页拖放 |
