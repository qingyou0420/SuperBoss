<script lang="ts">
import { reactive } from 'vue'

type StageState = 'done' | 'current' | 'next'
interface ProjectStage {
    id: string
    title: string
    start: string
    end: string
    state: StageState
    preparation: string[]
    document: string
    photoRequired: boolean
    evidence: string[]
    completedAt?: string
    completedBy?: string
}

const sharedPlans = reactive<Record<string, ProjectStage[]>>({})
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import Icon from './Icon.vue'
import { changeLog, projects, type PreviewRole } from './previewState'

const props = defineProps<{ role: PreviewRole; initialProject?: string }>()
const emit = defineEmits<{
    ask: [prompt: string]
    notify: [message: string]
}>()

const stageDefinitions = [
    {
        title: '筹备与资料确认',
        preparation: ['核对本小区议题和委托资料', '确认本项目联系人及沟通方式'],
        document: '筹备工作安排.pdf',
        photoRequired: false,
    },
    {
        title: '候选人报名',
        preparation: [
            '准备报名表与候选人资料清单',
            '核对报名截止时间及受理方式',
        ],
        document: '候选人报名通知.pdf',
        photoRequired: false,
    },
    {
        title: '候选人名单公示',
        preparation: [
            '使用经确认的候选人名单',
            '提前确认公示位置与照片留存方式',
        ],
        document: '候选人名单公示.pdf',
        photoRequired: true,
    },
    {
        title: '业主大会公告',
        preparation: [
            '核对公告中的议题、时间与地点',
            '确认投票材料与现场准备事项',
        ],
        document: '业主大会召开公告.pdf',
        photoRequired: true,
    },
    {
        title: '投票与现场执行',
        preparation: ['按最新日程准备投票材料', '确认现场物料与资料保管方式'],
        document: '投票执行安排.pdf',
        photoRequired: true,
    },
    {
        title: '开箱与结果统计',
        preparation: ['准备经确认的统计表及记录材料', '核对结果材料与原始记录'],
        document: '开箱及结果统计表.pdf',
        photoRequired: false,
    },
    {
        title: '结果公示与备案',
        preparation: ['核对结果公示定稿与相关材料', '按本项目要求整理备案资料'],
        document: '表决结果公示.pdf',
        photoRequired: true,
    },
]

function addDays(value: string, days: number): string {
    const day = new Date(`${value.slice(0, 10)}T12:00:00`)
    if (Number.isNaN(day.getTime())) return value
    day.setDate(day.getDate() + days)
    return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`
}

function shortDate(value: string): string {
    if (!value) return '待定'
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
    return match ? `${Number(match[2])}月${Number(match[3])}日` : value
}

function dayDistance(start: string, end: string): number {
    const distance = Math.round(
        (new Date(`${end.slice(0, 10)}T12:00:00`).getTime() -
            new Date(`${start.slice(0, 10)}T12:00:00`).getTime()) /
            86_400_000,
    )
    return Number.isFinite(distance) ? distance : 0
}

function money(value: unknown): string {
    const amount = Number(value)
    return Number.isFinite(amount)
        ? `¥${amount.toLocaleString('zh-CN')}`
        : String(value ?? '待确定')
}

const selectedId = ref(props.initialProject || projects[0]?.id || '')
const search = ref('')
const filter = ref<'all' | 'active' | 'finished'>('all')
const plans = sharedPlans
const focusedStageId = ref('')
const selectedProject = computed(() =>
    projects.find((item) => item.id === selectedId.value),
)
const canEdit = computed(
    () =>
        props.role === 'owner' ||
        (props.role === 'lead' && selectedId.value === 'p1'),
)
const visibleProjects = computed(() =>
    projects.filter((item) => {
        const matches = `${item.name} ${item.area} ${item.lead}`.includes(
            search.value.trim(),
        )
        const finished = item.progress >= 100
        return (
            matches &&
            (filter.value === 'all' ||
                (filter.value === 'finished' ? finished : !finished))
        )
    }),
)

function initializePlan(id: string): void {
    if (plans[id]) return
    const project = projects.find((item) => item.id === id)
    if (!project) return
    let index = Math.min(
        6,
        Math.max(0, Math.floor((project.progress * 7) / 100)),
    )
    const stageText = project.stage || ''
    if (/筹备|前期/.test(stageText)) index = 0
    else if (/报名/.test(stageText)) index = 1
    else if (/候选|名单/.test(stageText)) index = 2
    else if (/公告/.test(stageText)) index = 3
    else if (/投票/.test(stageText)) index = 4
    else if (/统计|开箱/.test(stageText)) index = 5
    else if (/结果|备案/.test(stageText)) index = 6
    const start = /^\d{4}-\d{2}-\d{2}/.test(project.start)
        ? project.start.slice(0, 10)
        : '2026-09-01'
    const span = Math.max(14, dayDistance(start, project.end) + 1)
    const nextDate = /^\d{4}-\d{2}-\d{2}/.test(project.nextDate)
        ? project.nextDate.slice(0, 10)
        : ''
    const nextOffset = dayDistance(start, nextDate)
    const anchorNext =
        project.progress < 100 &&
        index < 6 &&
        nextOffset >= index + 1 &&
        nextOffset < span - (6 - index)
    const boundary = (order: number): number => {
        if (!anchorNext)
            return Math.floor((span * order) / stageDefinitions.length)
        if (order <= index + 1)
            return Math.floor((nextOffset * order) / (index + 1))
        return (
            nextOffset +
            Math.floor(
                ((span - nextOffset) * (order - index - 1)) / (6 - index),
            )
        )
    }
    plans[id] = stageDefinitions.map((definition, order) => ({
        ...definition,
        preparation: [...definition.preparation],
        id: `${id}-stage-${order}`,
        title:
            order === index && stageText && project.progress < 100
                ? stageText
                : definition.title,
        start: addDays(start, boundary(order)),
        end: addDays(start, boundary(order + 1) - 1),
        state:
            project.progress >= 100 || order < index
                ? 'done'
                : order === index
                  ? 'current'
                  : 'next',
        evidence: [],
    }))
}

const stages = computed(() => plans[selectedId.value] ?? [])
const currentStage = computed(() =>
    stages.value.find((stage) => stage.state === 'current'),
)
const nextStage = computed(() =>
    stages.value.find((stage) => stage.state === 'next'),
)
const focusedStage = computed(
    () =>
        stages.value.find((stage) => stage.id === focusedStageId.value) ??
        currentStage.value ??
        stages.value[0],
)
const projectChanges = computed(() =>
    changeLog.filter((item) => item.projectId === selectedId.value).slice(0, 4),
)

function chooseProject(id: string): void {
    selectedId.value = id
    initializePlan(id)
    focusedStageId.value =
        plans[id]?.find((stage) => stage.state === 'current')?.id ??
        plans[id]?.[0]?.id ??
        ''
    drawer.value = null
}

function clearFilters(): void {
    search.value = ''
    filter.value = 'all'
}

watch(
    () => props.initialProject,
    (id) => {
        if (id && projects.some((item) => item.id === id)) chooseProject(id)
    },
)
watch(
    () => props.role,
    () => {
        drawer.value = null
    },
)

const drawer = ref<'schedule' | 'complete' | 'material' | null>(null)
const shiftStageId = ref('')
const shiftDirection = ref<1 | -1>(1)
const shiftDays = ref(3)
const shiftReason = ref('')
const localError = ref('')
const signedFile = ref(false)
const noticePhoto = ref(false)
const completionNote = ref('')
const openMaterialName = ref('')
const changeStart = computed(() =>
    stages.value.findIndex((stage) => stage.id === shiftStageId.value),
)
const validShift = computed(
    () =>
        Number.isInteger(Number(shiftDays.value)) &&
        Number(shiftDays.value) > 0 &&
        Number(shiftDays.value) <= 90,
)
const signedShift = computed(
    () => Number(shiftDays.value) * shiftDirection.value,
)
const impact = computed(() => {
    if (!validShift.value || changeStart.value < 0) return []
    return stages.value
        .filter(
            (stage, index) =>
                index >= changeStart.value && stage.state !== 'done',
        )
        .map((stage) => ({
            stage,
            nextStart: addDays(stage.start, signedShift.value),
            nextEnd: addDays(stage.end, signedShift.value),
        }))
})
const overlap = computed(() => {
    const previous = stages.value[changeStart.value - 1]
    return Boolean(
        previous &&
        impact.value[0] &&
        impact.value[0].nextStart <= previous.end,
    )
})
const canApply = computed(
    () =>
        canEdit.value &&
        impact.value.length > 0 &&
        !overlap.value &&
        shiftReason.value.trim().length > 0,
)
const canComplete = computed(
    () =>
        canEdit.value &&
        focusedStage.value?.state === 'current' &&
        signedFile.value &&
        (!focusedStage.value.photoRequired || noticePhoto.value),
)

function openSchedule(): void {
    if (!canEdit.value || !currentStage.value) return
    shiftStageId.value =
        focusedStage.value?.state !== 'done'
            ? focusedStage.value?.id || currentStage.value.id
            : currentStage.value.id
    shiftDays.value = 3
    shiftDirection.value = 1
    shiftReason.value = ''
    localError.value = ''
    drawer.value = 'schedule'
}

function appendChange(text: string, kind: string): void {
    changeLog.unshift({
        id: `project-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        projectId: selectedId.value,
        text,
        date: new Date().toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        }),
        kind,
    })
}

