# SuperBoss 审查与迭代方案：三层产品落地复核与 UI 体系（Fable 5.1，第二版）

日期：2026-09-06。基于 **`feature/p0-drop-devices-imports@f2ad4fe`**（提交 "feat: drop pairing and land three-role SuperBoss with 霜月"）的逐文件核查。

> **第三版已发布**：[`审查与迭代方案-Fable51-UI-第三版-d43f91b.md`](./审查与迭代方案-Fable51-UI-第三版-d43f91b.md) 基于 `d43f91b`（"feat: land agent reliability fixes and Fable UI system"）复核了本文 R1–R11、E0 与 C1–C17 的落地情况，并记录新引入的回归。本文第二章的"现状"描述与第五章的 `rg` 统计已过期；第三章 UI 规格（戒律、token、术语表、页面目标）仍有效。

**本文取代 2026-09-06 早先基于 `master@2b4ebd2` 的第一版。** 第一版写作时 `master` 仍是瘦身后的旧代码（`64ddffa` + 两份文档），因此它关于"财务 / 霜月 / 知识库 / 三层路由不存在、员工登录即进 `/forbidden` 死循环、需要新建 8 个页面"的**功能有无结论已全部过期，不再作为依据**。本分支已经把三层方案 P0 的全部步骤和一部分 P1 落地；本文回答的是新问题：**落地到什么程度、哪里与方案不一致、UI 质量差在哪里、接下来按什么顺序做。** 第一版中仍然成立的 UI 体系（token、戒律、术语表）在第三章保留并按当前代码修订。

产品方向、权限矩阵、数据模型仍以三层方案为准：本分支上是仓库根目录的 [`SuperBoss-迭代方案-三层账号与霜月.md`](../SuperBoss-迭代方案-三层账号与霜月.md)，`master` 上是 `docs/迭代方案-三层账号与霜月.md`，两份内容逐字相同（已 `diff` 验证），合并时需去重为一份。

凡标注"（已验证）"的结论均附文件路径与行号；标注"（推断）"的是从源码读出、但未在真实 LLM 或浏览器中实测的判断。

---

## 一、摘要

给老板的一页纸：

1. **方向对了，骨架全在。** 设备配对、Kimi 连接器、导入任务已彻底删除（`server/src`、`web/src` 中 `device|pairing|import_job|connector` 零命中，已验证）。MANAGER 角色、三层落点（OWNER→`/chat`，其余→`/projects`）、项目阶段与里程碑、网盘目录与可见性、财务条目与调整、知识库、审计只读页、霜月的 SOUL / 记忆 / 卡片 / 流式对话——三层方案 P0 八个步骤**全部有代码、有迁移、有测试**；P1 中的 SSE 流式、知识入库卡片、CSV 导出、成本预警、里程碑提醒也被提前做了。服务端 326 个单元+接口测试全绿，前端 131 个 vitest 全绿、类型检查通过（本地实测，见 2.1）。

2. **但有三处会让老板第一次真用就翻车。** （a）多轮对话历史回放时丢掉了 `tool_call_id` 与 `tool_calls`，任何用过工具的会话从第二轮起大概率被 OpenAI 兼容接口以 400 拒绝，而客户端把一切 HTTP 错误都折叠为"霜月暂时离线"，老板看不出原因（推断，需实测，见 2.4 R1）。（b）对话里上传的附件默认落在全员可见的 `项目` 根目录，老板私密文档一经扫描通过即对所有员工可见（已验证，R3）。（c）会话滚动摘要从未生成，长对话超过 16 条后早先内容对霜月不可见（已验证，R4）。这三条是**工程 P0**，先于任何 UI 工作。

3. **UI 是"功能验证台"，不是老板的工作台。** 第一版指出的所有廉价感来源不仅一个没修，还随着 8 个新页面**等比例复制**：33 处写死的 Element Plus 默认色（仍是第一版点名的那 9 个色值，只是出现次数翻了三倍多）、15 个 `el-alert`、18 个 `el-card`、15 处原生 `<select>/<input>/<button>/<progress>` 与 Element 混排、3 处 `globalThis.confirm`、全仓仍无一处 `font-family` 或设计变量、`index.html` 仍是 `lang="en"`。角色枚举 `OWNER/MANAGER/STAFF` 作为页面眉题印在财务/网盘/项目页顶部；网盘把 `CLEAN/SCANNING` 直出、把 `file_id` UUID 印在"本次上传"卡片里；记忆页用 `PREFERENCE/DAILY_DIGEST` 做小节标题；审计页直出 `finance.entry.create` 与 `actor_kind`。OWNER 顶栏有 **9** 个导航项。

4. **霜月卡片——产品的脸——目前是一张 JSON 表单。** 待确认卡片把 payload 的每个字段渲染为一行文本输入框：`项目` 一栏是 UUID，`金额（分）` 要老板自己乘 100，`可见范围` 显示 `MANAGEMENT`。"让霜月修改"的服务端接口（`/cards/{id}/revise`）与前端 API 都已存在，但页面没有接，只有"保存修改"的手改。已入库卡片显示为 `已入库 · finance_entry`；已放弃/已被修改/入库失败的卡片没有任何状态标注，与待确认卡片长得一样。这一章（3.5 对话页）是本文最重要的可执行规格。

5. **工程侧的小债一个没还，且加倍了。** 第一版列出的 S1–S4、F2–F9、T1 全部仍在：`core/errors.py` 默认错误码仍是 `PROJECT_*`；`get_session` 现在在 **7** 个 router 里各有一份；`project.list / project.read` 每次 GET 仍写一行 SUCCESS 审计（老板现在有审计页了，噩梦从此可见）；`GET /auth/me` 仍不返回 `id`；前端 `isRecord` 校验器从 5 份复制到 **8** 份；前端测试里含中文字面量的行**约 240**（第一版按 `getByText` 等定位器统计为 28，口径不同，趋势一致）；`is_test`"验收测试项目"勾选仍在项目页。另有一个 CI 事实：本分支 `npm run lint` 因 Prettier 未格式化两个文件而失败，**web job 在 CI 上会红**（已验证）。

6. **顺序建议：先修三处翻车（E0，纯服务端/对话页逻辑，不碰视觉），再立 UI 体系并以对话页为样板（U0），再逐页收敛（U1），最后做 P1 尾巴。** 明确暂不做的清单见第六章，新增决策点 J–M 见第七章。

---

## 二、核查证据

### 2.1 核查方式与实测结果

- 逐文件阅读 `web/src` 全部 31 个文件（6159 行）、`server/src` 全部 63 个 Python 文件（8109 行）、8 个迁移、`ci.yml`、`.env.example`、README、runbook；对 `docs/` 与 `master` 上的两份文档做了 `diff`。
- 本机实测（非 CI）：
  - `server`：`ruff check src tests` 通过；在本机 PostgreSQL 16 上 `pytest tests/unit tests/api` **326 passed**（CI 同一命令集；无 Docker 时 77 个依赖 testcontainers 的用例会 ERROR，属环境问题而非代码问题）。
  - `web`：`npm ci` 成功；`vue-tsc --noEmit` 通过；`vitest --run` **16 文件 131 用例全过**；`npm run lint` **失败**——`prettier --check` 报 `src/api/files.ts`、`tests/http.test.ts` 未格式化。本分支尚无 PR，CI 未跑过（`gh run list` 为空），合并前必须 `prettier --write` 这两个文件。
  - 未实测：真实 LLM 端点、浏览器观感、Playwright e2e（需要本地 HTTPS 栈）。凡涉及这三者的结论均标"推断"。
- 体量：服务端 56 条路由、17 张表（`agent_cards agent_conversations agent_memories agent_messages agent_soul_versions audit_logs files finance_adjustments finance_entries folders knowledge_docs knowledge_points project_members project_milestones projects sessions users`）；测试行数 server 7402 / web 3632；e2e 3 个 spec。

### 2.2 三层方案 P0/P1 落地对照

