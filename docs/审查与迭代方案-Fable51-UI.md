# SuperBoss 审查与迭代方案：产品质量与 UI 体系（Fable 5.1）

日期：2026-09-06。基于 `master@2b4ebd2` 的实际代码核查（该提交只比三层方案所依据的 `64ddffa` 多一份文档，产品代码完全一致）。

本文**不改变**[`迭代方案-三层账号与霜月.md`](迭代方案-三层账号与霜月.md)（下称"三层方案"）确定的产品方向、权限矩阵、数据模型与 P0/P1/P2 路线；它回答的是三层方案没有回答的三件事：

1. 现有代码与 UI 的**质量**问题在哪里（不是功能有无，而是做得好不好）；
2. 三层方案里的每一个页面应该**长什么样、说什么话**——一套可直接执行的 UI 体系；
3. 在三层方案 P0/P1/P2 之旁或之后，工程上还应做什么、明确不做什么。

凡标注"（已验证）"的结论均有文件路径与行号；标注"（假设）"的为未在浏览器中实测或未量化的判断，见 2.4。

---

## 一、审查结论摘要

给老板的一页纸：

1. **地基干净，外观廉价。** 服务端 7023 行、前端 3960 行，结构清楚、无明显 bug 债；但前端是"Element Plus 默认皮肤 + 每页一张表单顶在最上面 + 一列卡片"的原型观感。9 个写死的十六进制颜色全部是 Element Plus 默认色，全仓没有一处 `font-family`、没有一个设计变量（已验证：`web/src` 下 `rg 'font-family|:root|--el-'` 零命中）。这不是"再调调"能解决的，需要先立一套很小的视觉体系，再让三层方案的新页面长在上面。
2. **当前 UI 对员工/管理层是不可用的。** 非 OWNER 登录即进 `/forbidden`，且该页的"返回首页"链接会再次跳回 `/forbidden`（死循环，已验证：`ForbiddenPage.vue` L5 → `router.ts` L106、L119、L185）。三层方案已规划三层路由，本文给出落地时的导航结构与落点规则。
3. **文案在解释系统而不是服务用户。** 例如网盘页副标题"上传完成后仅展示本次文件的处理状态。"、按钮"检查并获取下载"、直接把 `file_id`/`project_id` 的 UUID 和 `ACTIVE`/`STAFF` 枚举值印在页面上（路径见 3.6）。本文第三章给出文案原则与术语表，目标是**能不说就不说**。
4. **即将新建的页面数量是现有的两倍。** 三层方案要新建 `/chat /finance /projects(v2) /drive(v1) /knowledge /soul /memory /audit` 8 个页面。若在没有体系的情况下按现有写法各写一遍，堆砌会翻倍且无法回头。因此本文建议：**UI 体系（阶段 U0）与三层方案 P0 的删除步骤（D1–D6）并行，先于任何新页面落地。**
5. **工程侧只有小修，没有大改。** 错误码默认值带项目语义（`core/errors.py` L38-54）、5 份重复的 `get_session`、每次 GET 列表都写一行审计（`projects/router.py` L53、L66）、前端 5 份重复的响应校验器、测试断言绑死中文文案（28 处）——都是半天级的收敛，但会显著降低后续 8 个页面的开发摩擦。
6. **明确暂不做**：深色模式、图表看板、插图空态、引导教程、图标堆砌、Webfont 下载、移动端优先。理由见第六章。

一句话：**先立规矩（U0），再删（D1–D6），再建（三层 P0）。** 规矩只有一页：一套 token、一份术语表、八个基元组件、十条戒律。

---

## 二、现状核查（对照三层方案）

### 2.1 核查方式

逐文件阅读 `web/src` 全部 24 个文件、`server/src` 的 core/main/router 与各模块 router/models/service/schemas、`docs/` 全部、`ci.yml`、compose、e2e spec 头部；用 `rg` 做了颜色、组件、文案、测试耦合的量化统计。未运行前端与浏览器（环境无 node_modules），所有"观感"判断基于源码与 Element Plus 默认样式推断，见 2.4。

### 2.2 已有 / 缺失 / 该删（UI 与工程视角）

三层方案第二节已给出功能层面的对照表，此处不重复，只补 UI 与工程质量维度：

| 维度 | 已有（已验证） | 缺失 | 该删 / 该改 |
|---|---|---|---|
| 布局壳 | `layouts/AppLayout.vue`：白色顶栏 + 文字链接导航 + 1120px 居中内容，`#f5f7fa` 页底 | 按角色生成的导航；账号菜单；页面标题区规范 | 导航项 "Users" 英文（L24）、"文件上传"作为网盘入口名（L21）、设备/导入任务两项（L22-23） |
| 路由 | `app/router.ts`：单一 `/owner/*` 组，全部页面**同步引入**（L17-23，无懒加载） | 三角色落点；`/chat` 等 OWNER 组件对非 OWNER 的 bundle 级隔离 | `/forbidden` 死循环；`RouteMeta.roles` 二值类型（L28） |
| 主题 | 无 | `styles/tokens.css`、Element Plus 变量覆盖、`ElConfigProvider :locale` | 9 个写死色值（统计见 3.6）；`index.html lang="en"`（L2） |
| 组件用法 | `el-button ×12、el-card ×9、el-input ×8、el-alert ×8、el-tag ×2、el-checkbox ×2、el-dialog ×1`（全仓统计） | 基元组件（页头、行列表、键值、内联错误、金额、日期） | 与原生 `<select>/<input>/<button>/<progress>` 混用（`DrivePage.vue` L135、`MultipartUploader.vue` L104-124、`DevicesPage.vue` L118）；`globalThis.confirm` ×3 |
| 文案 | 约 130 条中文字符串散落在 18 个文件 | 术语表；集中文案模块；错误码→文案映射表 | "OWNER" 眉题 ×5、UUID 与枚举直出、机制解释型副标题（清单见 3.6） |
| 空态/加载 | `v-loading` 遮罩；`DrivePage` 一条空态文字 | 统一"一行 + 一动作"的空态；加载延迟阈值 | — |
| 表单 | 每页顶部一张创建表单（Projects/Users/Devices） | 抽屉式表单；一屏一个主按钮 | `ProjectsPage.vue` L77 "设为验收测试项目"勾选（内部产品不应暴露 `is_test`） |
| 测试 | `web/tests` 3199 行，行为测试为主 | 文案常量化，测试引用常量 | 28 处 `getByText/heading/toHaveTextContent` 直接写中文原文（改文案 = 改测试） |
| 服务端接口形状 | 错误信封 `{error:{code,message,request_id}}` 统一（`main.py` L60-74）；服务端消息为英文、前端不直出（正确做法） | `GET /auth/me` 不返回 `id`（`auth/schemas.py` L46-52）；列表接口不带展示用字段（如上传者姓名） | `core/errors.py` 三个基类默认码带项目语义；`require_owner` 抛 `PROJECT_CREATE_FORBIDDEN`（`actors.py` L90-92）；`project.list/read` 每次 GET 写审计 |

