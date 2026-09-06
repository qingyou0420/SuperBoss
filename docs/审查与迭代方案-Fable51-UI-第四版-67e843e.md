# SuperBoss 审查与迭代方案：修复后记分卡（Fable 5.1，第四版）

日期：2026-09-06。基于 **`feature/p0-drop-devices-imports@67e843e`**（提交 "feat: complete Fable v3 E1/U1, drop project_members, and add visual plus LLM gates"，相对上一 tip `2563abf` 改动 85 个文件，+1401 / −744 行；相对第三版基线 `d43f91b` 的代码改动同为这一个提交）的逐文件核查与本机实测。

**本文是第三版（[`审查与迭代方案-Fable51-UI-第三版-d43f91b.md`](./审查与迭代方案-Fable51-UI-第三版-d43f91b.md)）的后续，只回答一个问题：第三版列出的 N1–N16、网盘 / 成员 / 对话页三处收尾、以及决策点 K–Q，在 `67e843e` 里哪些修好了、哪些修了一半、哪些没动，又引入了什么。** 第二版第三章的 UI 规格仍是唯一规格；第三版第五章的 E1 / U1 / U2 清单是本文对照的底稿。

标注约定同前：**（已验证）** 附文件与行号，行号以 `67e843e` 为准；**（实测）** 指在本机 PostgreSQL 16 + 真实 alembic 迁移上跑过；**（推断）** 指只读源码、未上浏览器或真实 LLM。

---

## 一、摘要

给老板的一页纸：

1. **第三版最要紧的两个问题（N1 记忆抽取必失败、N2 窗口顺序颠倒）真的修好了。** `maybe_summarize` 不再走归属校验（`service.py` L887-889），新单测用系统账号走完 `extract_memories` 并断言记忆条数 > 0、摘要非空；`agent_messages` 加了 `seq` 列（迁移 `0009`），窗口按 `seq` 取、排除 SYSTEM 行、取满后向前补齐到最近一条 `user` 行。实测 20 行同一事务写入（`created_at` 只有 1 个取值），窗口首行是 `user`，每个 `tool` 前一条都是带匹配 id 的 `assistant`。**霜月"用过工具第二轮就离线"和"永远没有长期记忆"这两条现在可以从风险清单上划掉。**

2. **N3–N7 里，四个修好、两个修了一半。** N4（FAILED 卡重试 409）服务端已放行，N6 摘要改成水位增量，N7 时区改上海，N8 死代码 / `is_test` / `ProjectMember` / runbook 全清。**N3 召回**加了停用词与按命中数排序，第三版举的两句都过了，但分词仍是"偶数位切两字 + 尾巴两字"——"给星野加个 10 月 20 日交付节点"（正是新验收脚本的第二轮）切出的是 `给星 / 野加 / 加个 / 日交`，**"星野""交付"都不在**，实测 `recall("给星野加个交付节点")` 返回空。**N5 savepoint** 对 `NotFoundError` 类失败有效（新单测覆盖），但第三版点名的 `ProjectService.create/update` 里的 `IntegrityError → session.rollback()` 没改：实测确认一张与现有项目同名的 `project_create` 卡，整个请求事务被回滚、卡片对象过期，`CardRead.model_validate` 抛 `MissingGreenlet`，接口 500，卡片停在 PROPOSED。这不是回归（`d43f91b` 同样 500），但 N5 只能算修了一半。

3. **前端收尾：成员页与对话卡片基本到位，网盘页几乎没动。** 成员页三个内联按钮删了，只剩 `···`；卡片已入库链接按 kind 显示、日期走 `dateLabel`、`file_move` 有了"直接改"字段、卡头不再重复"待确认"、发送按钮改为默认样式的 `↑`（决策 P 采纳）；回执改成 `已入库 · 记一笔 房租 ¥ 8,000.00`；`AppLayout / OwnerHomePage` 删了；文案层收口——`pages / components / layouts` 里中文字面量归零，只剩 `api/*.ts` 里 8 个错误前缀。**网盘页**只接了 `DropZone → useMultipartUpload → UploadTray` 这条线，每行四个文字按钮、行内展开的重命名 / 移动表单、左栏常驻"新建子目录"表单、上传结果块（两颗按钮只是从"检查并获取下载 / 下载本次文件"改名为"下载 / 下载"）、面包屑一排 `el-button`、`flex-wrap` 文件行——第三版 U1 第 8 条列的七件事做了一件。

4. **新加的"视觉门 + LLM 门"，现在都还不是门。** 视觉：`tests/e2e/specs/visual-pages.spec.ts` 六页 1280 截图 + 六张 `*-win32.png` 基线。CI（`.github/workflows/ci.yml`）不跑 Playwright，基线带 `win32` 平台后缀在 Linux 上直接找不到；更要紧的是**成员页基线本身是一张出错的页面**——红字"操作失败。"、Element 默认"暂无数据"和自家"还没有成员"三行叠在一起（见 4.2）。LLM：`server/scripts/llm_three_round.py` 是一个手工脚本，三轮 + 隔会话召回 + 等待记忆，判定标准合理；但它**会自动确认所有 PROPOSED 卡**（写入真实的 ¥8,000 房租、真实里程碑、必要时新建"星野合作"项目），只能对着一次性数据库跑，脚本头部没有任何提示。

5. **`project_members` 删除对 STAFF / MANAGER 权限零影响。** `d43f91b` 已经把 `Actor.project_ids` 的查询删掉、STAFF 看全部项目（`test_staff_sees_all_projects_and_cannot_create` 早已如此），权限模型是"角色 + 目录可见性 + 账目可见性"三层，成员表在那之后就没人读。本提交删的是死表、死接口（`PUT /owner/users/{id}/projects` → 404，`project_ids` 入参 → 422，均有测试）、死字段（`OwnerUserRead.projects`）与 7 个只测成员表的用例（330 → 329）。历史审计行的 `user.projects.replace` 保留了中文映射。