| 方案条目 | 状态 | 证据（已验证） | 偏差 / 缺口 |
|---|---|---|---|
| P0-1 删除 D1–D6 | ✅ | 迁移 `0002_drop_devices_and_imports.py`；`integrations/` 整目录、`connector-release.yml`、`ci.yml` connector job、e2e connector fixture 均已删除；`.gitignore`、README、`llm-setup.md` 新增 | 遗留文字：`docs/runbooks/m1-owner-acceptance.md` L135-136（Device ID / Import job ID）、L142-143（Connector pair、Device revocation）仍在验收表里 |
| P0-2 角色 | ✅ | `users/models.py` Role 三值 + 迁移 `0003`；`core/actors.py` L71-81 `require_role`，L84-86 `require_owner` 统一 `FORBIDDEN`；`Actor` 删掉 `kind/scopes`（L23-27）；`users/schemas.py` L19-24 `StaffCreate.role` 仅允许 MANAGER/STAFF；前端 `router.ts` L39 三值 roles、L66-69 `homePath`；`staff-denial.spec.ts` 已改为三层越权矩阵 | `Actor.project_ids` 仍每次请求查库（L54-64）但 `require_project_access`（L89-94）已无人调用；`project_members` 表、`PUT /owner/users/{id}/projects`（`users/router.py` L68）与用户页项目勾选（`UsersPage.vue` L220-240）仍保留——方案 4.3 说参与人应在项目详情维护 |
| P0-3 项目 v2 | ✅ | 迁移 `0004`；`Project.description/stage/progress_percent/starts_on/due_on`，`project_milestones`；`PATCH /projects/{id}`、`PUT /projects/{id}/milestones`（`projects/router.py` L87-114）；进度 = 已完成里程碑比（`service.py` L36-41）；列表对全员可见（L118-124） | `is_test` 字段与勾选仍在（`models.py` L63、`ProjectsPage.vue` L91）；无"参与人"展示 |
| P0-4 网盘 v1 | ✅ | 迁移 `0005`；三个根目录 `公司/项目/老板私有`（`files/service.py` L28-32）；`folder_is_visible` L35-42；子目录继承可见性 L411-422；`GET/POST /folders`，`GET /files?folder_id=`、`PATCH`、`DELETE`（`files/router.py` L214-270） | 目录无重命名/删除/改可见性接口；`files.project_id` 仍存在但已可空（`models.py` L111） |
| P0-5 财务 v1 | ✅ | 迁移 `0006`；`default_visibility` L55-58、`entry_is_visible` L61-77 与方案 6.1 矩阵一致；`summary` 对 STAFF 不含公司与收入（L221-274）；调整记录不覆盖原值（`apply_adjustments` L80-107）；接口测试 `test_finance.py` 4 例覆盖三角色 | 方案说"三个角色各一版页面"，实际同一组件按角色隐藏（正确做法）；`/finance/alerts`（L276-293）已实现但**前端未接** |
| P0-6 霜月 v1 | ✅（有缺口） | 迁移 `0007`（含 `pg_trgm` 扩展与 `agent_memories.search tsvector`，L18、L101）；`core/llm.py` OpenAI 兼容客户端含 tools 与 stream；`agent/soul.py` 系统约束 + 默认 SOUL；`tools.py` 6 只读 + 8 提案工具（L48-178）；`confirm/patch/revise/reject`（`router.py` L143-172）；`commit_card` 经领域服务写库（`cards.py` L98-212）；记忆抽取 Celery 任务与每日纪要（`tasks.py`）；`/chat /soul /memory` 页 | 见 2.4 R1、R3、R4、R5–R8：历史回放格式、附件落点、无滚动摘要、召回未用 tsvector、去重为精确匹配、纪要为拼接非摘要、FAILED 状态不落库、`archive` 与 `revise` 前端未接 |
| P0-7 审计只读 | ✅ | `GET /audit?limit=&action=`（`audit/router.py`）；`AuditPage.vue`；卡片确认、SOUL 写/激活、财务写、文件删除均写审计 | `AuditRead` 只有 `actor_id` UUID 无姓名（`schemas.py` L121-134）；页面直出原始 `action`；`project.list/read` 噪音仍在写 |
| P0-8 文档 | ✅ | README 重写为三层口径；`llm-setup.md` 新增；`local-auth-setup.md` 含 MANAGER | 三层方案放在仓库根目录而非 `docs/`（见文首） |
| P1 提前落地 | 部分 | SSE 流式（`router.py` L109-140、`agent.ts` L266-342）；`knowledge_ingest` 卡片与 `knowledge` 模块（迁移 `0008`）；CSV 导出（`finance/router.py` L51-66）；成本预警（服务端）；里程碑到期提醒（`projects/service.py` L128+，`AppLayout.vue` L20-35） | 知识库无编辑/下架 UI（服务端 `PATCH` 支持 `status`）、正文不渲染 Markdown；预警无 UI；对话内附件预览、财务项目×月透视、卡片内联编辑的"字段级"体验未做 |
| P2 提前落地 | 部分 | `SUPERBOSS_SCAN_ENABLED` 开关（`.env.example`，README） | `is_test/seed_acceptance/tests/compose` 未清理 |

### 2.3 第一版遗留问题的现状

| 编号（第一版） | 现状 | 证据 |
|---|---|---|
| F1 `/forbidden` 死循环 | **已修** | `ForbiddenPage.vue` L5 现链到 `/projects`；`router.ts` L318 从 forbidden 回 `homePath` |
| F2 路由同步引入、无 bundle 隔离 | 仍在 | `router.ts` L14-28 全部静态 import，包括 OWNER 专属的 Chat/Soul/Memory/Audit/Users |
| F3 登录页 `calc(100vh - 64px)` | 仍在 | `LoginPage.vue` L90；`PasswordChangePage.vue` L105 为 `100vh` |
| F4 项目列表本地合并 | 仍在 | `ProjectsPage.vue` L34-36 |
| F5 `\uXXXX` 写中文 | 部分 | `MultipartUploader.vue` L73 `'\u626b\u63cf\u4e2d'` |
| F6 日期格式不统一 | 仍在 | 全仓仅 `UsersPage.vue` L36 一处 `Intl.DateTimeFormat`，其余页面直出 ISO 字串（`AuditPage` `created_at`、财务 `occurred_on`、项目 `due_on`） |
| F7 Element 无 `locale`、全量引入 | 仍在 | `main.ts` L2-3；`App.vue` `el-config-provider` 无 `locale`；`AuditPage.vue` L41 已经用了 `el-table`，空态会显示英文 "No Data" |
| F8 校验器复制 | 加重 | `function isRecord` 8 份：`api/{agent,audit,auth,files,finance,knowledge,projects}.ts` + `uploads/multipart.ts` |
| F9 错误正文带 36 位 request_id | 仍在 | `http.ts` L85-97 `formatRequestError` |
| S1 `PROJECT_*` 默认错误码 | 仍在 | `core/errors.py` L38-53；`ForbiddenError()` 无参调用 3 处 |
| S2 `get_session` 复制 | 加重 | 7 个 router 各一份 |
| S3 GET 写 SUCCESS 审计 | 仍在 | `projects/router.py` L60、L83 |
| S4 `AuthUserRead` 无 `id` | 仍在 | `auth/schemas.py` L46-52 |
| T1 测试写中文原文 | 加重 | `web/tests` 含中文字面量的行约 240（第一版按定位器统计 28；13 个测试文件全部涉及） |
| T2 e2e 依赖 `is_test` | 已解除 | `staff-denial.spec.ts` 改为按 `老板私有` 目录名找 fixture；但 `is_test` 本身仍在 |
| C1–C14 廉价感来源 | 全部仍在 | 见 3.6 更新表 |

### 2.4 本轮新发现（按风险排序）

