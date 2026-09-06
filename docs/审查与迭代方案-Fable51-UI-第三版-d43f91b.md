# SuperBoss 审查与迭代方案：Grok Build 落地复核（Fable 5.1，第三版）

日期：2026-09-06。基于 **`feature/p0-drop-devices-imports@d43f91b`**（提交 "feat: land agent reliability fixes and Fable UI system"，相对上一 tip `045789b` 改动 104 个文件，+3887 / −1742 行）的逐文件核查与本机实测。

> **第四版已发布**：[`审查与迭代方案-Fable51-UI-第四版-67e843e.md`](./审查与迭代方案-Fable51-UI-第四版-67e843e.md) 基于 `67e843e`（"feat: complete Fable v3 E1/U1, drop project_members, and add visual plus LLM gates"）逐条复核了本文 N1–N16、E1 / U1 清单与决策点 K–Q 的落地情况（N1 / N2 已修，N3 / N5 修了一半，网盘页基本未动），并评估新加的视觉与 LLM 门。本文第三章的"新问题"与第四章的"现状"描述已过期，第五章清单以第四版第五章为准。

**本文是第二版（[`审查与迭代方案-Fable51-UI.md`](./审查与迭代方案-Fable51-UI.md)，基于 `f2ad4fe`）的后续，不取代它。** 第二版第三章（十条戒律、token、术语表、各页面目标结构与文案）仍是 UI 规格；本文只回答：**这一提交修了什么、没修什么、引入了什么新问题、观感现在到哪一步、下一轮做什么。** 第二版对"哪里有 `el-alert`、写死色值几处、导航几项"的现状描述已过期，以本文第四章为准。

凡标注"（已验证）"的结论附文件路径与行号，行号以 `d43f91b` 为准；"（实测）"指在本机真实 PostgreSQL 16 上跑过；"（推断）"指从源码读出、未在浏览器或真实 LLM 端点上实测。

---

## 一、摘要

给老板的一页纸：

1. **规矩立起来了，页面确实不再像验证台。** `tokens.css / element.css / base.css` 三个文件定义了纸色、单一强调色、字体栈、六档字号；`AppShell` + `navigation.ts` 把 OWNER 顶栏从 9 项收到 5 项（霜月 · 财务 · 项目 · 网盘 · 知识库），成员 / 审计 / 霜月设置 / 记忆 / 改密进右上姓名下拉；所有页面懒加载；`zhCn` locale、`lang="zh-CN"`。第二版第五章 U0 的十条 `rg` 验收里，**八条清零**：`el-alert` 0（原 15）、`el-card` 0（原 18）、`globalThis.confirm` 0（原 3）、原生 `<select>/<input>/<button>/<progress>` 0（原 15）、页面里的 UUID / 枚举直出 0、`isRecord` 1 份（原 8）、`lang="en"` 0、写死色值 1 处（原 33，剩一处是打印样式里的 `#000`）。八个基元组件、`copy/` 目录、`ProposalCard` 都有了。lint / typecheck / 131 个前端测试 / 330 个服务端测试全绿（实测），第二版说的 CI 会红已修。

2. **可靠性那一组（R1–R11）名义上全动了，但两处修法本身有 bug，一处修了一半，老板真用还是会翻车。**
   - **新引入的回归（最严重）**：记忆抽取的 Celery 任务现在**每次都失败**。`extract_memories` 末尾新增了 `maybe_summarize()`，它走 `_conversation()` 做归属校验，而任务用的是 `UUID(int=0)` 系统账号，校验必然抛 `CONVERSATION_NOT_FOUND`——异常冒出后整个 session 不提交，**本轮抽出的记忆也一起丢了**，任务再重试两次再失败（实测复现，见 3.1 N1）。这意味着 `d43f91b` 之后霜月**不再产生任何长期记忆**，滚动摘要也一条都写不出来。第二版的 R4 不但没修好，还把原来能用的记忆抽取弄坏了。
   - **R1 只修了一半**：`tool_call_id` 与 `tool_calls` 现在确实存了、也回放了，但同一轮里写入的所有消息 `created_at` 相同（`server_default=now()` 是事务开始时间），`_window` 按 `created_at desc` 取 16 条再 `reverse()`，同一时间戳内的顺序由数据库随意给——实测得到的窗口把整轮**顺序完全颠倒**，`tool` 行排在它所属的 `assistant.tool_calls` 行之前，且窗口把最新两条消息切掉了（实测，N2）。这正是 OpenAI 兼容接口返回 400 的形态。真实 LLM 下"用过工具的会话从第二轮起离线"的风险**仍在**，只是原因换了。
   - **R5 召回**：从整句 `ILIKE` 改成关键词 + 相似度 + tsvector，方向对；但分词是"每个连续汉字串按 2 字切、最多留 5 个"，句子以"帮我看看"这类虚词开头时，真正的关键词（"合作""项目"）被截掉；"昨天""那个"这类词又会把不相关记忆一起召回（实测，N3）。

3. **卡片——产品的脸——从 JSON 表单变成了像样的提案卡。** `ProposalCard` 自绘、细线框、术语映射、金额 `¥ 8,000.00`、项目名替代 UUID、`file_move` 到全员目录时有警示行；"修改"接了 `revise`，"直接改"按 kind 给对应控件；已入库 / 已放弃 / 已改写 / 入库失败四种状态各有折叠行。但四个小问题让它还差一口气：入库失败行的"重试"点了必报 409（服务端只允许 `PROPOSED` 再确认，N4）；已入库行的链接文字永远是"财务"（项目卡也写"财务"）；卡内日期仍是 `2026-09-06` 的 ISO 直出；线程里的入库回执显示 `已入库：finance_entry`（服务端写入的是枚举值）。

