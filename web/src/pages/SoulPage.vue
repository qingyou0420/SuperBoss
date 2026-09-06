<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { agentApi, agentErrorMessage, type SoulVersion } from '../api/agent'
import { dateTimeShort } from '../api/parse'
import Dot from '../components/ui/Dot.vue'
import InlineError from '../components/ui/InlineError.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import { soulCopy } from '../copy/pages/soul'

const versions = ref<SoulVersion[]>([])
const content = ref('')
const note = ref('')
const preview = ref('')
const previewOpen = ref(false)
const errorMessage = ref('')
const saving = ref(false)
const usage = ref({ prompt_tokens: 0, completion_tokens: 0 })

async function load(): Promise<void> {
    versions.value = await agentApi.listSoul()
    const active = versions.value.find((item) => item.is_active)
    if (active) content.value = active.content
    try {
        usage.value = await agentApi.monthlyUsage()
    } catch {
        usage.value = { prompt_tokens: 0, completion_tokens: 0 }
    }
}

async function save(): Promise<void> {
    saving.value = true
    errorMessage.value = ''
    try {
        await agentApi.writeSoul(content.value, note.value)
        note.value = ''
        await load()
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function activate(id: string): Promise<void> {
    try {
        await agentApi.activateSoul(id)
        await load()
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

async function showPreview(): Promise<void> {
    try {
        preview.value = await agentApi.previewSoul()
        previewOpen.value = true
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

onMounted(async () => {
    try {
        await load()
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
})
</script>

<template>
    <section class="soul-page" aria-labelledby="soul-title">
        <PageHeader :title="soulCopy.title" heading-id="soul-title">
            <el-button text @click="showPreview">{{
                soulCopy.preview
            }}</el-button>
            <el-button type="primary" :loading="saving" @click="save">{{
                soulCopy.save
            }}</el-button>
        </PageHeader>
        <InlineError :message="errorMessage" />
        <div class="soul-grid">
            <el-input
                v-model="content"
                type="textarea"
                class="editor"
                :autosize="{ minRows: 16 }"
            />
            <aside>
                <h2>{{ soulCopy.versions }}</h2>
                <ul>
                    <li v-for="item in versions" :key="item.id">
                        <Dot v-if="item.is_active" tone="ok" />
                        <span
                            >{{ dateTimeShort(item.created_at) }} ·
                            {{ item.note || '—' }}</span
                        >
                        <el-button
                            v-if="!item.is_active"
                            text
                            @click="activate(item.id)"
                            >{{ soulCopy.activate }}</el-button
                        >
                    </li>
                </ul>
                <p class="usage">
                    {{ soulCopy.usage }}
                    {{ usage.prompt_tokens + usage.completion_tokens }}
                    {{ soulCopy.tokens }}
                </p>
            </aside>
        </div>
        <el-drawer v-model="previewOpen" :title="soulCopy.preview" size="480px">
            <pre>{{ preview }}</pre>
        </el-drawer>
    </section>
</template>

<style scoped>
.soul-grid {
    display: grid;
    grid-template-columns: minmax(280px, 2fr) minmax(220px, 1fr);
    gap: 32px;
}
.editor :deep(textarea) {
    font-family: var(--sb-mono);
    font-size: 14px;
    line-height: 1.7;
    background: var(--sb-paper);
    box-shadow: none;
}
aside h2 {
    font-size: var(--sb-sm);
    color: var(--sb-ink-2);
    margin-bottom: 12px;
}
ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 8px;
}
li {
    display: flex;
    gap: 8px;
    align-items: center;
    min-height: 48px;
    border-bottom: 1px solid var(--sb-line);
    font-size: var(--sb-sm);
}
.usage {
    margin-top: 24px;
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
pre {
    white-space: pre-wrap;
    font-family: var(--sb-mono);
    font-size: 13px;
}
@media (max-width: 760px) {
    .soul-grid {
        grid-template-columns: 1fr;
    }
}
</style>
