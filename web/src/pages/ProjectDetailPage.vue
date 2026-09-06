<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { dateLabel, dateShort } from '../api/parse'
import {
    PROJECT_STAGES,
    projectErrorMessage,
    projectsApi,
    type MilestoneWrite,
    type Project,
    type ProjectStage,
} from '../api/projects'
import EmptyLine from '../components/ui/EmptyLine.vue'
import InlineError from '../components/ui/InlineError.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import { STAGE_LABEL } from '../copy/glossary'
import { projectsCopy } from '../copy/pages/projects'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const auth = useAuthStore()
const canEdit = computed(() => auth.user?.role === 'OWNER')
const projectId = computed(() => String(route.params.projectId ?? ''))
const project = ref<Project>()
const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const drawerOpen = ref(false)
const adding = ref(false)
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
        apply(await projectsApi.get(projectId.value))
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
        drawerOpen.value = false
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
        adding.value = false
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        saving.value = false
    }
}

function addMilestone(): void {
    adding.value = true
    draftMilestones.value.push({ title: '', due_on: '', done: false })
}

async function toggleMilestone(index: number): Promise<void> {
    const current = draftMilestones.value[index]
    if (!current) return
    current.done = !current.done
    await saveMilestones()
}

async function removeMilestone(index: number): Promise<void> {
    draftMilestones.value.splice(index, 1)
    await saveMilestones()
}

watch(projectId, load)
onMounted(load)
</script>

<template>
    <section class="detail" aria-labelledby="project-detail-title">
        <PageHeader
            :title="project?.name || projectsCopy.title"
            heading-id="project-detail-title"
        >
            <el-button v-if="canEdit" text @click="drawerOpen = true">{{
                projectsCopy.edit
            }}</el-button>
        </PageHeader>
        <InlineError :message="errorMessage" />
        <div v-loading="loading">
            <p v-if="project" class="meta">
                {{ STAGE_LABEL[project.stage] }}
                ·
                {{
                    project.starts_on
                        ? dateLabel(project.starts_on)
                        : projectsCopy.start
                }}
                –
                {{
                    project.due_on
                        ? dateLabel(project.due_on)
                        : projectsCopy.due
                }}
                · {{ project.progress_percent }}%
            </p>
            <p v-if="project?.description" class="body">
                {{ project.description }}
            </p>
            <h2>{{ projectsCopy.milestones }}</h2>
            <ol class="timeline">
                <li
                    v-for="(item, index) in project?.milestones ?? []"
                    :key="item.id"
                    :class="{ done: item.done_at }"
                >
                    <span>{{
                        item.due_on ? dateShort(item.due_on) : '—'
                    }}</span>
                    <strong>{{ item.title }}</strong>
                    <el-dropdown v-if="canEdit" trigger="click">
                        <el-button text>···</el-button>
                        <template #dropdown>
                            <el-dropdown-menu>
                                <el-dropdown-item
                                    @click="toggleMilestone(index)"
                                    >{{ projectsCopy.done }}</el-dropdown-item
                                >
                                <el-dropdown-item
                                    @click="removeMilestone(index)"
                                    >{{ projectsCopy.remove }}</el-dropdown-item
                                >
                            </el-dropdown-menu>
                        </template>
                    </el-dropdown>
                </li>
            </ol>
            <EmptyLine
                v-if="!project?.milestones.length"
                :message="projectsCopy.emptyMilestones"
            />
            <div v-if="canEdit" class="milestone-edit">
                <el-button text @click="addMilestone">{{
                    projectsCopy.add
                }}</el-button>
                <form
                    v-if="adding"
                    class="drawer-form"
                    @submit.prevent="saveMilestones"
                >
                    <label
                        v-for="(item, index) in draftMilestones"
                        :key="index"
                    >
                        <el-input v-model="item.title" />
                        <el-date-picker
                            v-model="item.due_on"
                            type="date"
                            value-format="YYYY-MM-DD"
                        />
                        <el-checkbox v-model="item.done">{{
                            projectsCopy.done
                        }}</el-checkbox>
                    </label>
                    <el-button
                        type="primary"
                        native-type="submit"
                        :loading="saving"
                        >{{ projectsCopy.save }}</el-button
                    >
                </form>
            </div>
        </div>
        <el-drawer v-model="drawerOpen" :title="projectsCopy.edit" size="400px">
            <form class="drawer-form" @submit.prevent="saveProject">
                <label for="project-stage">{{ projectsCopy.stage }}</label>
                <el-select id="project-stage" v-model="stage">
                    <el-option
                        v-for="item in PROJECT_STAGES"
                        :key="item"
                        :label="STAGE_LABEL[item]"
                        :value="item"
                    />
                </el-select>
                <label for="project-description">{{
                    projectsCopy.description
                }}</label>
                <el-input
                    id="project-description"
                    v-model="description"
                    type="textarea"
                    :rows="3"
                />
                <label for="project-start">{{ projectsCopy.start }}</label>
                <el-date-picker
                    id="project-start"
                    v-model="startsOn"
                    type="date"
                    value-format="YYYY-MM-DD"
                />
                <label for="project-due">{{ projectsCopy.due }}</label>
                <el-date-picker
                    id="project-due"
                    v-model="dueOn"
                    type="date"
                    value-format="YYYY-MM-DD"
                />
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="saving"
                    >{{ projectsCopy.save }}</el-button
                >
            </form>
        </el-drawer>
    </section>
</template>

<style scoped>
.meta,
.body {
    color: var(--sb-ink-2);
    margin-bottom: 24px;
}
.body {
    font-size: var(--sb-md);
    line-height: 1.75;
    color: var(--sb-ink);
}
h2 {
    font-size: var(--sb-lg);
    margin-bottom: 16px;
}
.timeline {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 12px;
}
.timeline li {
    display: flex;
    gap: 16px;
    align-items: baseline;
}
.timeline .done {
    color: var(--sb-ink-3);
}
.drawer-form,
.milestone-edit {
    display: grid;
    gap: 12px;
}
</style>