4. **廉价感的大头清掉了，剩下的集中在三页。** 网盘页最乱：每行 4 个文字按钮 + 行内展开的重命名 / 移动表单 + 上传完成后仍然出现"检查并获取下载""下载本次文件"（第二版 C9 点名的两个按钮原样保留，因为旧测试还在断言它们）+ 左栏常驻的"新建子目录"表单；`DropZone`/`UploadTray` 两个组件写了但没接线（拖放不触发上传，浮层永远为空）。成员页每行同时有 `···` 菜单**和**三个内联按钮，动作重复一遍。对话页每张待确认卡一个实心"确认入库"加底部实心"发送"，一屏多个主按钮；回形针是 📎 emoji。文案层只做了一半：`copy/` 有了，但页面与组件里仍有约 64 处中文字面量、`api/*.ts` 里的 8 条"…暂时无法…，请稍后重试。"原样未动。

5. **服务端小债这次真还了。** `core/errors.py` 默认码改为 `FORBIDDEN / NOT_FOUND / CONFLICT`（项目模块显式传回 `PROJECT_*`）；`get_session` 收敛到 `core/db.py` 一份（7 → 1）；`project.list / project.read` 不再写 SUCCESS 审计；`GET /auth/me` 返回 `id`；审计与文件列表补了 `actor_name` / `uploader_name`；`Actor.project_ids` 的每请求查询删了。没还的：`is_test` 仍在接口与前端类型里（13 处）、`require_project_access` 成了死代码、`AppLayout.vue` / `OwnerHomePage.vue` 仍在仓库、验收 runbook 里的设备 / 连接器条目仍在。

6. **顺序建议：先修 N1、N2（都是十行以内的服务端改动，但决定霜月能不能用），再补 N3–N5，然后做 U1 的三页收尾（网盘、成员、对话页主按钮）与文案清扫，最后才是真实 LLM 手工验收。** 见第五章。

---

## 二、核查表：第二版旧问题现状

### 2.1 核查方式与实测结果

- 逐文件阅读 `git diff 045789b..d43f91b`（104 文件），并通读当前 `web/src`（64 文件 7858 行）与 `server/src/superboss/modules/agent/*`、`core/*`、各 `router.py`。
- 本机实测（非 CI）：
  - `server`：`ruff check src tests` 通过；PostgreSQL 16（`CREATE EXTENSION pg_trgm`）上 `pytest tests/unit tests/api` **330 passed**（第二版 326，新增 `test_commit_failed / test_recall / test_window` 4 例）。`ruff format --check` 报 29 个文件需格式化，但 CI 不跑 format，仅记录。
  - `web`：`npm ci` 成功；`npm run lint`（eslint + prettier）**通过**；`vue-tsc --noEmit` 通过；`vitest --run` **16 文件 131 用例全过**。
  - 另写了一组临时探针测试（已删除、未入库）在真实数据库上复现 N1–N4，结果记在第三章。
  - 未实测：真实 LLM 端点、浏览器观感、Playwright。

### 2.2 第二版 R1–R11（本轮新发现）