6. **新问题里值得先修的三个**：N17 入库失败卡两次重试后"直接改"按钮点了没反应（编辑表单在 FAILED 分支之外），且失败原因 `card.error` 从不展示；N18 成员"禁用"从 `···` 菜单一点即生效、无二次确认（原来只有内联按钮有 popconfirm，这次连内联按钮一起删了）；N19 `element.css` 里 `.plain-table :deep(...)` 写进全局 CSS，构建产物原样输出 `:deep`，浏览器丢弃整条规则。其余见第三章。

7. **顺序建议**：先把 N17 / N18 和 N5 的 `rollback` 分支修掉（都是十几行），再把视觉基线改成可在 CI 上跑的形态并**重新抓成员页**，然后专做网盘页一轮，最后才用 `llm_three_round.py` 对着一次性库做真实 LLM 验收。见第五章。

---

## 二、核查表：第三版旧问题现状

### 2.1 核查方式与实测结果

- 逐文件阅读 `git diff 2563abf..67e843e`（85 文件），并通读 `server/src/superboss/modules/agent/{service,cards,models,tasks}.py`、两份新迁移、`web/src/pages/**`、`web/src/components/chat/**`、`web/src/components/files/**`、`web/src/styles/element.css`、`tests/e2e/specs/visual-pages.spec.ts`、`server/scripts/llm_three_round.py`。
- 本机实测（非 CI）：
  - `server`：`ruff check src tests` 通过；PostgreSQL 16（`pg_trgm`）上经 `alembic upgrade head`（含 `0009 / 0010`）后 `pytest tests/unit tests/api` **329 passed**（第三版 330；本次 −5 个成员表用例、+4 个新用例：`test_extract_task`、`test_window_order`、`test_recall::test_recall_keeps_keywords_after_polite_prefix`、`test_commit_failed::test_failed_project_create_does_not_leave_a_project`）。`ruff format --check` 仍报 29 文件，CI 不查，仅记录。
  - `web`：`npm ci`、`npm run lint`（eslint + prettier）、`vue-tsc --noEmit` 均通过；`vitest --run` **16 文件 131 用例全过**；`vite build` 通过（用于核对 N19 的构建产物）。
  - 另写一组临时探针测试（已删除、未入库）在真实数据库上复现 N2 / N3 / N4 / N5 的修复效果，并在 `d43f91b` 的 worktree 上对照跑了 N5 的 `IntegrityError` 场景，结果记入第三章。
  - **CI 状态**：`67e843e` 没有任何 CI 运行——`ci.yml` 只在 `pull_request` 与 `push: master` 触发，特性分支直推不触发（`gh run list --branch feature/p0-drop-devices-imports` 为空）。上面的绿都是本机的。
  - 未实测：真实 LLM 端点、浏览器观感（六张 `win32` 基线截图已逐张查看，作为观感证据）、Playwright 实跑。

### 2.2 第三版 N1–N16