### 2.3 本轮新发现的具体问题（三层方案未提及）

**前端结构**

- F1 `ForbiddenPage.vue` L5 链接 `to="/"`；`/` 重定向 `/owner`（`router.ts` L106），`/owner` 要求 `roles:['OWNER']`（L119），STAFF 被再次送回 `/forbidden`。**死循环**。
- F2 `router.ts` L17-23 所有页面同步 import；三层方案 4.3 要求 OWNER 专属页面不进入员工 bundle，需改为路由级 `() => import()`。
- F3 `LoginPage.vue` L88 `min-height: calc(100vh - 64px)`——减去的是上一轮已删除的 `App.vue` 页头高度（`App.vue` 现仅 5 行），登录卡片因此偏上 32px；`PasswordChangePage.vue` L101 用的是 `100vh`，两页不一致。
- F4 `ProjectsPage.vue` L22-24 把本地已创建项目与服务端列表**合并**而非以服务端为准；服务端删除后前端仍显示。
- F5 `MultipartUploader.vue` L24、L77 用 `\u6587\u6863`、`\u626b\u63cf\u4e2d` 转义写中文（"文档"、"扫描中"），源码不可读。
- F6 日期格式不统一：`UsersPage.vue` L35-38 `dateStyle:'medium', timeStyle:'short'`；`DevicesPage.vue` L20-24 `dateStyle:'short', timeStyle:'medium'`。
- F7 `main.ts` L2-3 全量引入 Element Plus 与 `dist/index.css`；`App.vue` 的 `el-config-provider` 未传 `locale`，Element Plus 内建文字（表格空态 "No Data"、日期选择器、分页）为英文。目前页面未用到这些组件所以不可见，加 `el-table` 的第一天就会暴露。
- F8 `web/src/api/{auth,projects,files,users,devices,imports}.ts` 与 `uploads/multipart.ts` 各自复制了一份 `isRecord/hasRequiredKeys/safeText`（至少 5 份），各自一个 `xxxContractError` 与 `xxxErrorMessage`。
- F9 错误提示格式 `项目操作失败（500，<36 位 request_id>）`（`http.ts` L85-97）：request_id 对排障有用，但以正文形式印在用户面前显得像崩溃日志。

**服务端**

- S1 `core/errors.py` L38-54：`ForbiddenError/NotFoundError/ConflictError` 的默认 code 与 message 是 `PROJECT_*`——核心层携带项目模块语义，users/files 模块继承后要逐个覆盖。
- S2 `get_session` 依赖在 `auth/projects/files/users/devices/imports` 六个 router 各写一份；提交时机还不一致（projects/users/auth 在 `else:` 提交，files 在 `try:` 内提交）。
- S3 `projects/router.py` L53、L66：`project.list`、`project.read` 成功也写审计，每次打开项目页都新增一行 `audit_logs`。三层方案要建审计只读页，这些噪音会淹没真正重要的"确认入库/删除文件/改 SOUL"。
- S4 `AuthUserRead` 无 `id`；前端无法判断"这条记录是不是我"（成员页禁止自我禁用、对话页归属等都需要）。

**测试与工具链**

- T1 `web/tests` 中 28 处直接以中文原文定位元素（例如 `'创建项目'`×8、`'检查并获取下载'`×4、`'生成配对码'`×5）。任何文案打磨都会连带改测试，这会在心理上阻止文案打磨。
- T2 `tests/e2e/specs/staff-denial.spec.ts` L27 以 `is_test` 查找验收项目 fixture；因此**在删掉"设为验收测试项目"勾选之前**，需先把 e2e fixture 改为按名称或 seed 脚本返回的 id 查找。

### 2.4 假设与未验证项

- H1 观感判断（"廉价""原型感"）基于源码与 Element Plus 默认样式推断，未截图实测。建议 U0 第一件事就是在本机跑起来截 6 张图作为"改造前"基线。
- H2 全量引入 Element Plus 的体积（估算 CSS ≈ 330 KB、JS ≈ 700 KB，gzip 前）未实测；内网 10 人无性能压力，此项只关乎整洁，列为 P2 可选。
- H3 `el-config-provider` 缺 `locale` 的可见影响目前极小（F7），是"必然会踩"而非"已经踩到"。
- H4 服务端 32 条路由、13 张表、compose 9 服务、ClamAV 约 4 GiB——沿用三层方案数据，本轮复核一致。

---

## 三、产品 UI 梳理

### 3.1 目标观感：一句话与十条戒律

**一句话**：像一份排版讲究的内部刊物，而不是一块运营看板。纸感底色、单一强调色、大字标题、细线分隔、大量留白；数据用字号与对齐表达层级，而不是用颜色块和图标。

**十条戒律**（执行者必须逐条遵守；违反任一条即不合并）：

1. 一屏最多一个实心主按钮（`type="primary"`）。其余动作用文字按钮或 `plain`。
2. 不用 `el-card` 做列表项。列表是行，行与行之间是 1px 细线。
3. 不用 `el-alert`。错误是一行 13px 的危险色文字，紧贴它所描述的对象（字段下方或表单底部）。
4. 不出现 UUID、英文枚举值、状态码、字段名。凡展示给人看的值都经过术语表映射。
5. 不解释系统机制。副标题、说明段、"提示："全部删除；标题本身就是全部说明。
6. 不用插图、不用图标装饰导航、不用彩色 tag 表示状态。状态用文字或一个 6px 圆点。
7. 表单进抽屉（`el-drawer`），不占列表页顶部。
8. 空态一行字（≤ 12 字）加一个动作，没有第二句。
9. 数字用等宽数字（`tabular-nums`），金额右对齐、千分位、两位小数；日期统一 `2026年9月6日` / 表格内 `09-06`。
10. 颜色只能引用 `tokens.css` 的变量；组件与页面文件中出现 `#` 色值即不合并。

