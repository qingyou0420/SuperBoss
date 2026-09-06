<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { bytesLabel } from '../../api/parse'
import {
    FileDownloadUnavailableError,
    filesApi,
    type DriveFile,
    type DriveFolder,
    type FileUploadCompleted,
} from '../../api/files'
import DateText from '../../components/ui/DateText.vue'
import Dot from '../../components/ui/Dot.vue'
import DropZone from '../../components/files/DropZone.vue'
import EmptyLine from '../../components/ui/EmptyLine.vue'
import InlineError from '../../components/ui/InlineError.vue'
import MultipartUploader from '../../components/files/MultipartUploader.vue'
import PageHeader from '../../components/ui/PageHeader.vue'
import UploadTray from '../../components/files/UploadTray.vue'
import { useMultipartUpload } from '../../components/files/useMultipartUpload'
import { FILE_STATE_LABEL, FOLDER_NAME } from '../../copy/glossary'
import { driveCopy } from '../../copy/pages/drive'
import { useAuthStore } from '../../stores/auth'

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
const { tray, upload, clearTray } = useMultipartUpload(
    () => props.allowedObjectOrigin,
)
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
const roots = computed(() =>
    folders.value.filter((folder) => folder.parent_id === null),
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
            return ''
        case 'INFECTED':
            return driveCopy.infected
        case 'FAILED':
            return driveCopy.scanFailed
        case 'QUARANTINED':
        case 'SCANNING':
            return driveCopy.scanning
        default:
            return ''
    }
})

const canCheckDownload = computed(() => {
    const state = currentResult.value?.state
    return Boolean(
        state &&
        !downloadUrl.value &&
        state !== 'INFECTED' &&
        state !== 'FAILED',
    )
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
                        folder.name === FOLDER_NAME.PROJECTS &&
                        folder.parent_id === null,
                )?.id ??
                folders.value.find((folder) => folder.parent_id === null)?.id ??
                ''
        }
        await loadFiles()
    } catch {
        errorMessage.value = driveCopy.empty
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
        errorMessage.value = driveCopy.createFailed
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
                    ? driveCopy.infected
                    : driveCopy.scanFailed
            return
        }
        errorMessage.value = driveCopy.stillScanning
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
        errorMessage.value = driveCopy.renameFailed
    }
}

async function removeFile(file: DriveFile): Promise<void> {
    try {
        await filesApi.remove(file.id)
        files.value = files.value.filter((item) => item.id !== file.id)
    } catch {
        errorMessage.value = driveCopy.deleteFailed
    }
}

async function moveFile(file: DriveFile): Promise<void> {
    if (!moveTarget.value || moveTarget.value === file.folder_id) return
    try {
        await filesApi.move(file.id, moveTarget.value)
        files.value = files.value.filter((item) => item.id !== file.id)
        movingId.value = ''
    } catch {
        errorMessage.value = driveCopy.moveFailed
    }
}

function showCompleted(result: FileUploadCompleted): void {
    currentResult.value = result
    downloadUrl.value = ''
    clearTray()
    void loadFiles()
}

async function prepareDownload(): Promise<void> {
    const result = currentResult.value
    if (!result) return
    errorMessage.value = ''
    try {
        downloadUrl.value = await filesApi.download(result.file_id)
        currentResult.value = { ...result, state: 'CLEAN' }
        clearTray()
    } catch (error) {
        if (error instanceof FileDownloadUnavailableError) {
            currentResult.value = { ...result, state: error.state }
            downloadUrl.value = ''
            return
        }
        errorMessage.value = driveCopy.stillScanning
    }
}

function fileTone(file: DriveFile): 'muted' | 'danger' | undefined {
    if (file.state === 'INFECTED' || file.state === 'FAILED') return 'danger'
    if (
        file.state === 'SCANNING' ||
        file.state === 'QUARANTINED' ||
        file.state === 'UPLOADING'
    )
        return 'muted'
    return undefined
}

async function onDropped(list: File[]): Promise<void> {
    if (!validObjectOrigin.value || !currentId.value) return
    for (const file of list) {
        const result = await upload(file, currentId.value)
        if (result) showCompleted(result)
    }
}

watch(currentId, () => {
    void loadFiles()
})
onMounted(loadFolders)
</script>