| 编号 | 现状 | 证据（已验证） | 残留 |
|---|---|---|---|
| R1 历史回放丢 `tool_call_id` / `tool_calls` | **PARTIAL** | `agent/service.py` L65-80 `replay_window_row` 按存储的 `tool_calls` 回放；L420-440 写入时 assistant 存 `{"tool_calls": [...]}`、tool 存 `{"tool_call_id": ...}`；`core/llm.py` L19-26 新增 `LLMRequestError`，L171-180 / L220-223 对 4xx/5xx 记 `warning` 并带响应体前 200 字；`tests/unit/agent/test_window.py` | **轮内顺序未定义**（N2）：同一事务内所有消息 `created_at` 相同（`agent/models.py` L120-122 `server_default=func.now()`），`_window` L460-477 `order_by(created_at.desc()).limit(16)` 后 `reverse()`，无二级排序键；实测窗口整轮倒序、tool 行先于 tool_calls 行。旧数据（`d43f91b` 之前的 tool 行）没有 `tool_call_id`，回放时被静默省略（L70-72），旧会话仍会 400 |
| R2 流式失败回退重复发送 | **FIXED** | `ChatPage.vue` L137-153：回退前 `listMessages` 比对最后一条用户消息是否已以本句开头 | 判定用 `startsWith`，附件场景用 `includes('附件')`，够用 |
| R3 附件默认进全员可见目录 | **FIXED** | `ChatPage.vue` L241-245 优先取 `老板私有`；`file_move` 卡目标为 `项目` 时 `cardFields.ts` L104-108 加警示行"可见范围将变为 全员" | 找不到 `老板私有` 时仍回落到 `项目`（L243），正常部署不会触发 |
| R4 滚动摘要从未生成 | **OPEN（且引入回归）** | `service.py` L793-834 新增 `maybe_summarize`，由 `extract_memories` L790 调用；`_run_turn` L376-380 把 `conversation.summary` 放进系统提示 | 见 N1：任务账号过不了 `_conversation` 归属校验，`maybe_summarize` 每次抛错，摘要一条都写不出，还连带丢掉本轮记忆。另外摘要不是增量：每次把 `count - 16` 条最早消息拼成 4000 字重算（L804-816），长会话中段内容永远进不了摘要 |
| R5 召回没用全文索引 | **PARTIAL** | `service.py` L83-99 `recall_needles`；L669-675 `ILIKE` × 关键词 ∪ `similarity() > 0.3` ∪ `search @@ plainto_tsquery('simple', …)`；去重改 `SequenceMatcher > 0.8`（L102-107）；纪要注入 7 → 3 条（L662）；`test_recall.py` 通过 | 见 N3：关键词最多 5 个且从句首切，虚词开头时丢掉真关键词；"昨天""那个"造成噪音召回。`plainto_tsquery('simple')` 对中文不分词，第三个条件基本无效 |
| R6 卡片 FAILED 不落库 | **FIXED（有副作用）** | `cards.py` L63-76 失败时置 `FAILED` 并 `return card`，不再抛；`confirm_card` L492-499 只在 `COMMITTED` 时写回执；`test_commit_failed.py` 通过 | 见 N4（重试 409）与 N5（无 savepoint，失败分支的部分写入会随 FAILED 一起提交） |
| R7 空"霜月"条目 / 回执被藏 | **FIXED（回执文案未映射）** | `service.py` L207-226 过滤空内容助手轮次、放出 `SYSTEM`；`ChatPage.vue` L312-314 回执渲染为居中灰字 | 回执内容仍是 `已入库：finance_entry`（`service.py` L497 写 `card.kind.value`）、`老板修改了卡片：…`（L522），违反戒律 4 |
| R8 服务端有、前端没接 | **FIXED** | `archive`：`agent.ts` L204-210，`ChatPage.vue` L104-112 会话行尾 `···` → 归档；`revise`：`ChatPage.vue` L202-209、`ProposalCard.vue` L165-167 / L175-185；`/finance/alerts`：`FinancePage.vue` L92、L225-227 一行警示色文字；知识库 `PATCH status/tags`：`KnowledgePage.vue` L94-102 发布 / 下架、L64-92 编辑抽屉；月度 token：新增 `GET /agent/usage`（`agent/router.py` L51-54，`service.py` L180-205），`SoulPage.vue` L107-111 显示 | 知识库"新建"不带标签（`api/knowledge.ts` L78-84 固定 `tags: []`）；成本预警只在有预警时出现，符合规格 |
| R9 每日纪要是拼接 | **FIXED** | `tasks.py` L86-118 调模型生成 ≤ 200 字，失败回落截断；`celery_app.py` L30 `crontab(hour=0, minute=30)`；`recall()` 纪要上限 3 | Celery `timezone="UTC"`（L46），0:30 UTC = 北京时间 08:30；`importance=4` 未变 |
| R10 CI 会红 | **FIXED** | 实测 `npm run lint` 通过 | — |
| R11 里程碑提醒横幅堆叠 | **FIXED（功能随之消失）** | `AppShell.vue` 无横幅；`ProjectsPage.vue` L41-46 到期 ≤ 14 天行尾警示圆点 | `GET /projects/reminders`（`projects/router.py` L50-57）现在无任何前端调用，方案 P1-4"霜月在会话开头提及"未做 |

### 2.3 第二版 E0 清单（第四章 1–10）

| # | 条目 | 现状 | 证据 |
|---|---|---|---|
| 1 | R1 回放 + 区分 `LLMRequestError` + 单测 | PARTIAL | 见上；单测只测 `replay_window_row` 单行，没测 `_window` 输出序列 |
| 2 | R3 附件 → `老板私有` | FIXED | `ChatPage.vue` L241-245 |
| 3 | R2 回退幂等 | FIXED | `ChatPage.vue` L137-153（客户端比对，未做 `Idempotency-Key`） |
| 4 | R6 FAILED 落库；R7 过滤 / 放出 SYSTEM | FIXED | 见上 |
| 5 | R10 prettier | FIXED | 实测 |
| 6 | R4 滚动摘要 | OPEN | N1 |
| 7 | R5 召回 | PARTIAL | N3 |
| 8 | R9 纪要 | FIXED | — |
| 9 | 默认错误码 / `get_session` 收敛 / GET 不写审计 / `AuthUserRead.id` | **FIXED** | `core/errors.py` L38-55；`core/db.py` L39-50，`rg -c 'async def get_session' server/src` 只剩 1 个文件；`projects/router.py` L44-47、L66-69 无 `commit_and_record_success`，`test_audit_events.py` L200-204 已改断言；`auth/schemas.py` L50 |
| 10 | 删 `require_project_access` / `project_ids` 查询 | PARTIAL | `core/actors.py` L52 每请求查询已删；但 `Actor.project_ids`（L25）与 `require_project_access`（L76-80）仍在，成为永远为空的死字段与死函数 |

### 2.4 第二版 F / S / T 遗留

| 编号 | 现状 | 证据 |
|---|---|---|
| F2 路由同步引入 | FIXED | `router.ts` 全部 `() => import()`（L98-197） |
| F3 登录页 `calc(100vh - 64px)` | FIXED | `LoginPage.vue` L81-86 `min-height:100vh; place-items:center`；改密页同 |
| F4 项目列表本地合并 | **OPEN** | `ProjectsPage.vue` L53-55 |
| F5 `\uXXXX` 写中文 | FIXED | 仅剩正则里的 `\u00a0`（不可见空白，合理） |
| F6 日期格式不统一 | PARTIAL | `api/parse.ts` L30-47 `dateLabel / dateShort / dateTimeShort` + `DateText` 基元，财务 / 项目 / 成员 / 审计 / SOUL / 记忆均已用；**卡片内** `due_on / occurred_on` 仍 ISO 直出（`cardFields.ts` L96-98） |
| F7 Element 无 locale | FIXED | `main.ts` L3、L15；`App.vue` L6；`index.html` L2 |
| F8 校验器复制 | FIXED | `api/parse.ts` 一份 |
| F9 36 位 request_id | FIXED | `http.ts` L94-97 取前 8 位 |
| S1–S4 | FIXED | 见 2.3 第 9 条 |
| T1 测试写中文原文 | PARTIAL | `chat-page.test.ts` 定位器改引 `chatCopy`（L116、L149、L174、L208），但全部 13 个测试文件仍含中文字面量（`rg -c` 合计约 240 行，与第二版持平；其中相当一部分是 fixture 数据，属合理） |
| T2 e2e 依赖 `is_test` | 已解除；`is_test` 本体 OPEN | `api/projects.ts` L35、L46、L150、L167、L181、L199-203；`ProjectsPage.vue` L82 固定传 `false`；服务端字段与迁移未删 |

