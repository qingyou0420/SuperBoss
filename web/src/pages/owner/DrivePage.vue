<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { filesApi, type FileUploadCompleted } from '../../api/files'
import { projectsApi, type Project } from '../../api/projects'
import MultipartUploader from '../../components/files/MultipartUploader.vue'

const props = defineProps<{
    allowedObjectOrigin: string
}>()

const activeProjects = ref<Project[]>([])
const selectedProjectId = ref('')
const currentResult = ref<FileUploadCompleted>()
const downloadUrl = ref('')
const loading = ref(true)
const errorMessage = ref('')

const validObjectOrigin = computed(() => {
    try {
        const parsed = new globalThis.URL(props.allowedObjectOrigin)
        return (
            parsed.protocol === 'https:' &&
            Boolean(parsed.hostname) &&
            !parsed.username &&
            !parsed.password &&
            parsed.pathname === '/' &&
            !parsed.search &&
            !parsed.hash &&
            parsed.origin === props.allowedObjectOrigin
        )
    } catch {
        return false
    }
})

async function loadProjects(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        activeProjects.value = (await projectsApi.list()).filter(
            (project) => project.status === 'ACTIVE',
        )
        selectedProjectId.value = activeProjects.value[0]?.id ?? ''
    } catch {
        errorMessage.value = '项目列表暂时无法加载，请稍后重试。'
    } finally {
        loading.value = false
    }
}

function showCompleted(result: FileUploadCompleted): void {
    currentResult.value = result
    downloadUrl.value = ''
}

async function prepareDownload(): Promise<void> {
    const result = currentResult.value
    if (!result) return
    errorMessage.value = ''
    try {
        downloadUrl.value = await filesApi.download(result.file_id)
        currentResult.value = { ...result, state: 'CLEAN' }
    } catch {
        errorMessage.value = '文件仍在扫描中，请稍后重试。'
    }
}

onMounted(loadProjects)
</script>

<template>
    <section class="drive-page" aria-labelledby="drive-title">
        <header>
            <p class="drive-page__eyebrow">OWNER</p>
            <h1 id="drive-title">文件上传</h1>
            <p>上传完成后仅展示本次文件的处理状态。</p>
        </header>

        <el-alert
            v-if="!validObjectOrigin"
            type="error"
            :closable="false"
            show-icon
        >
            对象存储来源尚未安全配置，暂时无法上传文件。
        </el-alert>
        <el-alert
            v-else-if="errorMessage"
            type="error"
            :closable="false"
            show-icon
        >
            {{ errorMessage }}
        </el-alert>

        <el-card v-if="validObjectOrigin" v-loading="loading" shadow="never">
            <label class="project-field">
                项目
                <select v-model="selectedProjectId" :disabled="loading">
                    <option
                        v-for="project in activeProjects"
                        :key="project.id"
                        :value="project.id"
                    >
                        {{ project.name }}
                    </option>
                </select>
            </label>

            <MultipartUploader
                v-if="selectedProjectId"
                :allowed-object-origin="allowedObjectOrigin"
                :project-id="selectedProjectId"
                @completed="showCompleted"
            />
            <p v-else-if="!loading">暂无可用于上传的启用项目。</p>
        </el-card>

        <el-card v-if="currentResult" shadow="never" class="current-result">
            <h2>本次上传</h2>
            <p>
                {{ currentResult.state === 'CLEAN' ? '处理完成' : '扫描中' }}
            </p>
            <p>{{ currentResult.file_id }}</p>
            <el-button v-if="!downloadUrl" @click="prepareDownload">
                检查并获取下载
            </el-button>
            <a v-if="downloadUrl" :href="downloadUrl">下载本次文件</a>
        </el-card>
    </section>
</template>

<style scoped>
.drive-page {
    display: grid;
    gap: 1rem;
}

.drive-page__eyebrow {
    margin: 0;
    color: #909399;
}

.project-field {
    display: grid;
    gap: 0.4rem;
    margin-bottom: 1.25rem;
}

.project-field select {
    min-height: 2.5rem;
    padding: 0.45rem 0.6rem;
    color: #303133;
    background: #fff;
    border: 1px solid #dcdfe6;
    border-radius: 6px;
}

.current-result {
    overflow-wrap: anywhere;
}
</style>