**R1（推断，高置信度）多轮对话历史回放格式不合法 → 用过工具后的会话从第二轮起"离线"。** `agent/service.py` `_window`（L372-390）把历史消息重建为 `{"role":"tool","content":...}`（**无 `tool_call_id`**）和 `{"role":"assistant","content":...}`（**丢掉 `tool_calls`**，虽然 L333-340 已把它们存进 `AgentMessage.tool_calls`）。OpenAI 及主流兼容接口（DeepSeek、Moonshot）要求 `tool` 消息必须紧跟带 `tool_calls` 的 assistant 消息，否则返回 400。`core/llm.py` L161-162 / L220-221 把**任何**异常（含 4xx）都转成 `LLMUnavailable`，`service.py` L355-356 再把它变成"霜月暂时离线"，且全程无日志。结论：只要一轮里调用了工具（几乎每轮都会），下一轮就会失败并显示离线。现有测试用假 LLM，测不出。**修法**：`_window` 按存储的 `tool_calls` 原样回放 assistant 消息，tool 消息带 `tool_call_id`（需在 `AgentMessage` 增一列或存进 `tool_calls` JSON）；`LLMUnavailable` 之外区分 `LLMRequestError`，并 `logging.warning` 状态码与响应体前 200 字。

**R2（已验证）流式失败回退可能重复发送。** `ChatPage.vue` L182-194：`stream()` 抛任何异常都回退到 `send()`。服务端 `chat_stream` 在 `_begin_turn` 里已写入用户消息并在流结束时 commit（`router.py` L126-138）；若客户端在收到 `done` 后解析失败（`agent.ts` L323 `parseTurn` 抛 `AgentContractError`）或网络在 commit 后断开，回退会**再发一次同一句话**，产生两条用户消息、两轮 LLM 调用、两套卡片。修法：回退前先 `listMessages` 比对最后一条是否已是本句；或服务端幂等键（`Idempotency-Key` 已在文件上传用过）。

**R3（已验证）对话附件默认进全员可见目录。** `ChatPage.vue` L256-262 取名为 `项目` 的根目录（可见性 `ALL`）作为上传落点。老板在对话里上传"合同""工资表"，扫描 CLEAN 后所有 STAFF 在网盘立即可见可下载。修法：默认 `老板私有`，由 `file_move` 卡片决定去向（这本来就是方案 5.5 的设计）。

**R4（已验证）会话滚动摘要从未生成。** `AgentConversation.summary` 全仓无写入（`rg '\.summary\s*='` 零命中），`_window` 只取最近 16 条（`_WINDOW = 16`，L50），其中还包括空内容的工具轮次。方案 5.3 L1 层"超窗口部分由 LLM 生成滚动摘要写回"没有实现。

**R5（已验证）记忆召回没有用已建好的全文索引。** 迁移 `0007` 建了 `pg_trgm` 扩展和 `search tsvector` 生成列，但 `recall()`（L578-592）用 `content ILIKE '%整句用户消息%'`——只有用户消息**整体**是某条记忆的子串才命中，实际召回率接近零；方案的两天场景（"昨天那个合作项目"）不会命中"星野合作"。去重同样是精确相等（`extract_memories` L657）而非 trgm > 0.8。

**R6（已验证）卡片 FAILED 状态不落库。** `cards.py` L63-76 在领域服务抛错时把卡片置 `FAILED` 再 `raise`；`agent/router.py` `get_session` L35-45 捕获异常后 `rollback()`，状态改动随之丢弃。结果：确认失败的卡片仍显示"待确认"，老板只看到一条错误提示，再点一次再失败。修法：在独立事务写 FAILED，或改为返回 200 + `status=FAILED`。

**R7（已验证）对话线程里会出现空的"霜月"条目，而"已入库"回执被藏起来。** `_run_turn` 每一轮工具调用都存一条 `role=ASSISTANT, content=""` 的消息（L333-340），`list_messages` 只按 `role IN (USER, ASSISTANT)` 过滤（L131-136），因此前端每次工具调用都会渲染一个只有"霜月"两字的空条目；而 `confirm_card` 写的 `role=SYSTEM` "已入库：finance_entry"（L405-411）被过滤掉，老板在对话里看不到入库回执。

**R8（已验证）服务端有、前端没接的能力。** `POST /agent/conversations/{id}/archive`（`router.py` L79-83）——`agent.ts` 无对应方法，会话无法归档；`POST /cards/{id}/revise`——`agent.ts` L349 已封装，`ChatPage.vue` 未调用；`GET /finance/alerts`——无 UI；`PATCH /knowledge/{id}` 的 `status` 与 `tags`——页面只有"发布"，无"下架"、无编辑；`token_usage` 已按消息记录，审计页/任何页都不显示月度用量（方案 5.6）。

**R9（已验证）每日纪要是拼接不是纪要。** `tasks.py` L87-97 把过去 24 小时每条消息截 80 字用分号连起来，最多 1800 字，`importance=4`，随后每轮都会被 `recall()` 的 "最近 7 天 DAILY_DIGEST" 规则注入提示词——7 天 × 1800 字 ≈ 每轮固定多花约 8k 中文 token，且内容是流水账。调度用 `schedule: 86400.0`（`celery_app.py` L90-94）即"worker 启动后每 24 小时"，不是每天固定时刻。

**R10（已验证）CI 会红。** `prettier --check` 失败于 `web/src/api/files.ts`、`web/tests/http.test.ts`。

**R11（已验证）里程碑提醒以 `el-alert` 堆叠在每一页顶部。** `AppLayout.vue` L61-70 对每条到期提醒渲染一个带图标的黄色横幅，三个里程碑同一周到期时，所有角色打开任何页面都先看到三条横幅。

---

## 三、产品 UI 梳理

### 3.1 目标观感与十条戒律

**一句话**：像一份排版讲究的内部刊物，而不是一块运营看板。纸感底色、单一强调色、大字标题、细线分隔、大量留白；数据用字号与对齐表达层级，而不是用颜色块和图标。

**十条戒律**（违反任一条即不合并）：

1. 一屏最多一个实心主按钮（`type="primary"`）。其余动作用文字按钮或 `plain`。
2. 不用 `el-card` 做列表项。列表是行，行与行之间是 1px 细线。
3. 不用 `el-alert`。错误是一行 13px 危险色文字，紧贴它所描述的对象。
4. 不出现 UUID、英文枚举值、状态码、字段名。凡展示给人看的值都经过术语表映射。
5. 不解释系统机制。副标题、说明段、"提示："全部删除；标题本身就是全部说明。
6. 不用插图、不用图标装饰导航、不用彩色 tag 表示状态。状态用文字或一个 6px 圆点。
7. 表单进抽屉（`el-drawer`），不占列表页顶部。
8. 空态一行字（≤ 12 字）加一个动作，没有第二句。
9. 数字用等宽数字（`tabular-nums`），金额右对齐、千分位、两位小数；日期统一 `2026年9月6日` / 表格内 `09-06`。
10. 颜色只能引用 `tokens.css` 的变量；组件与页面文件中出现 `#` 色值即不合并。

当前代码对这十条的违反统计见 3.6。

### 3.2 信息架构（按角色，对照当前 `AppLayout.vue`）

当前顶栏（`AppLayout.vue` L43-51）OWNER 可见 **9** 项：霜月 · 项目 · 财务 · 网盘 · 知识库 · 记忆 · SOUL · 审计 · 账号；右侧姓名 + "退出登录"文字按钮。目标：

| 角色 | 顶栏主导航（≤ 5 项） | 账号菜单（右上，姓名下拉） | 登录落点（已实现） |
|---|---|---|---|
| OWNER | **霜月** · 财务 · 项目 · 网盘 · 知识库 | 成员 · 审计 · 霜月设置（SOUL / 记忆）· 修改密码 · 退出 | `/chat` |
| MANAGER | 财务 · 项目 · 网盘 · 知识库 | 修改密码 · 退出 | `/projects` |
| STAFF | 财务 · 项目 · 网盘 · 知识库 | 修改密码 · 退出 | `/projects` |

规则：

