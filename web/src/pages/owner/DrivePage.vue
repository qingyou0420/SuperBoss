<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { bytesLabel } from '../../api/parse'
import {
    FileDownloadUnavailableError,
    filesApi,
    type DriveFile,
    type DriveFolder,
} from '../../api/files'
import DateText from '../../components/ui/DateText.vue'
import Dot from '../../components/ui/Dot.vue'
import DropZone from '../../components/files/DropZone.vue'
import InlineError from '../../components/ui/InlineError.vue'
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
const folderDrawerOpen = ref(false)
const { tray, upload, clearTray } = useMultipartUpload(
    () => props.allowedObjectOrigin,
)
const loading = ref(true)
const errorMessage = ref('')
const renamingId = ref('')
const renameValue = ref('')
const movingFile = ref<DriveFile>()
const moveTarget = ref('')
const headerFile = ref<HTMLInputElement | null>(null)
const pendingRemove = ref<DriveFile>()
const removeOpen = computed({
    get: () => pendingRemove.value !== undefined,
    set: (open: boolean) => {
        if (!open) pendingRemove.value = undefined
    },
})

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
        folderDrawerOpen.value = false
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
    movingFile.value = file
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

function requestRemove(file: DriveFile): void {
    pendingRemove.value = file
}

async function confirmRemove(): Promise<void> {
    const file = pendingRemove.value
    if (!file) return
    try {
        await filesApi.remove(file.id)
        files.value = files.value.filter((item) => item.id !== file.id)
        pendingRemove.value = undefined
    } catch {
        pendingRemove.value = undefined
        errorMessage.value = driveCopy.deleteFailed
    }
}

async function moveFile(file: DriveFile): Promise<void> {
    if (!moveTarget.value || moveTarget.value === file.folder_id) return
    try {
        await filesApi.move(file.id, moveTarget.value)
        files.value = files.value.filter((item) => item.id !== file.id)
        movingFile.value = undefined
    } catch {
        errorMessage.value = driveCopy.moveFailed
    }
}

function showCompleted(): void {
    clearTray()
    void loadFiles()
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
        if (result) showCompleted()
    }
}

async function onHeaderFiles(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement
    const list = input.files ? Array.from(input.files) : []
    input.value = ''
    await onDropped(list)
}