### 3.2 信息架构（按角色）

同一个 `AppShell`，导航由一份 `app/navigation.ts` 按 `auth.user.role` 生成，不在模板里散落 `v-if`。

| 角色 | 顶栏主导航（≤ 5 项） | 账号菜单（右上，姓名下拉） | 登录落点 |
|---|---|---|---|
| OWNER | **霜月** · 财务 · 项目 · 网盘 · 知识库 | 成员 · 审计 · 霜月设置（SOUL / 记忆）· 修改密码 · 退出 | `/chat` |
| MANAGER | 财务 · 项目 · 网盘 · 知识库 | 修改密码 · 退出 | `/projects` |
| STAFF | 财务 · 项目 · 网盘 · 知识库 | 修改密码 · 退出 | `/projects` |

规则：

- 主导航只放"每天都去"的页面；管理与配置类一律进账号菜单。OWNER 顶栏第一项是"霜月"而非"首页"，左上角字标点击也回 `/chat`。
- 路径去掉 `/owner/` 前缀（三层方案 3.1 已如此设计）：`/chat /finance /projects /projects/:id /drive /knowledge /members /audit /soul /memory`。
- `/forbidden` 只在直接输入越权 URL 时出现；页内导航永远不会把人带到那里。登录后与刷新后的落点都由 `roleHome(role)` 单点决定。
- 页面标题区统一：28px 标题 + 右侧动作区（≤ 2 个动作）；没有眉题、没有副标题、没有面包屑。

### 3.3 视觉系统

#### 色

近乎单色。全部定义在 `web/src/styles/tokens.css`，页面与组件只引用变量。

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
| `--sb-ok` / `--sb-warn` / `--sb-danger` | `#3B7A57` / `#9A6B1E` / `#A6453B` | 仅用于文字与 6px 圆点，不做底色 |
| `--sb-ok-bg` / `--sb-warn-bg` / `--sb-danger-bg` | `#EEF4F0` / `#F7F1E4` / `#F8ECEA` | 仅霜月卡片的"已入库/失败"一行底色 |

- 强调色是决策点 F（见文末）：墨蓝灰偏"编辑部"，若老板希望带一点"霜月"的冷调，可换 `#34506B`，其余不变。
- 禁止使用 Element Plus 默认 `#409eff` 及其派生色。

#### 字

不下载 Webfont。系统字体栈，中文优先苹方/鸿蒙/雅黑，西文若本机有 Inter 则用之：

```css
--sb-font: "Inter", -apple-system, "PingFang SC", "HarmonyOS Sans SC",
           "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif;
--sb-mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
```

字号只有六档，字重只有两档（400 / 600）：

| 档 | 字号 / 行高 | 用途 |
|---|---|---|
| xs | 12 / 1.5 | 时间戳、辅助 |
| sm | 13 / 1.5 | 表格单元、表单标签、次要说明 |
| base | 14 / 1.6 | 正文、按钮、导航 |
| md | 16 / 1.75 | 对话消息正文、知识库正文 |
| lg | 20 / 1.4 | 区块标题、抽屉标题 |
| xl | 28 / 1.2 | 页面标题、财务汇总大数（`letter-spacing:-0.01em`） |

金额与所有表格数字加 `font-variant-numeric: tabular-nums`。

#### 间距与宽度

8pt 网格：`4 8 12 16 24 32 48 64`。

- 页面顶部内边距 40；标题到内容 32；区块之间 48。
- 行列表：行高 48，左右内边距 0（与标题左缘对齐），上下 12。
- 表格单元格：上下 10、左右 12；无斑马纹、无外框、只有行间细线。
- 内容宽度两档：阅读型（对话、知识库正文、SOUL）**880px**；表格型（财务、网盘、成员、审计）**1120px**。宽度由 `AppShell` 按路由 meta 给，页面不自定宽度。
- 圆角统一 6px；弹层 8px。阴影：页面内为 0；只有抽屉与弹层用 `0 1px 2px rgba(0,0,0,.06), 0 8px 24px rgba(0,0,0,.08)`。

#### 组件克制：Element Plus 的用法边界

Element Plus 继续用，但通过变量覆盖成"看不出是 Element Plus"。`web/src/styles/element.css`：

```css
:root {
  --el-font-family: var(--sb-font);
  --el-font-size-base: 14px;
  --el-color-primary: var(--sb-accent);
  --el-color-primary-light-3: #56636F;
  --el-color-primary-light-5: #7C8791;
  --el-color-primary-light-7: #A9B0B7;
  --el-color-primary-light-8: #C5CACF;
  --el-color-primary-light-9: #E5E8EA;
  --el-color-primary-dark-2: #232C36;
  --el-color-success: var(--sb-ok);
  --el-color-warning: var(--sb-warn);
  --el-color-danger: var(--sb-danger);
  --el-color-error: var(--sb-danger);
  --el-text-color-primary: var(--sb-ink);
  --el-text-color-regular: var(--sb-ink);
  --el-text-color-secondary: var(--sb-ink-2);
  --el-text-color-placeholder: var(--sb-ink-3);
  --el-border-color: var(--sb-line);
  --el-border-color-light: var(--sb-line);
  --el-border-color-lighter: var(--sb-line);
  --el-fill-color-blank: var(--sb-surface);
  --el-fill-color-light: var(--sb-paper);
  --el-bg-color: var(--sb-surface);
  --el-bg-color-page: var(--sb-paper);
  --el-border-radius-base: 6px;
  --el-border-radius-small: 4px;
  --el-box-shadow: none;
  --el-box-shadow-light: none;
  --el-box-shadow-lighter: none;
}
```