### 2.5 第二版 C1–C17 廉价感来源

| # | 来源 | 现状 | 证据 |
|---|---|---|---|
| C1 写死色值 33 处 | FIXED | 仅 `FinancePage.vue` L441 `@media print { color:#000 }`（`base.css` L55 打印底色 `#fff` 属全局样式，可接受） |
| C2 角色枚举当眉题 | FIXED | 模板中无 `role` 插值 |
| C3 机制解释型副标题 | FIXED | 登录 / 改密 / 无权限 / 审计占位均删；成员页初始密码对话框只剩"关闭后不再显示。"一行（`UsersPage.vue` L251） |
| C4 UUID / 枚举直出 | PARTIAL | 页面模板清零；剩：审计页 `object_type` 列直出 `finance_entry / agent_card / project`（`AuditPage.vue` L75-79）；对话回执 `finance_entry`（R7）；未映射的审计 action 直出且不置灰（`copy/audit.ts` L20-22），且映射表缺 `project.milestones.replace / user.password.reset / auth.password.change / file.download / file.upload.start / user.projects.replace` |
| C5 页顶表单 + 卡片列表 | FIXED（网盘除外） | 财务 / 项目 / 成员 / 知识库表单进 `el-drawer`；网盘左栏仍常驻"新建子目录"表单（`DrivePage.vue` L291-303） |
| C6 `el-alert` ×15 | FIXED | 0 |
| C7 原生控件混排 ×15 | FIXED | 0 |
| C8 标题叠加 | FIXED | `MultipartUploader.vue` 无 h2 |
| C9 按钮文案暴露实现 | **OPEN** | `DrivePage.vue` L396-406"检查并获取下载""下载本次文件"，L380-382"确定移动"；`UsersPage.vue` L259-261"我已保存"（`membersCopy.saved`）；`drive-routing.test.ts` 8 处仍断言这些按钮 |
| C10 导航 9 项 | FIXED | `navigation.ts` L13-36；`AppShell.vue` L36-61 |
| C11 `globalThis.confirm` | FIXED | 0；改 `el-popconfirm`（`DrivePage.vue` L342-352，`UsersPage.vue` L196-208） |
| C12 request_id | FIXED | — |
| C13 验收脚手架 | PARTIAL | 勾选与 tag 已删；`is_test` 字段仍在（见 T2） |
| C14 字体 / lang / locale | FIXED | — |
| C15 每屏 2 个实心主按钮 | PARTIAL | 财务 / 项目详情 / 成员的 2 个分别在互斥的抽屉 / 对话框里，可接受；**对话页**每张 `PROPOSED` 卡一个实心（`ProposalCard.vue` L159-164）+ 底部实心"发送"（`ChatPage.vue` L370-376），多卡时一屏多个 |
| C16 卡片状态无区分 | FIXED | `ProposalCard.vue` L83-99 四种折叠行 |
| C17 日期 / 金额格式散落 | FIXED | `Money` / `DateText` / `moneyLabel` |

### 2.6 第二版 U0 十条 `rg` 验收（对 `d43f91b` 实跑）

| 命令 | 期望 | 实测 |
|---|---|---|
| `rg -n '#[0-9a-fA-F]{3,6}\b' web/src/pages web/src/components web/src/layouts` | 无 | 1 行（`FinancePage.vue` L441，打印） |
| `rg -n '<el-alert\|<el-card\|globalThis.confirm\|window.confirm' web/src` | 无 | **无** |
| `rg -n 'lang="en"' web/index.html` | 无 | **无** |
| `rg -n '\{\{\s*[a-z]+\.(role\|status\|state\|kind\|committed_object_type)\s*\}\}' web/src/pages` | 无 | **无** |
| `rg -n '\{\{[^}]*_id[^}]*\}\}' web/src/pages web/src/components` | 无 | **无** |
| `rg -c 'type="primary"' … \| awk '$2>1'` | 无 | 3 文件（财务 / 项目详情 / 成员，均在互斥容器内） |
| `rg -n '暂时无法\|请稍后重试' web/src --glob '!web/src/copy/**'` | 无 | **13 行**（`api/{projects,knowledge,files,finance,http,users,agent,audit}.ts` + `FinancePage.vue` L100） |
| `rg -n '<select\|<input type=\|<button\|<progress' web/src/pages web/src/components` | 无 | **无** |
| `rg -c 'function isRecord' web/src` 求和 | 1 | **1** |
| `rg -n "[\x{4e00}-\x{9fff}]" web/tests/chat-page.test.ts --pcre2` | 无 | 27 行（定位器已改常量，fixture 文本仍为中文） |

---

## 三、新问题 / 回归（按风险排序）

### 3.1 服务端