| 编号 | 第三版结论 | 现状 | 证据（已验证 / 实测） | 残留 |
|---|---|---|---|---|
| **N1** 记忆抽取必失败 | 回归，最高 | **FIXED** | `service.py` L884-889 `maybe_summarize` 改 `session.get(AgentConversation, id)`，`None` 即返回；`tests/unit/agent/test_extract_task.py` 用 `Actor(UUID(int=0), OWNER)` + 假 LLM 走 `extract_memories`，断言 `count > 0` 且 `summary` 非空（18 条消息 > `_WINDOW`）。实测通过 | 单测直接调 `AgentService.extract_memories`，没走 `tasks.execute_memory_extract`（L20-34），但两者之间只差一个 `session.commit()`，可接受 |
| **N2** 轮内顺序未定义 | 高 | **FIXED** | 迁移 `0009_message_seq.py` L18-22 `seq BIGSERIAL NOT NULL` + `(conversation_id, seq)` 索引；`models.py` L124 `Identity`；`_window` L523-556：`role != SYSTEM`、`order_by(seq.desc())`、取满后 `while rows[-1].role is not USER` 向前补齐；`list_messages` L281、`extract_memories` L804、`maybe_summarize` L903 / L919 均改 `seq`；`test_window_order.py`。实测：一个事务写 25 行（5 轮 × [user, assistant.tool_calls, tool, assistant, system]），`distinct created_at = 1`，`seq` 严格按 `session.add` 顺序递增；`_window` 返回 16 行、首行 `user`、无 `system`、每个 `tool` 前一条是含匹配 id 的 `assistant` | 迁移对**已有** `agent_messages` 行按物理顺序填 `seq`，不保证与 `created_at` 一致（低风险，老会话通常插入序即物理序）。ORM 用 `Identity`、迁移用 `BIGSERIAL`，两者行为一致但声明不同 |
| **N3** 召回截断 / 噪音 | 中 | **PARTIAL** | `service.py` L83-109 `_STOPWORDS`（23 个）；L123-146 `recall_needles`：停用词过滤、`len > 4` 时偶数位切两字 + **尾巴两字**、按长度降序去重、上限 8；`recall` L748-764 逐词 `count(ilike)` 计命中，按 `(−命中, −长度)` 排序取 8，全零时回落到非停用词；删掉 `similarity()` 与 `plainto_tsquery`；`test_recall.py` 新增用例。实测：`"帮我看看昨天那个合作项目" → ['帮我看看昨天那个合作项目','合作','项目']`、`"昨天那个" → ['昨天那个']`（不再命中"昨天开了周会"） | 实测 `"给星野加个 10 月 20 日交付节点" → ['给星野加个','日交付节点','给星','野加','加个','10','20','日交']`——**无"星野"、无"交付"**；`recall("给星野加个交付节点")` 返回 `[]`；`"看一下星野项目" → ['看一','下星','野项','项目']`，靛"项目"这个泛词把"老板偏好项目按季度复盘"也拉进来。根因：只切偶数偏移的两字，奇数位起的关键词永远切不出来。停用词表里出现 `"列一"` 这种为单句定制的条目（L98）。另：每个 needle 一条 `count` 查询，一轮最多 ~10 次往返 |
| **N4** FAILED 重试 409 | 中 | **FIXED（服务端）** | `confirm_card` L560-562、`patch_card` L592-594 放行 `{PROPOSED, FAILED}` 并清 `error`。实测：第一次确认 `FAILED / FINANCE_PROJECT_NOT_FOUND`，第二次不再 409，重新走 `commit_card` | 前端流程有死角，见 N17。`reject_card` L584 / `revise_card` L610 仍只收 PROPOSED，FAILED 卡不能"放弃"也不能"修改"——只剩重试与（失效的）直接改 |
| **N5** 无 savepoint | 中 | **PARTIAL** | `cards.py` L64-68 `async with session.begin_nested()` 包住 `parse + _dispatch`；`test_commit_failed.py::test_failed_project_create_does_not_leave_a_project` 用 monkeypatch 让 `replace_milestones` 抛 `NotFoundError`，断言 `projects` 无残留。实测通过 | 第三版点名的 `ProjectService.create` L112-113 / `update` L182-183 `except IntegrityError: await self.session.rollback()` **未改**。实测（卡片已提交、在新 session 里 `confirm_card`）：`project_create` 同名 → `rollback()` 回滚**整个**会话事务（savepoint 一起没了）→ `card` 过期 → `commit_card` 给过期对象赋值后 `CardRead.model_validate` 触发隐式 IO → `MissingGreenlet` → 未处理异常（500）；同一请求里其它未提交写入也丢。`d43f91b` 同样结果，**非回归**。`file_move` 走的 `FileService.patch_file`（L434）无 `rollback`，不受影响 |
| **N6** 摘要不增量 | 低 | **FIXED** | `models.py` L87 `summarized_until`；`maybe_summarize` L897-909 取窗口下沿 `seq`、L910-922 只取 `watermark < seq < window_floor`、L928-929 把旧摘要拼进输入、L945-946 写回水位 | `older` 未排除 SYSTEM 行（回执会进摘要输入）；`new_text[:4000]` 截断后仍把 `max(seq)` 记为已摘要，超长段落中被截掉的消息永远不会再被摘要（低） |
| **N7** 纪要时区 | 低 | **FIXED** | `celery_app.py` L46 `timezone="Asia/Shanghai"` | — |
| **N8** 死代码 / 残留 | 低 | **FIXED** | `core/actors.py` 删 `project_ids`（L25）与 `require_project_access`（L76-80）；`rg -n 'is_test' server/src web/src` 无输出（迁移 `0009` L26 `DROP COLUMN`，schema / 前端类型 / 测试 fixture 全清）；`ProjectMember` 模型与 `PUT /owner/users/{id}/projects` 删除（迁移 `0010`）；`docs/runbooks/m1-owner-acceptance.md` 删设备 / 连接器四行 | `router.ts` L195-229 `/users` 与三条 `/owner/*` 兼容重定向仍在（决策 K 说"本轮删"，未删） |
| **N9** 网盘两组件未接线、结果块未删 | 前端 | **PARTIAL** | `useMultipartUpload.ts` L11-48 改成真正的组合式函数（`tray / upload / clearTray`）；`DrivePage.vue` L267 `<DropZone @files="onDropped">`，L252-258 `onDropped` 逐文件上传并 `showCompleted`；`UploadTray` 有内容了 | `result` 块 L399-409 **原样保留**，两颗按钮只改文案：`el-button` 与 `<a>` 都叫"下载"（`drive-routing.test.ts` L189 / L200 / L246 改成断言"下载"）。其余见 4.2 第 1 条。两条上传路径并存（`el-upload` 方框走 `MultipartUploader` 自己的进度文字，拖放走 `tray`），一页两套进度表现 |
| **N10** 成员页动作重复 | 前端 | **FIXED（引入 N18）** | `UsersPage.vue` L157-185 只剩 `···` 菜单（"禁用"在 L178-181）；L186 空态改 `membersCopy.empty`（"还没有成员"）；`users-page.test.ts` L190-193 / L207-210 改为点菜单项 | 删掉的内联"禁用"带 `el-popconfirm`，菜单项从来没有——现在禁用无确认，见 N18。`membersCopy.disableConfirm`（L17）成死文案 |
| **N11** 卡片细节 | 前端 | **FIXED** | `cardFields.ts` L218-224 `committedLabel(kind)`，`ProposalCard.vue` L93-95 用它；`cardFields.ts` L105 / L107 `due_on / occurred_on` 走 `dateLabel`；L212-214 `file_move` 有 `editFields`（`new_name`）；`FIELD_LABEL` / `FINANCE_SCOPE_LABEL` / `VISIBILITY_LABEL` / `STAGE_LABEL` 替换所有内联标签与选项；`ProposalCard.vue` L117-119 卡头只剩 kind；`FinancePage.vue` L303-373 `el-option` 全部引 glossary | `CARD_STATUS_LABEL`（`glossary.ts` L56）已无人引用；`ProposalCard.vue` L153-161 无 `editFields` 的 kind 仍以原始键名做标签（现在只剩 `finance_adjust` 之外的兜底场景，影响小） |
| **N12** 文案层半成品 | 前端 | **FIXED** | `rg "[\x{4e00}-\x{9fff}]" web/src/{pages,components,layouts}` 排除 `copy` 引用后 **0 行**（第三版约 64）；`rg '暂时无法\|请稍后重试' web/src --glob '!web/src/copy/**'` **0 行**（第三版 13）；`authCopy.loginFailed` 改"用户名或密码不正确"；`membersCopy.saved` → "关闭"；`driveCopy.confirmMove` → "放到这里"；新增 `copy/pages/shell.ts`、`FIELD_LABEL`、`FOLDER_NAME`、`AUDIT_OBJECT_LABEL` | `api/{agent,audit,files,finance,knowledge,projects,http}.ts` 里 8 个 `formatRequestError` 前缀（"霜月操作失败""财务操作失败"…）与 `projects.ts` L222 "项目名称已存在。"、`http.ts` L58 "登录状态已失效"（与 `errorCopy.unauthorized` "登录已失效" 措辞不一）仍是字面量；`errorCopy.unavailable` 与 `generic` 同值、`membersCopy.saved` 与 `close` 同值 |
| **N13** 项目页 | 前端 | **FIXED / PARTIAL** | `ProjectsPage.vue` L86-87 新建传 `starts_on / due_on`（`projects-page.test.ts` L96-99 断言）；L50-63 删本地合并、加 `loadSeq` 防竞态；`ProjectDetailPage.vue` L170-184 里程碑行尾 `···`（完成 / 删除），L112-123 `toggleMilestone / removeMilestone` 直接保存 | 菜单项永远叫"完成"，已完成的里程碑没有"取消完成"文案（`toggleMilestone` 会翻转）；没有"编辑"项；L95-97 创建成功后 `loadProjects()` 再把 `created` 挪到列表末尾，多此一举 |
| **N14** 审计映射不全 | 前端 | **FIXED** | `copy/audit.ts` L14-19 补 6 个 action，L22-32 `AUDIT_OBJECT_LABEL`；`AuditPage.vue` L68-78 object 列走映射、未知 action 置灰（`.unknown`） | — |
| **N15** 未捕获 Promise | 前端 | **FIXED** | `KnowledgePage.vue` L101-117、`MemoryPage.vue` L63-85 加 `try/catch` 写 `errorMessage`；`MemoryPage.vue` L118-122 已置顶显示"取消置顶"；`KnowledgePage.vue` 元信息 `filter(Boolean).join(' · ')`；`knowledge.ts` L75-84 新建带标签 | — |
| **N16** `AppLayout / OwnerHomePage` 与重定向 | 前端 | **PARTIAL** | 两个文件删除；`drive-routing.test.ts` L9 / L117 改 `AppShell` | 重定向未删（见 N8 残留） |

