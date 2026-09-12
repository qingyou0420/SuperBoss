<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed, onMounted, ref, watch } from 'vue'

import {
    knowledgeApi,
    knowledgeErrorMessage,
    type KnowledgeDoc,
    type KnowledgeRevision,
} from '../api/knowledge'
import { dateLabel } from '../api/parse'
import { projectsApi, type Project } from '../api/projects'
import EmptyLine from '../components/ui/EmptyLine.vue'
import InlineError from '../components/ui/InlineError.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import { DOC_STATUS_LABEL } from '../copy/glossary'
import { knowledgeCopy } from '../copy/pages/knowledge'
import { useAuthStore } from '../stores/auth'

const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })

const auth = useAuthStore()
const canEdit = computed(() => auth.user?.role === 'OWNER')
const docs = ref<KnowledgeDoc[]>([])
const query = ref('')
const selectedId = ref('')
const title = ref('')
const body = ref('')
const tags = ref('')
const stageTitle = ref('')
const changeReason = ref('')
const isCanonical = ref(false)
const linkedProjectId = ref('')
const projects = ref<Project[]>([])
const errorMessage = ref('')
const drawerOpen = ref(false)
const editing = ref<KnowledgeDoc>()
const reviewingRevisionId = ref('')

const viewingRevisionId = ref('')
const selected = computed(
    () =>
        docs.value.find((item) => item.id === selectedId.value) ??
        docs.value[0],
)
const viewingRevision = computed(() => {
    const doc = selected.value
    if (!doc?.revisions?.length || !viewingRevisionId.value) return undefined
    return doc.revisions.find((item) => item.id === viewingRevisionId.value)
})
const displayedBody = computed(
    () => viewingRevision.value?.body_md ?? selected.value?.body_md ?? '',
)
const displayedPoints = computed(() => {
    const revision = viewingRevision.value
    if (!revision) return selected.value?.points ?? []
    return (revision.points_json || []).map((item, index) => ({
        id: String(item.id || `${revision.id}-${index}`),
        title: String(item.title || ''),
        body_md: String(item.body_md || ''),
        sort_order: Number(item.sort_order) || index,
        source_file_id:
            typeof item.source_file_id === 'string'
                ? item.source_file_id
                : null,
    }))
})
const displayedSourceIds = computed(() => {
    const ids: string[] = []
    const revision = viewingRevision.value
    const doc = selected.value
    const top =
        revision?.source_file_id || (!revision ? doc?.source_file_id : null)
    if (typeof top === 'string' && top) ids.push(top)
    for (const point of displayedPoints.value) {
        const sourceId = point.source_file_id
        if (
            typeof sourceId === 'string' &&
            sourceId &&
            !ids.includes(sourceId)
        ) {
            ids.push(sourceId)
        }
    }
    return ids
})
const hasUnpublishedDraft = computed(() => {
    const doc = selected.value
    if (!doc) return false
    return Boolean(
        doc.draft_revision_id &&
        doc.published_revision_id &&
        doc.draft_revision_id !== doc.published_revision_id,
    )
})
function revisionBadge(doc: KnowledgeDoc, item: KnowledgeRevision): string {
    if (item.points_review === 'NEEDS_REVIEW') return knowledgeCopy.needsReview
    if (item.points_review === 'CLEARED') return knowledgeCopy.clearedPoints
    if (item.id === doc.published_revision_id)
        return knowledgeCopy.publishedBadge
    if (item.id === doc.draft_revision_id) return knowledgeCopy.draftBadge
    if (item.released) return knowledgeCopy.releasedBadge
    return knowledgeCopy.internalBadge
}
const grouped = computed(() => {
    const buckets = new Map<string, KnowledgeDoc[]>()
    for (const doc of docs.value) {
        const key = doc.stage_title || knowledgeCopy.unspecified
        const list = buckets.get(key) ?? []
        list.push(doc)
        buckets.set(key, list)
    }
    return [...buckets.entries()]
})
const projectName = computed(() => {
    const id = selected.value?.project_id
    if (!id) return ''
    return projects.value.find((item) => item.id === id)?.name ?? ''
})

function render(source: string): string {
    return markdown.render(source)
}