允许使用：`el-button`（主按钮 ≤ 1/屏）、`el-input`、`el-select`、`el-date-picker`（仅月份）、`el-table`（细线、无边框、无斑马纹）、`el-drawer`（一切表单）、`el-dialog`（仅临时密码与危险确认）、`el-popconfirm`（轻确认）、`el-progress`（2px 细条）、`el-skeleton`（延迟 300ms 后才出现）、`el-dropdown`（账号菜单、行内 `···`）。

禁止使用：`el-alert`、`el-card`、彩色 `el-tag`、`el-empty` 的插图、`el-result`、`el-notification`、`el-message` 报成功（结果在屏幕上可见时不再弹）、`el-tooltip` 当说明文档、`el-badge`、`el-breadcrumb`、`el-steps`、`el-statistic`、`el-carousel`、导航图标。

`App.vue` 传 `:locale="zhCn"`；`index.html` 改 `lang="zh-CN"`。

#### 动效

只有一种：`transition: opacity .12s ease-out, background-color .12s ease-out`。无进场动画、无骨架屏闪烁（加载 < 300ms 不显示任何加载态）、无弹跳。

### 3.4 文案原则与术语表

**八条原则**

1. 标题即说明。页面、抽屉、卡片都不配副标题。
2. 不解释机制：不出现"系统""扫描""对象存储""会话""状态机""处理"等词。用户只需要知道"能不能下载"。
3. 不出现内部标识：UUID、`ACTIVE`、`STAFF`、`CLEAN`、状态码、字段名一律映射为术语表词汇；request_id 只以 12px 灰色短码出现在错误行末尾（取前 8 位，点击复制）。
4. 错误 = 一句话 + 一个可做的动作。"上传失败。重试" 而不是 "文件操作失败，请稍后重试。（500，…）"。
5. 确认只写后果："禁用后该账号立即退出登录。" 按钮就是动词："禁用"，不写"确认禁用吗？"。
6. 删除礼貌填充："请""吗""哦""~"；删除"暂时""稍后再试"这类无信息词，除非确实是暂时的。
7. 数字与中文间空格（"3 个里程碑"）；金额 `¥ 8,000.00`；日期 `2026年9月6日`，表格内 `09-06`，跨年才显示年份。
8. 所有面向用户的字符串集中在 `web/src/copy/`：`glossary.ts`（枚举→中文）、`pages/*.ts`（页面文案）、`errors.ts`（错误码→一句话）。测试只引用这些常量。

**术语表**（`copy/glossary.ts` 的内容，也是所有人说话的口径）

| 内部值 / 现有说法 | 统一用词 |
|---|---|
| OWNER / MANAGER / STAFF | 老板 / 管理层 / 员工 |
| Users / 员工账号 | 成员 |
| 文件上传 / Drive | 网盘 |
| ACTIVE / DISABLED（账号） | 正常 / 已禁用 |
| ACTIVE / ARCHIVED（项目） | 进行中 / 已归档 |
| PLANNING / ACTIVE / DELIVERING / REVIEW / ARCHIVED（阶段，三层方案） | 筹备 / 进行 / 交付 / 复盘 / 归档 |
| UPLOADING / QUARANTINED / SCANNING | 处理中 |
| CLEAN | （不显示任何状态） |
| INFECTED / FAILED | 未通过 |
| COST / INCOME | 成本 / 收入 |
| COMPANY / PROJECT（scope） | 公司运营 / 项目 |
| ALL / MANAGEMENT / OWNER_ONLY（visibility） | 全员 / 管理层 / 仅自己 |
| PROPOSED / COMMITTED / REVISED / REJECTED / FAILED（卡片） | 待确认 / 已入库 / 已修改 / 已放弃 / 入库失败 |
| DRAFT / PUBLISHED | 草稿 / 已发布 |
| 临时密码 | 初始密码 |
| 退出登录 | 退出 |

### 3.5 各角色关键页面：目标观感与改造点

每页给出：目标观感（一段话）、结构（自上而下）、文案（全部）、改造点（相对现状）。执行者不得增加此处未列出的说明文字。

#### 登录 `/login`（全员）

- 观感：整页纸色，左上角 14px 字标 "SuperBoss"，表单在垂直居中的 360px 单列里；没有卡片边框、没有眉题、没有欢迎语。
- 结构：标题"登录"（28px）→ 用户名 → 密码 → [登录]（主按钮，撑满 360px）→ 错误行。
- 文案：`登录` `用户名` `密码` `用户名或密码不正确`。
- 改造：删 `login-card__eyebrow`、`login-card__description`（`LoginPage.vue` L45-47）；删 `el-alert`；修 F3 的 `calc(100vh - 64px)`。首登改密页同样处理：标题"设置新密码"，一句说明也不要（"继续使用前，请先更换临时密码。" 删）。

#### OWNER 对话 `/chat`

- 观感：这是老板每天打开的第一屏，也是整个产品的脸。**像一份与助理往来的信件，而不是聊天软件。** 左侧 240px 会话栏可折叠；中间 880px 阅读列；底部输入框贴底。没有头像气泡、没有欢迎横幅、没有推荐问题网格。
- 结构：
  - 会话栏：顶部 [新对话]（文字按钮）；会话按日期分组（今天 / 本周 / 更早），每条只有一行标题；归档进 `···`。
  - 消息：发言人以 13px 小字标在消息上方（"霜月" / "你"），正文 16px/1.75；消息之间 32px 空白；不画气泡框。
  - 提案卡：嵌在霜月消息下方，1px 细线框、8px 圆角、24px 内边距：

    ```
    成本 · 公司运营                                   2026年9月
    房租                                          ¥ 8,000.00
    可见范围                                            管理层
    备注                                               9 月房租
    ────────────────────────────────────────────────────────
    [确认入库]        修改        放弃
    ```

    标题行 = `kind` 的术语 + 范围；键值两列（13px 灰标签 / 14px 值，金额等宽右对齐）；三个动作里只有"确认入库"是实心。"修改"展开为卡片内一行输入框（占位"告诉霜月哪里不对"）。已入库后整卡折叠为一行浅绿底：`✓ 已入库 · 成本 房租 ¥ 8,000.00 → 财务`（末尾是链接）。失败为浅红底一行 + "重试"。
  - 输入框：自动增高 textarea，左侧回形针（唯一允许的图标），右侧 ↑ 发送；Enter 发送，Shift+Enter 换行。占位文字"对霜月说……"。附件上传后在输入框上方以一行文件名显示，处理中显示灰点。
  - 空会话：整列只有输入框与占位文字，**没有任何欢迎语或功能介绍**。
  - 霜月离线：输入框上方一行 13px 灰字"霜月暂时离线"，输入框禁用。这是全站唯一允许的"横幅"，且只有一行。
