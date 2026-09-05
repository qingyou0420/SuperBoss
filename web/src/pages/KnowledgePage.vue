<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
    knowledgeApi,
    knowledgeErrorMessage,
    type KnowledgeDoc,
} from '../api/knowledge'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const canEdit = computed(() => auth.user?.role === 'OWNER')
const docs = ref<KnowledgeDoc[]>([])
const query = ref('')
const title = ref('')
const body = ref('')
const errorMessage = ref('')

async function load(): Promise<void> {
    try {
        docs.value = await knowledgeApi.list(query.value.trim() || undefined)
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

async function createDoc(): Promise<void> {
    if (!title.value.trim()) return
    try {
        await knowledgeApi.create(title.value.trim(), body.value)
        title.value = ''
        body.value = ''
        await load()
    } catch (error) {
        errorMessage.value = knowledgeErrorMessage(error)
    }
}

async function publish(doc: KnowledgeDoc): Promise<void> {
    await knowledgeApi.publish(doc.id)
    await load()
}

onMounted(load)
</script>

<template>
    <section class="knowledge-page" aria-labelledby="knowledge-title">
        <h1 id="knowledge-title">知识库</h1>
        <form class="search" @submit.prevent="load">
            <label for="knowledge-q">搜索</label>
            <el-input id="knowledge-q" v-model="query" />
            <el-button native-type="submit">查找</el-button>
        </form>
        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>
        <el-card v-if="canEdit" shadow="never">
            <form @submit.prevent="createDoc">
                <h2>新建文档</h2>
                <label for="doc-title">标题</label>
                <el-input id="doc-title" v-model="title" />
                <label for="doc-body">正文</label>
                <el-input id="doc-body" v-model="body" type="textarea" />
                <el-button type="primary" native-type="submit"
                    >保存草稿</el-button
                >
            </form>
        </el-card>
        <el-card v-for="doc in docs" :key="doc.id" shadow="never">
            <h2>{{ doc.title }}</h2>
            <p>{{ doc.status === 'PUBLISHED' ? '已发布' : '草稿' }}</p>
            <p>{{ doc.body_md }}</p>
            <ul>
                <li v-for="point in doc.points" :key="point.id">
                    <strong>{{ point.title }}</strong>
                    <span>{{ point.body_md }}</span>
                </li>
            </ul>
            <el-button
                v-if="canEdit && doc.status !== 'PUBLISHED'"
                @click="publish(doc)"
                >发布</el-button
            >
        </el-card>
        <p v-if="!docs.length">还没有可阅读的文档。</p>
    </section>
</template>

<style scoped>
.search,
form {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: end;
    margin-bottom: 1rem;
}
</style>
