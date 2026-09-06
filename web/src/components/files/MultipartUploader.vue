<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

import {
    fileErrorMessage,
    filesApi,
    type FileUploadCompleted,
} from '../../api/files'
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
}>()

const pending = ref(false)
const status = ref('')
const errorMessage = ref('')
const uploadedBytes = ref(0)
const totalBytes = ref(0)
let activeUploader: ReturnType<typeof createMultipartUploader> | undefined

function userErrorText(error: UploadUserError): string {
    if (error.code === 'TOO_LARGE') return '文件超过 100MB 上限。'
    if (error.code === 'EMPTY') return '请选择非空文件。'
    return '不支持的文件类型。'
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
                status.value = `上传中 ${percent}%`
            },
            uploadPart: transport.put,
        })
        const result = await activeUploader.upload({
            file,
            folder_id: props.folderId,
        })
        status.value = '扫描中'
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
            :drag="!compact"
            @change="onChange"
        >
            <span v-if="compact" class="clip" aria-label="上传">📎</span>
            <span v-else>{{ pending ? '上传中…' : '上传' }}</span>
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