### 2.3 第三版 E1 / U1 清单逐条

| # | 条目 | 现状 | 备注 |
|---|---|---|---|
| E1-1 | N1 + `test_extract_task.py` | FIXED | — |
| E1-2 | N2 `seq` + 补齐 + SYSTEM 不进窗口 + 单测 | FIXED | 决策 Q 采纳"加 `seq`" |
| E1-3 | N3 停用词 / 上限 8 / 按命中排序 / 删 `plainto_tsquery` | PARTIAL | 四件都做了，但分词仍是偶数位两字，见 N3 残留 |
| E1-4 | N4 + N5 | PARTIAL | N4 服务端 FIXED；N5 `begin_nested` 有、`rollback` 分支没改 |
| E1-5 | N6 + N7 | FIXED | — |
| E1-6 | 回执文案 | FIXED | `service.py` L149-165 `receipt_line`，`_KIND_LABEL` L111-120 与前端 `CARD_KIND_LABEL` 八项一致；`patch_card` L602 "老板修改了卡片：…" 未动（可接受） |
| E1-7 | 清理（`project_ids` / `is_test` / `AppLayout` / runbook） | FIXED | — |
| U1-8 | 网盘七件 | **1 / 7** | 只做了 `DropZone @files` 接线；行尾 `···`、移动进抽屉、删 `result` 块、"新建子目录"进菜单、文件行改表格、面包屑均未动 |
| U1-9 | 成员 | FIXED | 引入 N18 |
| U1-10 | 对话页六件 | **5 / 6** | 确认入库保留唯一实心、发送改 `↑` 默认样式（`ChatPage.vue` L375-381）、卡头去"待确认"、日期 `dateLabel`、链接按 kind、`file_move` 字段——全做；📎 仍是 emoji（`MultipartUploader.vue` L106） |
| U1-11 | 文案清扫 | FIXED | 见 N12 |
| U1-12 | 审计 | FIXED | — |
| U1-13 | 项目 | FIXED（小残留） | 见 N13 |
| U1-14 | 知识 / 记忆 | FIXED | — |
| U1-15 | Element 覆盖 + `.plain-table` | PARTIAL | `element.css` L31-37 七个变量加了；`.plain-table` 规则用了 `:deep`，全局 CSS 里无效，见 N19 |
| U2-16 | 真实 LLM 三轮 | 工具就位、未跑 | `llm_three_round.py`，见 3.3 |
| U2-17 | Playwright 六页截图 | 就位、不可在 CI 用 | 见 3.3 |

### 2.4 第二版 U0 十条 `rg` 验收（对 `67e843e` 实跑）

| 命令 | 期望 | 第三版 | 本版 |
|---|---|---|---|
| 写死色值（pages / components / layouts） | 0 | 1 | **1**（同一处，打印样式） |
| `el-alert / el-card / confirm` | 0 | 0 | **0** |
| 页面枚举直出 | 0 | 0 | **0** |
| `_id` 直出 | 0 | 0 | **0** |
| `type="primary"` 每文件 > 1 | 无 | 3 文件 | **3 文件**（财务 / 项目详情 / 成员，均在互斥容器内，不变） |
| `暂时无法 \| 请稍后重试`（copy 之外） | 0 | 13 | **0** |
| 原生控件 | 0 | 0 | **0** |
| `isRecord` 份数 | 1 | 1 | **1** |
| 第三版 U1 新增：`检查并获取下载 \| 下载本次文件 \| 我已保存 \| 确定移动` | 0 | 有 | **0** |
| 第三版 E1 新增：`is_test`、`require_project_access \| project_ids`（core） | 0 | 有 | **0 / 0** |

十条里九条清零，剩一处可接受的打印色。**文案与残留类的机械指标本轮全部达标；没达标的是结构性的（网盘页布局、`:deep`、FAILED 流程）。**

### 2.5 决策点 K–Q 执行情况

