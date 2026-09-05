<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
    FileDownloadUnavailableError,
    filesApi,
    type DriveFile,
    type DriveFolder,
    type FileUploadCompleted,
    type FolderVisibility,
} from '../../api/files'
import MultipartUploader from '../../components/files/MultipartUploader.vue'
import { useAuthStore } from '../../stores/auth'

const VISIBILITY_LABEL: Record<FolderVisibility, string> = {
    ALL: '全员',
    MANAGEMENT: '管理层',
    OWNER_ONLY: '仅老板',
}

const props = defineProps<{
    allowedObjectOrigin: string
}>()

const auth = useAuthStore()
const canManage = computed(() => auth.user?.role === 'OWNER')
const folders = ref<DriveFolder[]>([])
const files = ref<DriveFile[]>([])
const currentId = ref('')
const newFolderName = ref('')
const currentResult = ref<FileUploadCompleted>()
const downloadUrl = ref('')
const canCheckDownload = computed(() => {
    const state = currentResult.value?.state
    return Boolean(
        state &&
        !downloadUrl.value &&
        state !== 'INFECTED' &&
        state !== 'FAILED',
    )
})
const loading = ref(true)
const errorMessage = ref('')
const renamingId = ref('')
const renameValue = ref('')
const movingId = ref('')
const moveTarget = ref('')

const current = computed(
    () => folders.value.find((folder) => folder.id === currentId.value) ?? null,
)
const children = computed(() =>
    folders.value.filter((folder) => folder.parent_id === currentId.value),
)
const breadcrumbs = computed(() => {
    const trail: DriveFolder[] = []
    let cursor = current.value
    while (cursor) {
        trail.unshift(cursor)
        cursor =
            folders.value.find((folder) => folder.id === cursor?.parent_id) ??
            null
    }
    return trail
})

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

const currentStatusMessage = computed(() => {
    switch (currentResult.value?.state) {
        case 'CLEAN':
            return '处理完成'
        case 'INFECTED':
            return '检测到风险，文件不可下载'
        case 'FAILED':
            return '扫描失败，文件不可下载，请重新上传'
        case 'QUARANTINED':
        case 'SCANNING':
            return '扫描中'
        default:
            return ''
    }
})

async function loadFolders(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        folders.value = await filesApi.listFolders()
        if (!folders.value.some((folder) => folder.id === currentId.value)) {
            currentId.value =
                folders.value.find(
                    (folder) =>
                        folder.name === '项目' && folder.parent_id === null,
                )?.id ??
                folders.value.find((folder) => folder.parent_id === null)?.id ??
                ''
        }
        await loadFiles()
    } catch {
        errorMessage.value = '网盘暂时无法加载，请稍后重试。'
    } finally {
        loading.value = false
    }
}

async function loadFiles(): Promise<void> {
    if (!currentId.value) {
        files.value = []
        return
    }
    files.value = await filesApi.listFiles(currentId.value)
}

async function createFolder(): Promise<void> {
    const name = newFolderName.value.trim()
    if (!name || !currentId.value) return
    errorMessage.value = ''
    try {
        const created = await filesApi.createFolder(currentId.value, name)
        folders.value.push(created)
        newFolderName.value = ''
    } catch {
        errorMessage.value = '无法创建目录。'
    }
}

async function downloadFile(file: DriveFile): Promise<void> {
    errorMessage.value = ''
    try {
        globalThis.location.assign(await filesApi.download(file.id))
    } catch (error) {
        if (error instanceof FileDownloadUnavailableError) {
            errorMessage.value =
                error.state === 'INFECTED'
                    ? '检测到风险，文件不可下载'
                    : '扫描失败，文件不可下载'
            return
        }
        errorMessage.value = '文件仍在扫描中，请稍后重试。'
    }
}

function beginRename(file: DriveFile): void {
    renamingId.value = file.id
    renameValue.value = file.filename
}

function beginMove(file: DriveFile): void {
    movingId.value = file.id
    moveTarget.value = file.folder_id
}

async function renameFile(file: DriveFile): Promise<void> {
    const filename = renameValue.value.trim()
    if (!filename) return
    try {
        const updated = await filesApi.rename(file.id, filename)
        files.value = files.value.map((item) =>
            item.id === updated.id ? updated : item,
        )
        renamingId.value = ''
    } catch {
        errorMessage.value = '无法重命名。'
    }
}

async function removeFile(file: DriveFile): Promise<void> {
    if (!globalThis.confirm(`确认删除 ${file.filename} 吗？`)) return
    try {
        await filesApi.remove(file.id)
        files.value = files.value.filter((item) => item.id !== file.id)
    } catch {
        errorMessage.value = '无法删除文件。'
    }
}

async function moveFile(file: DriveFile): Promise<void> {
    if (!moveTarget.value || moveTarget.value === file.folder_id) return
    try {
        await filesApi.move(file.id, moveTarget.value)
        files.value = files.value.filter((item) => item.id !== file.id)
        movingId.value = ''
    } catch {
        errorMessage.value = '无法移动文件。'
    }
}

