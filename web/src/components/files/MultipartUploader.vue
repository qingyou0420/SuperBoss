<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

import {
    fileErrorMessage,
    filesApi,
    type FileUploadCompleted,
} from '../../api/files'
import { driveCopy } from '../../copy/pages/drive'
import {
    createMultipartUploader,
    createPresignedUploadTransport,
    UploadUserError,
} from '../../uploads/multipart'

const props = withDefaults(
    defineProps<{
        allowedObjectOrigin: string
        folderId: string
        compact?: boolean
    }>(),
    { compact: false },
)

const emit = defineEmits<{
    completed: [result: FileUploadCompleted]
    selected: [file: File]
    failed: [message: string]
}>()

const pending = ref(false)
let activeUploader: ReturnType<typeof createMultipartUploader> | undefined

function userErrorText(error: UploadUserError): string {
    if (error.code === 'TOO_LARGE') return driveCopy.tooLarge
    if (error.code === 'EMPTY') return driveCopy.emptyFile
    return driveCopy.unsupportedType
}

async function uploadFile(file: File): Promise<void> {
    if (pending.value) return
    emit('selected', file)
    pending.value = true
    try {
        const transport = createPresignedUploadTransport({
            allowedObjectOrigin: props.allowedObjectOrigin,
        })
        activeUploader = createMultipartUploader({
            filesApi,
            uploadPart: transport.put,
        })
        const result = await activeUploader.upload({
            file,
            folder_id: props.folderId,
        })
        emit('completed', result)
    } catch (error) {
        emit(
            'failed',
            error instanceof UploadUserError
                ? userErrorText(error)
                : fileErrorMessage(error),
        )
    } finally {
        pending.value = false
        activeUploader = undefined
    }
}

function onChange(item: { raw?: File }): void {
    if (item.raw) void uploadFile(item.raw)
}

function cancel(): void {
    activeUploader?.cancel()
}

onBeforeUnmount(cancel)
</script>

<template>
    <div class="uploader" :class="{ 'uploader--compact': compact }">
        <el-upload
            :auto-upload="false"
            :show-file-list="false"
            :disabled="pending"
            :drag="!compact"
            @change="onChange"
        >
            <svg
                v-if="compact"
                class="clip"
                width="16"
                height="16"
                viewBox="0 0 16 16"
                :aria-label="driveCopy.upload"
            >
                <path
                    fill="currentColor"
                    d="M4.5 6.5v5.2a3.3 3.3 0 0 0 6.6 0V4.2a2.1 2.1 0 0 0-4.2 0v6.8a.9.9 0 1 0 1.8 0V5.1h1.2v5.9a2.1 2.1 0 1 1-4.2 0V4.2a3.3 3.3 0 0 1 6.6 0v7.5a4.5 4.5 0 0 1-9 0V6.5z"
                />
            </svg>
            <span v-else>{{
                pending ? driveCopy.uploadingEllipsis : driveCopy.upload
            }}</span>
        </el-upload>
    </div>
</template>

<style scoped>
.uploader {
    display: grid;
    gap: 8px;
}
.clip {
    cursor: pointer;
    font-size: 16px;
}
.uploader--compact :deep(.el-upload) {
    display: inline-flex;
}
</style>