- 文案：`新对话` `对霜月说……` `确认入库` `修改` `放弃` `已入库` `入库失败` `重试` `霜月暂时离线` `今天` `本周` `更早`。
- 三层方案 5.5 的"`el-card` + `el-descriptions`"实现建议在本文中替换为上述自绘卡片（一个 `ProposalCard.vue`），以避免 Element Plus 的卡片阴影与描述列表边框。

#### 财务 `/finance`（三角色同一组件）

- 观感：先看到四个大数，再看到一张安静的表。没有图表、没有卡片、没有彩色。
- 结构：
  - 标题行：左"财务"，右月份切换 `‹ 2026年9月 ›`（文字按钮 + 月份）。
  - 汇总条：一行 3–4 个数字，28px 等宽，上方 13px 灰标签，数字之间 64px，下方一条细线：

    ```
    本月成本            公司运营            项目成本            收入
    ¥ 128,400.00        ¥ 36,000.00         ¥ 92,400.00         ¥ 210,000.00
    ```

    STAFF 只见"项目成本"一个数；MANAGER 见前三个，收入按条目 visibility 决定。
  - 明细表：`日期 · 类别 · 项目 · 备注 · 金额`，金额右对齐；OWNER 多一列"可见"（文字：全员/管理层/仅自己）与行尾 `···`（调整）。
  - OWNER 右上第二个动作 [记一笔]（文字按钮）打开抽屉表单；抽屉标题"记一笔"，字段：类型、范围、项目、金额、日期、类别、备注、可见范围，底部 [保存]。
- 文案：`财务` `记一笔` `本月成本` `公司运营` `项目成本` `收入` `日期` `类别` `项目` `备注` `金额` `可见` `保存` `本月还没有记录`。
- 空态：表格区一行"本月还没有记录。"，OWNER 加 [记一笔]。

#### 项目 `/projects` 与 `/projects/:id`（全员）

- 观感：一份项目名录。每行：名称、阶段（文字）、2px 进度条 + 百分比、到期日。
- 列表结构：标题行"项目"，OWNER 右侧 [新建]；筛选只有一个：`进行中 / 已归档` 文字切换；行：

  ```
  星野合作            交付        ━━━━━━━━━━━━░░░░░  68%        10月20日
  ```

- 详情结构：28px 项目名 → 一行元信息（阶段 · 起止日 · 参与人，13px 灰）→ 描述（16px 正文，无则不显示）→ "里程碑"区块（20px 标题）：竖向时间线，每条 `日期 — 标题`，完成的用 ✓ 与 ink-3 颜色；OWNER 有行尾 `···` 与区块右侧 [添加]。
- 改造：删除"设为验收测试项目"勾选与"验收测试" tag（`ProjectsPage.vue` L77、L110-112，先按 T2 改 e2e）；创建/编辑进抽屉；删除 `启用中/已归档` 的每卡片副文本，改为列表筛选；删 F4 的本地合并逻辑。
- 文案：`项目` `新建` `进行中` `已归档` `里程碑` `添加` `还没有项目` `名称` `描述` `阶段` `开始` `到期` `保存`。

#### 网盘 `/drive`（全员）

- 观感：左树右表，像一个安静的文件管理器。拖文件到页面任何位置即上传。
- 结构：
  - 左 240px 目录树：三个根（公司 / 项目 / 老板私有，按角色可见），OWNER 根节点 `···` 可新建子目录。
  - 右侧：面包屑只显示当前路径（文字，不用 `el-breadcrumb`），右侧 [上传]（文字按钮，主要靠拖放）。表：`名称 · 大小 · 日期 · 上传者`；处理中的文件名称前一个灰点，未通过的名称为危险色并在行尾写"未通过"；其余点击名称即下载。
  - 上传进度：右下角一条 320px 宽的浮层，每个文件一行：`文件名 ──── 42%`；完成即消失。没有"本次上传"卡片、没有 `file_id`、没有"检查并获取下载"。
  - OWNER 行尾 `···`：移动 / 重命名 / 删除（删除用 `el-popconfirm`，文字"删除后不可恢复。"）。
- 改造：`DrivePage.vue` 整页重写；`MultipartUploader.vue` 去掉分类与文件日期两个字段（三层方案 P0 已删库字段）、去掉 `<h2>上传文件</h2>`、原生控件换为拖放区；`allowedObjectOrigin` 未配置时不显示红色 alert，而是上传按钮禁用 + 13px 灰字"上传未配置"。
- 文案：`网盘` `上传` `名称` `大小` `日期` `上传者` `未通过` `新建目录` `移动` `重命名` `删除` `删除后不可恢复。` `这个目录是空的` `上传未配置`。

#### 知识库 `/knowledge`（全员，三层 P1）

- 观感：左边目录、右边正文，正文像一篇排好版的文章。
- 结构：左 280px 文档列表 + 顶部一个搜索框（占位"搜索"）；右侧 680px 正文：28px 标题、13px 灰色元信息（更新日期 · 标签）、16px/1.75 正文，标题层级只到 h3。OWNER 右上 [编辑]、[发布]/[下架]（文字按钮）。
- 文案：`知识库` `搜索` `编辑` `发布` `下架` `还没有文档` `没有匹配的内容`。

#### 成员 `/members`（OWNER）