- 导航由 `app/navigation.ts` 按 `auth.user.role` 生成，模板里不再散落 `v-if="isOwner"`（当前 5 处）。
- 路径已是 `/chat /finance /projects /projects/:id /drive /knowledge /users /audit /soul /memory`；`/users` 改名 `/members`（可选，决策点 K），`/owner/*` 的三条兼容重定向（`router.ts` L197-228）在 U1 结束后删除。
- OWNER 专属页面改为 `() => import()` 懒加载（F2），员工 bundle 不含对话与 SOUL 代码。
- 页面标题区统一：28px 标题 + 右侧 ≤ 2 个动作；**删除所有眉题**（当前财务/网盘/项目页把 `auth.user?.role` 枚举值当眉题，账号页写死 `OWNER`）。
- 里程碑提醒（R11）从横幅改为：项目页列表行尾一个 6px 警示圆点 + "10月8日到期"文字；OWNER 的 `/chat` 里由霜月在会话开头以一条普通消息提及（方案 P1-4 的形态）。全站唯一允许的横幅仍是对话页的"霜月暂时离线"一行。

### 3.3 视觉系统

#### 色

全部定义在 `web/src/styles/tokens.css`，页面与组件只引用变量。

| 变量 | 值 | 用途 |
|---|---|---|
| `--sb-paper` | `#F7F6F2` | 页面底色（暖白，纸感） |
| `--sb-surface` | `#FFFFFF` | 表格、抽屉、弹层表面 |
| `--sb-ink` | `#1C1C1A` | 主文字、标题 |
| `--sb-ink-2` | `#6E6B66` | 次要文字、表头、标签 |
| `--sb-ink-3` | `#A6A29B` | 占位、禁用、时间戳 |
| `--sb-line` | `#E8E5DF` | 细线、输入框边 |
| `--sb-line-strong` | `#D3CFC7` | 聚焦边、抽屉边 |
| `--sb-accent` | `#2E3A46` | **唯一强调色**（墨蓝灰）：主按钮、激活导航、链接 |
| `--sb-accent-hover` | `#3C4B5A` | 主按钮悬停 |
| `--sb-ok` / `--sb-warn` / `--sb-danger` | `#3B7A57` / `#9A6B1E` / `#A6453B` | 仅用于文字与 6px 圆点 |
| `--sb-ok-bg` / `--sb-warn-bg` / `--sb-danger-bg` | `#EEF4F0` / `#F7F1E4` / `#F8ECEA` | 仅霜月卡片折叠行底色 |

当前 33 处写死色值的分布：`#fff` ×7、`#909399` ×5、`#409eff` ×5、`#f5f7fa` ×4、`#e4e7ed` ×3、`#dcdfe6` ×3、`#606266` ×3、`#303133` ×2、`#ebeef5` ×1——全部是 Element Plus 默认调色板，禁止再出现。

#### 字

不下载 Webfont。系统字体栈：

```css
--sb-font: "Inter", -apple-system, "PingFang SC", "HarmonyOS Sans SC",
           "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif;
--sb-mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
```

字号六档 / 字重两档（400 / 600）：xs 12 · sm 13 · base 14 · md 16（对话正文、知识库正文）· lg 20（区块标题）· xl 28（页面标题、财务大数，`letter-spacing:-0.01em`）。金额与表格数字加 `font-variant-numeric: tabular-nums`。

#### 间距与宽度

8pt 网格。页面顶部内边距 40；标题到内容 32；区块之间 48；行列表行高 48；表格单元格上下 10 左右 12，无斑马纹、无外框。内容宽度两档：阅读型（对话、知识库正文、SOUL）880px；表格型（财务、网盘、成员、审计、项目）1120px，由 `AppShell` 按路由 `meta.width` 给。圆角 6px；弹层 8px；页面内阴影为 0。

#### Element Plus 的用法边界

继续用 Element Plus，但通过 `web/src/styles/element.css` 覆盖 `--el-color-primary`、`--el-font-family`、`--el-border-color*`、`--el-bg-color*`、`--el-border-radius-base`、`--el-box-shadow*`（完整变量清单见第一版 3.3，值不变）。`App.vue` 传 `:locale="zhCn"`；`index.html` 改 `lang="zh-CN"`。

允许：`el-button`（主按钮 ≤ 1/屏）、`el-input`、`el-select`、`el-date-picker`（月份）、`el-table`（细线、无边框、无斑马纹——当前 `AuditPage.vue` L41 的 `stripe` 要去掉）、`el-drawer`、`el-dialog`（仅初始密码与危险确认）、`el-popconfirm`、`el-progress`（2px）、`el-skeleton`（延迟 300ms）、`el-dropdown`。

禁止：`el-alert`（当前 15 处）、`el-card`（18 处）、彩色 `el-tag`（2 处）、`el-empty` 插图、`el-result`、`el-notification`、`el-message` 报成功、`el-tooltip` 当说明、`el-badge`、`el-breadcrumb`、`el-steps`、`el-statistic`、导航图标。同时禁止原生 `<select>/<input type=date|month|file|checkbox>/<button>/<progress>` 与 Element 混排（当前 15 处）。

### 3.4 文案原则与术语表

**八条原则**：标题即说明；不解释机制；不出现内部标识（request_id 只以 12px 灰色 8 位短码出现在错误行末尾）；错误 = 一句话 + 一个动作；确认只写后果、按钮就是动词；删除"请""吗""暂时""稍后再试"；数字与中文间空格、金额 `¥ 8,000.00`、日期 `2026年9月6日` / 表内 `09-06`；所有面向用户的字符串集中在 `web/src/copy/`，测试只引用常量。

**术语表**（`copy/glossary.ts`，按当前代码中实际存在的枚举更新）：

| 内部值 / 现有说法 | 统一用词 |
|---|---|
| OWNER / MANAGER / STAFF | 老板 / 管理层 / 员工 |
| 账号管理 / Users | 成员 |
| ACTIVE / DISABLED（账号） | 正常 / 已禁用 |
| PLANNING / ACTIVE / DELIVERING / REVIEW / ARCHIVED（阶段） | 筹备 / 进行 / 交付 / 复盘 / 归档（当前代码为 立项/进行中/交付中/复盘/已归档，两处重复定义于 `ProjectsPage.vue` L12-18 与 `ProjectDetailPage.vue` L15-21，需合一） |
| UPLOADING / QUARANTINED / SCANNING | 处理中（灰点） |
| CLEAN | （不显示） |
| INFECTED / FAILED（文件） | 未通过 |
| COST / INCOME | 成本 / 收入 |
| COMPANY / PROJECT（scope） | 公司运营 / 项目 |
| ALL / MANAGEMENT / OWNER_ONLY | 全员 / 管理层 / 仅自己 |
| PROPOSED / CONFIRMED / COMMITTED / REVISED / REJECTED / FAILED（卡片） | 待确认 / 入库中 / 已入库 / 已改写 / 已放弃 / 入库失败 |
| finance_entry / finance_adjust / project_create / project_update / milestone_change / file_move / memory / knowledge_ingest（卡片 kind） | 记一笔 / 调整账目 / 新建项目 / 更新项目 / 里程碑 / 移动文件 / 记住 / 知识入库 |
| FACT / PREFERENCE / DECISION / PROJECT_NOTE / DAILY_DIGEST（记忆） | 事实 / 偏好 / 决定 / 项目备注 / 每日纪要 |
| DRAFT / PUBLISHED | 草稿 / 已发布 |
| agent.card.confirm / finance.entry.create / project.update / file.delete / agent.soul.write …（审计 action） | 确认入库 / 记一笔 / 更新项目 / 删除文件 / 修改 SOUL …（映射表放 `copy/audit.ts`，未知 action 显示原文置灰） |
| SUCCESS / DENIED（审计 outcome） | 成功 / 被拒 |
| 临时密码 | 初始密码 |
| 退出登录 | 退出 |
| 给霜月（输入框标签） | （无标签，占位"对霜月说……"） |

### 3.5 各页面：现状、目标、改造点

每页列出**现状（已验证）**、**目标结构**、**全部文案**。执行者不得增加此处未列出的说明文字。

#### 登录 `/login`、首登改密 `/password/change`