| 编号 | 第三版默认 | 本版 |
|---|---|---|
| K 删 `/users` 与 `/owner/*` 重定向 | 本轮删 | **未删**（`router.ts` L195-229） |
| L 删 `project_members` 表与接口 | 删 | **已删**（迁移 `0010`；接口 404、入参 422 有测试） |
| M 纪要时区上海 | 改 | **已改** |
| N 财务"毛利"口径 | 改为含项目、服务端置 `null` | **未动**（`FinancePage.vue` 仍为前端相减，口径不变） |
| O FAILED 重试语义：原卡重新确认，两次后只留"直接改" | 采纳 | **采纳但失效**：`ProposalCard.vue` L106-114 两次后切"直接改"，但按钮无效，见 N17；`retries` 是组件内存状态，刷新归零 |
| P 唯一实心给"确认入库" | 采纳 | **已采纳**（发送 → `↑` 默认样式） |
| Q `seq` 列 vs Python 时间戳 | 加 `seq` | **加 `seq`** |

---

## 三、新问题 / 回归（按风险排序）

### 3.1 服务端

**N5-b（实测，中，非回归）同名项目卡确认 → 500。** 见 2.2 N5 残留。触发路径：霜月提出 `project_create`（或 `project_update` 改名）→ 老板确认 → 名字与现有项目重复 → `ProjectService.create` L112-113 `session.rollback()` → 整个事务回滚、savepoint 失效 → `commit_card` 给过期 `card` 赋 `FAILED` → `CardRead.model_validate` 隐式 IO → `MissingGreenlet` → 500，卡片仍 PROPOSED，再点仍 500，老板只能"放弃"。第三版原文已点名此分支，本次 savepoint 没有覆盖到。**修法**：`ProjectService.create / update`（以及 `users/service.py` L120、`files/service.py` 四处）改为**不在服务层 `rollback()`**——直接 `raise ConflictError`，让调用方（`get_session` 或 `begin_nested`）决定回滚范围；或在 `create` 前做 `select ... where lower(name) = ...` 预检抛 `ConflictError`，把 `IntegrityError` 留给真正的竞态。加单测：同名 `project_create` 卡确认后 `status == FAILED`、`error == PROJECT_NAME_CONFLICT`、`projects` 计数不变。

**N3-b（实测，中）奇数位关键词切不出来。** 见 2.2 N3 残留。**修法**：改成滑动窗口全部两字（`token[i:i+2] for i in range(len-1)`）再过停用词，命中数排序已经能把噪音压下去；上限 8 保留；删掉 `"列一"` 这种单句定制的停用词。补两条单测：`"给星野加个 10 月 20 日交付节点"` 必须命中"星野合作"；`"看一下星野项目"` 命中"星野合作"但不命中"老板偏好项目按季度复盘"（或至少排在其后）。

**N20（已验证，低）摘要输入含回执、截断后水位越界。** `maybe_summarize` L910-922 `older` 未过滤 SYSTEM；L923-925 `[:4000]` 截断后 L946 仍以 `max(seq)` 为水位。修法：`older` 加 `role != SYSTEM`；按消息累计到 4000 字就停，水位记最后一条**实际入摘要**的 `seq`。

**N21（已验证，低）`recall` 每轮 N 次 `count` 往返。** L751-760 逐 needle 一条 `SELECT count(*)`，加上后面的主查询最多 ~10 次。数据量小时无感；修法是一次 `SELECT content FROM agent_memory WHERE status=ACTIVE` 到内存里数，或 `UNION ALL` 一条 SQL。

**N22（推断，低）迁移对旧数据的 `seq` 顺序无保证。** `0009` L37 `ADD COLUMN seq BIGSERIAL` 按物理顺序填值，旧会话若曾 `UPDATE` 过消息行，顺序可能与 `created_at` 不一致。生产切换前可加一条 `UPDATE ... SET seq = row_number() OVER (PARTITION BY conversation_id ORDER BY created_at, id)` 的回填；测试库无此问题。

### 3.2 前端

**N17（已验证，中）FAILED 卡两次重试后进入死角。** `ProposalCard.vue` L103-115 是 FAILED 折叠行；两次重试后按钮切为"直接改" → `beginEdit()` 置 `editing = true`，但编辑表单 L120-166 在 `<template v-else>`（L116）里，FAILED 状态下根本不渲染——**按钮点了什么都不发生**。同时：`card.error`（`FINANCE_PROJECT_NOT_FOUND` 等）从不展示，老板不知道为什么失败；同一 payload 重试两次必然同样失败；`reject / revise` 服务端不收 FAILED，所以这张卡也不能"放弃"。**修法**：FAILED 行显示 `CARD_ERROR_LABEL[card.error]`（"项目不存在""同名项目已存在"…）；"直接改"改为把编辑表单渲染进 FAILED 分支（或 `patch_card` 成功后服务端把状态改回 PROPOSED，前端回到正常卡面）；`reject_card` 放行 FAILED。`retries` 若要跨刷新生效需服务端记次数，否则去掉两次限制、只保留"直接改"。

**N18（已验证，中）禁用成员无确认。** `UsersPage.vue` L178-181 菜单项"禁用" → `toggle(row)` L91-98 直接 `PATCH status=DISABLED`，服务端随即撤销该成员所有会话。删掉的内联按钮曾带 `el-popconfirm`（`d43f91b` `UsersPage.vue` L196-208），菜单项从未有过；`users-page.test.ts` L205-216 改成"点菜单即调接口"，把无确认固化进测试。**修法**：菜单项点击后弹 `el-popconfirm`（或 `ElMessageBox.confirm`，文案用现成的 `membersCopy.disableConfirm`），测试恢复"确定"一步。