**N1（实测，回归，最高优先级）记忆抽取任务必然失败，长期记忆与滚动摘要双双归零。** `tasks.py` L28 用 `Actor(UUID(int=0), Role.OWNER)` 构造 `AgentService`；`extract_memories`（`service.py` L702-791）在 L790 新增调用 `maybe_summarize()`；后者 L796 调 `self._conversation(conversation_id)`，L141-144 校验 `conversation.owner_id != self.actor.subject_id` 即抛 `NotFoundError("CONVERSATION_NOT_FOUND")`。系统账号的 `subject_id` 永远不等于任何真实老板 id，所以**每次**抛错；异常冒出 `execute_memory_extract`（`tasks.py` L20-34），`session.commit()` 不执行，本轮刚 `add` 的记忆随 session 关闭丢弃；Celery `autoretry_for=(Exception,), max_retries=2` 再失败两次。探针测试用 20 条消息 + 假 LLM 复现：`pytest.raises(NotFoundError)`，`exc.value.code == "CONVERSATION_NOT_FOUND"`。现有测试没有一个走 `extract_memories`，所以 330 绿测不到。**修法**：`maybe_summarize` 直接 `session.get(AgentConversation, id)`，不做归属校验（任务上下文本来就是系统身份）；或任务里先查会话拿到 `owner_id` 再构造 `Actor`。**加一个走 `execute_memory_extract` 路径的单测。**

**N2（实测，高）同一轮消息 `created_at` 全相同，`_window` 轮内顺序未定义，实测整轮倒序。** `agent/models.py` L120-122 `created_at = server_default=func.now()`；PostgreSQL 的 `now()` 是事务开始时间，一轮对话（用户消息 + 若干 assistant/tool 行 + 最终回复）在一个 `get_session` 事务里提交，全部同一时间戳。`_window` L460-477 `order_by(created_at.desc()).limit(16)` 再 `reverse()`，对相同键没有二级排序。探针：一个事务写入 18 行（`distinct created_at = 1`），`_window` 返回 `[user 闲聊12, assistant 闲聊11, …, user 闲聊0, tool, assistant(tool_calls), user 列项目]`——**顺序完全颠倒**，`tool` 行在 `assistant.tool_calls` 行之前，并且最新两条（闲聊 13、14）不在窗口内。真实场景下上一轮 [用户 → assistant(tool_calls) → tool → 回复] 会以任意顺序回放，OpenAI / DeepSeek / Moonshot 对 tool 行前无对应 tool_calls 一律 400 → `LLMRequestError` → "霜月暂时离线"。`list_messages`（升序）碰巧得到插入序，但同样没有保证。**修法**：加 `seq BIGSERIAL`（或 Python 侧 `default=utcnow` 保证微秒递增）并在 `_window / list_messages / maybe_summarize` 的 `order_by` 加二级键；`_window` 取满 16 条后若首行是 `tool` 或带 `tool_calls` 的 assistant，向前补齐到最近一条 `user` 行；SYSTEM 回执行不应进入模型窗口（当前 L80 以 `role: system` 原样回放，部分兼容接口不接受会话中段的 system 消息）。**单测**：构造一轮真实形态的四行消息，断言 `_window` 输出里每个 `tool` 前一条是含匹配 id 的 assistant。

**N3（实测，中）`recall_needles` 截断丢关键词、虚词造成噪音。** `service.py` L83-99：对每个连续汉字串（≥ 2 字）先整体入列，长度 > 4 再按偶数偏移切 2 字，去重后**取前 5 个**。`recall_needles("帮我看看昨天那个合作项目")` 实测得 `['帮我看看昨天那个合作项目', '帮我', '看看', '昨天', '那个']`——"合作""项目"被截掉，记忆"星野合作是合作类项目"召不回；`test_recall.py` 用的句子"昨天那个合作项目现在什么状态"恰好让"合作"排第 4，所以通过。反过来 `recall("昨天那个合作项目")` 实测同时召回"昨天开了周会""那个新来的员工叫小李"。**修法**：停用词表（帮我 / 看看 / 昨天 / 今天 / 那个 / 这个 / 一下 / 现在 / 什么 / 状态 …）；切词后按"在记忆库中的命中数"而非句首位置排序，上限放到 8；去掉 `plainto_tsquery('simple')` 条件（中文无效），或迁移列改 `to_tsvector('simple', regexp_replace(content, '(.)', '\1 ', 'g'))` 做单字索引。

**N4（实测，中）入库失败卡的"重试"必报 409。** `ProposalCard.vue` L94-99 `FAILED` 折叠行的"重试"触发 `emit('confirm')` → `confirm_card`；`service.py` L481-482 只接受 `PROPOSED`，`FAILED` 一律 `ConflictError("CARD_NOT_OPEN")`。探针：第一次确认得 `FAILED`，第二次 `pytest.raises(ConflictError)`，code `CARD_NOT_OPEN`。**修法**：`confirm_card / patch_card` 放行 `status in {PROPOSED, FAILED}`，重试前把 `error` 清空；或前端隐藏"重试"改为"直接改"。

**N5（已验证，中）`commit_card` 吞掉异常但没有 savepoint，失败分支的部分写入会被提交。** `cards.py` L63-76 捕获后 `return card`，router 的 `get_session`（`core/db.py` L39-50）看到正常返回就 `commit()`。`_dispatch` 里有多步写入的 kind：`project_create` 先 `create` 再 `replace_milestones`（L125-139），前者成功后者失败 → 项目已建、卡片却是 `FAILED`；`milestone_change` 先就地改 `target.title/due_on/done_at`（L150-166）再 `replace_milestones`（L186-188）。`ProjectService.create/update` 的 `IntegrityError` 分支还会 `session.rollback()`（`projects/service.py` L113-118、L183-188），把同事务里其他未提交改动一起回滚。**修法**：`commit_card` 用 `async with session.begin_nested():` 包住 `_dispatch`，失败自动回到 savepoint 再置 `FAILED`。

**N6（已验证，低）滚动摘要不增量、窗口越界。** `maybe_summarize` L804-816 每次取最早 `count − 16` 条拼成一段并截到 4000 字重算；不带上一次的 `summary`，会话超过约 40 轮后中段永远进不了摘要。修法：输入 = 旧 `summary` + 上次摘要以后的新出窗消息（需记录 `summarized_until`），输出替换。

