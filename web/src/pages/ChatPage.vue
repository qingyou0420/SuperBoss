<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
    agentApi,
    agentErrorMessage,
    type AgentCard,
    type AgentConversation,
    type AgentMessage,
} from '../api/agent'
import { filesApi, type FileUploadCompleted } from '../api/files'
import MultipartUploader from '../components/files/MultipartUploader.vue'

withDefaults(
    defineProps<{
        allowedObjectOrigin?: string
    }>(),
    { allowedObjectOrigin: '' },
)

const KIND_LABEL: Record<string, string> = {
    finance_entry: '财务入账',
    finance_adjust: '财务调整',
    project_create: '新建项目',
    project_update: '更新项目',
    milestone_change: '里程碑',
    file_move: '移动文件',
    memory: '记忆',
    knowledge_ingest: '知识入库',
}

const FIELD_LABEL: Record<string, string> = {
    kind: '类型',
    scope: '范围',
    project_id: '项目',
    amount_cents: '金额（分）',
    occurred_on: '日期',
    category: '类别',
    memo: '备注',
    visibility: '可见范围',
    name: '名称',
    title: '标题',
    description: '说明',
    filename: '文件名',
    content: '内容',
    entry_id: '账目',
    field: '字段',
    new_value: '新值',
    reason: '原因',
    stage: '阶段',
    progress_percent: '进度',
    starts_on: '开始',
    due_on: '截止',
    file_id: '文件',
    target_folder_id: '目标文件夹',
    new_name: '新文件名',
    importance: '重要度',
    pinned: '置顶',
}

const conversations = ref<AgentConversation[]>([])
const currentId = ref('')
const messages = ref<AgentMessage[]>([])
const cards = ref<AgentCard[]>([])
const draft = ref('')
const search = ref('')
const sending = ref(false)
const errorMessage = ref('')
const offline = ref(false)
const streamingText = ref('')
const folderId = ref('')
const pendingFileId = ref('')
const pendingFileName = ref('')
const cardNotes = reactive<Record<string, string>>({})
const cardDrafts = reactive<Record<string, Record<string, string>>>({})

const currentCards = computed(() => {
    const byMessage = new Map<string, AgentCard[]>()
    for (const card of cards.value) {
        const key = card.message_id || ''
        const list = byMessage.get(key) ?? []
        list.push(card)
        byMessage.set(key, list)
    }
    return byMessage
})

function fieldValue(value: unknown): string {
    if (value === null || value === undefined) return ''
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
}

function draftFor(card: AgentCard): Record<string, string> {
    if (!cardDrafts[card.id]) {
        cardDrafts[card.id] = Object.fromEntries(
            Object.entries(card.payload).map(([key, value]) => [
                key,
                fieldValue(value),
            ]),
        )
    }
    return cardDrafts[card.id]
}

function parsedDraft(draft: Record<string, string>): Record<string, unknown> {
    const payload: Record<string, unknown> = {}
    for (const [key, raw] of Object.entries(draft)) {
        if (raw === '') {
            payload[key] = null
            continue
        }
        if (
            key === 'amount_cents' ||
            key === 'importance' ||
            key === 'progress_percent'
        ) {
            payload[key] = Number(raw)
            continue
        }
        if (raw === 'true' || raw === 'false') {
            payload[key] = raw === 'true'
            continue
        }
        if (raw.startsWith('{') || raw.startsWith('[')) {
            try {
                payload[key] = JSON.parse(raw) as unknown
                continue
            } catch {
                payload[key] = raw
                continue
            }
        }
        payload[key] = raw
    }
    return payload
}

async function loadConversations(): Promise<void> {
    conversations.value = await agentApi.listConversations(
        search.value.trim() || undefined,
    )
    if (!currentId.value && conversations.value[0]) {
        currentId.value = conversations.value[0].id
    }
}

async function loadThread(): Promise<void> {
    if (!currentId.value) {
        messages.value = []
        cards.value = []
        return
    }
    const [nextMessages, nextCards] = await Promise.all([
        agentApi.listMessages(currentId.value),
        agentApi.listCards(currentId.value),
    ])
    messages.value = nextMessages
    cards.value = nextCards
}

async function createConversation(): Promise<void> {
    const created = await agentApi.createConversation()
    conversations.value.unshift(created)
    currentId.value = created.id
    await loadThread()
}

async function send(): Promise<void> {
    const content = draft.value.trim()
    const fileId = pendingFileId.value || undefined
    if ((!content && !fileId) || sending.value) return
    sending.value = true
    errorMessage.value = ''
    streamingText.value = ''
    try {
        if (!currentId.value) await createConversation()
        draft.value = ''
        pendingFileId.value = ''
        pendingFileName.value = ''
        try {
            const turn = await agentApi.stream(
                currentId.value,
                content,
                (piece) => {
                    streamingText.value += piece
                },
                fileId,
            )
            offline.value = turn.offline
        } catch {
            const turn = await agentApi.send(currentId.value, content, fileId)
            offline.value = turn.offline
        }
        streamingText.value = ''
        await loadThread()
        await loadConversations()
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    } finally {
        sending.value = false
    }
}

