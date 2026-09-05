<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { agentApi, agentErrorMessage, type AgentMemory } from '../api/agent'

const memories = ref<AgentMemory[]>([])
const groups = computed(() => {
    const kinds = [
        'PREFERENCE',
        'FACT',
        'DECISION',
        'PROJECT_NOTE',
        'DAILY_DIGEST',
    ]
    return kinds.map((kind) => ({
        kind,
        items: memories.value.filter((item) => item.kind === kind),
    }))
})
const errorMessage = ref('')
const editingId = ref('')
const editValue = ref('')

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
    const updated = await agentApi.patchMemory(item.id, {
        pinned: !item.pinned,
    })
    memories.value = memories.value.map((entry) =>
        entry.id === updated.id ? updated : entry,
    )
}

async function archive(item: AgentMemory): Promise<void> {
    await agentApi.patchMemory(item.id, { status: 'ARCHIVED' })
    memories.value = memories.value.filter((entry) => entry.id !== item.id)
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
        <h1 id="memory-title">记忆</h1>
        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>
        <section v-for="group in groups" :key="group.kind">
            <h2>{{ group.kind }}</h2>
            <p v-if="!group.items.length">暂无</p>
            <ul>
                <li v-for="item in group.items" :key="item.id">
                    <strong>{{ item.kind }}</strong>
                    <span v-if="item.pinned">置顶</span>
                    <p>{{ item.content }}</p>
                    <el-button text @click="togglePin(item)">{{
                        item.pinned ? '取消置顶' : '置顶'
                    }}</el-button>
                    <el-button text @click="beginEdit(item)">编辑</el-button>
                    <el-button text @click="archive(item)">归档</el-button>
                    <form
                        v-if="editingId === item.id"
                        @submit.prevent="save(item)"
                    >
                        <el-input v-model="editValue" />
                        <el-button native-type="submit">保存</el-button>
                    </form>
                </li>
            </ul>
        </section>
        <p v-if="!memories.length">还没有长期记忆。</p>
    </section>
</template>

<style scoped>
ul {
    list-style: none;
    padding: 0;
    display: grid;
    gap: 1rem;
}
li {
    border: 1px solid #ebeef5;
    border-radius: 8px;
    padding: 1rem;
    background: #fff;
}
</style>
