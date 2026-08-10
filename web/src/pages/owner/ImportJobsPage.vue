<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
    importErrorMessage,
    importsApi,
    type OwnerImportSummary,
} from '../../api/imports'

const jobs = ref<OwnerImportSummary[]>([])
const selected = ref<OwnerImportSummary>()
const loading = ref(false)
const errorMessage = ref('')

const statusLabels: Record<OwnerImportSummary['status'], string> = {
    UPLOADING: '上传中',
    SCANNING: '扫描中',
    RECEIVED: '已接收',
    REJECTED: '已拒绝',
    CONFLICT: '有冲突',
}

const resultReasons: Readonly<Record<string, string>> = {
    ATTACHMENT_INFECTED: '附件检出风险，导入已拒绝。',
    ATTACHMENT_SCAN_FAILED: '附件扫描未完成，导入已拒绝。',
    BASE_SHA256_MISMATCH: '基础版本不一致，导入发生冲突。',
}

function resultReason(code: string | null): string {
    if (code === null) return ''
    return resultReasons[code] ?? '任务未完成，请联系管理员查看审计记录。'
}

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        jobs.value = await importsApi.list(100)
        selected.value = jobs.value[0]
    } catch (error) {
        jobs.value = []
        selected.value = undefined
        errorMessage.value = importErrorMessage(error)
    } finally {
        loading.value = false
    }
}

onMounted(load)
</script>

<template>
    <section class="imports-page" aria-labelledby="imports-title">
        <header>
            <p class="imports-page__eyebrow">OWNER</p>
            <h1 id="imports-title">导入任务</h1>
        </header>

        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>

        <div v-loading="loading" class="imports-grid" aria-live="polite">
            <div class="imports-list">
                <button
                    v-for="job in jobs"
                    :key="job.id"
                    type="button"
                    class="import-item"
                    :class="{ 'is-selected': selected?.id === job.id }"
                    @click="selected = job"
                >
                    <strong>{{ job.local_task_id }}</strong>
                    <span>{{ statusLabels[job.status] }}</span>
                </button>
            </div>

            <el-card v-if="selected" shadow="never" class="import-detail">
                <h2>任务详情</h2>
                <dl>
                    <dt>项目</dt>
                    <dd>{{ selected.project_id }}</dd>
                    <dt>本地任务</dt>
                    <dd>{{ selected.local_task_id }}</dd>
                    <dt>模型</dt>
                    <dd>{{ selected.model_label }}</dd>
                    <template v-if="selected.external_document_reference">
                        <dt>外部文档</dt>
                        <dd>{{ selected.external_document_reference }}</dd>
                    </template>
                </dl>

                <h3>附件扫描</h3>
                <ul>
                    <li
                        v-for="attachment in selected.attachments"
                        :key="attachment.id"
                    >
                        <span>{{ attachment.kind }}</span>
                        <span>{{ attachment.file_state }}</span>
                    </li>
                </ul>

                <p v-if="selected.result_code" class="import-detail__reason">
                    {{ resultReason(selected.result_code) }}
                </p>
            </el-card>
        </div>
    </section>
</template>

<style scoped>
.imports-page,
.imports-list,
.import-detail {
    display: grid;
    gap: 1rem;
}

.imports-page__eyebrow {
    margin: 0;
    color: #909399;
}

.imports-grid {
    display: grid;
    grid-template-columns: minmax(220px, 0.7fr) minmax(320px, 1.3fr);
    gap: 1rem;
}

.import-item {
    display: flex;
    justify-content: space-between;
    padding: 0.9rem 1rem;
    text-align: left;
    cursor: pointer;
    background: #fff;
    border: 1px solid #dcdfe6;
    border-radius: 8px;
}

.import-item.is-selected {
    border-color: #409eff;
}

.import-detail dl {
    display: grid;
    grid-template-columns: 6rem 1fr;
    gap: 0.5rem;
    margin: 0;
}

.import-detail dd {
    margin: 0;
    overflow-wrap: anywhere;
}

.import-detail li {
    display: flex;
    gap: 1rem;
}

.import-detail__reason {
    color: #c45656;
}

@media (max-width: 760px) {
    .imports-grid {
        grid-template-columns: 1fr;
    }
}
</style>