<template>
    <DropZone @files="onDropped">
        <section class="drive-page" aria-labelledby="drive-title">
            <PageHeader :title="driveCopy.title" heading-id="drive-title">
                <span v-if="!validObjectOrigin" class="hint">{{
                    driveCopy.unconfigured
                }}</span>
            </PageHeader>
            <InlineError :message="errorMessage" />
            <nav class="crumbs" :aria-label="driveCopy.crumbs">
                <el-button
                    v-for="folder in breadcrumbs"
                    :key="folder.id"
                    text
                    @click="currentId = folder.id"
                    >{{ folder.name }}</el-button
                >
            </nav>
            <div v-loading="loading" class="drive-grid">
                <aside>
                    <el-button
                        v-for="folder in roots"
                        :key="folder.id"
                        text
                        :class="{ active: folder.id === currentId }"
                        @click="currentId = folder.id"
                        >{{ folder.name }}</el-button
                    >
                    <el-button
                        v-for="folder in children"
                        :key="folder.id"
                        text
                        @click="currentId = folder.id"
                        >{{ folder.name }}</el-button
                    >
                    <form
                        v-if="canManage && currentId"
                        class="new-folder"
                        @submit.prevent="createFolder"
                    >
                        <label for="new-folder">{{
                            driveCopy.newSubfolder
                        }}</label>
                        <el-input id="new-folder" v-model="newFolderName" />
                        <el-button native-type="submit">{{
                            driveCopy.create
                        }}</el-button>
                    </form>
                </aside>
                <div>
                    <ul class="file-list">
                        <li v-for="row in files" :key="row.id">
                            <Dot
                                v-if="fileTone(row) === 'muted'"
                                tone="muted"
                            />
                            <strong
                                :class="{ danger: fileTone(row) === 'danger' }"
                                >{{ row.filename }}</strong
                            >
                            <span>{{ bytesLabel(row.size_bytes) }}</span>
                            <DateText :value="row.created_at" format="short" />
                            <span>{{ row.uploader_name || '' }}</span>
                            <span
                                v-if="
                                    FILE_STATE_LABEL[
                                        row.state as keyof typeof FILE_STATE_LABEL
                                    ]
                                "
                                class="state"
                                >{{
                                    FILE_STATE_LABEL[
                                        row.state as keyof typeof FILE_STATE_LABEL
                                    ]
                                }}</span
                            >
                            <el-button text @click="downloadFile(row)">{{
                                driveCopy.download
                            }}</el-button>
                            <template v-if="canManage">
                                <el-button text @click="beginRename(row)">{{
                                    driveCopy.rename
                                }}</el-button>
                                <el-button text @click="beginMove(row)">{{
                                    driveCopy.move
                                }}</el-button>
                                <el-popconfirm
                                    :title="driveCopy.removeConfirm"
                                    :teleported="false"
                                    @confirm="removeFile(row)"
                                >
                                    <template #reference>
                                        <el-button text>{{
                                            driveCopy.remove
                                        }}</el-button>
                                    </template>
                                </el-popconfirm>
                            </template>
                            <form
                                v-if="renamingId === row.id"
                                @submit.prevent="renameFile(row)"
                            >
                                <el-input v-model="renameValue" />
                                <el-button native-type="submit">{{
                                    driveCopy.confirm
                                }}</el-button>
                            </form>
                            <form
                                v-if="movingId === row.id"
                                @submit.prevent="moveFile(row)"
                            >
                                <el-select
                                    id="move-target"
                                    v-model="moveTarget"
                                    :aria-label="driveCopy.targetFolder"
                                    :placeholder="driveCopy.targetFolder"
                                >
                                    <el-option
                                        v-for="folder in folders"
                                        :key="folder.id"
                                        :label="folder.name"
                                        :value="folder.id"
                                    />
                                </el-select>
                                <el-button native-type="submit">{{
                                    driveCopy.confirmMove
                                }}</el-button>
                            </form>
                        </li>
                    </ul>
                    <EmptyLine
                        v-if="!files.length"
                        :message="driveCopy.empty"
                    />
                    <MultipartUploader
                        v-if="validObjectOrigin && currentId"
                        :allowed-object-origin="allowedObjectOrigin"
                        :folder-id="currentId"
                        @completed="showCompleted"
                    />
                    <div v-if="currentResult" class="result">
                        <p>{{ currentStatusMessage }}</p>
                        <el-button
                            v-if="canCheckDownload"
                            @click="prepareDownload"
                            >{{ driveCopy.download }}</el-button
                        >
                        <a v-if="downloadUrl" :href="downloadUrl">{{
                            driveCopy.download
                        }}</a>
                    </div>
                </div>
            </div>
            <UploadTray :items="tray" />
        </section>
    </DropZone>
</template>

<style scoped>
.drive-grid {
    display: grid;
    grid-template-columns: 240px 1fr;
    gap: 32px;
}
aside {
    display: grid;
    align-content: start;
    gap: 4px;
}
.active {
    font-weight: 600;
    color: var(--sb-accent);
}
.crumbs {
    margin-bottom: 16px;
}
.new-folder {
    display: grid;
    gap: 8px;
    margin-top: 16px;
}
.hint,
.state {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
.danger {
    color: var(--sb-danger);
}
.file-list {
    list-style: none;
    margin: 0 0 16px;
    padding: 0;
}
.file-list li {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    min-height: 48px;
    border-bottom: 1px solid var(--sb-line);
}
.result {
    margin-top: 16px;
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
@media (max-width: 760px) {
    .drive-grid {
        grid-template-columns: 1fr;
    }
}
</style>
