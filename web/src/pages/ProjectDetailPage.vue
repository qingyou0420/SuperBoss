<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
    PROJECT_STAGES,
    projectErrorMessage,
    projectsApi,
    type MilestoneWrite,
    type Project,
    type ProjectStage,
} from '../api/projects'
import { useAuthStore } from '../stores/auth'

const STAGE_LABEL: Record<ProjectStage, string> = {
    PLANNING: '立项',
    ACTIVE: '进行中',
    DELIVERING: '交付中',
    REVIEW: '复盘',
    ARCHIVED: '已归档',
}

const route = useRoute()
const auth = useAuthStore()
const canEdit = computed(() => auth.user?.role === 'OWNER')
const projectId = computed(() => String(route.params.projectId ?? ''))
const project = ref<Project>()
const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const description = ref('')
const stage = ref<ProjectStage>('PLANNING')
const startsOn = ref('')
const dueOn = ref('')
const draftMilestones = ref<
    Array<{ title: string; due_on: string; done: boolean }>
>([])

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        const loaded = await projectsApi.get(projectId.value)
        apply(loaded)
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        loading.value = false
    }
}

function apply(loaded: Project): void {
    project.value = loaded
    description.value = loaded.description
    stage.value = loaded.stage
    startsOn.value = loaded.starts_on ?? ''
    dueOn.value = loaded.due_on ?? ''
    draftMilestones.value = loaded.milestones.map((item) => ({
        title: item.title,
        due_on: item.due_on ?? '',
        done: item.done_at !== null,
    }))
}

async function saveProject(): Promise<void> {
    if (!canEdit.value || saving.value) return
    saving.value = true
    errorMessage.value = ''
    try {
        apply(
            await projectsApi.update(projectId.value, {
                description: description.value,
                stage: stage.value,
                starts_on: startsOn.value || null,
                due_on: dueOn.value || null,
            }),
        )
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function saveMilestones(): Promise<void> {
    if (!canEdit.value || saving.value) return
    saving.value = true
    errorMessage.value = ''
    try {
        const milestones: MilestoneWrite[] = draftMilestones.value
            .map((item, index) => ({
                title: item.title.trim(),
                due_on: item.due_on || null,
                done: item.done,
                sort_order: index,
            }))
            .filter((item) => item.title)
        apply(await projectsApi.replaceMilestones(projectId.value, milestones))
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        saving.value = false
    }
}

function addMilestone(): void {
    draftMilestones.value.push({ title: '', due_on: '', done: false })
}

function removeMilestone(index: number): void {
    draftMilestones.value.splice(index, 1)
}

watch(projectId, load)
onMounted(load)
</script>

<template>
    <section class="detail" aria-labelledby="project-detail-title">
        <p class="eyebrow">
            <router-link to="/projects">项目</router-link>
        </p>
        <h1 id="project-detail-title">{{ project?.name || '项目详情' }}</h1>
        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>
        <div v-loading="loading">
            <p v-if="project" class="progress">
                进度 {{ project.progress_percent }}% ·
                {{ STAGE_LABEL[project.stage] }}
            </p>
            <el-card v-if="project" shadow="never">
                <h2>概况</h2>
                <template v-if="canEdit">
                    <label for="project-stage">阶段</label>
                    <select id="project-stage" v-model="stage">
                        <option
                            v-for="item in PROJECT_STAGES"
                            :key="item"
                            :value="item"
                        >
                            {{ STAGE_LABEL[item] }}
                        </option>
                    </select>
                    <label for="project-description">说明</label>
                    <el-input
                        id="project-description"
                        v-model="description"
                        type="textarea"
                        :rows="3"
                    />
                    <label for="project-start">开始</label>
                    <el-input
                        id="project-start"
                        v-model="startsOn"
                        type="date"
                    />
                    <label for="project-due">截止</label>
                    <el-input id="project-due" v-model="dueOn" type="date" />
                    <el-button
                        type="primary"
                        :loading="saving"
                        @click="saveProject"
                        >保存概况</el-button
                    >
                </template>
                <dl v-else>
                    <dt>说明</dt>
                    <dd>{{ project.description || '暂无' }}</dd>
                    <dt>开始</dt>
                    <dd>{{ project.starts_on || '未定' }}</dd>
                    <dt>截止</dt>
                    <dd>{{ project.due_on || '未定' }}</dd>
                </dl>
            </el-card>
            <el-card v-if="project" shadow="never" class="timeline">
                <h2>里程碑</h2>
                <ol v-if="canEdit">
                    <li v-for="(item, index) in draftMilestones" :key="index">
                        <el-input v-model="item.title" placeholder="节点名称" />
                        <el-input v-model="item.due_on" type="date" />
                        <label>
                            <input v-model="item.done" type="checkbox" />
                            完成
                        </label>
                        <el-button text @click="removeMilestone(index)"
                            >删除</el-button
                        >
                    </li>
                </ol>
                <ol v-else>
                    <li v-for="item in project.milestones" :key="item.id">
                        <strong>{{ item.title }}</strong>
                        <span>{{ item.due_on || '未定日期' }}</span>
                        <span>{{ item.done_at ? '已完成' : '未完成' }}</span>
                    </li>
                </ol>
                <div v-if="canEdit" class="timeline__actions">
                    <el-button @click="addMilestone">添加节点</el-button>
                    <el-button
                        type="primary"
                        :loading="saving"
                        @click="saveMilestones"
                        >保存里程碑</el-button
                    >
                </div>
            </el-card>
        </div>
    </section>
</template>

<style scoped>
.detail,
.timeline,
.timeline ol,
.timeline li,
.timeline__actions {
    display: grid;
    gap: 12px;
}
.eyebrow,
.progress,
dl {
    color: #606266;
}
.eyebrow a {
    color: #409eff;
    text-decoration: none;
}
h1,
h2,
p {
    margin: 0;
}
select,
.timeline li {
    align-items: center;
}
.timeline li {
    grid-template-columns: minmax(160px, 1fr) auto auto auto;
}
dl {
    display: grid;
    grid-template-columns: 4rem 1fr;
    gap: 8px;
}
dd {
    margin: 0;
}
@media (max-width: 680px) {
    .timeline li {
        grid-template-columns: 1fr;
    }
}
</style>
