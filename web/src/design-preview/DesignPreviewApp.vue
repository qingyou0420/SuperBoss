<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import Icon from './Icon.vue'
import ProjectWorkspace from './ProjectWorkspace.vue'
import BusinessMapWorkspace from './BusinessMapWorkspace.vue'
import FinanceWorkspace from './FinanceWorkspace.vue'
import KnowledgeWorkspace from './KnowledgeWorkspace.vue'
import OverviewWorkspace from './OverviewWorkspace.vue'
import { registry } from './registry'
import {
    changeLog,
    projects,
    shortDate,
    type PreviewRole,
    type PreviewView,
} from './previewState'

const role = ref<PreviewRole>('owner')
const view = ref<PreviewView>('home')
const selectedProject = ref('p1')
const mapQuery = ref('')
const mobileNav = ref(false)
const notificationsOpen = ref(false)
const searchOpen = ref(false)
const searchQuery = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const toast = ref('')
let toastTimer: ReturnType<typeof globalThis.setTimeout> | undefined
const assistantOpen = ref(false)
const assistantInput = ref('')
const assistantBody = ref<InstanceType<typeof globalThis.HTMLElement> | null>(
    null,
)
const attachedNames = ref<string[]>([])
const assistantMessages = ref<
    Array<{
        id: number
        kind: 'user' | 'assistant'
        content: string
        source?: PreviewView
        sourceLabel?: string
    }>
>([])
const fileInput = ref<HTMLInputElement | null>(null)
const isOwner = computed(() => role.value === 'owner')
const isExecutive = computed(
    () => role.value === 'staff' || role.value === 'lead',
)
const roleLabels: Record<PreviewRole, string> = {
    owner: '老板 · 清游',
    staff: '执行部 · 普通员工',
    lead: '执行部 · 主负责人',
    shareholder: '股东 · 经营查看',
}
const roleNames: Record<PreviewRole, string> = {
    owner: '清游',
    staff: '陈宁',
    lead: '林悦',
    shareholder: '股东',
}
const allNav: Array<{
    id: PreviewView
    name: string
    icon: string
    roles: PreviewRole[]
}> = [
    {
        id: 'home',
        name: '工作台',
        icon: 'grid',
        roles: ['owner', 'staff', 'lead'],
    },
    {
        id: 'projects',
        name: '会务项目',
        icon: 'projects',
        roles: ['owner', 'staff', 'lead', 'shareholder'],
    },
    {
        id: 'map',
        name: '业务地图',
        icon: 'map',
        roles: ['owner', 'staff', 'lead'],
    },
    {
        id: 'knowledge',
        name: '经验知识库',
        icon: 'book',
        roles: ['owner', 'staff', 'lead'],
    },
    {
        id: 'overview',
        name: '经营总览',
        icon: 'chart',
        roles: ['owner', 'shareholder'],
    },
    { id: 'finance', name: '财务台账', icon: 'wallet', roles: ['owner'] },
]
const nav = computed(() =>
    allNav.filter((item) => item.roles.includes(role.value)),
)
const currentTitle = computed(() =>
    view.value === 'tender'
        ? '招投标代理'
        : allNav.find((item) => item.id === view.value)?.name || '工作台',
)
const activeProjects = computed(() =>
    projects.filter((project) => project.status === 'active'),
)
const p1 = computed(() => projects.find((project) => project.id === 'p1')!)
const nextItems = computed(() =>
    activeProjects.value
        .slice()
        .sort((a, b) => a.nextDate.localeCompare(b.nextDate)),
)
const days = [7, 8, 9, 10, 11, 12, 13]
function projectOnDay(day: number) {
    return activeProjects.value.find(
        (project) =>
            project.nextDate === `2026-09-${String(day).padStart(2, '0')}`,
    )
}
function selectCalendarDay(day: number) {
    const project = projectOnDay(day)
    if (project) openProject(project.id)
    else notify(`9 月 ${day} 日没有待办节点；可从项目页面查看完整安排。`)
}
const searchResults = computed(() => {
    const q = searchQuery.value.trim()
    if (!q)
        return nav.value.map((item) => ({
            id: item.id,
            title: item.name,
            subtitle: '工作台页面',
            view: item.id,
            project: '',
            query: '',
        }))
    const pages = nav.value
        .filter((item) => item.name.includes(q))
        .map((item) => ({
            id: item.id,
            title: item.name,
            subtitle: '工作台页面',
            view: item.id,
            project: '',
            query: '',
        }))
    const matchingProjects = projects
        .filter((project) => `${project.name}${project.service}`.includes(q))
        .map((project) => ({
            id: project.id,
            title: project.name,
            subtitle: `${project.service} · ${project.stage}`,
            view: 'projects' as PreviewView,
            project: project.id,
            query: '',
        }))
    const estates =
        role.value === 'shareholder'
            ? []
            : registry
                  .filter((item) =>
                      `${item.name}${item.street}${item.community}`.includes(q),
                  )
                  .slice(0, 6)
                  .map((item) => ({
                      id: item.id,
                      title: item.name,
                      subtitle: `${item.district} · ${item.street || '街道待核实'} · 原始名录`,
                      view: 'map' as PreviewView,
                      project: '',
                      query: item.name,
                  }))
    return [...pages, ...matchingProjects, ...estates].slice(0, 9)
})

