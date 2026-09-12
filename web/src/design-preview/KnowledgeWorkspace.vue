<script setup lang="ts">
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import type { PreviewRole } from './previewState'

defineProps<{ role: PreviewRole }>()
const emit = defineEmits<{ ask: [prompt: string]; notify: [message: string] }>()
const filter = ref('all')
const query = ref('')
const openCase = ref<string | null>(null)
const version = ref(3)
const stage = ref('会议公告')
const examples = [
    {
        id: 'k1',
        title: '一次会议公告，怎样从初稿走到定稿',
        project: '水岸名庭',
        stage: '会议公告',
        type: '日期与数字',
        date: '08.30',
        versions: 3,
        files: 4,
        intro: '从沿用旧项目日期，到逐项对应最新排期。保留每一次修改的原因。',
        tags: ['日期核对', '公告定稿'],
    },
    {
        id: 'k2',
        title: '项目延期后，需要一起检查哪些文件',
        project: '云栖里',
        stage: '候选人公示',
        type: '流程与排期',
        date: '08.22',
        versions: 2,
        files: 3,
        intro: '把变更原因、受影响的节点和相关文件放在一起，方便下次回溯。',
        tags: ['排期变更', '文件关联'],
    },
    {
        id: 'k3',
        title: '联系人与电话号码，统一从哪里取',
        project: '南溪雅苑',
        stage: '筹备资料',
        type: '日期与数字',
        date: '08.15',
        versions: 3,
        files: 2,
        intro: '保留联系人身份与信息来源，避免把上一个项目的信息带进新文件。',
        tags: ['信息核对', '资料来源'],
    },
]
const visible = computed(() =>
    examples.filter(
        (item) =>
            (filter.value === 'all' || item.type === filter.value) &&
            `${item.title}${item.project}${item.tags.join('')}`.includes(
                query.value.trim(),
            ),
    ),
)
const detail = computed(() =>
    examples.find((item) => item.id === openCase.value),
)
const versionNotes = computed(() => {
    if (detail.value?.id === 'k2')
        return [
            {
                number: 1,
                label: '原安排',
                text: '保留当时的项目计划和已关联文件，作为变更前的依据。',
                change: '原日期及原文件均保留。',
                source: '示例原排期',
            },
            {
                number: 2,
                label: '调整后',
                text: '按已确定的新安排更新后续节点，并关联变更原因和需复核的文件。',
                change: '新增日期对照及变更说明。',
                source: '示例调期记录',
            },
        ]
    if (detail.value?.id === 'k3')
        return [
            {
                number: 1,
                label: '初稿',
                text: '沿用参考文件的联系信息，未核对本项目的实际对接人。',
                change: '发现联系人身份与项目不对应。',
                source: '示例初稿说明',
            },
            {
                number: 2,
                label: '修订',
                text: '把物业负责人和社区对接人分别记录，并注明资料来源。',
                change: '补充身份和信息来源。',
                source: '示例修改说明',
            },
            {
                number: 3,
                label: '参考定稿',
                text: '使用已核对的本项目联系人，并保留核对日期。',
                change: '完成信息核对后标记参考定稿。',
                source: '示例核对记录',
            },
        ]
    return [
        {
            number: 1,
            label: '初稿',
            text: '从往期参考文件开始整理，公告中的部分日期仍保留为上个项目的时间。',
            change: '发现公告日期与本项目排期不一致。',
            source: '示例初稿说明',
        },
        {
            number: 2,
            label: '修订',
            text: '对照本项目最新计划，逐项核对公告日期、投票起止日期和联系信息。',
            change: '更新日期，保留对应排期的来源。',
            source: '示例修改记录',
        },
        {
            number: 3,
            label: '参考定稿',
            text: '已按本项目当前安排核对。历史版本及修改原因继续保留，便于今后参考。',
            change: '整理修改经过，并标记本项目参考定稿。',
            source: '示例确认记录',
        },
    ]
})
const activeVersion = computed(
    () =>
        versionNotes.value.find((item) => item.number === version.value) ??
        versionNotes.value[versionNotes.value.length - 1],
)

function clearFilters() {
    query.value = ''
    filter.value = 'all'
}
function showCase(id: string) {
    openCase.value = id
    version.value = examples.find((item) => item.id === id)?.versions ?? 3
    stage.value = examples.find((item) => item.id === id)?.stage ?? '会议公告'
}