function openHeaderFile(): void {
    headerFile.value?.click()
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
                <input
                    v-if="validObjectOrigin && currentId"
                    id="drive-upload"
                    ref="headerFile"
                    hidden
                    multiple
                    tabindex="-1"
                    aria-hidden="true"
                    type="file"
                    @change="onHeaderFiles"
                />
                <el-button
                    v-if="validObjectOrigin && currentId"
                    text
                    @click="openHeaderFile"
                    >{{ driveCopy.upload }}</el-button
                >
                <el-dropdown v-if="canManage && currentId" trigger="click">
                    <el-button text>···</el-button>
                    <template #dropdown>
                        <el-dropdown-menu>
                            <el-dropdown-item
                                @click="folderDrawerOpen = true"
                                >{{ driveCopy.newSubfolder }}</el-dropdown-item
                            >
                        </el-dropdown-menu>
                    </template>
                </el-dropdown>
            </PageHeader>
            <InlineError :message="errorMessage" />
            <nav class="crumbs" :aria-label="driveCopy.crumbs">
                <template
                    v-for="(folder, index) in breadcrumbs"
                    :key="folder.id"
                >
                    <span v-if="index"> / </span>
                    <span v-if="index === breadcrumbs.length - 1">{{
                        folder.name
                    }}</span>
                    <a v-else href="#" @click.prevent="currentId = folder.id">{{
                        folder.name
                    }}</a>
                </template>
            </nav>
            <div v-loading="loading" class="drive-grid">
                <aside>
                    <button
                        v-for="folder in roots"
                        :key="folder.id"
                        type="button"
                        class="folder-link"
                        :class="{ active: folder.id === currentId }"
                        @click="currentId = folder.id"
                    >
                        {{ folder.name }}
                    </button>
                    <button
                        v-for="folder in children"
                        :key="folder.id"
                        type="button"
                        class="folder-link"
                        @click="currentId = folder.id"
                    >
                        {{ folder.name }}
                    </button>
                </aside>
                <div>
                    <el-table
                        :data="files"
                        class="plain-table"
                        :empty-text="driveCopy.empty"
                    >
                        <el-table-column
                            :label="driveCopy.name"
                            min-width="180"
                        >
                            <template #default="{ row }">
                                <form
                                    v-if="renamingId === row.id"
                                    class="rename"
                                    @submit.prevent="renameFile(row)"
                                >
                                    <el-input v-model="renameValue" />
                                    <el-button native-type="submit">{{
                                        driveCopy.confirm
                                    }}</el-button>
                                </form>
                                <span
                                    v-else
                                    :class="{
                                        danger: fileTone(row) === 'danger',
                                    }"
                                >
                                    <Dot
                                        v-if="fileTone(row) === 'muted'"
                                        tone="muted"
                                    />
                                    {{ row.filename }}
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
                                </span>
                            </template>
                        </el-table-column>
                        <el-table-column :label="driveCopy.size" width="100">
                            <template #default="{ row }">{{
                                bytesLabel(row.size_bytes)
                            }}</template>
                        </el-table-column>
                        <el-table-column :label="driveCopy.date" width="120">
                            <template #default="{ row }">
                                <DateText
                                    :value="row.created_at"
                                    format="short"
                                />
                            </template>
                        </el-table-column>
                        <el-table-column
                            :label="driveCopy.uploader"
                            min-width="100"
                        >
                            <template #default="{ row }">{{
                                row.uploader_name || ''
                            }}</template>
                        </el-table-column>
                        <el-table-column width="72">
                            <template #default="{ row }">
                                <el-dropdown trigger="click">
                                    <el-button text>···</el-button>
                                    <template #dropdown>
                                        <el-dropdown-menu>
                                            <el-dropdown-item
                                                @click="downloadFile(row)"
                                                >{{
                                                    driveCopy.download
                                                }}</el-dropdown-item
                                            >
                                            <el-dropdown-item
                                                v-if="canManage"
                                                @click="beginRename(row)"
                                                >{{
                                                    driveCopy.rename
                                                }}</el-dropdown-item
                                            >
                                            <el-dropdown-item
                                                v-if="canManage"
                                                @click="beginMove(row)"
                                                >{{
                                                    driveCopy.move
                                                }}</el-dropdown-item
                                            >
                                            <el-dropdown-item
                                                v-if="canManage"
                                                @click="requestRemove(row)"
                                                >{{
                                                    driveCopy.remove
                                                }}</el-dropdown-item
                                            >
                                        </el-dropdown-menu>
                                    </template>
                                </el-dropdown>
                            </template>
                        </el-table-column>
                    </el-table>
                </div>
            </div>
            <UploadTray :items="tray" />
            <el-dialog
                v-model="removeOpen"
                :title="driveCopy.remove"
                width="360px"
                :close-on-click-modal="false"
            >
                <p>{{ driveCopy.removeConfirm }}</p>
                <template #footer>
                    <el-button @click="pendingRemove = undefined">{{
                        driveCopy.close
                    }}</el-button>
                    <el-button type="primary" @click="confirmRemove">{{
                        driveCopy.confirm
                    }}</el-button>
                </template>
            </el-dialog>
            <el-drawer
                v-model="folderDrawerOpen"
                :title="driveCopy.newSubfolder"
                size="400px"
            >
                <form class="drawer-form" @submit.prevent="createFolder">
                    <label for="new-folder">{{ driveCopy.name }}</label>
                    <el-input id="new-folder" v-model="newFolderName" />
                    <el-button native-type="submit">{{
                        driveCopy.create
                    }}</el-button>
                </form>
            </el-drawer>
            <el-drawer
                :model-value="Boolean(movingFile)"
                :title="driveCopy.move"
                size="400px"
                @close="movingFile = undefined"
            >
                <form
                    v-if="movingFile"
                    class="drawer-form"
                    @submit.prevent="moveFile(movingFile)"
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
            </el-drawer>
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
    justify-items: start;
}
.folder-link {
    padding: 4px 0;
    border: 0;
    background: transparent;
    color: var(--sb-ink);
    text-align: left;
    cursor: pointer;
}
.active {
    font-weight: 600;
    color: var(--sb-accent);
}
.crumbs {
    margin-bottom: 16px;
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
.crumbs a {
    color: inherit;
    text-decoration: none;
}
.hint,
.state {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
.danger {
    color: var(--sb-danger);
}
.rename,
.drawer-form {
    display: grid;
    gap: 8px;
}
@media (max-width: 760px) {
    .drive-grid {
        grid-template-columns: 1fr;
    }
}
</style>