- 现状：白卡片 + 蓝色眉题 "SuperBoss" + 标题"登录工作台" + 副标题"使用本地账号继续。"（`LoginPage.vue` L47-49）；错误用 `el-alert`（L75-82）；`calc(100vh - 64px)` 偏上（L90）。改密页眉题"首次登录"、说明"继续使用前，请先更换临时密码。"（`PasswordChangePage.vue` L54-56）。
- 目标：整页纸色，左上角 14px 字标；360px 单列垂直居中；标题 28px；无卡片边框、无眉题、无说明；错误为表单底部一行。
- 文案：`登录` `用户名` `密码` `用户名或密码不正确` ；`设置新密码` `当前密码` `新密码` `确认新密码` `保存` `两次输入不一致`。

#### OWNER 对话 `/chat`（最高优先级）

- 现状（`ChatPage.vue`）：
  - 左栏：h1"霜月" + 小号"新对话"按钮（L273-277）；一个带 label"搜索会话"+"查找"按钮的表单（L278-282）；会话是带边框圆角的按钮块（L283-293，L428-441）。无归档、无日期分组。
  - 线程：`offline` 与 `errorMessage` 各一个 `el-alert`（L295-305）；消息为 `<strong>你/霜月</strong>` + 正文；每次工具调用产生一个空的"霜月"条目（R7）。
  - **卡片**（L312-371）：`el-card` 内 h2 = kind 中文；待确认时对 payload **每个字段一个 `el-input`**，标签来自 `FIELD_LABEL`（L32-59）：`项目` 一栏显示 UUID、`金额（分）` 要求以分为单位输入、`可见范围` 显示 `MANAGEMENT`、`日期` 为 ISO；再加一行"说明（可选）"输入框（占位"一句话说明这次修改"）；三个按钮"保存修改 / **确认入库** / 放弃"（L343-358）。已入库显示 `已入库 · finance_entry`（L318-323）。已放弃 / 已改写 / 入库失败三种状态走 `v-else`（L360-370）：一张 `<dl>` 原样列出 payload，**没有任何状态字样**。"让霜月修改"（`revise`）未接。
  - 输入区（L378-402）：label"给霜月" + textarea；附件走整个 `MultipartUploader` 组件（自带 h2"上传文件"、原生 file input、"开始上传"按钮、`<progress>`）；附件成功后显示"附件：已上传，发送时交给霜月"；实心"发送"按钮——本页因此有 **2** 个实心主按钮。
- 目标结构：
  - 会话栏 240px 可折叠：顶部文字按钮"新对话"；会话按 今天 / 本周 / 更早 分组，每条一行标题，行尾 `···`（归档——接 R8 的 archive 接口）；搜索为一个无标签输入框（占位"搜索"），输入即筛，无"查找"按钮。
  - 消息列 880px：发言人 13px 小字（"霜月" / "你"）在消息上方，正文 16px/1.75，消息间距 32px，无气泡。**服务端 `list_messages` 过滤掉 `content` 为空的助手轮次，并放出 `SYSTEM` 回执**（R7）；回执渲染为一行 13px 灰字居中：`已入库 · 记一笔 房租 ¥ 8,000.00`。
  - 提案卡 `components/chat/ProposalCard.vue`（自绘，1px 细线框、8px 圆角、24px 内边距）：

    ```
    记一笔 · 公司运营                                  2026年9月6日
    房租                                          ¥ 8,000.00
    可见范围                                            管理层
    备注                                               9 月房租
    ──────────────────────────────────────────────────────────
    [确认入库]        修改        放弃
    ```

    每种 kind 一个渲染器（`cards/FinanceEntryCard.vue` 等 8 个，可共用 `KeyValue`），负责：`project_id → 项目名`（从 `projectsApi.list()` 缓存映射）、`amount_cents → ¥ 元`、枚举 → 术语、日期 → 中文；**不再直出 payload 键值**。
    - "修改"展开为卡片内一行输入框（占位"告诉霜月哪里不对"），回车调用 `revise`：旧卡折叠为一行 `已改写`，新卡出现在下一条霜月消息下。字段级手改（`patch`）保留为"修改"行右侧的"直接改"文字按钮，展开时也只对**该 kind 的字段**给对应控件（金额输入元、日期选择器、可见范围下拉、项目下拉），不再对任意 key 给文本框。
    - 已入库：整卡折叠为一行浅绿底 `✓ 已入库 · 记一笔 房租 ¥ 8,000.00 → 财务`（末尾链接到 `/finance` 或 `/projects/:id`）。已放弃：一行灰字 `已放弃 · 记一笔 房租`。入库失败：浅红底一行 + "重试"（依赖 R6 修复）。
  - 输入框：贴底，自动增高，左侧回形针（全站唯一图标），右侧 ↑ 发送按钮（本页唯一实心）；Enter 发送、Shift+Enter 换行；占位"对霜月说……"。附件选择后在输入框上方一行显示文件名 + 灰点（上传/扫描中）或 ✓，不出现"开始上传"。**附件默认目录改为 `老板私有`**（R3）。
  - 空会话：整列只有输入框，无欢迎语。
  - 离线：输入框上方一行 13px 灰字"霜月暂时离线"，输入框禁用。这是全站唯一允许的横幅。
  - 错误：紧贴卡片或输入框下方一行危险色 13px。
- 文案：`新对话` `搜索` `今天` `本周` `更早` `归档` `对霜月说……` `确认入库` `修改` `直接改` `放弃` `告诉霜月哪里不对` `已入库` `已改写` `已放弃` `入库失败` `重试` `霜月暂时离线` `你` `霜月`。

#### 财务 `/finance`（三角色同一组件）

- 现状（`FinancePage.vue`）：眉题直出角色枚举（L165-167）；原生 `<input type="month">`（L172）；"导出 CSV"是裸 `<a>`（L174-176）；汇总是两张 `el-card`，内容为散文行"成本 8000.00 元 / 收入 … / 毛利 …"（L181-216）；"录入"是页面中段一张 `el-card` 表单，4 个原生 `<select>` + 原生 date（L217-274）；明细是 `<ul>` 每行 7 个 `<span>` 用 flex 排开（L275-318），无表头、无对齐；"调整"展开为行内三控件表单；错误文案"财务数据暂时无法加载，请稍后重试。"（L75）。
- 目标：
  - 标题行：左"财务"，右 `‹ 2026年9月 ›`（文字按钮）+ OWNER 的"记一笔"（文字按钮，开抽屉）+ "导出"（文字按钮）。
  - 汇总条：一行大数（28px 等宽，上方 13px 灰标签，下方一条细线）：OWNER/MANAGER 显示 `公司运营成本 · 项目成本 · 收入 · 毛利`，STAFF 只显示 `项目成本`。
  - 明细：`el-table` 细线无边框：`日期(09-06) · 类型 · 范围/项目 · 类别 · 备注 · 金额(右对齐)`，OWNER 多一列"可见"与行尾 `···`（调整——开抽屉：字段下拉、新值、原因、[保存]）。
  - 抽屉"记一笔"：类型、范围、项目（范围=项目时）、金额（元）、日期、类别、备注、可见范围（默认按规则预填并显示"默认：管理层"）；底部 [保存]。
  - 成本预警（R8）：有预警时在汇总条下一行 13px 警示色文字，不做横幅。
- 文案：`财务` `记一笔` `导出` `公司运营成本` `项目成本` `收入` `毛利` `日期` `类型` `范围` `项目` `类别` `备注` `金额` `可见` `调整` `原因` `保存` `本月还没有记录`。

#### 项目 `/projects` 与 `/projects/:id`