**N19（已验证，低）`.plain-table :deep(...)` 在全局 CSS 中无效。** `element.css` L46-49；`vite build` 产物 `index-*.css` 里原样出现 `.plain-table :deep(.el-table__inner-wrapper::before)`——`:deep` 只在 SFC `<style scoped>` 由编译器改写，普通 `.css` 文件不处理，浏览器视为未知伪类丢弃整条规则。`FinancePage.vue` L460 自己的 `.plain-table { width:100% }` 仍在。**修法**：去掉 `:deep()` 直接写 `.plain-table .el-table__inner-wrapper::before`。

**N23（已验证，低）网盘上传结果块两个"下载"。** `DrivePage.vue` L399-409：`canCheckDownload` 时是一颗"下载"按钮（其实是"检查扫描状态并取链接"），拿到链接后再出现一个"下载"`<a>`，两者可同屏。第三版 U1-8 要求删掉整个块改为刷新列表 + 行内下载，本次只改了字。

**N24（已验证，低）成员 / 财务 / 审计表空态双份。** `UsersPage.vue` L133 `el-table` 无 `empty-text` 覆盖，`L186 <EmptyLine>` 又渲染一行——空表时 Element 的"暂无数据"与"还没有成员"同时出现（成员页 `win32` 基线截图即如此，财务页基线同：`暂无数据` + `本月还没有记录`）。修法：`el-table` 传 `:empty-text="membersCopy.empty"` 并删 `EmptyLine`，或 `v-if="users.length"` 才渲染表。

**N25（已验证，低）对话页空态布局。** `ChatPage.vue` L389-393 `.chat-page { min-height: 70vh }` 只作用于网格容器，右列的 `.messages` 为空时 `.composer`（L458-460 `margin-top: 32px`）直接顶到页头下方（`chat-win32.png` 里输入框在页面 1/5 高度处，其下大片空白）。修法：右列 `display:grid; grid-template-rows: 1fr auto` 让输入框贴底，或空态时居中一行"对霜月说一句……"。

**N26（已验证，低）📎 仍是 emoji。** `MultipartUploader.vue` L106。第三版 U1-10 要求换 16px 内联 SVG。

### 3.3 "视觉门 + LLM 门"评估

**视觉（`tests/e2e/specs/visual-pages.spec.ts` + 六张 `*-win32.png`）**

- 是什么：1280×800、`fullPage`、`maxDiffPixelRatio 0.005`（L6-10）、遮罩 `.shell__account` 与财务 `.month-label`；登录页匿名抓，其余五页 OWNER 登录后抓；项目详情页取第一条项目、没有就现场建一个"视觉回归项目"（L86-116）。
- 为什么现在不是门：
  1. **不在 CI**。`ci.yml` 只有 `server` / `web` 两个 job，Playwright 只在 runbook 的手工发布流程里跑（`m1-owner-acceptance.md` L7-40，PowerShell）。
  2. **平台绑定**。Playwright 默认 `snapshotPathTemplate` 带 `{platform}`，基线是 `win32`；Linux / macOS 上会报"snapshot doesn't exist"并写新基线，等于永远通过或永远失败。要么设 `snapshotPathTemplate: '{snapshotDir}/{testFileName}-snapshots/{arg}{ext}'` 去掉平台后缀并固定在一个容器里抓，要么接受它只是老板机器上的手工对照。
  3. **基线内容错误**。`members-win32.png`：页头下红字"操作失败。"（`errorCopy.generic`，表示 `usersApi.list()` 抛了没有 HTTP 状态的错——契约错误或网络错误），表格里"暂无数据"，表格下"还没有成员"——**三行叠在一起的出错页被当成了正确答案**，任何修好的环境重抓都会 diff。`finance-win32.png` 同样是 `暂无数据` + `本月还没有记录` 双空态。`chat-win32.png` 是空会话（无消息、无卡片），看不到卡片与消息样式。
  4. **与数据耦合**。除账户名与月份外没有遮罩，网盘 / 项目 / 成员列表任何真实数据都会改变像素——只能对着固定种子库跑。
- 结论：**作为"老板机器上人眼对照六张图"的工具是有用的（本文第四章就是靠它写的），作为回归门还差 CI 接线、平台无关基线、正确的基线内容三步。**

**LLM（`server/scripts/llm_three_round.py`）**

- 是什么：登录 → 确保有"星野"项目 → 新会话三轮（"列一下项目"→"给星野加个 10 月 20 日交付节点"→"这个月房租 8000"），每轮**自动确认所有 PROPOSED 卡**（L89-101 `_confirm_proposed`）→ 等 15 秒 → 轮询 `/agent/memories` 最长 120 秒 → 新会话问"昨天那个合作项目" → 判定：任一轮 `offline` 或含"霜月暂时离线"即失败；回答与记忆都不含"星野"即失败（L116-171 `main`）。输出 JSON 报告，退出码 0/1。
- 好的地方：对应第三版 U2-16 的验收句一字不差；跨会话召回 + 记忆落库两项都查；不打印密码；`OFFLINE` 判定覆盖 N2 的症状。
- 问题：
  1. **写真实数据且无警示**：自动确认意味着会真的写一条 ¥8,000 房租成本、一条里程碑，必要时建项目。脚本 docstring 没有"仅对一次性库使用"的提示，也没有 `--dry-run`。
  2. **不在 CI、需要真实 LLM 密钥**：这是预期的（真实 LLM 不该进 CI），但 runbook 没有登记这一步怎么跑、跑完怎么清库。
  3. 召回句"昨天那个合作项目"恰好是 N3 分词能处理的形态；第二轮"给星野加个…"的召回（见 N3-b）不会命中"星野"记忆，靠的是工具 `list_projects`，脚本不会暴露这个缺口。建议再加一句 `"看一下星野项目"` 的召回断言。
- 结论：**它是手工验收的正确起点，不是门。** 在 N5-b / N17 修完、对着一次性库跑一遍并把 JSON 报告贴进 PR，才算第三版 U2-16 完成。

### 3.4 `project_members` 删除的权限影响（问题 4）

