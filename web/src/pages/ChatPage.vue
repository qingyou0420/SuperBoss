<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
    agentApi,
    agentErrorMessage,
    type AgentCard,
    type AgentConversation,
    type AgentMessage,
} from '../api/agent'
import { filesApi, type FileUploadCompleted } from '../api/files'
import { projectsApi } from '../api/projects'
import ProposalCard from '../components/chat/ProposalCard.vue'
import MultipartUploader from '../components/files/MultipartUploader.vue'
import InlineError from '../components/ui/InlineError.vue'
import { FOLDER_NAME } from '../copy/glossary'
import { chatCopy } from '../copy/pages/chat'

withDefaults(
    defineProps<{
        allowedObjectOrigin?: string
    }>(),
    { allowedObjectOrigin: '' },
)

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
const pendingPreview = ref('')
const projectNames = ref<Record<string, string>>({})
const folderNames = ref<Record<string, string>>({})

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

const grouped = computed(() => {
    const now = Date.now()
    const startOfToday = new Date()
    startOfToday.setHours(0, 0, 0, 0)
    const weekAgo = now - 7 * 24 * 60 * 60 * 1000
    const groups: {
        label: string
        items: AgentConversation[]
    }[] = [
        { label: chatCopy.today, items: [] },
        { label: chatCopy.thisWeek, items: [] },
        { label: chatCopy.earlier, items: [] },
    ]
    for (const item of conversations.value ?? []) {
        const stamp = new Date(item.last_message_at).getTime()
        if (stamp >= startOfToday.getTime()) groups[0].items.push(item)
        else if (stamp >= weekAgo) groups[1].items.push(item)
        else groups[2].items.push(item)
    }
    return groups.filter((group) => group.items.length)
})

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