async function load(): Promise<void> {
    try {
        docs.value = await knowledgeApi.list(query.value.trim() || undefined)
        if (
            selectedId.value &&
            !docs.value.some((item) => item.id === selectedId.value)
        ) {
            selectedId.value = docs.value[0]?.id ?? ''
        }
        if (!projects.value.length) {
            projects.value = await projectsApi.list().catch(() => [])
        }
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

function openCreate(): void {
    editing.value = undefined
    title.value = ''
    body.value = ''
    tags.value = ''
    stageTitle.value = ''
    changeReason.value = ''
    isCanonical.value = false
    linkedProjectId.value = ''
    drawerOpen.value = true
}

function openEdit(doc: KnowledgeDoc): void {
    editing.value = doc
    title.value = doc.title
    body.value = doc.body_md
    tags.value = doc.tags.join('、')
    stageTitle.value = doc.stage_title ?? ''
    changeReason.value = doc.change_reason ?? ''
    isCanonical.value = Boolean(doc.is_canonical)
    linkedProjectId.value = doc.project_id ?? ''
    drawerOpen.value = true
}

async function saveDoc(): Promise<void> {
    if (!title.value.trim()) return
    try {
        if (editing.value) {
            await knowledgeApi.update(editing.value.id, {
                title: title.value.trim(),
                body_md: body.value,
                tags: tags.value
                    .split(/[、,，]/)
                    .map((item) => item.trim())
                    .filter(Boolean),
                stage_title: stageTitle.value.trim(),
                change_reason: changeReason.value.trim(),
                is_canonical: isCanonical.value,
                project_id: linkedProjectId.value || null,
            })
        } else {
            await knowledgeApi.create(
                title.value.trim(),
                body.value,
                tags.value
                    .split(/[、,，]/)
                    .map((item) => item.trim())
                    .filter(Boolean),
                {
                    stage_title: stageTitle.value.trim(),
                    change_reason: changeReason.value.trim(),
                    is_canonical: isCanonical.value,
                    project_id: linkedProjectId.value || null,
                },
            )
        }
        drawerOpen.value = false
        await load()
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

async function publish(doc: KnowledgeDoc): Promise<void> {
    try {
        await knowledgeApi.publish(doc.id)
        await load()
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

async function downloadSource(
    doc: KnowledgeDoc,
    fileId?: string | null,
): Promise<void> {
    try {
        const result = await knowledgeApi.sourceDownload(
            doc.id,
            fileId || doc.source_file_id || undefined,
        )
        if (result.url) window.open(result.url, '_blank')
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

function viewRevision(item: KnowledgeRevision): void {
    viewingRevisionId.value = item.id
}

async function reviewPoints(
    doc: KnowledgeDoc,
    item: KnowledgeRevision,
    action: 'confirm' | 'clear',
): Promise<void> {
    if (reviewingRevisionId.value) return
    reviewingRevisionId.value = item.id
    try {
        await knowledgeApi.reviewPoints(doc.id, item.id, action)
        await load()
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    } finally {
        reviewingRevisionId.value = ''
    }
}

async function unpublish(doc: KnowledgeDoc): Promise<void> {
    try {
        await knowledgeApi.update(doc.id, { status: 'DRAFT' })
        await load()
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

watch(selectedId, () => {
    viewingRevisionId.value = ''
})
onMounted(load)
</script>

<template>
    <section class="knowledge-page" aria-labelledby="knowledge-title">
        <PageHeader :title="knowledgeCopy.title" heading-id="knowledge-title">
            <el-button v-if="canEdit" text @click="openCreate">{{
                knowledgeCopy.create
            }}</el-button>
        </PageHeader>
        <InlineError :message="errorMessage" />
        <div class="layout">
            <aside>
                <form class="search" @submit.prevent="load">
                    <label class="sr-only" for="knowledge-q">{{
                        knowledgeCopy.search
                    }}</label>
                    <el-input
                        id="knowledge-q"
                        v-model="query"
                        :placeholder="knowledgeCopy.search"
                    />
                </form>
                <ul class="doc-list">
                    <li v-for="[stage, items] in grouped" :key="stage">
                        <p class="stage-label">{{ stage }}</p>
                        <el-button
                            v-for="doc in items"
                            :key="doc.id"
                            text
                            :class="{ active: doc.id === selected?.id }"
                            @click="selectedId = doc.id"
                            >{{ doc.title }}</el-button
                        >
                    </li>
                </ul>
                <EmptyLine
                    v-if="!docs.length"
                    :message="
                        query ? knowledgeCopy.noMatch : knowledgeCopy.empty
                    "
                />
            </aside>
            <article v-if="selected">
                <header class="doc-head">
                    <h2>{{ selected.title }}</h2>
                    <div v-if="canEdit">
                        <el-button text @click="openEdit(selected)">{{
                            knowledgeCopy.edit
                        }}</el-button>
                        <el-button
                            v-if="
                                selected.status !== 'PUBLISHED' ||
                                hasUnpublishedDraft
                            "
                            text
                            @click="publish(selected)"
                            >{{
                                selected.status === 'PUBLISHED'
                                    ? knowledgeCopy.publishDraft
                                    : knowledgeCopy.publish
                            }}</el-button
                        >
                        <el-button
                            v-if="selected.status === 'PUBLISHED'"
                            text
                            @click="unpublish(selected)"
                            >{{ knowledgeCopy.unpublish }}</el-button
                        >
                    </div>
                </header>
                <p class="meta">
                    {{
                        [
                            dateLabel(selected.updated_at),
                            selected.stage_title,
                            selected.is_canonical
                                ? knowledgeCopy.canonical
                                : '',
                            projectName,
                            selected.tags.join('、'),
                            DOC_STATUS_LABEL[selected.status],
                        ]
                            .filter(Boolean)
                            .join(' · ')
                    }}
                </p>
                <p v-if="selected.change_reason" class="meta">
                    {{ knowledgeCopy.reason }}：{{ selected.change_reason }}
                </p>
                <p
                    v-for="fileId in displayedSourceIds"
                    :key="fileId"
                    class="meta"
                >
                    <el-button text @click="downloadSource(selected, fileId)">{{
                        knowledgeCopy.downloadSource
                    }}</el-button>
                </p>
                <p v-if="viewingRevision" class="meta">
                    {{
                        viewingRevision.id === selected.published_revision_id
                            ? knowledgeCopy.viewingPublished
                            : viewingRevision.id === selected.draft_revision_id
                              ? knowledgeCopy.viewingDraft
                              : knowledgeCopy.viewingRevision
                    }}
                    v{{ viewingRevision.version }}
                </p>
                <ol v-if="selected.revisions?.length" class="meta revisions">
                    <li v-for="item in selected.revisions" :key="item.id">
                        <el-button text @click="viewRevision(item)">{{
                            knowledgeCopy.viewRevision
                        }}</el-button>
                        v{{ item.version }} ·
                        {{ revisionBadge(selected, item) }} ·
                        {{ item.change_reason }}
                        <el-button
                            v-if="
                                canEdit && item.points_review === 'NEEDS_REVIEW'
                            "
                            text
                            :disabled="Boolean(reviewingRevisionId)"
                            @click="reviewPoints(selected, item, 'confirm')"
                            >{{ knowledgeCopy.confirmPoints }}</el-button
                        >
                        <el-button
                            v-if="
                                canEdit && item.points_review === 'NEEDS_REVIEW'
                            "
                            text
                            :disabled="Boolean(reviewingRevisionId)"
                            @click="reviewPoints(selected, item, 'clear')"
                            >{{ knowledgeCopy.clearPoints }}</el-button
                        >
                        <el-button
                            v-if="item.source_file_id"
                            text
                            @click="
                                downloadSource(selected, item.source_file_id)
                            "
                            >{{ knowledgeCopy.downloadSource }}</el-button
                        >
                    </li>
                </ol>
                <div class="markdown" v-html="render(displayedBody)" />
                <section v-for="point in displayedPoints" :key="point.id">
                    <h3>{{ point.title }}</h3>
                    <p v-if="point.source_file_id" class="meta">
                        <el-button
                            text
                            @click="
                                downloadSource(selected, point.source_file_id)
                            "
                            >{{ knowledgeCopy.downloadSource }}</el-button
                        >
                    </p>
                    <div class="markdown" v-html="render(point.body_md)" />
                </section>
            </article>
        </div>
        <el-drawer
            v-model="drawerOpen"
            :title="editing ? knowledgeCopy.edit : knowledgeCopy.create"
            size="480px"
        >
            <form class="drawer-form" @submit.prevent="saveDoc">
                <label for="doc-title">{{ knowledgeCopy.heading }}</label>
                <el-input id="doc-title" v-model="title" />
                <label>{{ knowledgeCopy.tags }}</label>
                <el-input v-model="tags" />
                <label>{{ knowledgeCopy.stage }}</label>
                <el-input v-model="stageTitle" />
                <label>{{ knowledgeCopy.reason }}</label>
                <el-input v-model="changeReason" type="textarea" :rows="2" />
                <label>{{ knowledgeCopy.project }}</label>
                <el-select v-model="linkedProjectId" clearable>
                    <el-option
                        v-for="item in projects"
                        :key="item.id"
                        :label="item.name"
                        :value="item.id"
                    />
                </el-select>
                <el-checkbox v-model="isCanonical">{{
                    knowledgeCopy.canonical
                }}</el-checkbox>
                <label for="doc-body">{{ knowledgeCopy.body }}</label>
                <el-input
                    id="doc-body"
                    v-model="body"
                    type="textarea"
                    :rows="10"
                />
                <el-button type="primary" native-type="submit">{{
                    editing ? knowledgeCopy.save : knowledgeCopy.saveDraft
                }}</el-button>
            </form>
        </el-drawer>
    </section>
</template>

<style scoped>
.layout {
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: 32px;
}
.doc-list {
    list-style: none;
    margin: 16px 0 0;
    padding: 0;
}
.stage-label {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
    margin: 12px 0 4px;
}
.active {
    font-weight: 600;
    color: var(--sb-accent);
}
.doc-head {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: start;
}
.doc-head h2 {
    font-size: var(--sb-xl);
    font-weight: 600;
    letter-spacing: -0.01em;
}
.meta {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
    margin: 8px 0 24px;
}
.markdown {
    font-size: var(--sb-md);
    line-height: 1.75;
}
.markdown :deep(h3) {
    font-size: var(--sb-lg);
    margin: 24px 0 8px;
}
.drawer-form {
    display: grid;
    gap: 12px;
}
@media (max-width: 760px) {
    .layout {
        grid-template-columns: 1fr;
    }
}
</style>