- **STAFF / MANAGER 运行时权限：无变化。** `d43f91b` 已删除 `Actor.project_ids` 的每请求查询（第三版 2.3 第 10 条），`require_project_access` 自那时起无人调用；本提交删的是空字段与死函数（`actors.py`）。现行模型：项目列表 / 详情对三种角色开放（`projects/service.py` L122 / L149 `require_project_actor`；`test_staff_sees_all_projects_and_cannot_create` 早在 `d43f91b` 即断言 STAFF 看到全部项目）；写操作 `require_owner`；文件按目录可见性（`files/service.py` L35-46 `folder_is_visible`：`老板私有` 仅 OWNER、`公司` MANAGER、`项目` 全员）；账目按 `visibility` + 角色（`finance/service.py` L61-77 `entry_is_visible`、L229-230 STAFF 不含收入与公司项）。
- **删掉的面**：`PUT /owner/users/{id}/projects`（现 404，`test_owner_users.py` L78-84）、`StaffCreate.project_ids`（`extra="forbid"` → 422，L99-104）、`OwnerUserRead.projects`、`ProjectMember` 模型、7 个只测成员表的用例（含并发替换、1000 上限、全有或全无三个），以及 `UsersPage.vue` 固定传的 `project_ids: []`。
- **数据**：迁移 `0010` `DROP TABLE IF EXISTS project_members` 不可逆（`downgrade` 只重建空表）；`0009` 同时 `DROP COLUMN is_test`。生产切换前若想保留历史归属，先 `COPY` 一份。
- **审计**：历史 `user.projects.replace` 行在审计页有中文"分配项目"（`copy/audit.ts` L15），不会变成灰色未知项。
- **文档**：`docs/迭代方案-三层账号与霜月.md` 若仍描述"员工按项目分配"，需要同步；本轮未查其内容，列入下一步。

---

## 四、UI 观感复核

依据：六张 `win32` 基线截图（1280×800，作者机器实抓）+ 源码。

### 4.1 达到的部分

- **登录页**：整页纸色、左上字标、360px 单列、28px"登录"、两个输入、一颗墨蓝实心按钮——与第二版 3.5 规格一致，无卡片无说明。
- **壳**：13px 字标 + 五项文字导航 + 右上账户下拉，1px 底线。六页一致。
- **财务页**：页头右侧 `‹ 月份 ›` + "记一笔 / 导出"两个文字动作；四个大数（公司运营成本 / 项目成本 / 收入 / 毛利）28px 等宽；表格表头已是透明底、灰字（`element.css` 覆盖生效）。这是六页里最接近目标的一页。
- **项目详情**：28px 项目名 + 右侧"编辑"；一行灰色元信息"筹备 · 开始 – 到期 · 0%"；"里程碑"二级标题 + 空态一行字 + "添加"。干净，但"开始 – 到期"在无日期时显示成字面量破折号，应隐藏。
- **对话卡片（源码层面）**：唯一实心"确认入库"、发送 `↑` 默认样式、卡头只剩"记一笔"、日期中文、链接按 kind。已入库回执 `已入库 · 记一笔 房租 ¥ 8,000.00` 居中灰字。
- **文案**：页面层零字面量；术语表统一。

### 4.2 仍显廉价的地方（按扎眼程度）

1. **网盘页（最扎眼，与第三版持平）**。`drive-win32.png`：左栏"公司 / 老板私有 / 项目"三个文字按钮居中排列（`el-button text` 默认居中，不是左对齐列表），下面常驻"新建子目录"标签 + 输入框 + 一颗与输入框同宽的"创建"按钮；右侧"这个目录是空的"灰字紧贴一个虚线上传框，框内只有"上传"二字；面包屑"项目"是一颗孤立的 `el-button`。有文件时每行"下载 重命名 移动 删除"四个文字按钮平铺 + 行内展开表单（源码 L342-389）。这一页与第二版 3.5 目标的距离没有缩短。
2. **成员页**（`members-win32.png`）：出错红字 + 表内"暂无数据" + 表下"还没有成员"三行叠加；表格白底与纸色页面形成一块白板；操作列宽 280px 只放一个 `···`。
3. **对话页**（`chat-win32.png`）：空会话时输入框悬在页面上部，其下 600px 空白；左栏"霜月"标题与"新对话"按钮挤在一行、搜索框紧贴其下，没有会话时整栏只有这两样；📎 是系统 emoji。
4. **上传结果块**（源码）：`currentStatusMessage` 一行 + "下载"按钮 + "下载"链接，仍是第二版 C9 的形态。
5. **Element 残留**：`.plain-table` 无边框规则无效（N19），表格四周仍有 Element 默认边框线；`el-button text` 悬停底色已透明化（L36 生效）。
6. **小处**：SOUL 页标题仍是英文 "SOUL"；项目新建按钮"创建项目"与其他页"保存"不一致（现已进 `projectsCopy.createSubmit`，改字即可）；里程碑菜单已完成项仍叫"完成"；财务"毛利"口径未按决策 N 改。

### 4.3 结论

按第二版十条戒律：**1（一屏一个实心）对话页按决策 P 解释后满足；2、3、5、6、7、8、9、10 满足；4（不出现枚举 / 字段名）本轮清零。** 机械指标全部达标；观感从"有体系但收尾粗糙"进到"五页像样、一页没动、两处空态露怯"。**下一轮 UI 只需要做网盘页 + 三个空态（成员 / 财务 / 对话），不需要再动体系。**

---

## 五、下一步迭代（短、可执行）

每条一个 PR；E2 完成前不要跑真实 LLM 验收。

**E2 — 服务端补漏（半天量级的改动）**