function downloadExample() {
    const text = `# ${detail.value?.title ?? '知识库'} — 演示内容\n\n本文件仅为SuperBoss前端设计演示，不是实际业务模板，不可作为公告使用。\n\n项目：${detail.value?.project}\n阶段：${stage.value}\n\n${activeVersion.value?.text}\n\n修改：${activeVersion.value?.change}\n来源：${activeVersion.value?.source}\n\n正式版本由老板上传并确认发布。\n`
    const url = globalThis.URL.createObjectURL(
        new globalThis.Blob([text], { type: 'text/markdown;charset=utf-8' }),
    )
    const anchor = globalThis.document.createElement('a')
    anchor.href = url
    anchor.download = 'SuperBoss-经验链演示.md'
    anchor.click()
    globalThis.URL.revokeObjectURL(url)
    emit('notify', '已下载演示说明文件；实际业务定稿将在接入资料后提供。')
}
</script>

<template>
    <section>
        <header class="sd-page-heading">
            <div>
                <h1>经验知识库</h1>
            </div>
            <div class="knowledge-heading-actions">
                <span class="sd-pill sd-pill--neutral">示例案例</span>
                <button
                    v-if="role === 'owner'"
                    class="sd-button sd-button--primary"
                    @click="emit('ask', '我想整理一个历史项目的经验资料包')"
                >
                    <Icon name="plus" :size="16" />整理经验资料
                </button>
            </div>
        </header>
        <div class="knowledge-toolbar">
            <div class="sd-tabs">
                <button
                    :class="{ active: filter === 'all' }"
                    @click="filter = 'all'"
                >
                    全部经验 <small>3</small></button
                ><button
                    :class="{ active: filter === '日期与数字' }"
                    @click="filter = '日期与数字'"
                >
                    日期与数字</button
                ><button
                    :class="{ active: filter === '流程与排期' }"
                    @click="filter = '流程与排期'"
                >
                    流程与排期
                </button>
            </div>
            <label class="knowledge-search"
                ><Icon name="search" :size="16" /><input
                    v-model="query"
                    placeholder="搜索经验、项目或关键词"
                    aria-label="搜索知识库"
            /></label>
        </div>
        <div class="knowledge-columns">
            <div class="knowledge-list">
                <button
                    v-for="item in visible"
                    :key="item.id"
                    class="knowledge-entry"
                    @click="showCase(item.id)"
                >
                    <span class="entry-order">{{ item.stage }}</span>
                    <div class="entry-content">
                        <div class="entry-meta">
                            <span>{{ item.project }} · 历史案例</span
                            ><span>2026.{{ item.date }}</span>
                        </div>
                        <h2>{{ item.title }}</h2>
                        <div class="entry-bottom">
                            <span
                                v-for="tag in item.tags"
                                :key="tag"
                                class="sd-pill sd-pill--neutral"
                                >{{ tag }}</span
                            ><small
                                >{{ item.versions }} 个版本 <i>·</i>
                                {{ item.files }} 份相关材料</small
                            >
                        </div>
                    </div>
                    <Icon name="arrow-right" :size="18" />
                </button>
                <div v-if="!visible.length" class="knowledge-no-match">
                    <Icon name="search" :size="27" />
                    <p>没有匹配的演示经验</p>
                    <button class="sd-link" @click="clearFilters">
                        清除筛选
                    </button>
                </div>
            </div>
            <aside class="knowledge-aside">
                <h3>按阶段查看</h3>
                <button
                    v-for="label in ['筹备资料', '会议公告', '候选人公示']"
                    :key="label"
                    @click="
                        showCase(
                            label === '筹备资料'
                                ? 'k3'
                                : label === '候选人公示'
                                  ? 'k2'
                                  : 'k1',
                        )
                    "
                >
                    <Icon name="file" :size="17" /><span>{{ label }}</span
                    ><Icon name="chevron-right" :size="15" />
                </button>
            </aside>
        </div>

        <div
            v-if="detail"
            class="sd-dialog-overlay"
            @click.self="openCase = null"
            @keydown.esc="openCase = null"
        >
            <section
                class="knowledge-dialog sd-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="knowledge-detail-title"
                tabindex="-1"
            >
                <header>
                    <div>
                        <div class="sd-eyebrow">
                            {{ detail.project }} / {{ stage }}
                        </div>
                        <h2 id="knowledge-detail-title">{{ detail.title }}</h2>
                    </div>
                    <button
                        class="sd-icon-button"
                        aria-label="关闭经验详情"
                        @click="openCase = null"
                    >
                        <Icon name="close" />
                    </button>
                </header>
                <div class="knowledge-detail-note">
                    <Icon name="info" :size="15" /><span
                        >示例资料，非实际业务定稿。</span
                    >
                </div>
                <div class="knowledge-detail-columns">
                    <nav aria-label="文件历史版本">
                        <p>文件版本</p>
                        <button
                            v-for="item in versionNotes"
                            :key="item.number"
                            :class="{ active: version === item.number }"
                            @click="version = item.number"
                        >
                            <span class="version-number"
                                >v{{ item.number }}</span
                            >
                            <div>
                                <strong>{{ item.label }}</strong
                                ><small>{{
                                    item.number === versionNotes.length
                                        ? '老板标记参考定稿'
                                        : '保留原稿与说明'
                                }}</small>
                            </div>
                            <Icon
                                v-if="item.number === versionNotes.length"
                                name="check"
                                :size="16"
                            />
                        </button>
                    </nav>
                    <div class="version-body">
                        <div class="version-file">
                            <div><Icon name="file" :size="25" /></div>
                            <span
                                ><strong>{{ stage }} · 修改说明</strong
                                ><small
                                    >v{{ activeVersion?.number }} ·
                                    {{ activeVersion?.label }}</small
                                ></span
                            ><span
                                v-if="
                                    activeVersion?.number ===
                                    versionNotes.length
                                "
                                class="sd-pill"
                                >参考定稿</span
                            >
                        </div>
                        <div class="version-section">
                            <label>版本情况</label>
                            <p>{{ activeVersion?.text }}</p>
                        </div>
                        <div class="version-section">
                            <label>修改内容与原因</label>
                            <p>{{ activeVersion?.change }}</p>
                        </div>
                        <div class="version-source">
                            <Icon name="link" :size="15" /><span
                                >依据：{{ activeVersion?.source }}</span
                            >
                        </div>
                        <div class="version-footer">
                            <button class="sd-button" @click="downloadExample">
                                <Icon name="download" :size="15" />下载演示说明
                            </button>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    </section>