- 现状：列表页标题"项目管理"、角色眉题、顶部 `el-card` 创建表单含"设为验收测试项目"勾选（`ProjectsPage.vue` L87-101）、每个项目一张 `el-card`、`验收测试` 黄 tag（L125-127）、本地合并 bug（F4）。详情页 OWNER **始终**看到编辑表单（原生 `<select>` 阶段、四个输入、"保存概况"）和里程碑编辑列表（原生 checkbox"完成"、"添加节点"、"保存里程碑"）——两个实心主按钮（L160、L200-205）；非 OWNER 看到 `<dl>` 与"未定 / 未定日期 / 已完成 / 未完成"。日期均为 ISO 直出。
- 目标：列表 = 行列表：`名称 · 阶段(文字) · 2px 进度条+百分比 · 到期(09-06，临期加警示圆点)`；OWNER 右上 [新建]（抽屉：名称、阶段、开始、到期、说明）；筛选只有 `进行中 / 已归档`。详情 = 28px 项目名 → 一行元信息（阶段 · 起止 · 进度）→ 说明正文 → "里程碑"区块竖向时间线（`日期 — 标题`，完成的 ✓ 置灰）；OWNER 通过右上 [编辑]（抽屉）和里程碑区块的 [添加] / 行尾 `···` 修改，**默认是阅读态**。删除 `is_test`（接口、勾选、tag 一起删；e2e 已不依赖）。
- 文案：`项目` `新建` `编辑` `进行中` `已归档` `里程碑` `添加` `名称` `阶段` `开始` `到期` `说明` `保存` `完成` `还没有项目` `还没有里程碑`。

#### 网盘 `/drive`

- 现状（`DrivePage.vue`）：角色眉题（L237）；未配置对象存储时整页顶部红色 `el-alert` "对象存储来源尚未安全配置，暂时无法上传文件。"（L240-247）；面包屑是一排带边框的按钮（L256-265）；左 `el-card`"目录"含"可见范围：全员"一行与"新建子目录"表单（实心"创建"）；右 `el-card`"文件"：每行 `文件名 · CLEAN(枚举直出 L298) · 下载 重命名 移动 删除`，重命名/移动展开为行内表单（原生 `<select>` L327）；`globalThis.confirm` 删除（L185）；底部嵌整个 `MultipartUploader`（h2"上传文件"、原生控件）；再一张 `el-card`"本次上传"打印 `file_id` UUID（L351）与按钮"检查并获取下载""下载本次文件"（L352-356）。
- 目标：左 240px 目录树（三根按角色可见；OWNER 在根/子目录 `···` 新建子目录）；右侧面包屑为文字；表 `名称 · 大小 · 日期 · 上传者`（需接口补 `size_bytes`、`uploader_name`）；处理中的文件名前灰点，未通过的名称危险色 + 行尾"未通过"，其余点击名称即下载；拖文件到页面任意位置上传，进度在右下角 320px 浮层逐文件一行，完成即消失；OWNER 行尾 `···`：移动（抽屉内目录树）/ 重命名（行内）/ 删除（`el-popconfirm`"删除后不可恢复。"）。删除"本次上传"卡片、`file_id`、"检查并获取下载"；对象存储未配置时上传按钮禁用 + 13px 灰字"上传未配置"。`MultipartUploader` 改为无 UI 的组合式函数 + 一个拖放区组件。
- 文案：`网盘` `上传` `名称` `大小` `日期` `上传者` `未通过` `新建目录` `移动` `重命名` `删除` `删除后不可恢复。` `这个目录是空的` `上传未配置`。

#### 知识库 `/knowledge`

- 现状（`KnowledgePage.vue`）：搜索表单带 label 与"查找"按钮；OWNER 顶部 `el-card`"新建文档"表单（标题、正文 textarea、"保存草稿"）；每篇文档一张 `el-card`，`body_md` 以纯文本 `<p>` 直出（L73），知识点为 `<ul>`；只有"发布"按钮，无编辑、无下架、无详情页。
- 目标：左 280px 文档列表 + 顶部搜索框；右 680px 正文：28px 标题、13px 灰元信息（更新日期 · 标签 · 草稿）、Markdown 渲染（引入 `markdown-it`，只允许到 h3，禁 HTML）、知识点作为正文内 h3 小节；OWNER 右上 [编辑]（抽屉：标题、标签、正文）与 [发布] / [下架]（文字按钮，接 `PATCH status`）。
- 文案：`知识库` `搜索` `新建` `编辑` `发布` `下架` `草稿` `标题` `标签` `正文` `保存` `还没有文档` `没有匹配的内容`。

#### 成员 `/users`（→ `/members`）

- 现状（`UsersPage.vue`）：眉题写死 `OWNER`（L140）、标题"账号管理"；顶部 `el-card` 添加表单（原生 `<select>` 角色）；每人一张 `el-card`：`<el-tag>{{ user.role }}</el-tag>`、`{{ user.status }}` 枚举直出（L180-182）、"项目：未分配"、四个实心/彩色按钮（重置密码 / 设为管理层 / 禁用(danger) / 启用(success)）、项目勾选组（L220-240）；`globalThis.confirm` ×2；对话框"临时密码"说明"请立即安全交给员工。关闭后系统不会再次显示该密码。"，按钮"我已保存"。
- 目标：表 `姓名 · 用户名 · 角色(中文) · 状态(仅"已禁用"时显示) · 最近登录(09-06 14:20) · ···`；`···`：改为管理层/员工 / 重置密码 / 禁用（`el-popconfirm`"禁用后该账号立即退出登录。"）/ 启用；右上 [添加]（抽屉：姓名、用户名、角色）。**移除项目勾选组**（方案 4.3；相关接口可暂留）。初始密码对话框：标题"初始密码"、等宽 20px 密码 + [复制]、一行 13px 灰字"关闭后不再显示。"、底部 [关闭]。
- 文案：`成员` `添加` `姓名` `用户名` `角色` `最近登录` `从未登录` `管理层` `员工` `已禁用` `改为管理层` `改为员工` `重置密码` `禁用` `启用` `禁用后该账号立即退出登录。` `初始密码` `复制` `关闭后不再显示。` `创建` `关闭`。

#### 霜月设置：SOUL `/soul`、记忆 `/memory`、审计 `/audit`

- SOUL 现状：`el-card` 内 label"当前人设" + textarea + "版本说明" + 实心"保存为新版本" + "预览提示词"，预览以 `<pre>` 追加在下方；右 `el-card`"版本"只列 `note || '未命名'` 与"（当前）"，无日期。目标：左 2/3 等宽字体编辑器（`--sb-mono` 14px/1.7，无边框只有底色）；右 1/3 版本行列表（`09-06 14:20 · 备注`，当前版本前圆点，其余行尾"设为当前"文字按钮）；顶部右侧 [保存为新版本]（唯一实心）与 [预览提示词]（抽屉）。
- 记忆现状：小节标题与每条前缀直出 `PREFERENCE/FACT/...`（`MemoryPage.vue` L77、L81）；每条一张带边框卡片，三个文字按钮。目标：按 事实 / 偏好 / 决定 / 项目备注 / 每日纪要 分组的行列表；每行内容 + 13px 灰日期 + 置顶圆点；行尾 `···`：置顶 / 编辑（行内）/ 归档；顶部搜索框。
- 审计现状：筛选输入框占位"例如 finance.entry.create"（L34，属说明文字）+ 实心"筛选"；`el-table stripe` 五列直出 `created_at` ISO、`action`、`outcome`、`object_type`、`actor_kind`。目标：表 `时间(09-06 14:20) · 谁(需接口补 actor 姓名) · 做了什么(术语映射) · 对象 · 结果(成功/被拒)`；顶部一个"动作"下拉（选项来自映射表）；无斑马纹；不做导出。服务端先停掉 `project.list/read` 的 SUCCESS 审计（S3），否则此页被"查看项目"淹没。
- 文案：`SOUL` `保存为新版本` `预览提示词` `版本` `设为当前` `记忆` `搜索` `置顶` `编辑` `归档` `审计` `时间` `谁` `做了什么` `对象` `结果` `成功` `被拒`。

#### 无权访问 `/forbidden`

- 现状：标题"无权访问" + 说明"当前账号没有访问此页面的权限。" + "返回首页"→ `/projects`（已不再死循环）。目标：标题"没有权限" + 链接"回到首页"→ `homePath(role)`，删说明句。

### 3.6 当前堆砌 / 廉价感来源（f2ad4fe 更新表）

