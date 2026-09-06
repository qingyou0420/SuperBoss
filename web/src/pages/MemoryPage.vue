<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { agentApi, agentErrorMessage, type AgentMemory } from '../api/agent'
import { dateTimeShort } from '../api/parse'
import Dot from '../components/ui/Dot.vue'
import InlineError from '../components/ui/InlineError.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import { MEMORY_KIND_LABEL } from '../copy/glossary'
import { memoryCopy } from '../copy/pages/memory'

const KIND_ORDER = [
    'FACT',
    'PREFERENCE',
    'DECISION',
    'PROJECT_NOTE',
    'DAILY_DIGEST',
] as const

const memories = ref<AgentMemory[]>([])
const query = ref('')
const errorMessage = ref('')
const editingId = ref('')
const editValue = ref('')

const filtered = computed(() => {
    const needle = query.value.trim()
    if (!needle) return memories.value
    return memories.value.filter((item) => item.content.includes(needle))
})

const groups = computed(() =>
    KIND_ORDER.map((kind) => ({
        kind,
        label: MEMORY_KIND_LABEL[kind],
        items: filtered.value.filter((item) => item.kind === kind),
    })).filter((group) => group.items.length),
)

async function load(): Promise<void> {
    memories.value = await agentApi.listMemories()
}

function beginEdit(item: AgentMemory): void {
    editingId.value = item.id
    editValue.value = item.content
}

async function save(item: AgentMemory): Promise<void> {
    try {
        const updated = await agentApi.patchMemory(item.id, {
            content: editValue.value,
        })
        memories.value = memories.value.map((entry) =>
            entry.id === updated.id ? updated : entry,
        )
        editingId.value = ''
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

async function togglePin(item: AgentMemory): Promise<void> {
    try {
        const updated = await agentApi.patchMemory(item.id, {
            pinned: !item.pinned,
        })
        memories.value = memories.value.map((entry) =>
            entry.id === updated.id ? updated : entry,
        )
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

async function archive(item: AgentMemory): Promise<void> {
    try {
        await agentApi.patchMemory(item.id, { status: 'ARCHIVED' })
        memories.value = memories.value.filter((entry) => entry.id !== item.id)
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
}

onMounted(async () => {
    try {
        await load()
    } catch (error) {
        errorMessage.value = agentErrorMessage(error)
    }
})
</script>

<template>
    <section class="memory-page" aria-labelledby="memory-title">
        <PageHeader :title="memoryCopy.title" heading-id="memory-title" />
        <label class="sr-only" for="memory-q">{{ memoryCopy.search }}</label>
        <el-input
            id="memory-q"
            v-model="query"
            :placeholder="memoryCopy.search"
        />
        <InlineError :message="errorMessage" />
        <section v-for="group in groups" :key="group.kind">
            <h2>{{ group.label }}</h2>
            <ul>
                <li v-for="item in group.items" :key="item.id">
                    <div class="row">
                        <Dot v-if="item.pinned" tone="ok" />
                        <p>{{ item.content }}</p>
                        <span>{{ dateTimeShort(item.created_at) }}</span>
                        <el-dropdown trigger="click">
                            <el-button text>···</el-button>
                            <template #dropdown>
                                <el-dropdown-menu>
                                    <el-dropdown-item
                                        @click="togglePin(item)"
                                        >{{
                                            item.pinned
                                                ? memoryCopy.unpin
                                                : memoryCopy.pin
                                        }}</el-dropdown-item
                                    >
                                    <el-dropdown-item
                                        @click="beginEdit(item)"
                                        >{{ memoryCopy.edit }}</el-dropdown-item
                                    >
                                    <el-dropdown-item @click="archive(item)">{{
                                        memoryCopy.archive
                                    }}</el-dropdown-item>
                                </el-dropdown-menu>
                            </template>
                        </el-dropdown>
                    </div>
                    <form
                        v-if="editingId === item.id"
                        @submit.prevent="save(item)"
                    >
                        <el-input v-model="editValue" />
                        <el-button native-type="submit">{{
                            memoryCopy.save
                        }}</el-button>
                    </form>
                </li>
            </ul>
        </section>
    </section>
</template>

<style scoped>
h2 {
    margin: 32px 0 8px;
    font-size: var(--sb-sm);
    color: var(--sb-ink-2);
    font-weight: 400;
}
ul {
    list-style: none;
    margin: 0;
    padding: 0;
}
li {
    border-bottom: 1px solid var(--sb-line);
    padding: 12px 0;
}
.row {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    gap: 12px;
    align-items: start;
}
.row span {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
</style>