function applySchedule(): void {
    if (!canApply.value || !selectedProject.value) return
    const changed = impact.value
    const from = changed[0].stage.title
    const previousEnd = selectedProject.value.end
    for (const item of changed) {
        item.stage.start = item.nextStart
        item.stage.end = item.nextEnd
    }
    selectedProject.value.end =
        stages.value.at(-1)?.end || selectedProject.value.end
    selectedProject.value.nextDate =
        nextStage.value?.start ?? currentStage.value?.end ?? ''
    if (changeStart.value === 0)
        selectedProject.value.start = stages.value[0].start
    const direction = signedShift.value > 0 ? '顺延' : '提前'
    appendChange(
        `从「${from}」起 ${changed.length} 个阶段${direction} ${Math.abs(signedShift.value)} 天；项目结束日 ${shortDate(previousEnd)} → ${shortDate(selectedProject.value.end)}。原因：${shiftReason.value.trim()}`,
        'schedule',
    )
    drawer.value = null
    emit(
        'notify',
        `已在本地更新 ${selectedProject.value.name} 的 ${changed.length} 个阶段，变更原因已记录。`,
    )
}

function openCompletion(): void {
    if (!canEdit.value || !currentStage.value) return
    focusedStageId.value = currentStage.value.id
    signedFile.value = false
    noticePhoto.value = false
    completionNote.value = ''
    localError.value = ''
    drawer.value = 'complete'
}

function completeStage(): void {
    const stage = focusedStage.value
    const project = selectedProject.value
    if (!canComplete.value || !stage || !project) return
    const completedTitle = stage.title
    stage.evidence = [
        stage.document,
        ...(noticePhoto.value ? ['公示照片 · 现场全景.jpg'] : []),
    ]
    stage.completedAt = new Date().toLocaleString('zh-CN')
    stage.completedBy = props.role === 'owner' ? '老板' : project.lead
    stage.state = 'done'
    const following = stages.value.find((item) => item.state === 'next')
    if (following) following.state = 'current'
    project.progress = Math.round(
        (stages.value.filter((item) => item.state === 'done').length /
            stages.value.length) *
            100,
    )
    project.stage = following?.title ?? '项目已完成'
    project.status = following ? 'active' : 'completed'
    project.nextDate =
        stages.value.find((item) => item.state === 'next')?.start ??
        following?.end ??
        ''
    appendChange(
        `${stage.completedBy}手动完成「${completedTitle}」，关联 ${stage.evidence.length} 份示例材料。${completionNote.value.trim() ? `说明：${completionNote.value.trim()}` : ''}`,
        'complete',
    )
    focusedStageId.value = following?.id ?? stage.id
    drawer.value = null
    emit(
        'notify',
        `已在本地完成「${completedTitle}」，项目进度与动态已同步。未上传真实文件。`,
    )
}

function showMaterial(name: string): void {
    openMaterialName.value = name
    drawer.value = 'material'
}

chooseProject(selectedId.value)
</script>