- 观感：一张表，一行一个人。
- 结构：标题"成员"，右上 [添加]；表：`姓名 · 用户名 · 角色 · 最近登录 · ···`；角色用术语表中文；`···`：改角色 / 重置密码 / 禁用（或启用）。添加进抽屉：姓名、用户名、角色（管理层 / 员工），[创建]。创建或重置后弹 `el-dialog`：标题"初始密码"，等宽 20px 密码 + [复制]，下方一行 13px 灰字"关闭后不再显示。"，底部 [关闭]。
- 改造：`UsersPage.vue` 的 `el-tag {{ user.role }}`、`{{ user.status }}` 直出（L160-163）改术语；`el-checkbox-group` 项目分配（L190-210）移除——按三层方案 4.3，参与人在项目详情里维护而非成员页；`globalThis.confirm` 换 `el-popconfirm`，文字只写后果。
- 文案：`成员` `添加` `姓名` `用户名` `角色` `最近登录` `从未登录` `改角色` `重置密码` `禁用` `启用` `禁用后该账号立即退出登录。` `初始密码` `复制` `关闭后不再显示。` `创建` `关闭`。

#### 霜月设置：SOUL `/soul`、记忆 `/memory`、审计 `/audit`（OWNER，账号菜单）

- SOUL：左 2/3 等宽字体编辑器（`--sb-mono`，14px/1.7，无边框只有底色 `--sb-surface`）；右 1/3 版本列表（日期 + 备注，一行一条，当前版本前一个圆点）；顶部右侧 [保存为新版本]、[预览提示词]（文字按钮，预览在抽屉里）。
- 记忆：按类型分组（事实 / 偏好 / 决定 / 项目备注 / 每日纪要）的行列表；每行内容 + 13px 灰日期；行尾 `···`：置顶 / 编辑 / 归档。顶部一个搜索框。
- 审计：表 `时间 · 谁 · 做了什么 · 对象 · 结果`；顶部一个"动作"下拉筛选；不做导出。"做了什么"用术语映射（`agent.card.confirm` → "确认入库"），不显示原始 action 字符串。
- 文案：`SOUL` `保存为新版本` `预览提示词` `版本` `记忆` `搜索` `置顶` `编辑` `归档` `审计` `时间` `谁` `做了什么` `对象` `结果`。

#### 无权访问 `/forbidden`

- 结构：垂直居中 360px 列：标题"没有权限"，一个链接"回到首页"→ `roleHome(role)`。
- 改造：修复 F1；登录后按角色落点，此页只对手输 URL 可达。

### 3.6 当前堆砌 / 廉价感来源（点名，含路径）

| # | 来源 | 位置 | 处置 |
|---|---|---|---|
| C1 | Element Plus 默认色直写：`#409eff` ×4、`#f5f7fa` ×5、`#909399` ×4、`#606266` ×3、`#e4e7ed` ×3、`#dcdfe6` ×2、`#303133`、`#c45656`、`#fff` ×5 | `AppLayout.vue` L42、L50-51、L67、L72；`LoginPage.vue` L91、L99-100、L111、L121；`PasswordChangePage.vue` L104、L112-113、L123；`ProjectsPage.vue` L129；`UsersPage.vue` L242、L267；`DrivePage.vue` L175、L187-189；`DevicesPage.vue` L202、L215；`ImportJobsPage.vue` L122、L137-138、L143、L164 | 全部替换为 `tokens.css` 变量（U0） |
| C2 | "OWNER" 眉题当装饰 | `DrivePage.vue` L110、`ProjectsPage.vue` L69、`UsersPage.vue` L125、`DevicesPage.vue` L110、`ImportJobsPage.vue` L55 | 删 |
| C3 | 机制解释型副标题 | `DrivePage.vue` L112 "上传完成后仅展示本次文件的处理状态。"；`OwnerHomePage.vue` L4 "管理项目与验收测试空间。"；`LoginPage.vue` L47 "使用本地账号继续。"；`PasswordChangePage.vue` L52；`UsersPage.vue` L220 | 删 |
| C4 | UUID 与枚举直出 | `DrivePage.vue` L158 `{{ currentResult.file_id }}`；`ImportJobsPage.vue` L82 `{{ selected.project_id }}`、L99-100 `attachment.kind/file_state`；`UsersPage.vue` L160 `{{ user.role }}`、L162 `{{ user.status }}` | 术语表映射 / 删 |
| C5 | 页顶创建表单 + 卡片列表（卡片套卡片） | `ProjectsPage.vue` L73-115；`UsersPage.vue` L128-212；`DevicesPage.vue` L114-186 | 表单进抽屉；列表改行 |
| C6 | `el-alert` 报错 ×8（带图标的彩色横条） | `LoginPage.vue` L73、`PasswordChangePage.vue` L86、`ProjectsPage.vue` L89、`UsersPage.vue` L148、`DrivePage.vue` L115、L123、`DevicesPage.vue` L147、`ImportJobsPage.vue` L59 | 改内联一行文字 |
| C7 | 原生控件与 Element 混排 | `DrivePage.vue` L135 `<select>`；`MultipartUploader.vue` L104-124 `<input><button><progress>`；`DevicesPage.vue` L118 `<input type=checkbox>`；`ImportJobsPage.vue` L65 `<button>` | 统一 |
| C8 | 三层标题叠加：页 h1"文件上传" → 卡片 → 组件 h2"上传文件" | `DrivePage.vue` L111 + `MultipartUploader.vue` L101 | 只留页面标题 |
| C9 | 按钮文案暴露实现："检查并获取下载""下载本次文件""我已安全保存""我已保存" | `DrivePage.vue` L160、L162；`DevicesPage.vue` L143；`UsersPage.vue` L226 | 行点击即下载；对话框按钮"关闭" |
| C10 | 导航中英混杂、命名不准 | `AppLayout.vue` L20-24："项目 / 文件上传 / 设备 / 导入任务 / Users" | 按 3.2 重排 |
| C11 | `globalThis.confirm` 浏览器原生弹窗 | `UsersPage.vue` L85、L100；`DevicesPage.vue` L93 | `el-popconfirm` |
| C12 | 错误正文带 36 位 request_id | `http.ts` L85-97 及各 `xxxErrorMessage` | 短码 + 复制 |
| C13 | 与内部验收流程相关的产品可见物："设为验收测试项目"勾选、"验收测试" tag | `ProjectsPage.vue` L77、L110-112 | 删（先做 T2） |
| C14 | 全局无字体、无 `lang`、Element 英文 locale | `index.html` L2；`main.ts`；`App.vue` | U0 |

### 3.7 前端目录与基元组件