**N7（已验证，低）纪要时区。** `celery_app.py` L46 `timezone="UTC"`，`crontab(hour=0, minute=30)` 是北京时间 08:30；`tasks.py` L73 "昨日" = 过去 24 小时。若老板早上 8 点先聊了几句，会被算进"昨日"。改 `timezone="Asia/Shanghai"` 即可。

**N8（已验证，低）死代码与残留。** `core/actors.py` L25 `Actor.project_ids` 永远为空、L76-80 `require_project_access` 无人调用；`ProjectMember` 表与 `PUT /owner/users/{id}/projects` 仍在但前端已不传项目（`UsersPage.vue` L66 固定 `project_ids: []`）——决策点 L 未执行；`is_test` 见 T2；`docs/runbooks/m1-owner-acceptance.md` L135-143 设备 / 连接器条目仍在。

### 3.2 前端

**N9（已验证）网盘页两个新组件未接线、旧结果块未删。** `DropZone.vue` 只 `emit('files')`，`DrivePage.vue` L257 `<DropZone>` 没有监听 `@files`，拖到页面任意位置什么都不发生（只有 `el-upload` 自己的方框可拖）；`UploadTray` 的 `tray` 只在 L38 初始化、L217 / L228 清空，从未 push，浮层永远不出现；`useMultipartUpload.ts` 只是两行 re-export，不是组合式函数。上传完成后 L396-406 的 `result` 块仍显示"检查并获取下载""下载本次文件"（C9），因为 `drive-routing.test.ts` 8 处仍在断言这些按钮。

**N10（已验证）成员页动作重复。** `UsersPage.vue` L160-189 的 `···` 菜单（改角色 / 重置密码 / 禁用 / 启用）与 L190-214 的三个内联文字按钮（重置密码 / 禁用 / 启用）同时渲染，同一动作出现两次；空态文案用 `membersCopy.title`（"成员"）当消息（L218）。

**N11（已验证）卡片细节。** `ProposalCard.vue` L86 已入库行链接文字固定为"财务"，项目 / 网盘 / 知识 / 记忆卡都写"财务"但跳到别处；`cardFields.ts` L96-98 日期 ISO 直出；`ProposalCard.vue` L138-143 无 `editFields` 的 kind（`file_move`）"直接改"时以原始键名（`file_id / target_folder_id / new_name`）为标签；卡片字段标签、Element 下拉选项文案（`cardFields.ts` L55-110、L121-183）与 `FinancePage.vue` L304-313、L344-351、L371-375 的 `el-option` 均为内联字面量，未进 `copy/` 与 `glossary`；卡头 L102-104 同时显示 kind 与"待确认"，与下方四个动作重复表达状态。

**N12（已验证）文案层只做了一半。** `web/src/pages` + `components` 内约 64 处中文字面量在 `copy/` 之外（`FinancePage.vue` L100 / L110 / L114 / L146 / L158、`DrivePage.vue` L150 / L190 / L199 / L210、`MultipartUploader.vue` L36-39 / L62 / L70、`ProjectsPage.vue` L176"创建项目"等）；`api/*.ts` 里 8 条"…暂时无法…，请稍后重试。"原样保留（2.6 表第 7 行）；`copy/pages/auth.ts` L5 "用户名或密码错误，请重试。"与第二版文案"用户名或密码不正确"不一致；`membersCopy.saved`"我已保存"、`driveCopy.confirmMove`"确定移动"仍是第二版点名的实现暴露型文案。

**N13（已验证）项目页小 bug。** `ProjectsPage.vue` L80-85 新建时收集了开始 / 到期日期（L157-168 两个日期选择器）但**没有传给接口**，填了就丢；F4 本地合并仍在（L53-55）。`ProjectDetailPage.vue` L164-194 要把已有里程碑标记完成，必须先点"添加"（会追加一个空行）再勾选，没有单行操作。

**N14（已验证）审计页映射不全。** 见 C4；另外 `AuditPage.vue` L19 下拉选项来自映射表，未映射的 action 既不能筛选也不显示中文。

**N15（已验证）未捕获的 Promise。** `KnowledgePage.vue` L94-102 `publish / unpublish`、`MemoryPage.vue` L63-75 `togglePin / archive` 没有 `try/catch`，接口失败时页面无提示、控制台报错；`MemoryPage.vue` L110 已置顶的记忆菜单项仍叫"置顶"。

**N16（已验证）`AppLayout.vue`、`OwnerHomePage.vue` 仍在仓库**（前者仅被 `drive-routing.test.ts` L9 / L118 引用），`router.ts` L195-229 的 `/users` 与三条 `/owner/*` 兼容重定向仍在——第二版说 U1 结束后删除，现在 U1 基本结束了。

---

## 四、UI 观感复核

### 4.1 达到的部分（已验证，源码层面）