function showCompleted(result: FileUploadCompleted): void {
    currentResult.value = result
    downloadUrl.value = ''
    void loadFiles()
}

async function prepareDownload(): Promise<void> {
    const result = currentResult.value
    if (!result) return
    errorMessage.value = ''
    try {
        downloadUrl.value = await filesApi.download(result.file_id)
        currentResult.value = { ...result, state: 'CLEAN' }
    } catch (error) {
        if (error instanceof FileDownloadUnavailableError) {
            currentResult.value = { ...result, state: error.state }
            downloadUrl.value = ''
            return
        }
        errorMessage.value = '文件仍在扫描中，请稍后重试。'
    }
}

watch(currentId, () => {
    void loadFiles()
})
onMounted(loadFolders)
</script>

<template>
    <section class="drive-page" aria-labelledby="drive-title">
        <header>
            <p class="drive-page__eyebrow">{{ auth.user?.role || '工作台' }}</p>
            <h1 id="drive-title">网盘</h1>
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
        <nav class="crumbs" aria-label="目录">
            <button
                v-for="folder in breadcrumbs"
                :key="folder.id"
                type="button"
                @click="currentId = folder.id"
            >
                {{ folder.name }}
            </button>
        </nav>
        <div v-loading="loading" class="drive-grid">
            <el-card shadow="never">
                <h2>目录</h2>
                <p v-if="current">
                    可见范围：{{ VISIBILITY_LABEL[current.visibility] }}
                </p>
                <button
                    v-for="folder in children"
                    :key="folder.id"
                    type="button"
                    class="folder-item"
                    @click="currentId = folder.id"
                >
                    {{ folder.name }}
                </button>
                <form
                    v-if="canManage && currentId"
                    class="new-folder"
                    @submit.prevent="createFolder"
                >
                    <label for="new-folder">新建子目录</label>
                    <el-input id="new-folder" v-model="newFolderName" />
                    <el-button native-type="submit" type="primary"
                        >创建</el-button
                    >
                </form>
            </el-card>
            <el-card shadow="never">
                <h2>文件</h2>
                <ul class="file-list">
                    <li v-for="file in files" :key="file.id">
                        <strong>{{ file.filename }}</strong>
                        <span>{{ file.state }}</span>
                        <el-button text @click="downloadFile(file)"
                            >下载</el-button
                        >
                        <template v-if="canManage">
                            <el-button text @click="beginRename(file)"
                                >重命名</el-button
                            >
                            <el-button text @click="beginMove(file)"
                                >移动</el-button
                            >
                            <el-button
                                text
                                type="danger"
                                @click="removeFile(file)"
                                >删除</el-button
                            >
                        </template>
                        <form
                            v-if="renamingId === file.id"
                            @submit.prevent="renameFile(file)"
                        >
                            <el-input v-model="renameValue" />
                            <el-button native-type="submit">确定</el-button>
                        </form>
                        <form
                            v-if="movingId === file.id"
                            @submit.prevent="moveFile(file)"
                        >
                            <select v-model="moveTarget" aria-label="目标目录">
                                <option
                                    v-for="folder in folders"
                                    :key="folder.id"
                                    :value="folder.id"
                                >
                                    {{ folder.name }}
                                </option>
                            </select>
                            <el-button native-type="submit">确定移动</el-button>
                        </form>
                    </li>
                </ul>
                <MultipartUploader
                    v-if="validObjectOrigin && currentId"
                    :allowed-object-origin="allowedObjectOrigin"
                    :folder-id="currentId"
                    @completed="showCompleted"
                />
            </el-card>
        </div>
        <el-card v-if="currentResult" shadow="never">
            <h2>本次上传</h2>
            <p>{{ currentStatusMessage }}</p>
            <p>{{ currentResult.file_id }}</p>
            <el-button v-if="canCheckDownload" @click="prepareDownload">
                检查并获取下载
            </el-button>
            <a v-if="downloadUrl" :href="downloadUrl">下载本次文件</a>
        </el-card>
    </section>
</template>

<style scoped>
.drive-page,
.drive-grid,
.file-list,
.new-folder {
    display: grid;
    gap: 1rem;
}
.drive-page__eyebrow {
    margin: 0;
    color: #909399;
}
.crumbs {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}
.folder-item,
.crumbs button {
    text-align: left;
    cursor: pointer;
    background: #fff;
    border: 1px solid #dcdfe6;
    border-radius: 8px;
    padding: 0.6rem 0.9rem;
}
.drive-grid {
    grid-template-columns: minmax(220px, 0.8fr) minmax(320px, 1.2fr);
}
.file-list {
    list-style: none;
    padding: 0;
    margin: 0;
}
.file-list li {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
}
@media (max-width: 760px) {
    .drive-grid {
        grid-template-columns: 1fr;
    }
}
</style>