改造后的目录（只列新增与变化）：

```
web/src/
  styles/tokens.css        变量（3.3）
  styles/element.css       Element Plus 覆盖（3.3）
  styles/base.css          reset、字体、tabular-nums、链接色
  app/router.ts            懒加载路由；meta.roles 三值；meta.width: 'read' | 'table'
  app/navigation.ts        角色 → 导航项；roleHome(role)
  copy/glossary.ts         枚举 → 中文（3.4 术语表）
  copy/errors.ts           错误码 → 一句话
  copy/pages/*.ts          各页文案常量
  api/parse.ts             唯一一份 isRecord/hasKeys/safeText（F8）
  api/errors.ts            HttpClientError → code；替代 5 个 xxxErrorMessage
  components/ui/           八个基元（下表）
  components/chat/ProposalCard.vue
  layouts/AppShell.vue     顶栏 + 账号菜单 + 内容宽度
  pages/{chat,finance,projects,drive,knowledge,members,audit,soul,memory,auth}/
```

八个基元组件（不多于此数；新增需在本文登记）：

| 组件 | 职责 | 禁止 |
|---|---|---|
| `PageHeader` | 28px 标题 + 右侧 ≤ 2 个动作 | 副标题、眉题、面包屑 |
| `RowList` / `Row` | 细线分隔的行列表，行高 48，支持行尾 `···` | 卡片、阴影 |
| `KeyValue` | 两列键值（13px 灰 / 14px 值），用于卡片与详情 | 三列以上 |
| `InlineError` | 一行 13px 危险色 + 可选短码 | 图标、底色 |
| `EmptyLine` | 一行文字 + 可选一个文字按钮 | 插图、第二句 |
| `Money` | `¥ 8,000.00`，tabular，右对齐 | 颜色区分正负（用符号） |
| `DateText` | 统一日期/时间格式（3.4 第 7 条） | 页面自行 `Intl` |
| `Dot` | 6px 状态圆点（ok/warn/danger/muted） | 彩色 tag |

---

## 四、工程与产品优化意见（按优先级）

**P0 — 阻塞三层方案落地或决定后续 8 个页面质量**

1. **UI 体系先行**（本文 3.3、3.7）：`tokens.css`、`element.css`、`base.css`、`AppShell`、八个基元、`copy/`、`navigation.ts`。在任何新页面之前合并；用 3 个现有页面（登录、项目、成员）作为样板重写，证明体系可用。
2. **路由与角色**：`roleHome(role)`；三值 `RouteMeta.roles`；懒加载；修 F1 死循环；`/forbidden` 只对直达 URL。与三层方案 P0 步骤 2 同一 PR。
3. **文案与测试解耦**：`copy/` 常量化；`web/tests` 用 `getByRole(name: copy.x)` 或 `data-testid`；此后改文案不改测试。
4. **服务端小收敛**（与三层 P0 步骤 2 同 PR）：`core/errors.py` 默认码改为中性 `FORBIDDEN / NOT_FOUND / CONFLICT`，模块自带具体码；`get_session` 收敛到 `core/db.py` 一份并统一提交时机（S2）；`project.list / project.read` 不再写 SUCCESS 审计，只保留 DENIED 与写操作（S3）；`AuthUserRead` 加 `id`（S4）。
5. **删除 D1–D6**：按三层方案执行；本文补充删除 `DevicesPage/ImportJobsPage` 时连带删除 C1–C11 中属于这两页的条目，`AppLayout` 直接被 `AppShell` 取代。

**P1 — 显著降低新页面开发成本**

6. **接口为展示而设计**：列表接口返回展示所需字段（网盘行带上传者姓名、财务行带项目名），前端不做二次拼接；金额一律整数分，日期一律 ISO；除审计外不分页（≤ 1000 行由前端处理）。
7. **前端 API 层收敛**：`api/parse.ts` 一份校验工具、`api/errors.ts` 一份 code→文案；各模块只保留类型与请求。删除 5 个 `xxxContractError`，统一为 `ApiContractError`。
8. **上传组件重写**：拖放区 + 右下进度浮层（3.5 网盘）；去掉分类与日期字段；`UploadUserError` 三个 code 映射到 `copy/errors.ts`。
9. **Element Plus 按需引入**（可选，H2）：`unplugin-vue-components` + `ElementPlusResolver`，去掉 `dist/index.css` 全量引入，改由按需样式 + `element.css` 覆盖。只在体积或首屏成为可感知问题时做。

**P2 — 打磨**

10. **视觉回归门**：Playwright 对 6 个关键页面（登录、对话、财务、项目详情、网盘、成员）截图入库，PR 中像素差异 > 0.5% 需老板或指定人过目。这是防止"高级感"随迭代退化的唯一机械手段。
11. **财务打印样式**：`@media print` 隐藏导航、汇总条与表格黑白排版——三层方案 P2 "月报一页纸"的低成本实现。
12. **键盘**：`/chat` 中 `Cmd/Ctrl+K` 新对话、`Esc` 关抽屉。不做更多快捷键。

---

## 五、分阶段迭代方案

与三层方案的阶段对齐关系：**U0 先于并伴随三层 P0 步骤 1–2；U1 与三层 P0 步骤 3–7 逐页配对；U2 对应三层 P1；U3 对应三层 P2。** 每阶段独立可合并，验收以 `rg` 命令为准，避免主观争论。

### U0：立规矩（与三层 P0 步骤 1–2 并行；三层步骤 3 起的所有新页面以此为前提）

**做**

1. 在本机跑起当前前端，截 6 张"改造前"图放入 `docs/ui/before/`（H1）。
2. `styles/tokens.css`、`element.css`、`base.css`；`App.vue` 传 `zhCn` locale；`index.html lang="zh-CN"`。
3. `AppShell.vue` 替代 `AppLayout.vue`；`navigation.ts`、`roleHome`；路由懒加载、三值 roles、`meta.width`；修 F1、F3。
4. 八个基元组件 + `copy/glossary.ts`、`copy/errors.ts`、`api/parse.ts`、`api/errors.ts`。
5. 用体系重写三个现有页作为样板：登录 / 首登改密、项目（先只做列表 + 抽屉新建，字段仍是现有的 `name`）、成员（现有字段）。同步改这三页的测试引用 `copy/`。
6. 服务端 P0 第 4 条（错误码、`get_session`、审计噪音、`me.id`）。
7. T2：e2e fixture 改为按名称查找，然后删 `is_test` 勾选与 tag。