<template>
    <section class="project-workspace" @keydown.esc="drawer = null">
        <header class="workspace-heading">
            <div>
                <h1 class="project-page-title">
                    会务项目<span class="heading-count">{{
                        projects.length
                    }}</span>
                </h1>
            </div>
            <button
                v-if="role === 'owner'"
                class="sd-button sd-button--quiet"
                @click="
                    emit(
                        'ask',
                        '帮我查看各个项目目前的阶段，以及接下来需要准备的事项。',
                    )
                "
            >
                向霜月了解进展<Icon name="arrow-right" :size="16" />
            </button>
        </header>

        <div class="project-layout">
            <aside class="project-index" aria-label="项目列表">
                <label class="project-search"
                    ><span class="visually-hidden">查找项目</span
                    ><input
                        v-model="search"
                        type="search"
                        placeholder="查找小区、区域或负责人"
                /></label>
                <div class="project-filters" aria-label="筛选项目">
                    <button
                        :class="{ active: filter === 'all' }"
                        @click="filter = 'all'"
                    >
                        全部
                    </button>
                    <button
                        :class="{ active: filter === 'active' }"
                        @click="filter = 'active'"
                    >
                        进行中
                    </button>
                    <button
                        :class="{ active: filter === 'finished' }"
                        @click="filter = 'finished'"
                    >
                        已完成
                    </button>
                </div>
                <div class="project-items">
                    <button
                        v-for="project in visibleProjects"
                        :key="project.id"
                        class="project-item"
                        :class="{ selected: project.id === selectedId }"
                        :aria-pressed="project.id === selectedId"
                        @click="chooseProject(project.id)"
                    >
                        <span class="project-item__top"
                            ><span class="project-item__area">{{
                                project.area
                            }}</span
                            ><span
                                v-if="project.id === 'p1' && role === 'lead'"
                                class="mine"
                                >我主责</span
                            ></span
                        >
                        <strong>{{ project.name }}</strong>
                        <span class="project-item__stage"
                            >{{ project.stage
                            }}<Icon name="chevron-right" :size="14"
                        /></span>
                        <span class="project-item__bottom"
                            ><span>{{ project.lead }}</span
                            ><span>{{ project.progress }}%</span></span
                        >
                        <span class="project-item__track"
                            ><span :style="{ width: `${project.progress}%` }"
                        /></span>
                    </button>
                    <p v-if="!visibleProjects.length" class="empty-projects">
                        没有符合条件的项目。<button @click="clearFilters">
                            查看全部
                        </button>
                    </p>
                </div>
            </aside>

            <main v-if="selectedProject" class="project-detail sd-panel">
                <div class="detail-topline">
                    <span
                        >{{ selectedProject.area }}
                        <span class="separator">/</span>
                        {{ selectedProject.service }}</span
                    ><span class="sd-pill">{{
                        selectedProject.progress >= 100 ? '已完成' : '进行中'
                    }}</span>
                </div>
                <div class="detail-heading">
                    <h2>{{ selectedProject.name }}</h2>
                    <span class="detail-progress"
                        >{{ selectedProject.progress }}<small>%</small
                        ><span>项目进度</span></span
                    >
                </div>
                <dl class="project-facts">
                    <div>
                        <dt>项目服务费</dt>
                        <dd>{{ money(selectedProject.fee) }}</dd>
                    </div>
                    <div>
                        <dt>项目主负责人</dt>
                        <dd>
                            {{ selectedProject.lead
                            }}<span
                                v-if="role === 'lead' && selectedId === 'p1'"
                                class="you-label"
                                >你</span
                            >
                        </dd>
                    </div>
                    <div>
                        <dt>小区规模</dt>
                        <dd>
                            {{ selectedProject.households.toLocaleString() }}
                            <small>户</small>
                        </dd>
                    </div>
                    <div>
                        <dt>计划周期</dt>
                        <dd class="period">
                            {{ shortDate(selectedProject.start) }} —
                            {{ shortDate(selectedProject.end) }}
                        </dd>
                    </div>
                </dl>

                <div class="stage-brief">
                    <div class="stage-brief__current">
                        <span class="brief-label"
                            ><span class="live-dot" />当前阶段</span
                        ><strong>{{
                            currentStage?.title ?? '所有阶段已完成'
                        }}</strong
                        ><span>{{
                            currentStage
                                ? `${shortDate(currentStage.start)} — ${shortDate(currentStage.end)}`
                                : '—'
                        }}</span>
                    </div>
                    <Icon class="brief-arrow" name="arrow-right" :size="20" />
                    <div class="stage-brief__next">
                        <span class="brief-label">下一阶段</span
                        ><strong>{{
                            nextStage?.title ?? '暂无后续阶段'
                        }}</strong
                        ><span>{{
                            nextStage
                                ? `${shortDate(nextStage.start)} 开始`
                                : '—'
                        }}</span>
                    </div>
                </div>

                <div class="section-heading">
                    <div>
                        <h3>流程与阶段</h3>
                    </div>
                    <button
                        v-if="canEdit && currentStage"
                        class="sd-button sd-button--quiet schedule-button"
                        @click="openSchedule"
                    >
                        <Icon name="calendar" :size="16" />调整后续日期</button
                    ><span v-else class="read-only">{{
                        role === 'lead' ? '仅主责项目可调整' : '查看模式'
                    }}</span>
                </div>
                <div class="timeline-layout">
                    <ol class="stage-timeline" aria-label="项目阶段">
                        <li
                            v-for="(stage, index) in stages"
                            :key="stage.id"
                            :class="[
                                stage.state,
                                { focused: focusedStage?.id === stage.id },
                            ]"
                        >
                            <button
                                class="stage-row"
                                :aria-pressed="focusedStage?.id === stage.id"
                                @click="focusedStageId = stage.id"
                            >
                                <span class="stage-node"
                                    ><Icon
                                        v-if="stage.state === 'done'"
                                        name="check"
                                        :size="13"
                                    /><span v-else>{{
                                        String(index + 1).padStart(2, '0')
                                    }}</span></span
                                >
                                <span class="stage-row__body"
                                    ><span class="stage-row__title"
                                        >{{ stage.title
                                        }}<span
                                            v-if="stage.state === 'current'"
                                            class="current-label"
                                            >当前</span
                                        ></span
                                    ><span class="stage-row__date"
                                        >{{ shortDate(stage.start) }} —
                                        {{ shortDate(stage.end)
                                        }}<span v-if="stage.state === 'done'">
                                            · 已完成</span
                                        ></span
                                    ></span
                                >
                                <Icon
                                    class="stage-chevron"
                                    name="chevron-right"
                                    :size="16"
                                />
                            </button>
                        </li>
                    </ol>

                    <article v-if="focusedStage" class="stage-inspector">
                        <div class="inspector-kicker">
                            <span>{{
                                focusedStage.state === 'done'
                                    ? '已完成阶段'
                                    : focusedStage.state === 'current'
                                      ? '正在进行'
                                      : '提前准备'
                            }}</span
                            ><Icon
                                :name="
                                    focusedStage.state === 'done'
                                        ? 'check'
                                        : 'clock'
                                "
                                :size="16"
                            />
                        </div>
                        <h4>{{ focusedStage.title }}</h4>
                        <p class="inspector-date">
                            {{ shortDate(focusedStage.start) }} —
                            {{ shortDate(focusedStage.end) }}
                        </p>
                        <h5>准备事项</h5>
                        <ul class="preparation-list">
                            <li
                                v-for="item in focusedStage.preparation"
                                :key="item"
                            >
                                <span />{{ item }}
                            </li>
                        </ul>
                        <h5>对应材料 <span>示例</span></h5>
                        <button
                            class="material-row"
                            @click="showMaterial(focusedStage.document)"
                        >
                            <span class="file-mark"
                                ><Icon name="file" :size="19" /></span
                            ><span
                                ><strong>{{ focusedStage.document }}</strong
                                ><small>{{
                                    focusedStage.evidence.includes(
                                        focusedStage.document,
                                    )
                                        ? '已关联 · 本地演示'
                                        : '参考定稿 · 演示材料'
                                }}</small></span
                            ><Icon name="chevron-right" :size="15" />
                        </button>
                        <button
                            v-if="focusedStage.photoRequired"
                            class="material-row"
                            @click="showMaterial('公示照片 · 现场全景.jpg')"
                        >
                            <span class="file-mark photo"
                                ><Icon name="image" :size="19" /></span
                            ><span
                                ><strong>公示照片</strong
                                ><small>{{
                                    focusedStage.evidence.length > 1
                                        ? '已关联 · 本地演示'
                                        : '完成时关联现场照片'
                                }}</small></span
                            ><Icon name="chevron-right" :size="15" />
                        </button>
                        <div
                            v-if="canEdit && focusedStage.state === 'current'"
                            class="stage-action"
                        >
                            <button
                                class="sd-button sd-button--primary"
                                @click="openCompletion"
                            >
                                <Icon name="check" :size="16" />完成阶段
                            </button>
                        </div>
                    </article>
                </div>

                <section class="project-activity" aria-label="项目最近动态">
                    <div class="section-heading activity-heading">
                        <div>
                            <h3>最近动态</h3>
                        </div>
                    </div>
                    <ol v-if="projectChanges.length" class="activity-list">
                        <li v-for="item in projectChanges" :key="item.id">
                            <span class="activity-dot" />
                            <div>
                                <p>{{ item.text }}</p>
                                <time>{{ item.date }}</time>
                            </div>
                        </li>
                    </ol>
                    <div v-else class="empty-activity">
                        <Icon name="clock" :size="18" />
                        <p>暂无变更记录</p>
                    </div>
                </section>
            </main>
            <main v-else class="project-detail sd-panel">
                尚无可展示的项目。
            </main>
        </div>

        <div v-if="drawer" class="drawer-overlay" @click.self="drawer = null">
            <section
                class="project-drawer"
                role="dialog"
                aria-modal="true"
                :aria-label="
                    drawer === 'schedule'
                        ? '调整项目日期'
                        : drawer === 'complete'
                          ? '确认阶段完成'
                          : '示例材料说明'
                "
            >
                <header class="drawer-header">
                    <div>
                        <p class="sd-eyebrow">{{ selectedProject?.name }}</p>
                        <h2>
                            {{
                                drawer === 'schedule'
                                    ? '调整项目日期'
                                    : drawer === 'complete'
                                      ? '完成阶段'
                                      : '阶段材料'
                            }}
                        </h2>
                    </div>
                    <button
                        class="icon-button"
                        aria-label="关闭面板"
                        @click="drawer = null"
                    >
                        <Icon name="close" :size="20" />
                    </button>
                </header>

                <form
                    v-if="drawer === 'schedule'"
                    class="drawer-form"
                    @submit.prevent="applySchedule"
                >
                    <div class="drawer-body">
                        <label class="form-label"
                            >起始阶段<select
                                v-model="shiftStageId"
                                class="sd-field"
                            >
                                <option
                                    v-for="stage in stages.filter(
                                        (item) => item.state !== 'done',
                                    )"
                                    :key="stage.id"
                                    :value="stage.id"
                                >
                                    {{ stage.title }}
                                </option>
                            </select></label
                        >
                        <div class="shift-controls">
                            <div class="shift-direction">
                                <button
                                    type="button"
                                    :class="{ active: shiftDirection === 1 }"
                                    @click="shiftDirection = 1"
                                >
                                    顺延</button
                                ><button
                                    type="button"
                                    :class="{ active: shiftDirection === -1 }"
                                    @click="shiftDirection = -1"
                                >
                                    提前
                                </button>
                            </div>
                            <label class="shift-number"
                                ><input
                                    v-model.number="shiftDays"
                                    class="sd-field"
                                    type="number"
                                    min="1"
                                    max="90"
                                    step="1"
                                    aria-label="调整天数"
                                /><span>天</span></label
                            >
                        </div>
                        <label class="form-label"
                            >调整原因
                            <span class="required-label"
                                >必填 · 随新日程一起展示</span
                            ><textarea
                                v-model="shiftReason"
                                class="sd-field"
                                rows="3"
                                maxlength="500"
                                placeholder="例如：与社区确认公示场地时间，后续节点顺延三天。"
                            />
                        </label>
                        <div class="impact-heading">
                            <h3>影响预览</h3>
                            <span>{{ impact.length }} 个阶段</span>
                        </div>
                        <p v-if="!validShift" class="form-error">
                            请输入 1 至 90 之间的整数天数。
                        </p>
                        <p v-else-if="overlap" class="form-error">
                            调整后的开始日早于前一阶段结束。请缩短提前天数，保留流程先后关系。
                        </p>
                        <div
                            v-for="item in impact"
                            :key="item.stage.id"
                            class="impact-row"
                        >
                            <strong>{{ item.stage.title }}</strong>
                            <div>
                                <span
                                    >{{ shortDate(item.stage.start) }} —
                                    {{ shortDate(item.stage.end) }}</span
                                ><Icon name="arrow-right" :size="14" /><b
                                    >{{ shortDate(item.nextStart) }} —
                                    {{ shortDate(item.nextEnd) }}</b
                                >
                            </div>
                        </div>
                        <p class="impact-note">
                            <Icon name="check" :size="15" />{{
                                stages.filter((item) => item.state === 'done')
                                    .length
                            }}
                            个已完成阶段保留原日期。仅更新当前项目。
                        </p>
                    </div>
                    <footer class="drawer-footer">
                        <p>本地演示 · 不写入真实业务</p>
                        <div>
                            <button
                                class="sd-button sd-button--quiet"
                                type="button"
                                @click="drawer = null"
                            >
                                取消</button
                            ><button
                                class="sd-button sd-button--primary"
                                :disabled="!canApply"
                                type="submit"
                            >
                                应用变更（本地）<Icon
                                    name="arrow-right"
                                    :size="16"
                                />
                            </button>
                        </div>
                    </footer>
                </form>

                <form
                    v-else-if="drawer === 'complete' && focusedStage"
                    class="drawer-form"
                    @submit.prevent="completeStage"
                >
                    <div class="drawer-body">
                        <div class="completion-stage">
                            <span>待完成阶段</span>
                            <h3>{{ focusedStage.title }}</h3>
                            <p>
                                {{ shortDate(focusedStage.start) }} —
                                {{ shortDate(focusedStage.end) }}
                            </p>
                        </div>
                        <h3 class="materials-heading">完成材料</h3>
                        <label
                            class="evidence-choice"
                            :class="{ picked: signedFile }"
                            ><input v-model="signedFile" type="checkbox" /><span
                                class="file-mark"
                                ><Icon name="file" :size="21" /></span
                            ><span
                                ><strong>{{ focusedStage.document }}</strong
                                ><small>示例定稿 · 本阶段必需</small></span
                            ><Icon
                                v-if="signedFile"
                                name="check"
                                :size="17" /></label
                        ><label
                            v-if="focusedStage.photoRequired"
                            class="evidence-choice"
                            :class="{ picked: noticePhoto }"
                            ><input
                                v-model="noticePhoto"
                                type="checkbox" /><span class="file-mark photo"
                                ><Icon name="image" :size="21" /></span
                            ><span
                                ><strong>公示照片 · 现场全景.jpg</strong
                                ><small>示例照片 · 本阶段必需</small></span
                            ><Icon v-if="noticePhoto" name="check" :size="17"
                        /></label>
                        <p class="material-requirement">
                            {{
                                focusedStage.photoRequired
                                    ? '已选择'
                                    : '本阶段无需公示照片，已选择'
                            }}
                            {{ Number(signedFile) + Number(noticePhoto) }} /
                            {{ focusedStage.photoRequired ? 2 : 1 }} 份材料
                        </p>
                        <label class="form-label completion-note"
                            >完成说明 <span class="required-label">可选</span
                            ><textarea
                                v-model="completionNote"
                                class="sd-field"
                                rows="3"
                                maxlength="300"
                                placeholder="补充完成情况"
                            />
                        </label>
                        <p v-if="localError" class="form-error">
                            {{ localError }}
                        </p>
                    </div>
                    <footer class="drawer-footer">
                        <p>本地演示 · 材料只是示例引用</p>
                        <div>
                            <button
                                class="sd-button sd-button--quiet"
                                type="button"
                                @click="drawer = null"
                            >
                                取消</button
                            ><button
                                class="sd-button sd-button--primary"
                                type="submit"
                                :disabled="!canComplete"
                            >
                                <Icon name="check" :size="16" />确认完成（本地）
                            </button>
                        </div>
                    </footer>
                </form>

                <div v-else class="drawer-form">
                    <div class="drawer-body">
                        <div class="material-preview">
                            <span class="large-file"
                                ><Icon
                                    :name="
                                        openMaterialName.endsWith('.jpg')
                                            ? 'image'
                                            : 'file'
                                    "
                                    :size="36"
                            /></span>
                            <h3>{{ openMaterialName }}</h3>
                            <span class="sd-pill">示例材料</span>
                        </div>
                        <dl class="material-facts">
                            <div>
                                <dt>所属项目</dt>
                                <dd>{{ selectedProject?.name }}</dd>
                            </div>
                            <div>
                                <dt>所属阶段</dt>
                                <dd>{{ focusedStage?.title }}</dd>
                            </div>
                            <div>
                                <dt>材料用途</dt>
                                <dd>
                                    {{
                                        openMaterialName.endsWith('.jpg')
                                            ? '保留公示现场与完成依据'
                                            : '沿当前阶段查找经确认的定稿'
                                    }}
                                </dd>
                            </div>
                            <div>
                                <dt>当前状态</dt>
                                <dd>
                                    {{
                                        focusedStage?.evidence.includes(
                                            openMaterialName,
                                        )
                                            ? '已在本地关联到阶段'
                                            : '演示引用，未上传真实文件'
                                    }}
                                </dd>
                            </div>
                        </dl>
                    </div>
                    <footer class="drawer-footer">
                        <button
                            class="sd-button sd-button--primary"
                            @click="drawer = null"
                        >
                            返回项目
                        </button>
                    </footer>
                </div>
            </section>
        </div>
    </section>