| # | 来源 | 位置（已验证） | 处置 |
|---|---|---|---|
| C1 | Element 默认色直写 33 处 | `AppLayout.vue` L79、L87-88、L104、L109；`LoginPage.vue` L93、L101-102、L113、L123；`PasswordChangePage.vue` L108、L116-117、L127；`ChatPage.vue` L434、L436、L440、L452；`DrivePage.vue` L370、L381-382；`ProjectsPage.vue` L144、L170；`ProjectDetailPage.vue` L223、L227；`UsersPage.vue` L286、L288、L305；`FinancePage.vue` L333；`MemoryPage.vue` L111、L114 | 全部换 `tokens.css` 变量（U0） |
| C2 | 角色枚举 / "OWNER" 当眉题 | `FinancePage.vue` L165-167、`DrivePage.vue` L237、`ProjectsPage.vue` L81-83、`UsersPage.vue` L140 | 删 |
| C3 | 机制解释型副标题与说明 | `LoginPage.vue` L49；`PasswordChangePage.vue` L56；`UsersPage.vue` L250；`DrivePage.vue` L246、L269-271；`ChatPage.vue` L296、L337-341；`AuditPage.vue` L34；`ForbiddenPage.vue` L4 | 删 |
| C4 | UUID 与枚举直出 | `DrivePage.vue` L298 `file.state`、L351 `file_id`；`UsersPage.vue` L180 `user.role`、L182 `user.status`；`MemoryPage.vue` L77、L81 `kind`；`ChatPage.vue` L321 `committed_object_type`、L334 payload 值（UUID、分、枚举）；`AuditPage.vue` L42-46 五列 | 术语表映射 / 卡片渲染器 |
| C5 | 页顶创建表单 + 卡片列表 | `ProjectsPage.vue` L87-129；`UsersPage.vue` L143-241；`FinancePage.vue` L217-274；`KnowledgePage.vue` L58-85；`DrivePage.vue` L281-291 | 表单进抽屉；列表改行 |
| C6 | `el-alert` ×15 | `AppLayout.vue` L61-70（提醒堆叠）；其余每页 1–2 个错误横幅 | 内联一行文字；提醒改圆点 |
| C7 | 原生控件与 Element 混排 ×15 | `FinancePage.vue` L172、L222、L229、L236、L250、L259、L298；`DrivePage.vue` L327；`ProjectDetailPage.vue` L136、L183；`UsersPage.vue` L155；`MultipartUploader.vue` L100、L103-106、L108；`ChatPage.vue` L283（会话按钮） | 统一 |
| C8 | 标题叠加：页 h1 → 卡片 h2 → 上传组件 h2"上传文件" | `DrivePage.vue` L293 + `MultipartUploader.vue` L97；`ChatPage.vue` L389-394 同样嵌入 | 只留页面标题 |
| C9 | 按钮文案暴露实现 | `DrivePage.vue` L353"检查并获取下载"、L356"下载本次文件"、L336"确定移动"；`FinancePage.vue` L313"确定调整"；`UsersPage.vue` L256"我已保存"；`ChatPage.vue` L344"保存修改" | 行点击即下载；动词按钮 |
| C10 | 导航 9 项、"退出登录"、"SOUL"英文项 | `AppLayout.vue` L43-57 | 按 3.2 重排；SOUL/记忆/审计/成员进账号菜单 |
| C11 | `globalThis.confirm` ×3 | `UsersPage.vue` L88、L103；`DrivePage.vue` L185 | `el-popconfirm` |
| C12 | 错误正文带 36 位 request_id | `http.ts` L85-97 及 8 个 `xxxErrorMessage` | 短码 + 复制 |
| C13 | 验收脚手架可见物 | `ProjectsPage.vue` L91 勾选、L125-127 tag；`api/projects.ts` L36、L47、L162、L179、L193 | 删 |
| C14 | 无字体、`lang="en"`、Element 英文 locale | `index.html` L2；`main.ts`；`App.vue` | U0 |
| C15（新） | 每屏 2 个实心主按钮 | `ChatPage.vue` L348 + L396；`ProjectDetailPage.vue` L160 + L200；`UsersPage.vue` L161 + L255 | 戒律 1 |
| C16（新） | 卡片状态无区分、payload 表单化 | `ChatPage.vue` L324-370 | 3.5 对话页 `ProposalCard` |
| C17（新） | 日期/金额格式散落 | 全仓仅 `UsersPage.vue` L36 一处 `Intl`；金额 `yuanFromCents(...) 元` 拼接（`FinancePage.vue` L186-214、L284） | `Money` / `DateText` 基元 |

### 3.7 前端目录与基元组件

```
web/src/
  styles/{tokens,element,base}.css
  app/router.ts            懒加载；meta.width: 'read' | 'table'
  app/navigation.ts        角色 → 导航项 / 账号菜单；homePath 迁入
  copy/glossary.ts         枚举 → 中文（3.4）
  copy/audit.ts            action → 中文
  copy/errors.ts           错误码 → 一句话
  copy/pages/*.ts          各页文案常量；测试只引用这里
  api/parse.ts             唯一一份 isRecord/hasKeys/uuid（替代 8 份）
  api/errors.ts            ApiContractError + 一份 formatRequestError
  components/ui/           PageHeader RowList/Row KeyValue InlineError EmptyLine Money DateText Dot
  components/chat/ProposalCard.vue + cards/{FinanceEntry,FinanceAdjust,ProjectCreate,ProjectUpdate,MilestoneChange,FileMove,Memory,KnowledgeIngest}Card.vue
  components/files/{useMultipartUpload.ts, DropZone.vue, UploadTray.vue}
  layouts/AppShell.vue     替代 AppLayout
  pages/{chat,finance,projects,drive,knowledge,members,audit,soul,memory,auth,forbidden}/
```

八个基元组件（不多于此数；新增需在本文登记）与第一版 3.7 表相同：`PageHeader`、`RowList/Row`、`KeyValue`、`InlineError`、`EmptyLine`、`Money`、`DateText`、`Dot`。

---

## 四、优化优先级

**E0 — 工程阻塞项（先于一切 UI 工作；不改视觉）**

1. R1 历史回放：`_window` 按 `AgentMessage.tool_calls` 回放 assistant 消息；tool 消息补 `tool_call_id`（在 `_run_turn` L345-352 存入 `tool_calls` JSON 的 `{"tool_call_id": ...}` 即可，无需新列）。`core/llm.py` 区分不可用与请求错误并记日志。加一个用真实 OpenAI 报文格式校验 `_window` 输出的单元测试。
2. R3 附件默认目录 → `老板私有`。
3. R2 流式回退幂等：回退前比对最后一条用户消息；或给 `/messages` 加 `Idempotency-Key`。
4. R6 FAILED 落库；R7 `list_messages` 过滤空助手轮次、放出 SYSTEM 回执（`MessageRead` 已含 `role`，前端按 role 渲染）。
5. R10 `prettier --write web/src/api/files.ts web/tests/http.test.ts`；开 PR 让 CI 跑一次。
6. R4 滚动摘要：`_run_turn` 结束后若消息数 > `_WINDOW`，异步（复用 `extract_memories` 的 Celery 通道）让模型把窗口外部分压成 ≤ 300 字写回 `conversation.summary`；`_window` 取窗口内 + `summary`。
7. R5 召回：`recall()` 改为 `search @@ plainto_tsquery('simple', q)` ∪ `similarity(content, q) > 0.3`（对用户消息分词取前 5 个词而非整句）；去重改 `similarity > 0.8`。这是方案 5.3 验收场景能否通过的关键。
8. R9 每日纪要：改为调用模型生成 ≤ 200 字纪要；`beat_schedule` 用 `crontab(hour=0, minute=30)`；`recall()` 的纪要注入上限改为 3 条。
9. 服务端小收敛（第一版 P0-4，全部仍未做）：`core/errors.py` 默认码 `FORBIDDEN / NOT_FOUND / CONFLICT`；`get_session` 收敛到 `core/db.py` 一份（7 → 1）；`project.list / project.read` 不写 SUCCESS 审计；`AuthUserRead` 加 `id`。
10. 删除死代码：`require_project_access`、`Actor.project_ids` 的每请求查询（若决策点 A 维持"全员可见"）。