function notify(message: string) {
    toast.value = message
    globalThis.clearTimeout(toastTimer)
    toastTimer = globalThis.setTimeout(() => {
        toast.value = ''
    }, 6000)
}
function navigate(target: PreviewView) {
    if (target !== 'tender' && !nav.value.some((item) => item.id === target))
        return
    view.value = target
    mobileNav.value = false
    notificationsOpen.value = false
    globalThis.history.replaceState(
        null,
        '',
        `${globalThis.location.pathname}${globalThis.location.search}#${target}`,
    )
    globalThis.scrollTo({ top: 0 })
}
function openProject(id: string) {
    selectedProject.value = id
    navigate('projects')
}
function onSearchResult(result: (typeof searchResults.value)[number]) {
    if (result.project) selectedProject.value = result.project
    if (result.query) mapQuery.value = result.query
    navigate(result.view)
    searchOpen.value = false
}
async function openSearch() {
    searchOpen.value = true
    await nextTick()
    searchInput.value?.focus()
}
function ask(prompt = '') {
    if (!isOwner.value) return
    assistantOpen.value = true
    if (prompt) assistantInput.value = prompt
}
function sendSuggestedPrompt(prompt: string) {
    assistantInput.value = prompt
    void sendMessage()
}
function openAssistantSource(source: PreviewView) {
    navigate(source)
    assistantOpen.value = false
}
async function sendMessage() {
    const prompt = assistantInput.value.trim()
    if (!prompt) return
    assistantMessages.value.push({
        id: Date.now(),
        kind: 'user',
        content: prompt,
    })
    assistantInput.value = ''
    let content = ''
    let source: PreviewView = 'projects'
    let sourceLabel = '查看项目与日程'
    if (
        /地图|小区|业务|联系人|沟通|名录/.test(prompt) &&
        !/云栖里|澄湖|南溪|延期|排期/.test(prompt)
    ) {
        source = 'map'
        sourceLabel = '查看业务地图'
        content =
            '两份名录保留了304条原始记录，具体小区位置和业委会情况仍需核实。你可以在地图中找到小区，再用“记录沟通”体验需求、联系人和时间怎样挂到档案。此处为对话演示，不会写入真实业务。'
    } else if (/财务|费用|奖金|凭证|入账|账|报销/.test(prompt)) {
        source = 'finance'
        sourceLabel = '查看财务登记'
        content =
            '项目直接成本额度为约定服务费的20%；节余计为奖金，超支从另计的10%执行部抽成中抵扣。尾款收齐后的下一个20日结算。财务页可以试算并体验示例导入；你选取的真实文件在本预览中不会被解析或上传。'
    } else if (/知识|经验|历史|模板|文件|资料包/.test(prompt)) {
        source = 'knowledge'
        sourceLabel = '查看经验版本链'
        content =
            '经验按历史项目、阶段、文件和版本组织。每次修改保留原因与依据，参考定稿由你发布。制作与审核继续使用Kimi或WorkBuddy。本预览的案例仅展示整理方式，不是可用于业务的正式文件。'
    } else if (/利润|经营|曲线|股东|发展|收入/.test(prompt)) {
        source = 'overview'
        sourceLabel = '查看经营总览'
        content =
            '经营总览分别展示完结项目成果、实际收付和已确定的在手业务。图表中的金额是演示数据，可以切换期间和点击月份查看。未来业务以实际记录为依据，未确定回款日不会被当成确定收入。'
    } else if (/延期|顺延|调期|推迟|提前/.test(prompt)) {
        content = `可以先查看${p1.value.name}的调期影响。项目页中选择调整范围、天数和原因，会同时列出前后日期；应用演示后，后续节点和工作台日期一起更新，已完成阶段保留。`
        sourceLabel = '打开调期预览'
        selectedProject.value = 'p1'
    } else {
        content = `目前有${activeProjects.value.length}个会务项目在执行。${p1.value.name}当前处于“${p1.value.stage}”，下一节点日期为${shortDate(p1.value.nextDate)}。点击来源可以查看完整日程、准备事项和阶段材料。`
    }
    assistantMessages.value.push({
        id: Date.now() + 1,
        kind: 'assistant',
        content,
        source,
        sourceLabel,
    })
    await nextTick()
    assistantBody.value?.scrollTo({
        top: assistantBody.value.scrollHeight,
        behavior: 'smooth',
    })
}
function onAttach(event: Event) {
    const files = (event.target as HTMLInputElement).files
    attachedNames.value = files
        ? Array.from(files).map((file) => file.name)
        : []
    if (attachedNames.value.length)
        notify('文件仅在本地预览中显示名称，未上传或解析。')
}
function closeOverlays() {
    searchOpen.value = false
    assistantOpen.value = false
    notificationsOpen.value = false
    mobileNav.value = false
}
watch(role, () => {
    globalThis.clearTimeout(toastTimer)
    toast.value = ''
    assistantOpen.value = false
    searchOpen.value = false
    notificationsOpen.value = false
    navigate(role.value === 'shareholder' ? 'overview' : 'home')
})
const hash = globalThis.location.hash.slice(1) as PreviewView
if (allNav.some((item) => item.id === hash) || hash === 'tender')
    view.value = hash
</script>

