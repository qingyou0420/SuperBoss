<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { projectsApi, type Project, type ProjectNode } from '../api/projects'
import EmptyLine from '../components/ui/EmptyLine.vue'
import InlineError from '../components/ui/InlineError.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import { dateShort } from '../api/parse'
import { projectsCopy } from '../copy/pages/projects'
import { shellCopy } from '../copy/pages/shell'

const router = useRouter()
const projects = ref<Project[]>([])
const errorMessage = ref('')

function currentNode(project: Project): ProjectNode | undefined {
    return (project.nodes ?? []).find((item) => item.status === 'OPEN')
}

function statusLabel(project: Project, node?: ProjectNode): string {
    if (project.service_completed_on) return '服务已完成'
    if (project.workflow_pending || !(project.nodes ?? []).length)
        return '待排期'
    if (!node) return '节点已完成'
    return node.title
}

const rows = computed(() =>
    projects.value.map((project) => {
        const node = currentNode(project)
        return {
            project,
            node,
            label: statusLabel(project, node),
            next: node?.planned_end ? dateShort(node.planned_end) : '待定',
        }
    }),
)

onMounted(async () => {
    try {
        projects.value = await projectsApi.list()
    } catch {
        errorMessage.value = projectsCopy.loadFailed
    }
})
</script>

<template>
    <section aria-labelledby="bench-title">
        <PageHeader :title="shellCopy.workbench" heading-id="bench-title" />
        <InlineError :message="errorMessage" />
        <EmptyLine v-if="!rows.length" :message="projectsCopy.empty" />
        <ul class="rows">
            <li v-for="row in rows" :key="row.project.id">
                <button
                    type="button"
                    @click="router.push(`/projects/${row.project.id}`)"
                >
                    <strong>{{ row.project.name }}</strong>
                    <span>{{ row.label }} · {{ row.next }}</span>
                </button>
            </li>
        </ul>
    </section>
</template>

<style scoped>
.rows {
    list-style: none;
    margin: 0;
    padding: 0;
}
.rows li {
    border-bottom: 1px solid var(--sb-line);
}
.rows button {
    display: grid;
    width: 100%;
    padding: 12px 0;
    text-align: left;
    border: 0;
    background: none;
    color: inherit;
    cursor: pointer;
}
.rows span {
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
</style>
