<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { dateShort } from '../../api/parse'
import {
    projectErrorMessage,
    projectsApi,
    type Project,
    type ProjectStage,
} from '../../api/projects'
import Dot from '../../components/ui/Dot.vue'
import EmptyLine from '../../components/ui/EmptyLine.vue'
import InlineError from '../../components/ui/InlineError.vue'
import PageHeader from '../../components/ui/PageHeader.vue'
import { STAGE_LABEL } from '../../copy/glossary'
import { projectsCopy } from '../../copy/pages/projects'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const canCreate = computed(() => auth.user?.role === 'OWNER')
const projects = ref<Project[]>([])
const name = ref('')
const description = ref('')
const stage = ref<ProjectStage>('PLANNING')
const startsOn = ref('')
const dueOn = ref('')
const loading = ref(false)
const creating = ref(false)
const errorMessage = ref('')
const drawerOpen = ref(false)
const filter = ref<'active' | 'archived'>('active')
let loadSeq = 0

const visible = computed(() =>
    projects.value.filter((project) =>
        filter.value === 'archived'
            ? project.stage === 'ARCHIVED'
            : project.stage !== 'ARCHIVED',
    ),
)

function dueTone(project: Project): 'warn' | 'muted' {
    if (!project.due_on) return 'muted'
    const due = new Date(`${project.due_on}T00:00:00`)
    const soon = Date.now() + 14 * 24 * 60 * 60 * 1000
    return due.getTime() <= soon ? 'warn' : 'muted'
}

async function loadProjects(): Promise<void> {
    const seq = (loadSeq += 1)
    loading.value = true
    errorMessage.value = ''
    try {
        const loaded = await projectsApi.list()
        if (seq !== loadSeq) return
        projects.value = loaded
    } catch {
        if (seq !== loadSeq) return
        errorMessage.value = projectsCopy.loadFailed
    } finally {
        if (seq === loadSeq) loading.value = false
    }
}

async function createProject(): Promise<void> {
    if (creating.value) return
    const canonicalName = name.value.replace(
        /^[ \t\r\n\u00a0]+|[ \t\r\n\u00a0]+$/g,
        '',
    )
    if (!canonicalName) {
        errorMessage.value = projectsCopy.nameRequired
        return
    }
    if ([...canonicalName].length > 255) {
        errorMessage.value = projectsCopy.tooLong
        return
    }
    creating.value = true
    errorMessage.value = ''
    try {
        const created = await projectsApi.create({
            name: canonicalName,
            description: description.value,
            stage: stage.value,
            starts_on: startsOn.value || null,
            due_on: dueOn.value || null,
        })
        name.value = ''
        description.value = ''
        stage.value = 'PLANNING'
        startsOn.value = ''
        dueOn.value = ''
        drawerOpen.value = false
        await loadProjects()
        const others = projects.value.filter((item) => item.id !== created.id)
        projects.value = [...others, created]
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
        <PageHeader :title="projectsCopy.title" heading-id="projects-title">
            <el-button
                text
                :class="{ active: filter === 'active' }"
                @click="filter = 'active'"
                >{{ projectsCopy.active }}</el-button
            >
            <el-button
                text
                :class="{ active: filter === 'archived' }"
                @click="filter = 'archived'"
                >{{ projectsCopy.archived }}</el-button
            >
            <el-button v-if="canCreate" text @click="drawerOpen = true">{{
                projectsCopy.create
            }}</el-button>
        </PageHeader>
        <InlineError :message="errorMessage" />
        <ul v-loading="loading" class="rows">
            <li v-for="project in visible" :key="project.id">
                <router-link :to="`/projects/${project.id}`">{{
                    project.name
                }}</router-link>
                <span>{{ STAGE_LABEL[project.stage] }}</span>
                <span class="progress">
                    <i :style="{ width: `${project.progress_percent}%` }" />
                </span>
                <span class="tabular">{{ project.progress_percent }}%</span>
                <span v-if="project.due_on" class="due">
                    <Dot :tone="dueTone(project)" />
                    {{ dateShort(project.due_on) }}
                </span>
            </li>
        </ul>
        <EmptyLine v-if="!visible.length" :message="projectsCopy.empty" />
        <el-drawer
            v-model="drawerOpen"
            :title="projectsCopy.create"
            size="400px"
        >
            <form class="drawer-form" @submit.prevent="createProject">
                <label for="project-name">{{ projectsCopy.nameLabel }}</label>
                <el-input id="project-name" v-model="name" />
                <label>{{ projectsCopy.stage }}</label>
                <el-select v-model="stage">
                    <el-option
                        v-for="(label, item) in STAGE_LABEL"
                        :key="item"
                        :label="label"
                        :value="item"
                    />
                </el-select>
                <label>{{ projectsCopy.start }}</label>
                <el-date-picker
                    v-model="startsOn"
                    type="date"
                    value-format="YYYY-MM-DD"
                />
                <label>{{ projectsCopy.due }}</label>
                <el-date-picker
                    v-model="dueOn"
                    type="date"
                    value-format="YYYY-MM-DD"
                />
                <label>{{ projectsCopy.description }}</label>
                <el-input v-model="description" type="textarea" />
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="creating"
                    :disabled="creating"
                    >{{ projectsCopy.createSubmit }}</el-button
                >
            </form>
        </el-drawer>
    </section>
</template>

<style scoped>
.rows {
    list-style: none;
    margin: 0;
    padding: 0;
}
.rows li {
    display: grid;
    grid-template-columns: minmax(140px, 1.4fr) 4em 120px 3em 6em;
    gap: 16px;
    align-items: center;
    min-height: 48px;
    border-bottom: 1px solid var(--sb-line);
}
.progress {
    display: block;
    height: 2px;
    background: var(--sb-line);
}
.progress i {
    display: block;
    height: 2px;
    background: var(--sb-accent);
}
.due {
    display: flex;
    gap: 6px;
    align-items: center;
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
.active {
    font-weight: 600;
    color: var(--sb-accent);
}
.drawer-form {
    display: grid;
    gap: 12px;
}
@media (max-width: 760px) {
    .rows li {
        grid-template-columns: 1fr 4em;
    }
}
</style>