async function confirm(card: AgentCard): Promise<void> {
    try {
        const updated = await agentApi.confirm(card.id)
        cards.value = cards.value.map((item) =>
            item.id === updated.id ? updated : item,
        )
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

async function reject(card: AgentCard): Promise<void> {
    try {
        const updated = await agentApi.reject(card.id)
        cards.value = cards.value.map((item) =>
            item.id === updated.id ? updated : item,
        )
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

async function saveCard(card: AgentCard): Promise<void> {
    try {
        const updated = await agentApi.patch(
            card.id,
            parsedDraft(draftFor(card)),
            cardNotes[card.id] || '',
        )
        cards.value = cards.value.map((item) =>
            item.id === updated.id ? updated : item,
        )
        delete cardDrafts[updated.id]
        cardNotes[card.id] = ''
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

function onUploaded(result: FileUploadCompleted): void {
    pendingFileId.value = result.file_id
    pendingFileName.value = '已上传，发送时交给霜月'
}

async function selectConversation(id: string): Promise<void> {
    currentId.value = id
    await loadThread()
}

onMounted(async () => {
    try {
        await loadConversations()
        await loadThread()
        const folders = await filesApi.listFolders()
        folderId.value =
            folders.find((folder) => folder.name === '项目')?.id ??
            folders[0]?.id ??
            ''
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
})
</script>

<template>
    <section class="chat-page" aria-labelledby="chat-title">
        <aside>
            <div class="side-head">
                <h1 id="chat-title">霜月</h1>
                <el-button size="small" @click="createConversation"
                    >新对话</el-button
                >
            </div>
            <form class="search" @submit.prevent="loadConversations">
                <label for="chat-search">搜索会话</label>
                <el-input id="chat-search" v-model="search" />
                <el-button native-type="submit">查找</el-button>
            </form>
            <button
                v-for="item in conversations"
                :key="item.id"
                type="button"
                class="conversation"
                :class="{ active: item.id === currentId }"
                @click="selectConversation(item.id)"
            >
                {{ item.title }}
            </button>
        </aside>
        <div class="thread">
            <el-alert v-if="offline" type="warning" :closable="false" show-icon>
                霜月暂时离线，你仍可以直接使用各页面。
            </el-alert>
            <el-alert
                v-if="errorMessage"
                type="error"
                :closable="false"
                show-icon
            >
                {{ errorMessage }}
            </el-alert>
            <ol class="messages">
                <li v-for="message in messages" :key="message.id">
                    <strong>{{
                        message.role === 'user' ? '你' : '霜月'
                    }}</strong>
                    <p class="message-body">{{ message.content }}</p>
                    <el-card
                        v-for="card in currentCards.get(message.id) ?? []"
                        :key="card.id"
                        shadow="never"
                    >
                        <h2>{{ KIND_LABEL[card.kind] || card.kind }}</h2>
                        <p v-if="card.status === 'COMMITTED'">
                            已入库
                            <span v-if="card.committed_object_type">
                                · {{ card.committed_object_type }}
                            </span>
                        </p>
                        <form
                            v-else-if="card.status === 'PROPOSED'"
                            class="card-edit"
                            @submit.prevent="saveCard(card)"
                        >
                            <label
                                v-for="(value, key) in draftFor(card)"
                                :key="String(key)"
                            >
                                {{ FIELD_LABEL[String(key)] || key }}
                                <el-input v-model="draftFor(card)[key]" />
                            </label>
                            <label>
                                说明（可选）
                                <el-input
                                    v-model="cardNotes[card.id]"
                                    placeholder="一句话说明这次修改"
                                />
                            </label>
                            <div class="card-actions">
                                <el-button native-type="submit"
                                    >保存修改</el-button
                                >
                                <el-button
                                    type="primary"
                                    native-type="button"
                                    @click="confirm(card)"
                                    >确认入库</el-button
                                >
                                <el-button
                                    native-type="button"
                                    @click="reject(card)"
                                    >放弃</el-button
                                >
                            </div>
                        </form>
                        <dl v-else>
                            <div
                                v-for="(value, key) in card.payload"
                                :key="String(key)"
                            >
                                <dt>
                                    {{ FIELD_LABEL[String(key)] || key }}
                                </dt>
                                <dd>{{ value }}</dd>
                            </div>
                        </dl>
                    </el-card>
                </li>
                <li v-if="streamingText">
                    <strong>霜月</strong>
                    <p class="message-body">{{ streamingText }}</p>
                </li>
            </ol>
            <form class="composer" @submit.prevent="send">
                <label for="chat-draft">给霜月</label>
                <el-input
                    id="chat-draft"
                    v-model="draft"
                    type="textarea"
                    :autosize="{ minRows: 2, maxRows: 6 }"
                />
                <p v-if="pendingFileName" class="pending-file">
                    附件：{{ pendingFileName }}
                </p>
                <MultipartUploader
                    v-if="allowedObjectOrigin && folderId"
                    :allowed-object-origin="allowedObjectOrigin"
                    :folder-id="folderId"
                    @completed="onUploaded"
                />
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="sending"
                    :disabled="sending"
                    >发送</el-button
                >
            </form>
        </div>
    </section>
</template>

<style scoped>
.chat-page {
    display: grid;
    grid-template-columns: minmax(180px, 240px) 1fr;
    gap: 1rem;
    min-height: 70vh;
}
.side-head,
.card-actions,
.composer,
.search {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
}
.search,
.card-edit,
.composer {
    display: grid;
}
.conversation {
    display: block;
    width: 100%;
    text-align: left;
    margin-bottom: 6px;
    padding: 0.6rem;
    border: 1px solid #dcdfe6;
    border-radius: 8px;
    background: #fff;
    cursor: pointer;
}
.conversation.active {
    border-color: #409eff;
}
.messages {
    list-style: none;
    padding: 0;
    display: grid;
    gap: 1rem;
}
.message-body {
    white-space: pre-wrap;
}
.pending-file {
    color: #909399;
}
@media (max-width: 760px) {
    .chat-page {
        grid-template-columns: 1fr;
    }
}
</style>
