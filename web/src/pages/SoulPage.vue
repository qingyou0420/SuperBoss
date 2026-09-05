<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { agentApi, agentErrorMessage, type SoulVersion } from '../api/agent'

const versions = ref<SoulVersion[]>([])
const content = ref('')
const note = ref('')
const preview = ref('')
const errorMessage = ref('')
const saving = ref(false)

async function load(): Promise<void> {
    versions.value = await agentApi.listSoul()
    const active = versions.value.find((item) => item.is_active)
    if (active) content.value = active.content
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
        <h1 id="soul-title">SOUL</h1>
        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>
        <div class="soul-grid">
            <el-card shadow="never">
                <form @submit.prevent="save">
                    <label for="soul-content">当前人设</label>
                    <el-input
                        id="soul-content"
                        v-model="content"
                        type="textarea"
                        :autosize="{ minRows: 12 }"
                    />
                    <label for="soul-note">版本说明</label>
                    <el-input id="soul-note" v-model="note" />
                    <el-button
                        type="primary"
                        native-type="submit"
                        :loading="saving"
                        >保存为新版本</el-button
                    >
                    <el-button @click="showPreview">预览提示词</el-button>
                </form>
                <pre v-if="preview">{{ preview }}</pre>
            </el-card>
            <el-card shadow="never">
                <h2>版本</h2>
                <ul>
                    <li v-for="item in versions" :key="item.id">
                        <strong>{{ item.note || '未命名' }}</strong>
                        <span v-if="item.is_active">（当前）</span>
                        <el-button
                            v-if="!item.is_active"
                            text
                            @click="activate(item.id)"
                            >回滚为当前</el-button
                        >
                    </li>
                </ul>
            </el-card>
        </div>
    </section>
</template>

<style scoped>
.soul-grid {
    display: grid;
    grid-template-columns: minmax(280px, 1.4fr) minmax(220px, 0.8fr);
    gap: 1rem;
}
form,
ul {
    display: grid;
    gap: 8px;
}
ul {
    list-style: none;
    padding: 0;
}
pre {
    white-space: pre-wrap;
}
@media (max-width: 760px) {
    .soul-grid {
        grid-template-columns: 1fr;
    }
}
</style>