<template>
    <div class="design-app" @keydown.esc="closeOverlays">
        <div
            v-if="mobileNav"
            class="mobile-scrim"
            @click="mobileNav = false"
        ></div>
        <aside class="main-sidebar" :class="{ 'mobile-open': mobileNav }">
            <a
                href="#home"
                class="sidebar-brand"
                @click.prevent="
                    navigate(role === 'shareholder' ? 'overview' : 'home')
                "
                ><span class="brand-symbol">S</span><span>SuperBoss</span></a
            >
            <div class="sidebar-company">
                <div>
                    <strong>鹭行 · 漳州分公司</strong>
                </div>
            </div>
            <div class="nav-group-label">工作空间</div>
            <nav class="main-nav" aria-label="工作空间导航">
                <button
                    v-for="item in nav"
                    :key="item.id"
                    :class="{ active: view === item.id }"
                    @click="navigate(item.id)"
                >
                    <Icon :name="item.icon" :size="18" /><span>{{
                        item.name
                    }}</span
                    ><small v-if="item.id === 'projects'">{{
                        activeProjects.length
                    }}</small>
                </button>
            </nav>
            <div class="sidebar-reserved">
                <div class="nav-group-label">后续业务</div>
                <button
                    :class="{ active: view === 'tender' }"
                    @click="navigate('tender')"
                >
                    <Icon name="building" :size="18" /><span>招投标代理</span
                    ><small>预留</small>
                </button>
            </div>
            <div class="sidebar-bottom">
                <button v-if="isOwner" class="sidebar-assistant" @click="ask()">
                    <Icon name="spark" :size="18" />
                    <div>
                        <strong>霜月助手</strong>
                    </div>
                    <Icon name="arrow-right" :size="16" />
                </button>
                <div class="sidebar-footer">
                    <span>SUPERBOSS</span><small>漳州 / 福建</small>
                </div>
            </div>
        </aside>

        <div class="main-frame">
            <header class="topbar">
                <div class="topbar-location">
                    <button
                        class="sd-icon-button mobile-menu"
                        aria-label="打开导航"
                        @click="mobileNav = !mobileNav"
                    >
                        <Icon name="menu" /></button
                    ><span>工作空间</span
                    ><Icon name="chevron-right" :size="13" /><strong>{{
                        currentTitle
                    }}</strong>
                </div>
                <button class="global-search" @click="openSearch">
                    <Icon name="search" :size="16" /><span
                        >搜索项目、小区、经验</span
                    ><kbd>搜索</kbd>
                </button>
                <div class="topbar-actions">
                    <button
                        class="notification-button sd-icon-button"
                        :aria-expanded="notificationsOpen"
                        aria-label="查看项目动态"
                        @click="notificationsOpen = !notificationsOpen"
                    >
                        <Icon name="bell" /><i></i>
                    </button>
                    <div class="topbar-divider"></div>
                    <span class="account-avatar">{{
                        roleNames[role].slice(0, 1)
                    }}</span
                    ><span class="account-name"
                        >{{ roleNames[role]
                        }}<small>{{
                            isOwner
                                ? '老板'
                                : role === 'shareholder'
                                  ? '经营查看'
                                  : '执行部'
                        }}</small></span
                    >
                </div>
            </header>
            <div class="preview-strip">
                <div>
                    <span class="preview-mark">设计预览</span
                    ><span
                        class="preview-description"
                        title="项目、金额与经验为演示数据；名录来自提供的 Excel。预览操作不写入正式业务，刷新后恢复。"
                        >演示数据 · 不保存</span
                    >
                </div>
                <label
                    ><span>当前视角</span
                    ><select v-model="role" aria-label="切换预览身份">
                        <option
                            v-for="(label, value) in roleLabels"
                            :key="value"
                            :value="value"
                        >
                            {{ label }}
                        </option>
                    </select></label
                >
            </div>

            <main class="workspace-content">
                <section v-if="view === 'home'" class="home-workspace">
                    <header class="sd-page-heading">
                        <div>
                            <h1>
                                工作台
                                <span class="heading-date"
                                    >2026年9月10日 · 星期四</span
                                >
                            </h1>
                        </div>
                        <button
                            v-if="isOwner"
                            class="sd-button sd-button--primary"
                            @click="ask('帮我记录一项新的业务需求')"
                        >
                            <Icon name="plus" :size="16" />记录新业务</button
                        ><button
                            v-else
                            class="sd-button"
                            @click="navigate('projects')"
                        >
                            <Icon name="calendar" :size="16" />查看项目日程
                        </button>
                    </header>

                    <div class="home-metrics">
                        <button @click="navigate('projects')">
                            <span class="metric-icon"
                                ><Icon name="projects" :size="20"
                            /></span>
                            <div>
                                <span>执行中项目</span
                                ><strong
                                    >{{ activeProjects.length
                                    }}<small>个</small></strong
                                >
                            </div></button
                        ><button @click="navigate('projects')">
                            <span class="metric-icon blue"
                                ><Icon name="calendar" :size="20"
                            /></span>
                            <div>
                                <span>近期节点</span
                                ><strong
                                    >{{ nextItems.length
                                    }}<small>项</small></strong
                                >
                            </div></button
                        ><button v-if="isOwner" @click="navigate('finance')">
                            <span class="metric-icon sand"
                                ><Icon name="wallet" :size="20"
                            /></span>
                            <div>
                                <span>待回款</span
                                ><strong>18,000<small>元</small></strong>
                            </div></button
                        ><button v-else @click="notificationsOpen = true">
                            <span class="metric-icon sand"
                                ><Icon name="clock" :size="20"
                            /></span>
                            <div>
                                <span>项目动态</span
                                ><strong
                                    >{{ changeLog.length
                                    }}<small>条</small></strong
                                >
                            </div>
                        </button>
                    </div>

                    <div class="home-main-grid">
                        <section class="sd-panel home-projects">
                            <div class="home-panel-heading sd-section-title">
                                <div>
                                    <h2>
                                        进行中项目
                                        <span>{{ activeProjects.length }}</span>
                                    </h2>
                                </div>
                                <button
                                    class="sd-link"
                                    @click="navigate('projects')"
                                >
                                    全部项目
                                    <Icon name="arrow-right" :size="15" />
                                </button>
                            </div>
                            <div class="sd-table-wrap">
                                <table class="sd-table">
                                    <thead>
                                        <tr>
                                            <th>项目 / 业务</th>
                                            <th>当前阶段</th>
                                            <th>下一节点</th>
                                            <th>主负责人</th>
                                            <th></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <tr
                                            v-for="project in activeProjects"
                                            :key="project.id"
                                            tabindex="0"
                                            role="button"
                                            :aria-label="`查看${project.name}项目`"
                                            @click="openProject(project.id)"
                                            @keydown.enter="
                                                openProject(project.id)
                                            "
                                        >
                                            <td>
                                                <div class="home-project-name">
                                                    <span>{{
                                                        project.name.slice(0, 1)
                                                    }}</span>
                                                    <div>
                                                        <strong>{{
                                                            project.name
                                                        }}</strong
                                                        ><small>{{
                                                            project.service
                                                        }}</small>
                                                    </div>
                                                </div>
                                            </td>
                                            <td>
                                                <span
                                                    class="sd-pill"
                                                    :class="{
                                                        'sd-pill--blue':
                                                            project.id === 'p2',
                                                        'sd-pill--neutral':
                                                            project.id === 'p3',
                                                    }"
                                                    >{{ project.stage }}</span
                                                >
                                                <div class="mini-progress">
                                                    <i
                                                        :style="{
                                                            width: `${project.progress}%`,
                                                        }"
                                                    ></i>
                                                </div>
                                            </td>
                                            <td class="home-next-date">
                                                {{
                                                    shortDate(project.nextDate)
                                                }}
                                            </td>
                                            <td>
                                                <span class="lead-avatar">{{
                                                    project.lead.slice(0, 1)
                                                }}</span
                                                >{{ project.lead }}
                                            </td>
                                            <td>
                                                <Icon
                                                    name="chevron-right"
                                                    :size="16"
                                                />
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        </section>
                        <aside class="sd-panel upcoming-panel">
                            <div class="sd-section-title">
                                <h2>近期节点</h2>
                                <Icon name="clock" :size="17" />
                            </div>
                            <button
                                v-for="(project, index) in nextItems"
                                :key="project.id"
                                class="upcoming-item"
                                @click="openProject(project.id)"
                            >
                                <div class="upcoming-time">
                                    <i :class="{ first: index === 0 }"></i
                                    ><span>{{
                                        shortDate(project.nextDate)
                                    }}</span>
                                </div>
                                <strong>{{ project.name }}</strong>
                                <p>
                                    {{
                                        project.id === 'p1'
                                            ? '核对下一阶段公告与公示材料'
                                            : project.id === 'p3'
                                              ? '整理筹备信息及对应文件'
                                              : '准备开箱统计和结果记录材料'
                                    }}
                                </p>
                            </button>
                        </aside>
                    </div>

                    <div class="home-bottom-grid">
                        <section class="sd-panel recent-changes">
                            <div class="sd-section-title">
                                <h2>项目动态</h2>
                            </div>
                            <button
                                v-for="item in changeLog.slice(0, 2)"
                                :key="item.id"
                                @click="openProject(item.projectId)"
                            >
                                <span class="change-dot"></span>
                                <div>
                                    <strong
                                        >{{
                                            projects.find(
                                                (project) =>
                                                    project.id ===
                                                    item.projectId,
                                            )?.name
                                        }}<span>{{ item.kind }}</span></strong
                                    >
                                    <p>{{ item.text }}</p>
                                    <small>{{ item.date }}</small>
                                </div>
                                <Icon name="chevron-right" :size="16" />
                            </button>
                        </section>
                        <section class="week-calendar sd-panel">
                            <div>
                                <strong>本周日程</strong><span>2026年9月</span
                                ><Icon name="calendar" :size="16" />
                            </div>
                            <div class="week-days">
                                <button
                                    v-for="(day, index) in days"
                                    :key="day"
                                    :class="{
                                        today: day === 10,
                                        event: Boolean(projectOnDay(day)),
                                    }"
                                    @click="selectCalendarDay(day)"
                                >
                                    <small>{{
                                        [
                                            '一',
                                            '二',
                                            '三',
                                            '四',
                                            '五',
                                            '六',
                                            '日',
                                        ][index]
                                    }}</small
                                    ><strong>{{ day }}</strong
                                    ><i></i>
                                </button>
                            </div>
                            <div class="calendar-next">
                                <i></i
                                ><span
                                    >{{ shortDate(p1.nextDate)
                                    }}<strong
                                        >{{ p1.name }} · 下一节点</strong
                                    ></span
                                ><button
                                    class="sd-icon-button"
                                    aria-label="查看下一个节点"
                                    @click="openProject('p1')"
                                >
                                    <Icon name="chevron-right" :size="17" />
                                </button>
                            </div>
                        </section>
                    </div>
                </section>

                <KeepAlive
                    ><ProjectWorkspace
                        v-if="view === 'projects'"
                        :role="role"
                        :initial-project="selectedProject"
                        @ask="ask"
                        @notify="notify" /><BusinessMapWorkspace
                        v-else-if="view === 'map' && role !== 'shareholder'"
                        :role="role"
                        :initial-query="mapQuery"
                        @ask="ask"
                        @notify="notify" /><KnowledgeWorkspace
                        v-else-if="
                            view === 'knowledge' && role !== 'shareholder'
                        "
                        :role="role"
                        @ask="ask"
                        @notify="notify" /><OverviewWorkspace
                        v-else-if="view === 'overview' && !isExecutive"
                        @navigate="navigate"
                        @ask="ask" /><FinanceWorkspace
                        v-else-if="view === 'finance' && isOwner"
                        :role="role"
                        @ask="ask"
                        @notify="notify"
                /></KeepAlive>
                <section v-if="view === 'tender'" class="tender-preview">
                    <div class="tender-illustration">
                        <Icon name="building" :size="55" />
                    </div>
                    <h1>招投标代理</h1>
                    <p>功能待开放</p>
                    <div class="tender-scope">
                        <div>
                            <Icon name="check" :size="17" /><span
                                >在业务地图记录招标需求</span
                            >
                        </div>
                        <div>
                            <Icon name="check" :size="17" /><span
                                >在经营报表保留招标分类</span
                            >
                        </div>
                        <div>
                            <Icon name="clock" :size="17" /><span
                                >流程、节点和文书模板后续加入</span
                            >
                        </div>
                    </div>
                    <button
                        class="sd-button sd-button--primary"
                        @click="
                            navigate(
                                role === 'shareholder' ? 'overview' : 'map',
                            )
                        "
                    >
                        {{
                            role === 'shareholder'
                                ? '查看经营分类'
                                : '查看业务地图'
                        }}<Icon name="arrow-right" :size="16" />
                    </button>
                </section>
                <footer class="workspace-footer">
                    <span>鹭行 · 漳州分公司</span><span>SuperBoss V1</span>
                </footer>
            </main>
        </div>

        <section
            v-if="notificationsOpen"
            class="notifications-popover sd-panel"
            aria-label="项目动态"
        >
            <header>
                <strong>项目动态</strong
                ><button
                    class="sd-icon-button"
                    aria-label="关闭动态"
                    @click="notificationsOpen = false"
                >
                    <Icon name="close" :size="16" />
                </button>
            </header>
            <button
                v-for="item in changeLog"
                :key="item.id"
                @click="openProject(item.projectId)"
            >
                <small>{{ item.kind }} · {{ item.date }}</small>
                <p>{{ item.text }}</p>
            </button>
        </section>
        <div
            v-if="searchOpen"
            class="sd-dialog-overlay search-overlay"
            @click.self="searchOpen = false"
        >
            <section
                class="search-dialog sd-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="search-title"
            >
                <h2 id="search-title" class="sd-visually-hidden">
                    搜索工作空间
                </h2>
                <div class="search-dialog-input">
                    <Icon name="search" :size="21" /><input
                        ref="searchInput"
                        v-model="searchQuery"
                        placeholder="搜索项目、小区、街道或页面"
                        aria-label="全局搜索"
                        @keydown.enter="
                            searchResults[0] && onSearchResult(searchResults[0])
                        "
                    /><button
                        class="sd-icon-button"
                        aria-label="关闭搜索"
                        @click="searchOpen = false"
                    >
                        <Icon name="close" :size="17" />
                    </button>
                </div>
                <p class="search-caption">
                    {{
                        searchQuery
                            ? `找到 ${searchResults.length} 项匹配`
                            : '快速前往'
                    }}
                </p>
                <button
                    v-for="result in searchResults"
                    :key="result.id"
                    class="search-result"
                    @click="onSearchResult(result)"
                >
                    <Icon
                        :name="
                            result.view === 'map'
                                ? 'pin'
                                : result.view === 'projects'
                                  ? 'projects'
                                  : 'grid'
                        "
                        :size="18"
                    /><span
                        ><strong>{{ result.title }}</strong
                        ><small>{{ result.subtitle }}</small></span
                    ><Icon name="arrow-right" :size="16" />
                </button>
                <p v-if="!searchResults.length" class="no-search-results">
                    没有匹配结果。试试“小区名称”或“项目”。
                </p>
            </section>
        </div>

        <div
            v-if="assistantOpen && isOwner"
            class="assistant-scrim"
            @click.self="assistantOpen = false"
        >
            <aside
                class="assistant-drawer"
                role="dialog"
                aria-modal="true"
                aria-labelledby="assistant-title"
            >
                <header>
                    <div class="assistant-drawer-avatar">
                        <Icon name="moon" :size="24" />
                    </div>
                    <div>
                        <h2 id="assistant-title">霜月</h2>
                        <span>记录 · 查询 · 排期辅助</span>
                    </div>
                    <button
                        class="sd-icon-button"
                        aria-label="关闭霜月对话"
                        @click="assistantOpen = false"
                    >
                        <Icon name="close" />
                    </button>
                </header>
                <div class="assistant-preview-note">
                    <Icon name="info" :size="14" /><span
                        >对话交互演示 · 当前未连接模型，不写入真实业务</span
                    >
                </div>
                <div ref="assistantBody" class="assistant-messages">
                    <div
                        v-if="!assistantMessages.length"
                        class="assistant-welcome"
                    >
                        <span><Icon name="moon" :size="34" /></span>
                        <h3>常用操作</h3>
                        <button
                            v-for="prompt in [
                                '帮我查一下云栖里的最新进度',
                                '如果云栖里延期5天，后面怎么排',
                                '我想登记一批财务Excel和凭证',
                            ]"
                            :key="prompt"
                            @click="sendSuggestedPrompt(prompt)"
                        >
                            {{ prompt }}<Icon name="arrow-right" :size="15" />
                        </button>
                    </div>
                    <div
                        v-for="message in assistantMessages"
                        :key="message.id"
                        class="chat-message"
                        :class="message.kind"
                    >
                        <span>{{
                            message.kind === 'user' ? '清游' : '霜月 · 演示响应'
                        }}</span>
                        <p>{{ message.content }}</p>
                        <button
                            v-if="message.source"
                            class="chat-source"
                            @click="openAssistantSource(message.source)"
                        >
                            <Icon name="link" :size="14" />{{
                                message.sourceLabel
                            }}<Icon name="arrow-right" :size="14" />
                        </button>
                    </div>
                </div>
                <form class="assistant-composer" @submit.prevent="sendMessage">
                    <div v-if="attachedNames.length" class="attached-files">
                        <span v-for="name in attachedNames" :key="name"
                            ><Icon name="file" :size="13" />{{ name }}</span
                        ><button
                            type="button"
                            class="sd-icon-button"
                            aria-label="移除预览附件"
                            @click="attachedNames = []"
                        >
                            <Icon name="close" :size="14" />
                        </button>
                    </div>
                    <textarea
                        v-model="assistantInput"
                        placeholder="输入业务记录或查询内容"
                        aria-label="给霜月发送内容"
                        rows="3"
                        @keydown.enter.exact.prevent="sendMessage"
                    ></textarea>
                    <div>
                        <button
                            type="button"
                            class="sd-icon-button"
                            aria-label="选择本地预览附件"
                            @click="fileInput?.click()"
                        >
                            <Icon name="plus" /></button
                        ><span>Enter 发送 · Shift + Enter 换行</span
                        ><button
                            class="send-message"
                            type="submit"
                            :disabled="!assistantInput.trim()"
                            aria-label="发送演示消息"
                        >
                            <Icon name="arrow-right" :size="19" />
                        </button>
                    </div>
                    <input
                        ref="fileInput"
                        class="sd-visually-hidden"
                        type="file"
                        multiple
                        @change="onAttach"
                    />
                </form>
            </aside>
        </div>
        <Transition name="toast"
            ><div v-if="toast" class="app-toast" role="status">
                <Icon name="check" :size="17" /><span>{{ toast }}</span
                ><button
                    class="sd-icon-button"
                    aria-label="关闭提示"
                    @click="toast = ''"
                >
                    <Icon name="close" :size="14" />
                </button></div
        ></Transition>
    </div>
