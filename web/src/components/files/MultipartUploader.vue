<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

import { filesApi, type FileUploadCompleted } from '../../api/files'
import {
    createIndexedDbProgressStore,
    createMultipartUploader,
    createPresignedUploadTransport,
    createWorkerHasher,
    UploadUserError,
} from '../../uploads/multipart'

const props = defineProps<{
    allowedObjectOrigin: string
    projectId: string
}>()

const emit = defineEmits<{
    completed: [result: FileUploadCompleted]
}>()

const category = ref('\u6587\u6863')
const file = ref<globalThis.File>()
const fileDate = ref(new Date().toISOString().slice(0, 10))
const pending = ref(false)
const status = ref('')
const errorMessage = ref('')
let activeUploader: ReturnType<typeof createMultipartUploader> | undefined

function selectFile(event: globalThis.Event): void {
    const input = event.target as globalThis.HTMLInputElement
    file.value = input.files?.[0]
    errorMessage.value = ''
    status.value = ''
}

async function submit(): Promise<void> {
    if (!file.value || pending.value) return
    pending.value = true
    status.value = ''
    errorMessage.value = ''
    try {
        const transport = createPresignedUploadTransport({
            allowedObjectOrigin: props.allowedObjectOrigin,
        })
        activeUploader = createMultipartUploader({
            filesApi,
            hasher: createWorkerHasher(),
            progressStore: createIndexedDbProgressStore(),
            uploadPart: transport.put,
        })
        const result = await activeUploader.upload({
            category: category.value,
            file: file.value,
            file_date: fileDate.value,
            project_id: props.projectId,
        })
        status.value = '\u626b\u63cf\u4e2d'
        emit('completed', result)
    } catch (error) {
        if (error instanceof UploadUserError && error.code === 'TOO_LARGE') {
            errorMessage.value = '文件超过 100MB 上限。'
        } else if (error instanceof UploadUserError && error.code === 'EMPTY') {
            errorMessage.value = '请选择非空文件。'
        } else if (error instanceof UploadUserError && error.code === 'BAD_TYPE') {
            errorMessage.value = '不支持的文件类型。'
        } else {
            errorMessage.value =
                '\u6587\u4ef6\u4e0a\u4f20\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002'
        }
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
        <label>
            分类
            <input v-model="category" :disabled="pending" maxlength="255" />
        </label>
        <label>
            文件日期
            <input v-model="fileDate" :disabled="pending" type="date" />
        </label>
        <div class="multipart-uploader__actions">
            <button type="button" :disabled="pending || !file" @click="submit">
                {{ pending ? '上传中…' : '开始上传' }}
            </button>
            <button v-if="pending" type="button" @click="cancel">取消</button>
        </div>
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
</style>