**验收（全部为零或全部通过）**

```bash
# 1 组件与页面中无写死色值
rg -n '#[0-9a-fA-F]{3,6}\b' web/src/pages web/src/components web/src/layouts        # 期望：无输出
# 2 禁用组件与原生弹窗
rg -n '<el-alert|<el-card|globalThis.confirm|window.confirm' web/src                # 期望：无输出
# 3 语言标记
rg -n 'lang="en"' web/index.html                                                    # 期望：无输出
# 4 模板中不直出枚举文本（>OWNER< 这类紧贴标签的字面量）
rg -n '>(OWNER|MANAGER|STAFF|ACTIVE|DISABLED|CLEAN|INFECTED|FAILED)<' web/src        # 期望：无输出
# 5 模板中不直出 id
rg -n '\{\{[^}]*_id[^}]*\}\}' web/src/pages web/src/components                      # 期望：无输出
# 6 每页最多一个实心主按钮
rg -c 'type="primary"' web/src/pages | awk -F: '$2 > 1'                             # 期望：无输出
# 7 万能错误话术只允许出现在 copy/ 下
rg -n '暂时无法|请稍后重试' web/src --glob '!web/src/copy/**'                         # 期望：无输出
# 8 测试不写中文原文
rg -n "'[^']*[\x{4e00}-\x{9fff}]" web/tests --pcre2                                  # 期望：无输出
```

- STAFF 账号登录落 `/projects`，导航四项，账号菜单两项；手输 `/members` 见"没有权限"，点"回到首页"回 `/projects`。
- CI 绿。

### U1：逐页建设（与三层 P0 步骤 3–7 一一配对）

每个三层 P0 步骤的 PR 必须同时满足本文 3.5 对应页面的结构与文案表；页面 PR 描述中贴一张截图。

| 三层 P0 步骤 | 页面 | 本文规格 | 额外验收 |
|---|---|---|---|
| 3 项目 v2 | `/projects`、`/projects/:id` | 3.5 项目 | 列表行高 48；进度条 2px；详情无卡片 |
| 4 网盘 v1 | `/drive` | 3.5 网盘 | 拖放上传；无 `file_id`；进度浮层完成即消失 |
| 5 财务 v1 | `/finance` | 3.5 财务 | 三角色同一组件，用 e2e 断言 STAFF 汇总条只有一个数 |
| 6 霜月 v1 | `/chat`、`/soul`、`/memory` | 3.5 对话与设置 | 空会话页只有输入框；提案卡三动作仅一个实心；已入库折叠一行 |
| 7 审计 | `/audit` | 3.5 审计 | action 全部经术语映射 |
| — 成员 | `/members` | 3.5 成员 | 角色中文；初始密码对话框只有一行说明 |

阶段末：删除 `docs/ui/before/`，补 `docs/ui/after/` 六张图；`rg` 验收集全部保持为零。

### U2：阅读与流式（对应三层 P1）

- 知识库阅读布局（3.5 知识库）；对话 SSE 流式时保持"无气泡、无打字机音效、三个点"；提案卡"修改"改内联字段编辑；财务按项目 × 月透视仍以表格呈现，不引入图表。
- 视觉回归门（P2 第 10 条）在此阶段启用，因为页面已趋于稳定。

### U3：打磨（对应三层 P2）

- 财务打印样式；`Cmd/Ctrl+K`；Element Plus 按需引入（若做）；复核 3.6 清单确认无回潮；本文归档到 `docs/archive/` 并在三层方案中留一行引用。

---

## 六、明确暂不做

- 深色模式、主题切换。
- 任何图表、仪表盘、KPI 卡片墙；财务用表格与大数表达。
- 插图式空态、引导教程（tour）、新手提示气泡、"了解更多"链接、帮助中心。
- 导航图标、彩色状态标签、徽标计数、通知中心。
- Webfont 下载（包括 Inter 的在线引用）；系统字体足够。
- 移动端优先设计；只保证 ≥ 1024px 正常、≤ 760px 不破版。
- 国际化（英文界面）；`copy/` 只是集中管理，不是 i18n 框架。
- 动效库、页面转场、骨架屏常驻。
- 自定义组件库替代 Element Plus；覆盖变量已足够。
- 员工/管理层任何形式的霜月入口或"只读问答"（与三层方案第九节一致）。

---

## 七、给执行者（Grok Build / 设计开发）的交付约束

执行本文任何一页时，遵守以下约束，避免"再发明"：

1. 只能使用 3.3 的变量、3.7 的八个基元与 3.3 允许清单中的 Element Plus 组件。需要新组件先在本文 3.7 表格登记。
2. 页面上出现的每一个字符串必须能在 3.5 对应页面的"文案"行或 3.4 术语表中找到；找不到就不写。**不要**添加占位说明、tooltip、帮助文字、"温馨提示"。
3. 不解决用户没有的问题：不做批量操作、不做高级筛选、不做导出（除三层 P1 财务 CSV）、不做排序控件（默认排序即可）。
4. 交付物包含：截图 1 张（1280 宽）、第五章对应阶段的 `rg` 验收输出、改动的 `copy/` 常量清单。
5. 遇到本文与三层方案冲突：功能与权限以三层方案为准，观感与文案以本文为准；若仍无法调和，停下来记入下表，不要自行折中。

### 待老板拍板的决策点（本文新增，编号接续三层方案的 A–E）

| 编号 | 问题 | 本文默认 |
|---|---|---|
| F | 强调色：墨蓝灰 `#2E3A46`（编辑部感）还是偏冷的 `#34506B`（呼应"霜月"） | 墨蓝灰 |
| G | 对话页是否保留左侧会话栏（可折叠） | 保留，默认展开 |
| H | 财务汇总条是否显示"收入"给管理层 | 按条目 visibility，默认管理层可见（与三层决策点 E 一致） |
| I | 是否启用视觉回归门（U2 起，每次 UI PR 需人看图） | 启用 |