</template>

<style scoped>
.main-sidebar {
    width: 200px;
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 30;
    background: #fff;
    border-right: 1px solid var(--sd-line);
    display: flex;
    flex-direction: column;
    padding: 0 12px;
}
.sidebar-brand {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 18px 10px 12px;
}
.brand-symbol {
    width: 28px;
    height: 28px;
    color: #fff;
    display: grid;
    place-items: center;
    background: var(--sd-accent);
    font-size: 19px;
    font-weight: 700;
    border-radius: 5px;
}
.sidebar-brand > span:last-child {
    font-size: 19px;
    font-weight: 650;
    letter-spacing: -0.4px;
}
.sidebar-company {
    display: flex;
    align-items: center;
    gap: 10px;
    background: transparent;
    border: 0;
    border-radius: 6px;
    padding: 6px 10px 16px;
    margin-bottom: 12px;
}
.sidebar-company strong {
    font-size: 12px;
    font-weight: 400;
    color: var(--sd-muted);
}
.sidebar-company small {
    display: block;
    margin-top: 5px;
    color: #64748b;
    font-size: 11px;
}
.sidebar-company > svg {
    color: #64748b;
    margin-left: auto;
}
.nav-group-label {
    font-size: 11px;
    color: #94a3b8;
    letter-spacing: 0;
    padding: 0 10px;
    margin-bottom: 8px;
}
.main-nav {
    display: flex;
    flex-direction: column;
    gap: 3px;
}
.main-nav > button,
.sidebar-reserved > button {
    width: 100%;
    height: 38px;
    border: 0;
    border-radius: 4px;
    background: transparent;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 10px;
    text-align: left;
    color: #475569;
    font-size: 13px;
}
.main-nav > button:hover,
.sidebar-reserved > button:hover {
    background: #f8fafc;
}
.main-nav > button.active,
.sidebar-reserved > button.active {
    background: var(--sd-soft);
    color: var(--sd-accent);
    font-weight: 600;
}
.main-nav > button > span:nth-child(2),
.sidebar-reserved > button > span {
    flex: 1;
}
.main-nav small {
    display: grid;
    place-items: center;
    width: 19px;
    height: 18px;
    color: #64748b;
    font-size: 11px;
    background: #f1f5f9;
    border-radius: 4px;
}
.sidebar-reserved {
    border-top: 1px solid #f1f5f9;
    margin-top: 16px;
    padding-top: 16px;
}
.sidebar-reserved > button {
    color: #64748b;
}
.sidebar-reserved small {
    font-size: 11px;
    padding: 3px 5px;
    color: #64748b;
    border: 1px solid #f1f5f9;
    border-radius: 4px;
}
.sidebar-bottom {
    margin-top: auto;
    padding-top: 25px;
}
.sidebar-assistant {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 11px 10px;
    background: #f8fafc;
    border: 1px solid var(--sd-line);
    border-radius: 5px;
    text-align: left;
}
.sidebar-assistant:hover {
    background: #f1f5f9;
}
.sidebar-assistant strong {
    display: block;
    font-size: 13px;
    font-weight: 550;
}
.sidebar-assistant small {
    display: block;
    color: #64748b;
    font-size: 11px;
    margin-top: 5px;
}
.sidebar-assistant > svg {
    color: var(--sd-accent);
    margin-left: auto;
}
.sidebar-footer {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: #94a3b8;
    padding: 16px 7px;
    letter-spacing: 0.06em;
}
.sidebar-footer small {
    font-size: 11px;
}
.main-frame {
    margin-left: 200px;
}
.topbar {
    height: 56px;
    display: flex;
    align-items: center;
    padding: 0 24px;
    background: #fff;
    gap: 20px;
    border-bottom: 1px solid var(--sd-line);
}
.topbar-location {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12px;
    color: var(--sd-muted);
}
.topbar-location strong {
    font-size: 12px;
    color: var(--sd-ink);
    font-weight: 500;
}
.global-search {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
    width: 250px;
    height: 33px;
    background: #f8fafc;
    border: 1px solid #f1f5f9;
    border-radius: 6px;
    color: #64748b;
    font-size: 11px;
    padding: 0 10px;
}
.global-search kbd {
    margin-left: auto;
    font-family: inherit;
    font-size: 11px;
    padding: 2px 4px;
    border: 1px solid #f1f5f9;
    border-radius: 3px;
    color: #64748b;
}
.topbar-actions {
    display: flex;
    gap: 10px;
    align-items: center;
}
.notification-button {
    position: relative;
}
.notification-button i {
    position: absolute;
    width: 4px;
    height: 4px;
    right: 8px;
    top: 7px;
    border-radius: 50%;
    background: #c5a368;
    border: 1px solid white;
}
.topbar-divider {
    height: 20px;
    width: 1px;
    background: var(--sd-line);
    margin: 0 4px;
}
.account-avatar {
    width: 31px;
    height: 31px;
    display: grid;
    place-items: center;
    background: #f1f5f9;
    color: #64748b;
    font-size: 12px;
    border-radius: 50%;
}
.account-name {
    font-size: 11px;
    min-width: 39px;
}
.account-name small {
    display: block;
    font-size: 11px;
    color: #64748b;
    margin-top: 3px;
}
.preview-strip {
    min-height: 30px;
    padding: 4px 24px;
    border-bottom: 1px solid #f1f5f9;
    background: #fafbfc;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 15px;
    font-size: 11px;
    color: var(--sd-muted);
}
.preview-strip > div {
    display: flex;
    align-items: center;
    gap: 10px;
}
.preview-mark {
    color: #64748b;
    background: #f1f5f9;
    padding: 2px 5px;
    border-radius: 3px;
    white-space: nowrap;
}
.preview-strip label {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
}
.preview-strip select {
    color: #475569;
    font-size: 11px;
    border: 0;
    background: transparent;
    max-width: 152px;
    cursor: pointer;
}
.workspace-content {
    max-width: 1600px;
    margin: 0 auto;
    padding: 20px 24px 0;
}
.home-workspace .sd-eyebrow {
    font-size: 11px;
    letter-spacing: 0.12em;
    color: #64748b;
    margin-bottom: 10px;
}
.home-workspace .sd-page-heading h1 {
    font-size: 22px;
    font-weight: 600;
}
.home-workspace .sd-page-heading > button {
    margin-top: 0;
}
.week-calendar {
    padding: 16px;
}
.week-calendar > div:first-child {
    display: flex;
    align-items: center;
    gap: 10px;
}
.week-calendar > div:first-child strong {
    font-size: 12px;
    font-weight: 550;
}
.week-calendar > div:first-child span {
    margin-left: auto;
    font-size: 11px;
    color: #64748b;
}
.week-calendar > div:first-child svg {
    color: #64748b;
}
.week-days {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 5px;
    margin-top: 12px;
}
.week-days button {
    display: flex;
    flex-direction: column;
    align-items: center;
    border: 0;
    background: transparent;
    padding: 7px 0 5px;
    border-radius: 6px;
    gap: 9px;
}
.week-days small {
    font-size: 11px;
    color: #64748b;
}
.week-days strong {
    font-size: 12px;
    font-weight: 500;
}
.week-days i {
    width: 3px;
    height: 3px;
    border-radius: 50%;
    background: transparent;
}
.week-days button.event i {
    background: #94a3b8;
}
.week-days button.today {
    background: var(--sd-accent);
    color: white;
}
.week-days button.today small {
    color: #dbeafe;
}
.week-days button.today i {
    background: #cbd5e1;
}
.week-days button:hover {
    background: #f1f5f9;
    color: #64748b;
}
.calendar-next {
    display: flex;
    align-items: center;
    gap: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--sd-line);
    margin-top: 10px;
}
.calendar-next > i {
    width: 3px;
    height: 29px;
    border-radius: 2px;
    background: #94a3b8;
}
.calendar-next > span {
    font-size: 11px;
    color: #64748b;
    flex: 1;
}
.calendar-next strong {
    display: block;
    font-size: 11px;
    color: #64748b;
    font-weight: 500;
    margin-top: 5px;
}
.home-metrics {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
    margin: 0 0 16px;
}
.home-metrics > button {
    display: flex;
    align-items: center;
    gap: 12px;
    text-align: left;
    padding: 16px;
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    background: white;
}
.home-metrics > button:hover {
    border-color: #cbd5e1;
}
.metric-icon {
    width: 34px;
    height: 34px;
    display: grid;
    place-items: center;
    background: #f1f5f9;
    color: #64748b;
    border-radius: 5px;
    flex-shrink: 0;
}
.metric-icon.blue {
    background: #f0f4fa;
    color: #95a9c5;
}
.metric-icon.sand {
    background: #faf6ed;
    color: #b5a47d;
}
.home-metrics > button > div > span {
    color: var(--sd-muted);
    font-size: 12px;
}
.home-metrics strong {
    display: block;
    font-size: 26px;
    font-weight: 600;
    margin-top: 8px;
    line-height: 1;
    font-variant-numeric: tabular-nums;
}
.home-metrics strong small {
    font-size: 11px;
    font-weight: 400;
    color: #64748b;
    margin-left: 7px;
}
.home-main-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 260px;
    gap: 16px;
}
.home-panel-heading {
    padding: 16px 16px 0;
}
.home-panel-heading h2 > span {
    margin-left: 6px;
    font-size: 11px;
    color: #64748b;
    font-weight: 400;
}
.home-panel-heading .sd-link {
    font-size: 11px;
}
.home-projects .sd-table th {
    font-size: 12px;
    padding-left: 17px;
}
.home-projects .sd-table td {
    font-size: 12px;
    padding: 14px 16px;
}
.home-projects .sd-table td:last-child {
    color: #64748b;
    padding-left: 0;
}
.home-projects .sd-table tbody tr {
    cursor: pointer;
}
.home-project-name {
    display: flex;
    align-items: center;
    gap: 10px;
}
.home-project-name > span {
    width: 32px;
    height: 35px;
    display: grid;
    place-items: center;
    background: #f1f5f9;
    color: #64748b;
    font-size: 14px;
    border-radius: 6px;
    flex-shrink: 0;
}
.home-project-name strong {
    display: block;
    font-size: 12px;
    font-weight: 550;
}
.home-project-name small {
    display: block;
    font-size: 11px;
    color: var(--sd-muted);
    margin-top: 4px;
    white-space: nowrap;
}
.mini-progress {
    width: 65px;
    height: 3px;
    background: #f1f5f9;
    border-radius: 5px;
    overflow: hidden;
    margin-top: 9px;
}
.mini-progress i {
    display: block;
    height: 100%;
    background: var(--sd-accent);
}
.home-next-date {
    white-space: nowrap;
}
.lead-avatar {
    display: inline-grid;
    place-items: center;
    width: 23px;
    height: 23px;
    background: #f1f5f9;
    border-radius: 50%;
    margin-right: 6px;
    font-size: 11px;
    color: #64748b;
}
.upcoming-panel {
    padding: 16px;
}
.upcoming-panel .sd-section-title {
    color: #64748b;
}
.upcoming-panel .sd-section-title h2 {
    color: var(--sd-ink);
    font-size: 13px;
}
.upcoming-item {
    position: relative;
    text-align: left;
    display: block;
    width: 100%;
    padding: 12px 0 12px 15px;
    margin-left: 3px;
    background: transparent;
    border: 0;
    border-left: 1px solid #e2e8f0;
}
.upcoming-item:last-child {
    border-color: transparent;
}
.upcoming-time {
    display: flex;
    align-items: center;
    gap: 8px;
}
.upcoming-time i {
    position: absolute;
    width: 6px;
    height: 6px;
    left: -3.5px;
    top: 5px;
    background: #cbd5e1;
    border: 2px solid white;
    border-radius: 50%;
    outline: 1px solid #e2e8f0;
}
.upcoming-time i.first {
    background: #64748b;
    outline-color: #94a3b8;
}
.upcoming-time span {
    color: #64748b;
    font-size: 11px;
}
.upcoming-item strong {
    display: block;
    font-size: 12px;
    font-weight: 600;
    margin-top: 6px;
}
.upcoming-item p {
    color: var(--sd-muted);
    font-size: 11px;
    line-height: 1.5;
    margin-top: 4px;
}
.upcoming-item small {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    color: #64748b;
    margin-top: 8px;
}
.upcoming-item:hover strong {
    color: var(--sd-accent);
}
.home-bottom-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 300px;
    gap: 16px;
    margin-top: 16px;
}
.recent-changes {
    padding: 16px;
}
.recent-changes .sd-section-title > span {
    font-size: 11px;
    color: #64748b;
}
.recent-changes > button {
    display: flex;
    text-align: left;
    align-items: flex-start;
    gap: 12px;
    padding: 12px 0;
    border: 0;
    border-bottom: 1px solid var(--sd-line);
    background: transparent;
    width: 100%;
}
.recent-changes > button:last-child {
    border: 0;
    padding-bottom: 0;
}
.change-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #94a3b8;
    margin-top: 5px;
}
.recent-changes > button > div {
    flex: 1;
}
.recent-changes strong {
    font-size: 11px;
    font-weight: 550;
}
.recent-changes strong span {
    font-size: 11px;
    color: #64748b;
    font-weight: 400;
    margin-left: 10px;
}
.recent-changes p {
    color: #64748b;
    font-size: 12px;
    line-height: 1.6;
    margin-top: 5px;
}
.recent-changes small {
    display: block;
    color: #64748b;
    font-size: 11px;
    margin-top: 7px;
}
.recent-changes > button > svg {
    margin-top: 4px;
    color: #64748b;
}
.workspace-footer {
    display: flex;
    justify-content: space-between;
    padding: 16px 0;
    color: #94a3b8;
    font-size: 11px;
}
.notifications-popover {
    width: min(375px, calc(100vw - 30px));
    position: fixed;
    right: 32px;
    top: 61px;
    z-index: 60;
    padding: 15px 20px;
    box-shadow: 0 12px 40px rgb(35 56 37 / 9%);
    max-height: 60vh;
    overflow-y: auto;
}
.notifications-popover header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 7px;
}
.notifications-popover header strong {
    font-size: 13px;
}
.notifications-popover > button {
    border: 0;
    border-top: 1px solid var(--sd-line);
    background: transparent;
    text-align: left;
    width: 100%;
    padding: 15px 0;
}
.notifications-popover small {
    font-size: 11px;
    color: #64748b;
}
.notifications-popover p {
    font-size: 12px;
    line-height: 1.8;
    margin-top: 6px;
}
.search-overlay {
    align-items: start;
    padding-top: 120px;
}
.search-dialog {
    padding: 14px;
    width: min(580px, 100%);
}
.search-dialog-input {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 9px 13px;
    color: #64748b;
    border-bottom: 1px solid var(--sd-line);
}
.search-dialog-input input {
    width: 100%;
    border: 0;
    padding: 9px 0;
    background: transparent;
    font-size: 14px;
    color: var(--sd-ink);
}
.search-caption {
    font-size: 11px;
    color: #64748b;
    padding: 15px 12px 8px;
}
.search-result {
    display: flex;
    align-items: center;
    gap: 13px;
    width: 100%;
    padding: 13px;
    border: 0;
    background: transparent;
    text-align: left;
    border-radius: 6px;
    color: #64748b;
}
.search-result:hover {
    background: var(--sd-soft);
}
.search-result > span {
    flex: 1;
}
.search-result strong {
    display: block;
    color: var(--sd-ink);
    font-size: 12px;
    font-weight: 500;
}
.search-result small {
    display: block;
    color: #64748b;
    font-size: 11px;
    margin-top: 5px;
}
.no-search-results {
    padding: 30px 12px;
    color: #64748b;
    font-size: 12px;
}
.assistant-scrim {
    position: fixed;
    inset: 0;
    z-index: 70;
    background: rgb(26 43 36 / 18%);
    display: flex;
    justify-content: flex-end;
}
.assistant-drawer {
    width: min(460px, 100%);
    height: 100%;
    background: #f8fafc;
    display: flex;
    flex-direction: column;
    border-left: 1px solid #e2e8f0;
}
.assistant-drawer header {
    display: flex;
    align-items: center;
    gap: 13px;
    padding: 23px;
    background: white;
    border-bottom: 1px solid var(--sd-line);
}
.assistant-drawer-avatar {
    width: 42px;
    height: 42px;
    border: 1px solid #e2e8f0;
    background: #f1f5f9;
    border-radius: 50%;
    display: grid;
    place-items: center;
    color: #64748b;
}
.assistant-drawer header h2 {
    font-size: 17px;
    font-weight: 500;
}
.assistant-drawer header span {
    display: block;
    color: #64748b;
    font-size: 11px;
    margin-top: 6px;
}
.assistant-drawer header button {
    margin-left: auto;
}
.assistant-preview-note {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    color: #64748b;
    padding: 10px;
    background: #f8fafc;
    font-size: 11px;
    border-bottom: 1px solid #f1f5f9;
}
.assistant-messages {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 23px;
}
.assistant-welcome {
    padding-top: 18px;
}
.assistant-welcome > span {
    display: inline-grid;
    place-items: center;
    height: 63px;
    width: 63px;
    background: #f1f5f9;
    color: #64748b;
    border-radius: 50%;
}
.assistant-welcome h3 {
    font-size: 21px;
    font-weight: 500;
    color: #64748b;
    margin-top: 23px;
}
.assistant-welcome p {
    font-size: 12px;
    line-height: 1.9;
    color: #64748b;
    margin: 13px 0 27px;
}
.assistant-welcome button {
    display: flex;
    gap: 10px;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    background: white;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 14px 13px;
    text-align: left;
    font-size: 11px;
    color: #64748b;
    margin-bottom: 10px;
}
.assistant-welcome button:hover {
    background: #f1f5f9;
}
.chat-message {
    margin-bottom: 26px;
}
.chat-message > span {
    display: block;
    font-size: 11px;
    color: #64748b;
    margin-bottom: 9px;
}
.chat-message p {
    line-height: 1.95;
    font-size: 12px;
    color: #64748b;
    padding: 15px 17px;
    border: 1px solid #f1f5f9;
    background: white;
    border-radius: 0 9px 9px;
    white-space: pre-wrap;
}
.chat-message.user {
    margin-left: 35px;
}
.chat-message.user > span {
    text-align: right;
}
.chat-message.user p {
    background: #f1f5f9;
    border-color: #e2e8f0;
    border-radius: 9px 0 9px 9px;
}
.chat-source {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    border: 1px solid #e2e8f0;
    border-radius: 5px;
    padding: 8px 10px;
    background: #f8fafc;
    color: #64748b;
    font-size: 11px;
    margin-top: 9px;
}
.assistant-composer {
    margin: 0 18px 19px;
    background: white;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 10px;
}
.assistant-composer textarea {
    width: 100%;
    min-height: 82px;
    resize: none;
    border: 0;
    background: transparent;
    padding: 9px;
    font-size: 12px;
    line-height: 1.8;
    color: #64748b;
}
.assistant-composer textarea::placeholder {
    color: #64748b;
}
.assistant-composer > div:last-of-type {
    display: flex;
    align-items: center;
    gap: 6px;
}
.assistant-composer > div:last-of-type > span {
    font-size: 11px;
    color: #94a3b8;
}
.send-message {
    margin-left: auto;
    border: 0;
    border-radius: 6px;
    height: 31px;
    width: 34px;
    display: grid;
    place-items: center;
    background: var(--sd-accent);
    color: white;
}
.attached-files {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
    max-height: 74px;
    overflow-y: auto;
}
.attached-files > span {
    display: flex;
    gap: 4px;
    align-items: center;
    background: #f1f5f9;
    font-size: 11px;
    color: #64748b;
    padding: 5px;
    max-width: 260px;
    overflow: hidden;
}
.tender-preview {
    max-width: 620px;
    margin: 50px auto 80px;
    text-align: center;
}
.tender-illustration {
    width: 110px;
    height: 125px;
    border: 1px solid #e2e8f0;
    border-radius: 65px 65px 14px 14px;
    background: #f1f5f9;
    margin: 30px auto;
    display: grid;
    place-items: center;
    color: #64748b;
}
.tender-preview h1 {
    font-size: 29px;
    font-weight: 550;
}
.tender-preview > p {
    color: #64748b;
    font-size: 13px;
    line-height: 1.8;
    margin: 15px 0;
}
.tender-scope {
    text-align: left;
    max-width: 315px;
    padding: 15px 0 25px;
    margin: 0 auto;
}
.tender-scope > div {
    display: flex;
    gap: 12px;
    align-items: center;
    color: #64748b;
    font-size: 12px;
    padding: 12px 0;
}
.app-toast {
    position: fixed;
    left: calc(50% + 113px);
    transform: translateX(-50%);
    bottom: 25px;
    z-index: 100;
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: min(630px, calc(100vw - 30px));
    padding: 11px 12px 11px 17px;
    border: 1px solid #cbd5e1;
    background: #f8fafc;
    border-radius: 6px;
    color: #64748b;
    font-size: 12px;
    box-shadow: 0 5px 22px rgb(37 59 33 / 9%);
}
.app-toast > svg {
    flex-shrink: 0;
}
.app-toast > span {
    line-height: 1.6;
}
.toast-enter-active,
.toast-leave-active {
    transition: opacity 0.15s ease;
}
.toast-enter-from,
.toast-leave-to {
    opacity: 0;
}
.mobile-menu {
    display: none;
}
.home-workspace .sd-page-heading {
    align-items: center;
    margin-bottom: 16px;
}
.heading-date {
    font-size: 12px;
    font-weight: 400;
    letter-spacing: 0;
    color: var(--sd-muted);
    margin-left: 16px;
}
@media (max-width: 1280px) {
    .main-sidebar {
        width: 200px;
        padding: 0 13px;
    }
    .main-frame {
        margin-left: 200px;
    }
    .sidebar-brand {
        gap: 7px;
    }
    .sidebar-brand > span:last-child {
        font-size: 19px;
    }
    .sidebar-company {
        padding: 11px 8px;
        gap: 7px;
    }
    .sidebar-company > svg {
        display: none;
    }
    .workspace-content {
        padding: 27px 25px 0;
    }
    .topbar {
        padding: 0 25px;
    }
    .preview-strip {
        padding-left: 25px;
        padding-right: 25px;
        font-size: 11px;
    }
    .preview-strip select {
        font-size: 11px;
    }
    .home-main-grid {
        grid-template-columns: minmax(0, 1fr) 240px;
        gap: 18px;
    }
    .home-metrics {
        gap: 18px;
    }
    .home-metrics > button {
        padding: 17px 15px;
        gap: 10px;
    }
    .home-panel-heading {
        padding: 16px 16px 0;
    }
    .home-projects .sd-table td {
        padding: 14px 10px;
    }
    .home-project-name {
        gap: 7px;
    }
    .home-project-name > span {
        width: 26px;
        height: 30px;
    }
    .home-project-name small {
        font-size: 11px;
    }
    .home-bottom-grid {
        grid-template-columns: minmax(0, 1fr) 300px;
        gap: 18px;
    }
    .app-toast {
        left: calc(50% + 102px);
    }
}
@media (max-width: 1050px) {
    .global-search {
        width: 200px;
    }
    .home-main-grid {
        grid-template-columns: 1fr;
    }
    .upcoming-panel {
        display: flex;
        flex-wrap: wrap;
        gap: 15px;
        padding-bottom: 13px;
    }
    .upcoming-panel .sd-section-title {
        width: 100%;
        margin-bottom: 0;
    }
    .upcoming-item {
        flex: 1;
        min-width: 160px;
    }
    .home-workspace .sd-page-heading h1 {
        font-size: 22px;
    }
    .preview-description {
        max-width: 340px;
        line-height: 1.65;
    }
    .home-bottom-grid {
        grid-template-columns: 1fr 270px;
    }
}
@media (max-width: 850px) {
    .main-sidebar {
        transform: translateX(-100%);
        transition: transform 0.2s;
        width: 226px;
    }
    .main-sidebar.mobile-open {
        transform: translateX(0);
    }
    .main-frame {
        margin-left: 0;
    }
    .mobile-menu {
        display: inline-flex;
        margin-left: -10px;
    }
    .mobile-scrim {
        position: fixed;
        inset: 0;
        z-index: 25;
        background: rgb(36 56 38 / 23%);
    }
    .app-toast {
        left: 50%;
    }
    .topbar {
        gap: 15px;
        height: 62px;
    }
    .preview-description {
        max-width: none;
    }
}
@media (max-width: 620px) {
    .workspace-content {
        padding: 23px 16px 0;
    }
    .topbar {
        padding: 0 17px;
        gap: 7px;
    }
    .topbar-location {
        gap: 7px;
    }
    .topbar-location > span {
        display: none;
    }
    .global-search {
        width: 33px;
        padding: 0;
        display: grid;
        place-items: center;
        border: 0;
        background: transparent;
    }
    .global-search span,
    .global-search kbd {
        display: none;
    }
    .topbar-actions {
        gap: 6px;
    }
    .topbar-divider {
        margin: 0 1px;
    }
    .account-name {
        font-size: 11px;
        min-width: 26px;
    }
    .account-name small {
        font-size: 11px;
    }
    .preview-strip {
        padding: 8px 16px;
        flex-wrap: wrap;
        gap: 8px;
    }
    .preview-strip > div {
        gap: 7px;
        align-items: flex-start;
    }
    .preview-description {
        line-height: 1.65;
    }
    .preview-strip label {
        margin-left: auto;
    }
    .home-workspace .sd-page-heading h1 {
        font-size: 21px;
    }
    .home-workspace .sd-page-heading > button {
        margin-top: 0;
    }
    .week-calendar {
        padding: 18px 20px;
    }
    .week-days {
        margin-top: 10px;
    }
    .week-days button {
        gap: 7px;
    }
    .calendar-next {
        margin-top: 8px;
        padding-top: 12px;
    }
    .home-metrics {
        gap: 8px;
        margin: 16px 0;
    }
    .home-metrics > button {
        padding: 14px 10px;
        display: block;
    }
    .metric-icon {
        display: none;
    }
    .home-metrics > button > div > span {
        font-size: 11px;
    }
    .home-metrics strong {
        font-size: 21px;
        margin-top: 10px;
    }
    .home-metrics strong small {
        margin-left: 4px;
        font-size: 11px;
    }
    .home-project-name small {
        max-width: 145px;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .home-projects .sd-table {
        min-width: 570px;
    }
    .home-main-grid {
        gap: 15px;
    }
    .home-bottom-grid {
        grid-template-columns: 1fr;
        margin-top: 15px;
        gap: 15px;
    }
    .upcoming-item {
        min-width: 120px;
    }
    .upcoming-panel {
        gap: 10px;
    }
    .workspace-footer {
        font-size: 11px;
    }
    .search-overlay {
        padding-top: 70px;
    }
}
@media (max-width: 620px) {
    .heading-date {
        display: block;
        margin: 5px 0 0;
        font-size: 11px;
    }
    .workspace-content {
        padding: 16px 16px 0;
    }
    .home-workspace .sd-page-heading h1 {
        font-size: 20px;
    }
    .preview-description {
        display: none;
    }
}
</style>
