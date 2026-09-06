<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

import {
    fileErrorMessage,
    filesApi,
    type FileUploadCompleted,
} from '../../api/files'
import { driveCopy, uploadingPercent } from '../../copy/pages/drive'
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
        textButton?: boolean
    }>(),
    { compact: false, textButton: false },
)

const emit = defineEmits<{
    completed: [result: FileUploadCompleted]
    selected: [file: File]
}>()

const pending = ref(false)
const status = ref('')
const errorMessage = ref('')
const uploadedBytes = ref(0)
const totalBytes = ref(0)
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
    status.value = ''
    errorMessage.value = ''
    uploadedBytes.value = 0
    totalBytes.value = file.size
    try {
        const transport = createPresignedUploadTransport({
            allowedObjectOrigin: props.allowedObjectOrigin,
        })
        activeUploader = createMultipartUploader({
            filesApi,
            onProgress(done, total) {
                uploadedBytes.value = done
                totalBytes.value = total
                const percent =
                    total === 0 ? 0 : Math.round((done / total) * 100)
                status.value = uploadingPercent(percent)
            },
            uploadPart: transport.put,
        })
        const result = await activeUploader.upload({
            file,
            folder_id: props.folderId,
        })
        status.value = driveCopy.scanning
        emit('completed', result)
    } catch (error) {
        if (error instanceof UploadUserError) {
            errorMessage.value = userErrorText(error)
        } else {
            errorMessage.value = fileErrorMessage(error)
        }
        status.value = ''
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
            :drag="!compact && !textButton"
            @change="onChange"
        >
            <el-button v-if="textButton" text :disabled="pending">{{
                driveCopy.upload
            }}</el-button>
            <svg
                v-else-if="compact"
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
        <p v-if="status" role="status">{{ status }}</p>
        <p v-if="errorMessage" role="alert">{{ errorMessage }}</p>
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
