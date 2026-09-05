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

const props = defineProps<{
    allowedObjectOrigin: string
    folderId: string
}>()

const emit = defineEmits<{
    completed: [result: FileUploadCompleted]
}>()

const file = ref<globalThis.File>()
const pending = ref(false)
const status = ref('')
const errorMessage = ref('')
const uploadedBytes = ref(0)
const totalBytes = ref(0)
let activeUploader: ReturnType<typeof createMultipartUploader> | undefined

function selectFile(event: globalThis.Event): void {
    const input = event.target as globalThis.HTMLInputElement
    file.value = input.files?.[0]
    errorMessage.value = ''
    status.value = ''
    uploadedBytes.value = 0
    totalBytes.value = 0
}

function userErrorText(error: UploadUserError): string {
    if (error.code === 'TOO_LARGE') return '文件超过 100MB 上限。'
    if (error.code === 'EMPTY') return '请选择非空文件。'
    return '不支持的文件类型。'
}

async function submit(): Promise<void> {
    if (!file.value || pending.value) return
    pending.value = true
    status.value = ''
    errorMessage.value = ''
    uploadedBytes.value = 0
    totalBytes.value = file.value.size
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
            file: file.value,
            folder_id: props.folderId,
        })
        status.value = '\u626b\u63cf\u4e2d'
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

function cancel(): void {
    activeUploader?.cancel()
}

onBeforeUnmount(cancel)
</script>

<template>
    <section class="multipart-uploader" aria-labelledby="upload-title">
        <h2 id="upload-title">上传文件</h2>
        <label>
            文件
            <input type="file" :disabled="pending" @change="selectFile" />
        </label>
        <div class="multipart-uploader__actions">
            <button type="button" :disabled="pending || !file" @click="submit">
                {{ pending ? '上传中…' : '开始上传' }}
            </button>
            <button v-if="pending" type="button" @click="cancel">取消</button>
        </div>
        <progress
            v-if="pending && totalBytes > 0"
            :max="totalBytes"
            :value="uploadedBytes"
        />
        <p v-if="status" role="status">{{ status }}</p>
        <p v-if="errorMessage" role="alert">{{ errorMessage }}</p>
    </section>
</template>

<style scoped>
.multipart-uploader {
    display: grid;
    gap: 1rem;
}

.multipart-uploader label {
    display: grid;
    gap: 0.4rem;
}

.multipart-uploader__actions {
    display: flex;
    gap: 0.75rem;
}

progress {
    width: 100%;
}
</style>