</template>

<style scoped>
.project-workspace {
    color: var(--sd-ink);
}
.workspace-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
}
.workspace-heading h1 {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 0;
    font-size: 24px;
    font-weight: 600;
    letter-spacing: -0.7px;
}
.heading-count {
    display: inline-flex;
    justify-content: center;
    align-items: center;
    width: 27px;
    height: 27px;
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    color: var(--sd-muted);
    font-size: 13px;
    font-weight: 500;
}
.heading-description {
    margin: 10px 0 0;
    font-size: 13px;
}
.project-layout {
    display: grid;
    grid-template-columns: 242px minmax(0, 1fr);
    align-items: start;
    gap: 16px;
}
.project-index {
    position: sticky;
    top: 24px;
}
.project-search input {
    width: 100%;
    box-sizing: border-box;
    padding: 12px 13px;
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    background: var(--sd-surface);
    color: var(--sd-ink);
    font: inherit;
    font-size: 12px;
    outline: none;
}
.project-search input:focus {
    border-color: var(--sd-accent);
}
.project-filters {
    display: flex;
    gap: 16px;
    border-bottom: 1px solid var(--sd-line);
    margin: 10px 0 12px;
}
.project-filters button {
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 13px 0 11px;
    margin-bottom: -1px;
    background: transparent;
    color: var(--sd-muted);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
}
.project-filters button.active {
    color: var(--sd-accent);
    border-bottom-color: var(--sd-accent);
    font-weight: 600;
}
.project-items {
    display: grid;
    gap: 9px;
}
.project-item {
    display: flex;
    flex-direction: column;
    gap: 8px;
    position: relative;
    width: 100%;
    padding: 17px 16px;
    border: 1px solid transparent;
    border-radius: 6px;
    background: transparent;
    color: var(--sd-ink);
    font: inherit;
    text-align: left;
    cursor: pointer;
    transition:
        background 0.15s,
        border-color 0.15s;
}
.project-item:hover {
    background: var(--sd-surface);
}
.project-item.selected {
    background: var(--sd-surface);
    border-color: var(--sd-line);
    box-shadow: none;
}
.project-item.selected::before {
    content: '';
    position: absolute;
    left: -1px;
    top: 24px;
    bottom: 24px;
    width: 3px;
    border-radius: 0 3px 3px 0;
    background: var(--sd-accent);
}
.project-item__top,
.project-item__bottom,
.project-item__stage {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
}
.project-item__area,
.project-item__bottom {
    color: var(--sd-muted);
    font-size: 11px;
}
.project-item strong {
    font-size: 15px;
    font-weight: 600;
    line-height: 1.5;
}
.project-item__stage {
    font-size: 12px;
    color: var(--sd-muted);
}
.project-item.selected .project-item__stage {
    color: var(--sd-accent);
}
.project-item__bottom {
    margin-top: 7px;
}
.project-item__track {
    height: 3px;
    width: 100%;
    overflow: hidden;
    background: var(--sd-line);
    border-radius: 2px;
}
.project-item__track > span {
    display: block;
    height: 100%;
    border-radius: 2px;
    background: var(--sd-accent);
    opacity: 0.7;
}
.mine {
    color: var(--sd-accent);
    font-size: 10px;
}
.index-note {
    padding: 12px 16px;
    color: var(--sd-muted);
    font-size: 11px;
    line-height: 1.8;
}
.empty-projects {
    color: var(--sd-muted);
    font-size: 12px;
    padding: 25px 10px;
    line-height: 1.8;
}
.empty-projects button {
    display: block;
    background: transparent;
    border: 0;
    padding: 8px 0;
    color: var(--sd-accent);
    cursor: pointer;
}
.project-detail {
    min-width: 0;
    padding: 16px;
}
.detail-topline {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    color: var(--sd-muted);
    font-size: 11px;
}
.separator {
    margin: 0 8px;
    color: var(--sd-line);
}
.detail-heading {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    margin: 12px 0 16px;
}
.detail-heading h2 {
    margin: 0;
    font-size: clamp(20px, 2vw, 25px);
    letter-spacing: -0.6px;
    font-weight: 600;
}
.detail-progress {
    display: block;
    color: var(--sd-accent);
    font-size: 27px;
    line-height: 1.2;
    font-variant-numeric: tabular-nums;
}
.detail-progress small {
    margin-left: 2px;
    font-size: 13px;
}
.detail-progress > span {
    display: block;
    color: var(--sd-muted);
    font-size: 10px;
    text-align: right;
    margin-top: 4px;
}
.project-facts {
    display: grid;
    grid-template-columns: 1.05fr 1fr 0.8fr 1.6fr;
    gap: 14px;
    margin: 0;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--sd-line);
}
.project-facts dt {
    margin-bottom: 8px;
    color: var(--sd-muted);
    font-size: 10px;
}
.project-facts dd {
    margin: 0;
    font-size: 14px;
    font-weight: 500;
}
.project-facts small {
    color: var(--sd-muted);
    font-size: 11px;
    font-weight: 400;
}
.project-facts .period {
    font-size: 12px;
    line-height: 1.7;
    font-weight: 400;
}
.you-label {
    color: var(--sd-accent);
    margin-left: 6px;
    font-size: 10px;
}
.stage-brief {
    display: grid;
    grid-template-columns: 1fr 28px 1fr;
    gap: 16px;
    align-items: center;
    margin: 16px 0;
    padding: 16px;
    border-radius: 6px;
    background: var(--sd-bg);
    border: 1px solid var(--sd-line);
}
.stage-brief__current,
.stage-brief__next {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 7px;
}
.brief-label {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--sd-muted);
    font-size: 10px;
}
.live-dot {
    height: 6px;
    width: 6px;
    border-radius: 50%;
    background: var(--sd-accent);
}
.stage-brief strong {
    font-size: 15px;
    font-weight: 500;
}
.stage-brief__current strong {
    color: var(--sd-accent);
}
.stage-brief__current > span:last-child,
.stage-brief__next > span:last-child {
    font-size: 11px;
    color: var(--sd-muted);
}
.brief-arrow {
    color: var(--sd-muted);
    opacity: 0.5;
}
.section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
}
.section-heading h3 {
    font-size: 15px;
    font-weight: 600;
    margin: 0;
}
.section-heading p {
    font-size: 11px;
    color: var(--sd-muted);
    margin: 6px 0 0;
}
.schedule-button {
    font-size: 11px;
    white-space: nowrap;
}
.read-only {
    color: var(--sd-muted);
    font-size: 10px;
}
.timeline-layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(250px, 0.95fr);
    gap: 16px;
}
.stage-timeline {
    list-style: none;
    padding: 0;
    margin: 0;
}
.stage-timeline li {
    position: relative;
}
.stage-timeline li:not(:last-child)::before {
    position: absolute;
    content: '';
    left: 24px;
    top: 36px;
    bottom: -15px;
    width: 1px;
    background: var(--sd-line);
}
.stage-timeline li.done:not(:last-child)::before {
    background: var(--sd-accent);
    opacity: 0.24;
}
.stage-row {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    width: 100%;
    min-height: 69px;
    padding: 13px 10px 12px 12px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    text-align: left;
    color: var(--sd-ink);
    font: inherit;
    cursor: pointer;
}
.stage-row:hover {
    background: var(--sd-bg);
}
.stage-timeline .focused .stage-row {
    background: var(--sd-soft);
}
.stage-node {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    height: 25px;
    width: 25px;
    border: 1px solid var(--sd-line);
    border-radius: 50%;
    background: var(--sd-surface);
    color: var(--sd-muted);
    box-sizing: border-box;
    position: relative;
    z-index: 1;
    font-size: 9px;
}
.done .stage-node {
    border-color: transparent;
    color: var(--sd-accent);
    background: var(--sd-soft);
}
.current .stage-node {
    background: var(--sd-accent);
    border-color: var(--sd-accent);
    color: white;
    box-shadow: 0 0 0 4px var(--sd-surface);
}
.stage-row__body {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 7px;
    min-width: 0;
    padding-top: 2px;
}
.stage-row__title {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 7px;
    line-height: 1.5;
    font-size: 12px;
}
.current .stage-row__title {
    font-weight: 500;
    color: var(--sd-accent);
}
.stage-row__date {
    font-size: 10px;
    color: var(--sd-muted);
    font-variant-numeric: tabular-nums;
}
.current-label {
    font-size: 9px;
    font-weight: 400;
    color: var(--sd-accent);
}
.stage-chevron {
    margin-top: 5px;
    color: var(--sd-muted);
    opacity: 0;
    flex-shrink: 0;
}
.focused .stage-chevron,
.stage-row:hover .stage-chevron {
    opacity: 0.65;
}
.stage-inspector {
    padding: 16px;
    align-self: start;
    background: var(--sd-bg);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
}
.inspector-kicker {
    display: flex;
    justify-content: space-between;
    color: var(--sd-accent);
    font-size: 10px;
}
.stage-inspector h4 {
    font-size: 15px;
    line-height: 1.5;
    margin: 11px 0 6px;
    font-weight: 600;
}
.inspector-date {
    margin: 0;
    color: var(--sd-muted);
    font-size: 11px;
}
.stage-inspector h5 {
    font-size: 11px;
    font-weight: 500;
    margin: 16px 0 10px;
}
.stage-inspector h5 > span {
    font-size: 9px;
    font-weight: 400;
    color: var(--sd-muted);
    margin-left: 5px;
}
.preparation-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 10px;
}
.preparation-list li {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    color: var(--sd-muted);
    font-size: 11px;
    line-height: 1.8;
}
.preparation-list li > span {
    width: 4px;
    height: 4px;
    flex-shrink: 0;
    margin-top: 8px;
    border-radius: 50%;
    background: var(--sd-muted);
    opacity: 0.5;
}
.material-row {
    display: flex;
    align-items: center;
    width: 100%;
    gap: 9px;
    padding: 11px 0;
    color: var(--sd-ink);
    border: 0;
    border-bottom: 1px solid var(--sd-line);
    background: transparent;
    font: inherit;
    text-align: left;
    cursor: pointer;
}
.file-mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 37px;
    flex-shrink: 0;
    border: 1px solid var(--sd-line);
    border-radius: 5px;
    color: var(--sd-accent);
    background: var(--sd-surface);
}
.file-mark.photo {
    color: var(--sd-warn);
}
.material-row > span:nth-child(2) {
    flex: 1;
    min-width: 0;
}
.material-row strong {
    display: block;
    font-size: 10px;
    font-weight: 500;
    line-height: 1.6;
    overflow-wrap: anywhere;
}
.material-row small {
    display: block;
    color: var(--sd-muted);
    font-size: 9px;
    margin-top: 3px;
}
.material-row > svg {
    color: var(--sd-muted);
    flex-shrink: 0;
}
.stage-action {
    margin-top: 16px;
}
.stage-action .sd-button {
    width: 100%;
    justify-content: center;
    font-size: 11px;
}
.inspector-footnote {
    display: flex;
    gap: 6px;
    align-items: flex-start;
    margin: 17px 0 0;
    color: var(--sd-muted);
    font-size: 10px;
    line-height: 1.7;
}
.inspector-footnote svg {
    flex-shrink: 0;
    margin-top: 2px;
}
.project-activity {
    border-top: 1px solid var(--sd-line);
    padding-top: 16px;
    margin-top: 16px;
}
.activity-heading {
    margin-bottom: 18px;
}
.local-tag {
    color: var(--sd-muted);
    font-size: 9px;
}
.activity-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    gap: 16px;
}
.activity-list li {
    display: flex;
    gap: 12px;
    align-items: flex-start;
}
.activity-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--sd-accent);
    flex-shrink: 0;
    margin-top: 7px;
    opacity: 0.7;
}
.activity-list p {
    margin: 0;
    font-size: 11px;
    line-height: 1.8;
}
.activity-list time {
    display: block;
    margin-top: 5px;
    font-size: 10px;
    color: var(--sd-muted);
}
.empty-activity {
    display: flex;
    align-items: center;
    gap: 9px;
    color: var(--sd-muted);
}
.empty-activity p {
    margin: 0;
    font-size: 11px;
    line-height: 1.8;
}
.empty-activity svg {
    flex-shrink: 0;
}
.drawer-overlay {
    position: fixed;
    inset: 0;
    display: flex;
    justify-content: flex-end;
    background: #11182740;
    z-index: 100;
    backdrop-filter: blur(2px);
}
.project-drawer {
    display: flex;
    flex-direction: column;
    width: min(545px, 100%);
    height: 100%;
    background: var(--sd-surface);
    box-shadow: -10px 0 60px #11182712;
}
.drawer-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    padding: 16px;
    border-bottom: 1px solid var(--sd-line);
}
.drawer-header h2 {
    font-size: 21px;
    line-height: 1.5;
    font-weight: 500;
    letter-spacing: -0.4px;
    margin: 9px 0 0;
}
.icon-button {
    display: flex;
    justify-content: center;
    align-items: center;
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    background: transparent;
    color: var(--sd-muted);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    cursor: pointer;
}
.drawer-form {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
}
.drawer-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
}
.drawer-intro {
    margin: 0 0 25px;
    font-size: 12px;
    line-height: 1.9;
    color: var(--sd-muted);
}
.form-label {
    display: block;
    color: var(--sd-ink);
    font-size: 12px;
    font-weight: 500;
}
.form-label .sd-field {
    display: block;
    width: 100%;
    box-sizing: border-box;
    margin-top: 10px;
}
.sd-field {
    color: var(--sd-ink);
    font: inherit;
    font-size: 12px;
    background: var(--sd-surface);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    padding: 11px 12px;
}
.sd-field:focus {
    outline: 2px solid var(--sd-soft);
    border-color: var(--sd-accent);
}
textarea.sd-field {
    resize: vertical;
    line-height: 1.8;
}
.shift-controls {
    display: flex;
    align-items: center;
    gap: 15px;
    margin: 16px 0;
}
.shift-direction {
    display: flex;
    padding: 3px;
    background: var(--sd-bg);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
}
.shift-direction button {
    padding: 8px 17px;
    background: transparent;
    border: 0;
    border-radius: 4px;
    color: var(--sd-muted);
    cursor: pointer;
    font: inherit;
    font-size: 12px;
}
.shift-direction button.active {
    background: var(--sd-surface);
    color: var(--sd-accent);
    box-shadow: 0 1px 3px #11182710;
}
.shift-number {
    display: flex;
    align-items: center;
    gap: 9px;
    font-size: 12px;
    color: var(--sd-muted);
}
.shift-number input {
    width: 76px;
    font-variant-numeric: tabular-nums;
}
.required-label {
    font-size: 10px;
    color: var(--sd-muted);
    font-weight: 400;
    margin-left: 6px;
}
.impact-heading {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin: 16px 0 8px;
}
.impact-heading h3 {
    margin: 0;
    font-size: 13px;
    font-weight: 500;
}
.impact-heading > span {
    font-size: 11px;
    color: var(--sd-accent);
}
.impact-row {
    padding: 15px 0;
    border-bottom: 1px solid var(--sd-line);
}
.impact-row strong {
    font-size: 12px;
    font-weight: 500;
}
.impact-row > div {
    display: flex;
    align-items: center;
    gap: 9px;
    flex-wrap: wrap;
    margin-top: 8px;
    color: var(--sd-muted);
    font-size: 10px;
}
.impact-row b {
    color: var(--sd-accent);
    font-weight: 500;
}
.impact-note {
    display: flex;
    gap: 7px;
    color: var(--sd-muted);
    font-size: 10px;
    line-height: 1.8;
    margin: 17px 0 0;
}
.impact-note svg {
    flex-shrink: 0;
    margin-top: 2px;
}
.form-error {
    color: var(--sd-warn);
    font-size: 11px;
    line-height: 1.7;
    padding: 11px 13px;
    background: var(--sd-bg);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
}
.drawer-footer {
    padding: 16px;
    border-top: 1px solid var(--sd-line);
    background: var(--sd-surface);
}
.drawer-footer p {
    font-size: 10px;
    color: var(--sd-muted);
    margin: 0 0 12px;
}
.drawer-footer > div {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
}
.drawer-footer .sd-button {
    font-size: 12px;
}
.sd-button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
}
.completion-stage {
    padding: 16px;
    border-radius: 6px;
    background: var(--sd-soft);
    margin-bottom: 16px;
}
.completion-stage > span {
    color: var(--sd-muted);
    font-size: 10px;
}
.completion-stage h3 {
    margin: 8px 0;
    color: var(--sd-accent);
    font-size: 16px;
    font-weight: 500;
}
.completion-stage p {
    margin: 0;
    color: var(--sd-muted);
    font-size: 11px;
}
.materials-heading {
    font-size: 12px;
    font-weight: 500;
    margin: 0 0 12px;
}
.evidence-choice {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 15px;
    margin-bottom: 10px;
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    cursor: pointer;
}
.evidence-choice.picked {
    border-color: var(--sd-accent);
    background: var(--sd-soft);
}
.evidence-choice input {
    accent-color: var(--sd-accent);
    width: 15px;
    height: 15px;
    flex-shrink: 0;
}
.evidence-choice > span:nth-of-type(2) {
    flex: 1;
    min-width: 0;
}
.evidence-choice strong {
    font-size: 12px;
    font-weight: 500;
    line-height: 1.7;
    display: block;
    overflow-wrap: anywhere;
}
.evidence-choice small {
    display: block;
    color: var(--sd-muted);
    font-size: 10px;
    margin-top: 5px;
}
.evidence-choice > svg {
    color: var(--sd-accent);
    flex-shrink: 0;
}
.material-requirement {
    color: var(--sd-muted);
    font-size: 10px;
    margin: 10px 0 16px;
}
.after-completion {
    display: flex;
    gap: 12px;
    margin-top: 16px;
    padding-top: 20px;
    border-top: 1px solid var(--sd-line);
}
.after-completion > svg {
    color: var(--sd-accent);
    flex-shrink: 0;
}
.after-completion strong {
    font-size: 11px;
    font-weight: 500;
}
.after-completion p {
    color: var(--sd-muted);
    font-size: 11px;
    line-height: 1.8;
    margin: 6px 0 0;
}
.material-preview {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    padding: 16px;
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    background: var(--sd-bg);
}
.large-file {
    display: flex;
    justify-content: center;
    align-items: center;
    width: 44px;
    height: 52px;
    flex-shrink: 0;
    color: var(--sd-accent);
    background: var(--sd-surface);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
}
.material-preview h3 {
    flex: 1;
    min-width: 160px;
    font-size: 14px;
    overflow-wrap: anywhere;
    line-height: 1.7;
    margin: 0;
    font-weight: 500;
}
.material-facts {
    margin: 16px 0;
}
.material-facts > div {
    display: grid;
    grid-template-columns: 80px 1fr;
    gap: 16px;
    padding: 13px 0;
    border-bottom: 1px solid var(--sd-line);
    font-size: 12px;
    line-height: 1.8;
}
.material-facts dt {
    color: var(--sd-muted);
}
.material-facts dd {
    margin: 0;
}
.material-explanation {
    font-size: 12px;
    line-height: 1.9;
    color: var(--sd-muted);
}
.visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}
@media (max-width: 1250px) {
    .project-layout {
        grid-template-columns: 210px minmax(0, 1fr);
        gap: 16px;
    }
    .project-detail {
        padding: 16px;
    }
    .timeline-layout {
        gap: 16px;
    }
    .stage-inspector {
        padding: 16px;
    }
    .stage-brief {
        gap: 12px;
        padding: 16px;
    }
}
@media (max-width: 1030px) {
    .project-layout {
        grid-template-columns: 185px minmax(0, 1fr);
        gap: 16px;
    }
    .project-item {
        padding: 15px 12px;
    }
    .project-facts {
        grid-template-columns: 1fr 1fr;
        row-gap: 16px;
    }
    .timeline-layout {
        grid-template-columns: 1fr;
    }
    .stage-timeline {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 4px 8px;
    }
    .stage-timeline li::before {
        display: none;
    }
    .stage-inspector {
        margin-top: 8px;
    }
    .stage-brief {
        grid-template-columns: 1fr 18px 1fr;
        gap: 10px;
    }
    .stage-brief strong {
        font-size: 13px;
    }
    .stage-brief__next > span:last-child,
    .stage-brief__current > span:last-child {
        line-height: 1.7;
    }
}
@media (max-width: 760px) {
    .workspace-heading {
        align-items: flex-start;
        margin-bottom: 16px;
    }
    .workspace-heading > button {
        padding: 8px;
        font-size: 10px;
    }
    .project-layout {
        display: block;
    }
    .project-index {
        position: static;
        margin-bottom: 16px;
    }
    .project-search {
        display: block;
    }
    .project-items {
        display: flex;
        overflow-x: auto;
        padding-bottom: 5px;
    }
    .project-item {
        min-width: 205px;
        max-width: 230px;
    }
    .index-note {
        display: none;
    }
    .project-detail {
        padding: 16px;
    }
    .stage-brief {
        margin: 16px 0;
        padding: 16px;
    }
    .detail-heading h2 {
        font-size: 21px;
    }
    .stage-timeline {
        display: block;
    }
    .stage-timeline li:not(:last-child)::before {
        display: block;
    }
    .timeline-layout {
        gap: 15px;
    }
    .drawer-header {
        padding: 16px;
    }
    .drawer-header h2 {
        font-size: 19px;
    }
    .drawer-body {
        padding: 16px;
    }
    .drawer-footer {
        padding: 16px;
    }
}
@media (prefers-reduced-motion: reduce) {
    .project-item {
        transition: none;
    }
}
</style>