- **体系**：`tokens.css` 与第二版 3.3 逐值一致（纸色 `#F7F6F2`、墨蓝灰强调 `#2E3A46`、三态色仅用于文字与圆点）；`element.css` 覆盖了主色九级、文字四级、边框、底色、圆角、阴影归零；字体栈与六档字号、`tabular-nums` 类都有。所有页面与组件只引用变量。
- **壳**：顶栏 13px 字标 + 5 项文字导航 + 姓名下拉，1px 底线，无图标；内容宽两档（880 / 1120）由路由 `meta.width` 给；打印样式隐藏壳与操作区。
- **页头**：`PageHeader` 28px 标题 + 右侧文字动作，无眉题、无副标题。
- **列表**：项目 / 网盘 / SOUL 版本 / 记忆为 48px 行 + 1px 细线；财务 / 成员 / 审计用 `el-table` 无 `stripe`；空态一行字。
- **表单**：全部进 `el-drawer`（400 / 480px）；初始密码对话框 20px 等宽 + 复制。
- **登录 / 改密 / 无权限**：整页纸色、左上字标、360px 单列居中、28px 标题、无卡片无说明。
- **对话页**：会话栏分组（今天 / 本周 / 更早）+ `···` 归档 + 无按钮搜索框；消息为 13px 发言人 + 16px/1.75 正文，无气泡，32px 间距；回执居中灰字；离线为一行灰字；`ProposalCard` 细线框 24px 内边距，四种折叠状态，浅绿 / 浅红底只用于折叠行。
- **财务**：一行大数（28px 等宽）+ 细线；表格金额右对齐；`‹ 2026年9月 ›` 月份切换。
- **知识库**：左 280 列表 + 右正文，Markdown 渲染（`markdown-it`，`html:false`），28px 标题 + 13px 灰元信息。

### 4.2 仍显廉价的地方（按扎眼程度）

1. **网盘页**（最扎眼）：每行"下载 重命名 移动 删除"四个文字按钮平铺；重命名 / 移动在行内展开一整套表单；上传完毕出现"检查并获取下载 / 下载本次文件"两颗按钮和一段状态文字；左栏常驻"新建子目录"标签 + 输入框 + 按钮；面包屑是一排 `el-button text`；文件行用 `flex-wrap` 而非对齐的表格列，大小 / 日期 / 上传者不成列。这一页离第二版 3.5 的目标最远。
2. **成员页**：`···` 菜单和三个内联按钮并存，每行 4 个可点元素。
3. **对话页**：每张待确认卡四个动作（确认入库 / 修改 / 直接改 / 放弃）+ 卡头"记一笔 · 待确认"，多卡叠加时一屏出现多个实心按钮；📎 emoji 随系统字体变化，在 Windows 上会是彩色图标；回执"已入库：finance_entry"是英文枚举。
4. **文案层半成品**：出错时仍能看到"财务数据暂时无法加载，请稍后重试。""霜月暂时无法完成操作，请稍后重试。（500，1a2b3c4d）"这类第二版禁掉的句式。
5. **Element 残留（推断，未截图）**：`el-table` 表头默认加粗灰底、`el-button text` 的悬停底色、`el-drawer` 标题栏默认字号，`element.css` 没有覆盖 `--el-table-header-bg-color / --el-table-header-text-color / --el-button-hover-*`；`.plain-table` 类只设了 `width:100%`（`FinancePage.vue` L425-427），名字暗示的"细线无边框"并没有实现。需要浏览器截图确认。
6. **小处**：SOUL 页标题仍是英文 "SOUL"；知识库元信息无标签时显示"· · 草稿"（`KnowledgePage.vue` L162-167）；财务页"项目 × 月"标题下其实是单月的按项目汇总，不是透视（`FinancePage.vue` L272-294）；项目新建按钮"创建项目"与其他页的"保存"不一致；成员空态文案是"成员"。

### 4.3 结论

按第二版十条戒律逐条对照：**1（一屏一个实心）对话页不满足；2、3、6、7、8、9、10 满足；4（不出现枚举 / 字段名）审计 `object_type`、回执、卡片日期三处不满足；5（不解释机制）满足。** 观感从"功能验证台"进到"有体系但收尾粗糙"，距离"高级感"差的不是方向而是三页收尾和一次文案清扫。

---

## 五、下一步迭代（短、可执行）

每条一个 PR，先服务端后前端；E1 完成前不要开始真实 LLM 验收。

**E1 — 服务端修正（阻塞霜月可用性）**

1. **N1**：`maybe_summarize` 改为 `session.get(AgentConversation, id)` 不做归属校验；新增 `tests/unit/agent/test_extract_task.py`，用假 LLM 走 `execute_memory_extract` 全路径，断言记忆条数 > 0 且 `conversation.summary` 非空（消息数 > 16 时）。
2. **N2**：`agent_messages` 加 `seq BIGSERIAL`（迁移 `0009`），`_window / list_messages / maybe_summarize / extract_memories` 的 `order_by` 加 `seq`；`_window` 取满后向前补齐到最近 `user` 行；SYSTEM 行不进模型窗口。单测：一轮四行形态，断言 tool 前一条是含匹配 id 的 assistant，且首行是 user。
3. **N3**：`recall_needles` 加停用词、上限 8、按命中数排序；删 `plainto_tsquery` 条件。单测补 `"帮我看看昨天那个合作项目"` 必须命中"星野合作"，`"昨天那个"` 不得命中"昨天开了周会"。
4. **N4 + N5**：`confirm_card / patch_card` 放行 `FAILED`；`commit_card` 用 `begin_nested()`。单测：`project_create` 带非法里程碑失败后 `projects` 表无新行。
5. **N6、N7**：摘要增量化（加 `summarized_until` 列或用 `seq` 水位）；Celery `timezone="Asia/Shanghai"`。
6. **回执文案**：SYSTEM 回执改存 `已入库 · 记一笔 房租 ¥ 8,000.00`（服务端用 `CARD_KIND` 中文表 + 卡片 headline），或前端按 `card_ids` 关联渲染。
7. **清理**：删 `Actor.project_ids / require_project_access`；删 `is_test`（模型、迁移、schema、前端类型、`ProjectsPage.vue` L82）；删 `AppLayout.vue / OwnerHomePage.vue` 并改 `drive-routing.test.ts` 用 `AppShell`；runbook L135-143。

**验收**（命令输出为准）：