**U0 — 立规矩，并以对话页为样板**

11. `tokens.css / element.css / base.css`；`zhCn` locale；`lang="zh-CN"`；`AppShell` + `navigation.ts`；懒加载；八个基元；`copy/`；`api/parse.ts` + `api/errors.ts`（8 → 1）。
12. **`ProposalCard` 与 8 个卡片渲染器 + 接 `revise` 与 `archive`**——对话页按 3.5 整体重写，作为体系可用的证明。同步把 `chat-page.test.ts` 改为引用 `copy/` 常量。
13. 登录 / 改密 / 无权限三页按 3.5 重写（最小页，验证 token 与基元）。

**U1 — 逐页收敛（每页一个 PR，PR 描述贴 1280 宽截图）**

14. 财务（含预警一行、导出文字按钮、抽屉记一笔）。
15. 项目列表 + 详情（阅读态默认；删 `is_test` 连带接口字段）。
16. 网盘（拖放、浮层、行表；接口补 `size_bytes`、`uploader_name`）。
17. 成员（移除项目勾选；`···` 菜单；初始密码对话框）。
18. 审计（action 映射；接口补 `actor_name`；下拉筛选）+ SOUL + 记忆。
19. 知识库（Markdown 渲染、编辑/下架抽屉、左右布局）。
20. 清理：`/owner/*` 重定向、`AppLayout.vue`、`OwnerHomePage.vue`（已无路由引用）、`HealthPage.vue` 英文文案、`m1-owner-acceptance.md` L135-143 设备/连接器条目、根目录三层方案文档移入 `docs/` 并与 `master` 去重。

**U2 — P1 尾巴**

21. 对话内附件预览（文本前 N 字折叠区）；会话归档视图；月度 token 用量一行（放 SOUL 页底部，不放审计页）。
22. 财务项目 × 月透视（仍是表格）；`@media print` 月报。
23. 视觉回归门：Playwright 对 登录 / 对话 / 财务 / 项目详情 / 网盘 / 成员 六页截图入库，像素差异 > 0.5% 需人看。
24. Element Plus 按需引入（可选，仅当体积成为可感知问题）。

---

## 五、分阶段迭代方案

每阶段独立可合并；验收以命令输出为准。

### E0（服务端 + 对话页逻辑，约 10 个小 PR）

**做**：第四章 1–10。**验收**：

- 新增单元测试：`_window` 输出中每个 `role=tool` 消息前一条必须是含匹配 `tool_calls[].id` 的 assistant 消息；`recall("昨天那个合作项目")` 能命中内容为"星野合作是合作类项目"的记忆；`commit_card` 失败后重新 GET 卡片为 `FAILED`。
- `rg -n '"role": "tool", "content"' server/src` 无输出（即 tool 消息已带 `tool_call_id`）。
- `rg -c 'async def get_session' server/src | awk -F: '$2>0' | wc -l` 输出 `1`。
- `rg -n 'project\.list|project\.read' server/src/superboss/modules/projects/router.py` 只剩 DENIED 路径。
- 真实 LLM 手工验收（老板或指定人，配好 `SUPERBOSS_LLM_*`）：同一会话连续三轮，每轮都触发工具（"列一下项目"→"给星野加个 10 月 20 日交付节点"→"这个月房租 8000"），三轮均不离线；第二天新会话问"昨天那个合作项目"能答出名字。
- CI 绿（含 web lint）。

### U0（前端体系 + 对话页样板 + 三个小页）

**做**：第四章 11–13。**验收**（全部为零或全部通过）：

```bash
rg -n '#[0-9a-fA-F]{3,6}\b' web/src/pages web/src/components web/src/layouts        # 无输出
rg -n '<el-alert|<el-card|globalThis.confirm|window.confirm' web/src                # 无输出
rg -n 'lang="en"' web/index.html                                                    # 无输出
rg -n '\{\{\s*[a-z]+\.(role|status|state|kind|committed_object_type)\s*\}\}' web/src/pages   # 无输出
rg -n '\{\{[^}]*_id[^}]*\}\}' web/src/pages web/src/components                      # 无输出
rg -c 'type="primary"' web/src/pages web/src/components | awk -F: '$2 > 1'          # 无输出
rg -n '暂时无法|请稍后重试' web/src --glob '!web/src/copy/**'                         # 无输出
rg -n '<select|<input type=|<button|<progress' web/src/pages web/src/components     # 无输出
rg -c 'function isRecord' web/src | awk -F: '{s+=$2} END {print s}'                  # 1
rg -n "[\x{4e00}-\x{9fff}]" web/tests/chat-page.test.ts --pcre2                     # 无输出（其余测试在 U1 逐页清零）
```

- 对话页：空会话只有输入框；一张 `finance_entry` 卡片显示项目名与 `¥ 8,000.00`，三个动作仅一个实心；点"修改"输入一句话后旧卡变"已改写"、新卡出现；确认后折叠为一行并可点到财务页。
- STAFF 登录落 `/projects`，导航四项，账号菜单两项；手输 `/chat` 见"没有权限"，点"回到首页"回 `/projects`；员工 bundle 的 chunk 列表里没有 `ChatPage`。

### U1（逐页）

第四章 14–20，每页 PR 满足 3.5 对应结构与文案表，附截图；阶段末上面的 `rg` 集对整个 `web/tests` 也为零。

### U2

第四章 21–24。视觉回归门在此阶段启用。

---

## 六、明确暂不做

- 深色模式、主题切换；任何图表、仪表盘、KPI 卡片墙。
- 插图式空态、引导教程、tooltip 说明、帮助中心、"温馨提示"。
- 导航图标（对话输入框的回形针除外）、彩色状态标签、徽标计数、通知中心、消息推送。
- Webfont 下载；移动端优先（只保证 ≥ 1024px 正常、≤ 760px 不破版）。
- 国际化；自定义组件库替代 Element Plus；动效库、页面转场、常驻骨架屏。
- pgvector / embedding——在 R5 修复并实测召回仍不够之前不引入（与三层方案一致）。
- 员工 / 管理层的任何霜月入口或只读问答。
- 目录级 ACL、项目级隐藏（决策点 A 未触发）。
- 给外部程序发 API 令牌（决策点 B 未定）。
- 卡片的"自动入库"或任何无需确认的写操作。

---

## 七、给执行者的交付约束与决策点

**交付约束**（沿用第一版第七章）：只用 3.3 变量、3.7 基元与允许清单组件；页面上每个字符串必须能在 3.5 文案行或 3.4 术语表找到；不做批量操作、高级筛选、排序控件；每个 UI PR 交付 1280 宽截图 + 第五章 `rg` 输出 + `copy/` 常量清单；本文与三层方案冲突时，功能与权限以三层方案为准、观感与文案以本文为准，无法调和则记入下表。

**待老板拍板的决策点**（编号接续三层方案 A–E 与第一版 F–I）：

| 编号 | 问题 | 本文默认 |
|---|---|---|
| F | 强调色：墨蓝灰 `#2E3A46` 还是偏冷的 `#34506B` | 墨蓝灰 |
| G | 对话页是否保留左侧会话栏 | 保留，默认展开，可折叠 |
| H | 财务汇总条是否向管理层显示"收入 / 毛利" | 显示（与决策点 E 一致） |
| I | 是否启用视觉回归门 | U2 起启用 |
| J（新） | 对话附件默认落 `老板私有` 后，是否允许霜月在 `file_move` 卡片里把文件移到 `项目`（全员可见）目录 | 允许，但卡片上"可见范围将变为：全员"一行必须以警示色显示 |
| K（新） | `/users` 是否改路径为 `/members` | 改（同时删 `/owner/*` 重定向） |
| L（新） | `project_members` / 用户页项目分配：删除接口与表，还是保留为项目详情的"参与人"展示 | 保留表与接口，从成员页移到项目详情抽屉；不做访问控制 |
| M（新） | 每日纪要是否调用模型生成（每天约 1 次调用、几千 token） | 是 |