async function archiveConversation(id: string): Promise<void> {
    await agentApi.archive(id)
    conversations.value = conversations.value.filter((item) => item.id !== id)
    if (currentId.value === id) {
        currentId.value = conversations.value[0]?.id ?? ''
        await loadThread()
    }
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
        pendingPreview.value = ''
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
            const existing = await agentApi.listMessages(currentId.value)
            const lastUser = [...existing]
                .reverse()
                .find((item) => item.role === 'user')
            const alreadyStored =
                lastUser &&
                ((content && lastUser.content.startsWith(content)) ||
                    (!content &&
                        fileId &&
                        lastUser.content.includes(chatCopy.attachment)))
            if (!alreadyStored) {
                const turn = await agentApi.send(
                    currentId.value,
                    content,
                    fileId,
                )
                offline.value = turn.offline
            }
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

async function saveCard(
    card: AgentCard,
    payload: Record<string, unknown>,
    note: string,
): Promise<void> {
    try {
        const updated = await agentApi.patch(card.id, payload, note)
        cards.value = cards.value.map((item) =>
            item.id === updated.id ? updated : item,
        )
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

async function reviseCard(card: AgentCard, instruction: string): Promise<void> {
    try {
        await agentApi.revise(card.id, instruction)
        await loadThread()
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

function onUploaded(result: FileUploadCompleted): void {
    pendingFileId.value = result.file_id
}

async function onFilePicked(file: File): Promise<void> {
    pendingFileName.value = file.name
    pendingPreview.value = ''
    if (file.type.startsWith('text/') && file.size < 200_000) {
        const text = await file.slice(0, 400).text()
        pendingPreview.value = text
    }
}

async function selectConversation(id: string): Promise<void> {
    currentId.value = id
    await loadThread()
}

watch(search, () => {
    void loadConversations()
})

onMounted(async () => {
    try {
        await loadConversations()
        await loadThread()
        const [folders, projects] = await Promise.all([
            filesApi.listFolders(),
            projectsApi.list().catch(() => []),
        ])
        folderId.value =
            folders.find((folder) => folder.name === FOLDER_NAME.OWNER_PRIVATE)
                ?.id ??
            folders.find((folder) => folder.name === FOLDER_NAME.PROJECTS)
                ?.id ??
            folders[0]?.id ??
            ''
        folderNames.value = Object.fromEntries(
            folders.map((item) => [item.id, item.name]),
        )
        projectNames.value = Object.fromEntries(
            projects.map((item) => [item.id, item.name]),
        )
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
})
</script>

<template>
    <section class="chat-page" aria-labelledby="chat-title">
        <aside>
            <div class="side-head">
                <h1 id="chat-title">{{ chatCopy.brand }}</h1>
                <el-button text @click="createConversation">{{
                    chatCopy.newConversation
                }}</el-button>
            </div>
            <form class="search" @submit.prevent="loadConversations">
                <label class="sr-only" for="chat-search">{{
                    chatCopy.searchSessions
                }}</label>
                <el-input
                    id="chat-search"
                    v-model="search"
                    :placeholder="chatCopy.search"
                />
            </form>
            <section v-for="group in grouped" :key="group.label">
                <h2>{{ group.label }}</h2>
                <div
                    v-for="item in group.items"
                    :key="item.id"
                    class="conversation"
                    :class="{ active: item.id === currentId }"
                >
                    <el-button
                        text
                        class="conversation__open"
                        @click="selectConversation(item.id)"
                        >{{ item.title }}</el-button
                    >
                    <el-dropdown
                        trigger="click"
                        @command="archiveConversation(item.id)"
                    >
                        <el-button text native-type="button">···</el-button>
                        <template #dropdown>
                            <el-dropdown-menu>
                                <el-dropdown-item>{{
                                    chatCopy.archive
                                }}</el-dropdown-item>
                            </el-dropdown-menu>
                        </template>
                    </el-dropdown>
                </div>
            </section>
        </aside>
        <div class="thread">
            <p v-if="offline" class="offline">{{ chatCopy.offline }}</p>
            <InlineError :message="errorMessage" />
            <ol class="messages">
                <li v-for="message in messages" :key="message.id">
                    <p v-if="message.role === 'system'" class="receipt">
                        {{ message.content }}
                    </p>
                    <template v-else>
                        <strong>{{
                            message.role === 'user'
                                ? chatCopy.you
                                : chatCopy.assistant
                        }}</strong>
                        <p class="message-body">{{ message.content }}</p>
                        <ProposalCard
                            v-for="card in currentCards.get(message.id) ?? []"
                            :key="card.id"
                            :card="card"
                            :project-names="projectNames"
                            :folder-names="folderNames"
                            @confirm="confirm(card)"
                            @reject="reject(card)"
                            @revise="reviseCard(card, $event)"
                            @patch="
                                (payload, note) => saveCard(card, payload, note)
                            "
                        />
                    </template>
                </li>
                <li v-if="streamingText">
                    <strong>{{ chatCopy.assistant }}</strong>
                    <p class="message-body">{{ streamingText }}</p>
                </li>
            </ol>
            <form class="composer" @submit.prevent="send">
                <p v-if="pendingFileName" class="pending-file">
                    {{ pendingFileName }}
                    <span v-if="pendingPreview" class="preview">{{
                        pendingPreview
                    }}</span>
                </p>
                <div class="composer__row">
                    <MultipartUploader
                        v-if="allowedObjectOrigin && folderId"
                        compact
                        :allowed-object-origin="allowedObjectOrigin"
                        :folder-id="folderId"
                        @completed="onUploaded"
                        @selected="onFilePicked"
                    />
                    <label class="sr-only" for="chat-draft">{{
                        chatCopy.composerLabel
                    }}</label>
                    <el-input
                        id="chat-draft"
                        v-model="draft"
                        type="textarea"
                        :autosize="{ minRows: 2, maxRows: 6 }"
                        :placeholder="chatCopy.placeholder"
                        :disabled="offline"
                        @keydown.enter.exact.prevent="send"
                    />
                    <el-button
                        native-type="submit"
                        :loading="sending"
                        :disabled="sending || offline"
                        :aria-label="chatCopy.send"
                        >↑</el-button
                    >
                </div>
            </form>
        </div>
    </section>
</template>

<style scoped>
.chat-page {
    display: grid;
    grid-template-columns: minmax(180px, 240px) 1fr;
    gap: 32px;
    min-height: 70vh;
}
.side-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
}
.side-head h1 {
    font-size: var(--sb-lg);
    font-weight: 600;
}
aside h2 {
    margin: 16px 0 8px;
    color: var(--sb-ink-3);
    font-size: var(--sb-xs);
    font-weight: 400;
}
.conversation {
    display: flex;
    align-items: center;
    border-bottom: 1px solid var(--sb-line);
}
.conversation.active .conversation__open {
    font-weight: 600;
    color: var(--sb-accent);
}
.conversation__open {
    flex: 1;
    justify-content: flex-start;
}
.messages {
    list-style: none;
    padding: 0;
    display: grid;
    gap: 32px;
}
.messages strong {
    display: block;
    margin-bottom: 6px;
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
    font-weight: 400;
}
.message-body {
    white-space: pre-wrap;
    font-size: var(--sb-md);
    line-height: 1.75;
}
.receipt,
.offline,
.pending-file {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
.receipt {
    text-align: center;
}
.preview {
    display: block;
    margin-top: 6px;
    white-space: pre-wrap;
    max-height: 8em;
    overflow: hidden;
}
.composer {
    margin-top: 32px;
}
.composer__row {
    display: flex;
    gap: 8px;
    align-items: flex-end;
}
.composer__row :deep(.el-textarea) {
    flex: 1;
}
@media (max-width: 760px) {
    .chat-page {
        grid-template-columns: 1fr;
    }
}
</style>