</template>

<style scoped>
.knowledge-heading-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
}
.knowledge-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-top: 16px;
    border-bottom: 1px solid var(--sd-line);
}
.knowledge-toolbar .sd-tabs {
    border: 0;
}
.sd-tabs small {
    display: inline-block;
    margin-left: 4px;
    padding: 0 4px;
    border-radius: 3px;
    background: var(--sd-soft);
    font-size: 10px;
}
.knowledge-search {
    display: flex;
    gap: 8px;
    align-items: center;
    color: var(--sd-muted);
}
.knowledge-search input {
    border: 0;
    background: transparent;
    font-size: 12px;
    width: 190px;
    color: var(--sd-ink);
    padding: 8px 0;
}
.knowledge-search input::placeholder {
    color: var(--sd-muted);
}
.knowledge-columns {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 220px;
    gap: 16px;
}
.knowledge-entry {
    width: 100%;
    display: flex;
    align-items: center;
    text-align: left;
    gap: 16px;
    padding: 16px 0;
    border: 0;
    border-bottom: 1px solid var(--sd-line);
    background: transparent;
    color: var(--sd-ink);
}
.knowledge-entry:hover h2 {
    color: var(--sd-accent);
}
.entry-order {
    width: 84px;
    padding-right: 12px;
    border-right: 1px solid var(--sd-line);
    flex-shrink: 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--sd-muted);
}
.entry-content {
    min-width: 0;
    flex: 1;
}
.entry-meta {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    font-size: 11px;
    color: var(--sd-muted);
}
.entry-content h2 {
    font-size: 15px;
    font-weight: 600;
    line-height: 1.5;
    margin: 8px 0;
}
.entry-bottom {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 7px;
}
.entry-bottom small {
    margin-left: 5px;
    color: var(--sd-muted);
    font-size: 11px;
}
.entry-bottom i {
    font-style: normal;
    margin: 0 6px;
}
.knowledge-entry > svg {
    color: var(--sd-muted);
    flex-shrink: 0;
}
.knowledge-aside {
    padding: 16px 0 16px 16px;
    border-left: 1px solid var(--sd-line);
}
.knowledge-aside h3 {
    font-size: 13px;
    font-weight: 600;
    margin: 0 0 12px;
}
.knowledge-aside > button {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    background: var(--sd-surface);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    padding: 12px;
    margin-top: 8px;
    font-size: 12px;
    color: var(--sd-ink);
}
.knowledge-aside > button:hover {
    border-color: var(--sd-accent);
    color: var(--sd-accent);
}
.knowledge-aside > button span {
    flex: 1;
    text-align: left;
}
.knowledge-no-match {
    padding: 40px 16px;
    text-align: center;
    color: var(--sd-muted);
}
.knowledge-no-match p {
    margin: 16px 0;
}
.knowledge-dialog {
    width: min(900px, 100%);
    padding: 16px;
    border-radius: 6px;
}
.knowledge-dialog .sd-eyebrow {
    margin-bottom: 8px;
    color: var(--sd-muted);
}
.knowledge-dialog header h2 {
    font-size: 18px;
    margin-top: 0;
}
.knowledge-detail-note {
    display: flex;
    gap: 8px;
    align-items: center;
    background: var(--sd-bg);
    color: var(--sd-muted);
    padding: 10px 12px;
    border-radius: 6px;
    font-size: 11px;
}
.knowledge-detail-columns {
    display: grid;
    grid-template-columns: 180px minmax(0, 1fr);
    margin-top: 16px;
}
.knowledge-detail-columns nav {
    border-right: 1px solid var(--sd-line);
    padding-right: 16px;
}
.knowledge-detail-columns nav p {
    color: var(--sd-muted);
    font-size: 11px;
    margin-bottom: 12px;
}
.knowledge-detail-columns nav button {
    display: flex;
    align-items: center;
    text-align: left;
    width: 100%;
    gap: 10px;
    padding: 12px 8px;
    background: transparent;
    color: var(--sd-ink);
    border: 0;
    border-radius: 6px;
    margin-top: 8px;
}
.knowledge-detail-columns nav button.active {
    background: var(--sd-soft);
}
.version-number {
    color: var(--sd-muted);
    font-size: 12px;
}
.knowledge-detail-columns nav strong {
    display: block;
    font-size: 12px;
    font-weight: 500;
}
.knowledge-detail-columns nav small {
    display: block;
    font-size: 10px;
    color: var(--sd-muted);
    margin-top: 5px;
}
.knowledge-detail-columns nav svg {
    margin-left: auto;
    color: var(--sd-accent);
}
.version-body {
    min-width: 0;
    padding-left: 16px;
}
.version-file {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--sd-line);
}
.version-file > div {
    height: 40px;
    width: 34px;
    display: grid;
    place-items: center;
    border: 1px solid var(--sd-line);
    background: var(--sd-surface);
    border-radius: 6px;
    color: var(--sd-accent);
}
.version-file strong {
    display: block;
    font-size: 13px;
    font-weight: 500;
}
.version-file small {
    display: block;
    font-size: 11px;
    color: var(--sd-muted);
    margin-top: 6px;
}
.version-file .sd-pill {
    margin-left: auto;
}
.version-section {
    margin-top: 16px;
}
.version-section label {
    color: var(--sd-muted);
    font-size: 11px;
}
.version-section p {
    line-height: 1.8;
    font-size: 13px;
    margin-top: 8px;
    color: var(--sd-ink);
}
.version-source {
    display: flex;
    align-items: flex-start;
    gap: 7px;
    color: var(--sd-muted);
    font-size: 12px;
    margin-top: 16px;
    line-height: 1.7;
}
.version-source svg {
    flex-shrink: 0;
    margin-top: 3px;
}
.version-footer {
    display: flex;
    justify-content: flex-end;
    border-top: 1px solid var(--sd-line);
    padding-top: 16px;
    margin-top: 16px;
}
@media (max-width: 1000px) {
    .knowledge-columns {
        grid-template-columns: minmax(0, 1fr) 190px;
    }
    .entry-order {
        width: 70px;
    }
}
@media (max-width: 700px) {
    .knowledge-columns {
        grid-template-columns: 1fr;
    }
    .knowledge-aside {
        padding: 0 0 16px;
        border-left: 0;
    }
    .knowledge-toolbar {
        align-items: flex-start;
        flex-direction: column-reverse;
        gap: 8px;
    }
    .knowledge-toolbar .sd-tabs {
        width: 100%;
    }
    .knowledge-search,
    .knowledge-search input {
        width: 100%;
    }
    .knowledge-entry {
        gap: 12px;
    }
    .entry-order {
        width: 52px;
        padding-right: 8px;
        font-size: 11px;
    }
    .entry-content h2 {
        font-size: 14px;
    }
    .knowledge-entry > svg {
        display: none;
    }
    .entry-meta {
        flex-wrap: wrap;
        font-size: 10px;
    }
    .knowledge-detail-columns {
        grid-template-columns: 1fr;
    }
    .knowledge-detail-columns nav {
        border-right: 0;
        border-bottom: 1px solid var(--sd-line);
        padding: 0 0 16px;
        display: flex;
        flex-wrap: wrap;
        gap: 7px;
    }
    .knowledge-detail-columns nav p,
    .knowledge-detail-columns nav small {
        display: none;
    }
    .knowledge-detail-columns nav button {
        padding: 10px;
        width: auto;
    }
    .version-body {
        padding: 16px 0 0;
    }
}
</style>