```bash
cd server && pytest tests/unit/agent -q                                   # 全绿，含新增 3 个测试
rg -n 'UUID\(int=0\)' server/src/superboss/modules/agent/tasks.py        # 允许存在，但 maybe_summarize 不再依赖 actor
rg -n 'is_test' server/src web/src                                        # 无输出
rg -n 'require_project_access|project_ids' server/src/superboss/core      # 无输出
```

**U1 收尾 — 三页 + 文案（前端）**

8. **网盘**：行尾 `···`（下载 / 重命名 / 移动 / 删除），重命名行内、移动进抽屉；删 `result` 块与 `drive-routing.test.ts` 对"检查并获取下载"的 8 处断言（改为断言列表刷新）；`DropZone @files` 接 `useMultipartUpload`（改成真正的组合式函数）并把进度推进 `tray`，或者删掉这两个组件；"新建子目录"进当前目录 `···`；文件行改 `el-table`（名称 · 大小 · 日期 · 上传者）。
9. **成员**：删 L190-214 内联按钮；空态文案"还没有成员"；`saved` → "关闭"。
10. **对话页**：`ProposalCard` 的"确认入库"改默认样式、底部"发送"保留唯一实心（或反之，二选一写进 `chatCopy` 注释）；卡头去掉"待确认"；日期走 `DateText`；已入库链接文字按 kind（财务 / 项目 / 网盘 / 知识库 / 记忆）；`file_move` 补 `editFields`；📎 换 16px 内联 SVG。
11. **文案清扫**：把 `rg -n "'[\x{4e00}-\x{9fff}][^']*'" web/src/pages web/src/components --pcre2` 的 64 处迁到 `copy/`；`api/*.ts` 的 8 条 fallback 改引 `errorCopy.generic`；`authCopy.loginFailed` 改"用户名或密码不正确"；卡片字段标签与 `el-option` 文案引 `glossary`。
12. **审计**：`copy/audit.ts` 补齐 3.2 N14 列出的 action，加 `AUDIT_OBJECT_LABEL`（`finance_entry → 账目` 等）；未映射 action 置灰。
13. **项目**：新建时传 `starts_on / due_on`；删 F4 合并逻辑（创建成功后直接 `loadProjects()`）；里程碑行尾 `···`（完成 / 编辑 / 删除）。
14. **知识 / 记忆**：`publish / unpublish / togglePin / archive` 加 `try/catch` 写 `errorMessage`；置顶项菜单显示"取消置顶"；元信息无标签时不输出中间的 `·`；新建带标签。
15. **Element 覆盖补齐**：`element.css` 加 `--el-table-header-bg-color: transparent`、`--el-table-header-text-color: var(--sb-ink-2)`、`--el-table-border-color: var(--sb-line)`、`--el-button-text-hover-bg-color: transparent`；`base.css` 给 `.plain-table` 真正的细线无边框样式。

**验收**：第二版 U0 十条 `rg` 全部为零（含 `暂时无法|请稍后重试` 与 `type="primary"` 计数）；`rg -n '检查并获取下载|下载本次文件|我已保存|确定移动' web/src web/tests` 无输出；1280 宽截图六页贴 PR。

**U2 — 真实 LLM 与视觉回归**

16. 配好 `SUPERBOSS_LLM_*`，同一会话连续三轮触发工具（"列一下项目" → "给星野加个 10 月 20 日交付节点" → "这个月房租 8000"）均不离线；第二天新会话问"昨天那个合作项目"能答出名字；`/memory` 页出现本轮抽取的记忆。这是 E1 的最终验收。
17. Playwright 六页截图入库（第二版第四章 23）。

---

## 六、暂不做 / 决策点

**暂不做**（沿用第二版第六章，全部仍成立）：深色模式、图表、插图空态、导航图标、Webfont、国际化、pgvector（N3 修完实测仍不够时再议）、员工侧霜月、目录级 ACL、外部 API 令牌、卡片自动入库。

**本轮新增：**

- `ruff format` 的 29 个文件不在此轮处理（CI 不检查，改了会污染 diff）。
- `SYSTEM_ACTOR` 记忆抽取改为按会话 owner 构造 `Actor`（比 N1 的最小修法更"正确"，但涉及任务签名）——等 N1 最小修法落地并有测试后再评估。
- 会话内 SYSTEM 消息（"老板修改了卡片：…"）是否保留为对话可见回执——保留，但不进模型窗口（N2 修法已含）。

**待老板拍板**（编号接续第二版 F–M）：

| 编号 | 问题 | 本文默认 |
|---|---|---|
| K | `/users` → `/members`、删 `/owner/*` 重定向 | 已改路径；**本轮删重定向**（E1 第 7 条） |
| L | `project_members` 表与接口 | 前端已不传；**删表与接口**（迁移 `0009` 一并处理），项目"参与人"以后按需再加 |
| M | 每日纪要调模型 | 已做；时区改上海（N7） |
| N（新） | 财务"毛利"口径：当前 = 公司运营收入 − 公司运营成本，不含项目收入与项目成本（`FinancePage.vue` L82，与 `f2ad4fe` 一致） | 改为 (公司收入 + 项目收入) − (公司成本 + 项目成本)，并把 STAFF 不可见的部分在服务端置 `null` 而非前端相减 |
| O（新） | 入库失败卡的"重试"语义：原卡重新确认（N4 修法）还是让霜月出新卡 | 原卡重新确认；连续两次失败后折叠行只留"直接改" |
| P（新） | 对话页唯一实心按钮给"发送"还是给"确认入库" | 给"确认入库"（写操作更重要），发送改为 ↑ 图标默认样式 |
| Q（新） | 是否给 `agent_messages` 加 `seq` 列（需迁移）还是只改 Python 侧 `created_at` 默认值 | 加 `seq`；时间戳继续用数据库时间，避免多 worker 时钟漂移 |