1. **N5-b**：`ProjectService.create / update` 删 `session.rollback()`，改为预检同名 + 直接 `raise ConflictError`；同样处理 `users/service.py` L120（`create_staff` 不在卡片路径，但同一模式）。单测：同名 `project_create` 卡确认 → `FAILED / PROJECT_NAME_CONFLICT`，`projects` 计数不变，接口 200。
2. **N3-b**：`recall_needles` 改滑动窗口两字；删 `"列一"`；两条新单测（见 3.1）。
3. **N20**：`maybe_summarize` 排除 SYSTEM、水位记实际入摘要的最后一条。
4. **N4 补全**：`reject_card` 放行 FAILED；`patch_card` 成功后把 FAILED 改回 PROPOSED（这样前端不需要在 FAILED 分支里渲染编辑表单）。
5. **N22**：`0009` 追加一条按 `(created_at, id)` 回填 `seq` 的 `UPDATE`（仅影响已有行）。

**U1-b — 前端收尾（网盘一页 + 三处空态 + 两个小 bug）**

6. **N17**：FAILED 折叠行显示 `CARD_ERROR_LABEL[card.error]`；"直接改"依赖第 4 条后回到正常卡面；去掉组件内 `retries` 计数或改由服务端记。
7. **N18**：菜单"禁用"加 `el-popconfirm`（复用 `membersCopy.disableConfirm`），`users-page.test.ts` 恢复"确定"一步。
8. **N19**：`element.css` 去 `:deep`。
9. **网盘**（第三版 U1-8 剩下六件）：行尾 `···`（下载 / 重命名 / 移动 / 删除）；重命名行内、移动进 `el-drawer`；删 `result` 块，上传完成只刷新列表并在该行显示扫描状态；"新建子目录"移到当前目录 `···`；文件行改 `el-table`（名称 · 大小 · 日期 · 上传者）；面包屑改为 `a / span` 文字。`drive-routing.test.ts` 相应改为断言列表刷新与行内状态。
10. **空态**（N24 / N25）：`el-table` 统一 `:empty-text`，删表下 `EmptyLine`；对话页右列 `grid-template-rows: 1fr auto`，空会话居中一行提示。
11. **N26**：📎 换 16px 内联 SVG。
12. **决策 K**：删 `/users` 与 `/owner/*` 重定向；**决策 N**：毛利口径与服务端置 `null`。
13. **小处**：SOUL 页标题"霜月设置"；"创建项目" → "保存"；里程碑已完成项菜单"取消完成"；`http.ts` L58 改引 `errorCopy.unauthorized`；`api/*.ts` 8 个前缀进 `errorCopy`。

**G — 把两扇门装上**

14. **视觉**：`playwright.config.ts` 设 `snapshotPathTemplate` 去平台后缀；在 `ci.yml` 加一个 job：起 Postgres + API + `vite build` 静态服务 + `seed_acceptance` 固定种子 + `npx playwright test visual-pages.spec.ts`，Linux 上重抓六张基线（**先修 N24 / N25 再抓，成员页必须是正常空态**）；为对话页补一张"有一张待确认卡 + 一条回执"的基线（用 `ChatPage` 的 mock 或种子会话）。
15. **LLM**：`llm_three_round.py` 顶部加"仅对一次性库使用，会写入真实账目与里程碑"的警示与 `--dry-run`（只发消息不确认卡）；加 `"看一下星野项目"` 召回断言；runbook 增加"如何跑、跑完 `DROP DATABASE`"两段；E2 完成后跑一次，JSON 报告贴 PR。

**验收（命令输出为准）**

```bash
cd server && pytest tests/unit/agent -q                                     # 全绿，含 N5-b / N3-b 新用例
rg -n 'session.rollback' server/src/superboss/modules/projects/service.py   # 无输出
rg -n ':deep' web/src/styles                                                # 无输出
rg -n '📎' web/src                                                          # 无输出
rg -n "redirect: '/members'|path: '/owner" web/src/app/router.ts            # 无输出
rg -n '<EmptyLine' web/src/pages/owner/UsersPage.vue web/src/pages/FinancePage.vue web/src/pages/AuditPage.vue  # 无输出（改用 empty-text）
ls tests/e2e/specs/visual-pages.spec.ts-snapshots/                          # 无平台后缀，七张（含对话页有卡片一张）
```

---

## 六、暂不做 / 决策点

**暂不做**（沿用二、三版，全部仍成立）：深色模式、图表、插图空态、导航图标、Webfont、国际化、pgvector（N3-b 修完实测仍不够时再议）、员工侧霜月、目录级 ACL、外部 API 令牌、卡片自动入库、`ruff format` 的 29 个文件。

**本轮新增暂不做：**

- 把 `recall` 的逐词计数改成一条 SQL（N21）——数据量到千条以上再做。
- 记忆抽取任务改按会话 owner 构造 `Actor`——N1 最小修法已生效且有测试，不再动。
- 视觉基线的对话页"有卡片"截图依赖种子会话或 mock，若成本高，先只补空态修复后的六张。

**待老板拍板**（编号接续第三版 K–Q）：

| 编号 | 问题 | 本文默认 |
|---|---|---|
| K | `/users`、`/owner/*` 重定向 | 第三版说本轮删、未删；**下一轮删**（U1-b 第 12 条） |
| N | 财务"毛利"口径 | 未动；**下一轮按第三版默认改** |
| O | FAILED 卡两次重试后的去向 | 第三版默认"只留直接改"，实现无效；**改为**：失败行显示原因 + "直接改"（改完回到 PROPOSED 卡面）+ "放弃"，不限次数，去掉前端计数 |
| R（新） | 视觉回归放 CI 还是继续手工 | **放 CI**：Linux 容器固定字体与种子库，去平台后缀；老板机器上的 `win32` 基线删除 |
| S（新） | `llm_three_round.py` 是否允许对生产库跑 | **不允许**：脚本加警示与 `--dry-run`，runbook 只写一次性库流程 |
| T（新） | 服务层 `session.rollback()` 的统一处理 | **服务层不回滚**，只 `raise`；事务边界只在 `get_session` 与 `commit_card` 的 `begin_nested` |
| U（新） | 成员"禁用"二次确认的形态 | `el-popconfirm` 挂在菜单项上；不用全屏 `MessageBox` |
