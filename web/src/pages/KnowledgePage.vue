<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed, onMounted, ref } from 'vue'

import {
    knowledgeApi,
    knowledgeErrorMessage,
    type KnowledgeDoc,
} from '../api/knowledge'
import { dateLabel } from '../api/parse'
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
const errorMessage = ref('')
const drawerOpen = ref(false)
const editing = ref<KnowledgeDoc>()

const selected = computed(
    () =>
        docs.value.find((item) => item.id === selectedId.value) ??
        docs.value[0],
)

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
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

function openCreate(): void {
    editing.value = undefined
    title.value = ''
    body.value = ''
    tags.value = ''
    drawerOpen.value = true
}

function openEdit(doc: KnowledgeDoc): void {
    editing.value = doc
    title.value = doc.title
    body.value = doc.body_md
    tags.value = doc.tags.join('、')
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
            })
        } else {
            await knowledgeApi.create(
                title.value.trim(),
                body.value,
                tags.value
                    .split(/[、,，]/)
                    .map((item) => item.trim())
                    .filter(Boolean),
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

async function unpublish(doc: KnowledgeDoc): Promise<void> {
    try {
        await knowledgeApi.update(doc.id, { status: 'DRAFT' })
        await load()
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

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
                    <li v-for="doc in docs" :key="doc.id">
                        <el-button
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
                            v-if="selected.status !== 'PUBLISHED'"
                            text
                            @click="publish(selected)"
                            >{{ knowledgeCopy.publish }}</el-button
                        >
                        <el-button v-else text @click="unpublish(selected)">{{
                            knowledgeCopy.unpublish
                        }}</el-button>
                    </div>
                </header>
                <p class="meta">
                    {{
                        [
                            dateLabel(selected.updated_at),
                            selected.tags.join('、'),
                            DOC_STATUS_LABEL[selected.status],
                        ]
                            .filter(Boolean)
                            .join(' · ')
                    }}
                </p>
                <div class="markdown" v-html="render(selected.body_md)" />
                <section v-for="point in selected.points" :key="point.id">
                    <h3>{{ point.title }}</h3>
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
