<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
    projectErrorMessage,
    projectsApi,
    type Project,
} from '../../api/projects'

const projects = ref<Project[]>([])
const name = ref('')
const isTest = ref(false)
const loading = ref(false)
const creating = ref(false)
const errorMessage = ref('')

async function loadProjects(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        projects.value = await projectsApi.list()
    } catch {
        errorMessage.value = '项目列表暂时无法加载，请稍后重试。'
    } finally {
        loading.value = false
    }
}

async function createProject(): Promise<void> {
    if (creating.value) return
    const canonicalName = name.value.replace(
        /^[ \t\r\n\u00a0]+|[ \t\r\n\u00a0]+$/g,
        '',
    )
    if (!canonicalName) {
        errorMessage.value = '请输入项目名称。'
        return
    }
    creating.value = true
    errorMessage.value = ''
    try {
        const created = await projectsApi.create({
            name: canonicalName,
            is_test: isTest.value,
        })
        projects.value.push(created)
        name.value = ''
        isTest.value = false
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        creating.value = false
    }
}

onMounted(loadProjects)
</script>

<template>
    <section class="projects-page" aria-labelledby="projects-title">
        <header>
            <p class="projects-page__eyebrow">OWNER</p>
            <h1 id="projects-title">项目管理</h1>
        </header>

        <el-card shadow="never">
            <form class="project-form" @submit.prevent="createProject">
                <label for="project-name">项目名称</label>
                <el-input id="project-name" v-model="name" maxlength="255" />
                <el-checkbox v-model="isTest">设为验收测试项目</el-checkbox>
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="creating"
                    :disabled="creating"
                >
                    创建项目
                </el-button>
            </form>
        </el-card>

        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>

        <div v-loading="loading" class="project-list" aria-live="polite">
            <el-card
                v-for="project in projects"
                :key="project.id"
                shadow="never"
            >
                <div class="project-row">
                    <div>
                        <strong>{{ project.name }}</strong>
                        <p>
                            {{
                                project.status === 'ACTIVE'
                                    ? '启用中'
                                    : '已归档'
                            }}
                        </p>
                    </div>
                    <el-tag v-if="project.is_test" type="warning"
                        >验收测试</el-tag
                    >
                </div>
            </el-card>
        </div>
    </section>
</template>

<style scoped>
.projects-page,
.project-list {
    display: grid;
    gap: 18px;
}

.projects-page__eyebrow,
.project-row p {
    margin: 0;
    color: #909399;
}

.projects-page h1 {
    margin: 6px 0 0;
}

.project-form {
    display: grid;
    grid-template-columns: minmax(220px, 1fr) auto auto;
    gap: 12px;
    align-items: end;
}

.project-form label {
    grid-column: 1 / -1;
    font-weight: 600;
}

.project-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

@media (max-width: 680px) {
    .project-form {
        grid-template-columns: 1fr;
    }
}
</style>
