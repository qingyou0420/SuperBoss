<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { filesApi, type DriveFile } from '../api/files'
import { centsFromYuan, financeApi } from '../api/finance'
import { dateLabel, dateShort, moneyLabel } from '../api/parse'
import { usersApi, type OwnerUser } from '../api/users'
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
const router = useRouter()
const auth = useAuthStore()
const canEdit = computed(() => auth.user?.role === 'OWNER')
const canLead = computed(() => {
    if (auth.user?.role === 'OWNER') return true
    return (
        auth.user?.role === 'STAFF' &&
        Boolean(auth.user.id) &&
        auth.user.id === project.value?.lead_user_id
    )
})
const projectId = computed(() => String(route.params.projectId ?? ''))
const project = ref<Project>()
const staff = ref<OwnerUser[]>([])
const feeYuan = ref('')
const leadUserId = ref('')
const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const drawerOpen = ref(false)
const adding = ref(false)
const deleteOpen = ref(false)
const shiftOpen = ref(false)
const completeOpen = ref(false)
const activeNodeId = ref('')
const shiftDays = ref(5)
const shiftReason = ref('')
const evidenceIds = ref<string[]>([''])
const availableFiles = ref<DriveFile[]>([])
const selectedFileIds = ref<string[]>([])
const shiftPreview = ref('')
let loadSeq = 0
const rewards = ref<Record<string, unknown>>()
const allocations = computed(() => {
    const rows = rewards.value?.allocations
    if (!Array.isArray(rows)) return []
    return rows as Array<{
        kind: string
        person_name: string
        share_cents: number
    }>
})
const description = ref('')
const stage = ref<ProjectStage>('PLANNING')
const startsOn = ref('')
const dueOn = ref('')
const draftMilestones = ref<
    Array<{ title: string; due_on: string; done: boolean }>
>([])

async function load(): Promise<void> {
    const id = projectId.value
    const seq = ++loadSeq
    loading.value = true
    errorMessage.value = ''
    try {
        const loaded = await projectsApi.get(id)
        if (seq !== loadSeq || projectId.value !== id) return
        apply(loaded)
        if (auth.user?.role !== 'STAFF') {
            const nextRewards = await financeApi
                .rewards(id)
                .catch(() => undefined)
            if (seq !== loadSeq || projectId.value !== id) return
            rewards.value = nextRewards
        }
        if (canEdit.value) {
            const nextStaff = await usersApi.list().catch(() => [])
            if (seq !== loadSeq || projectId.value !== id) return
            staff.value = nextStaff
        }
    } catch (error) {
        if (seq !== loadSeq || projectId.value !== id) return
        errorMessage.value = projectErrorMessage(error)
        project.value = undefined
    } finally {
        if (seq === loadSeq) loading.value = false
    }
}

function apply(loaded: Project): void {
    project.value = loaded
    description.value = loaded.description
    stage.value = loaded.stage
    startsOn.value = loaded.starts_on ?? ''
    dueOn.value = loaded.contract_due_on ?? ''
    draftMilestones.value = loaded.milestones.map((item) => ({
        title: item.title,
        due_on: item.due_on ?? '',
        done: item.done_at !== null,
    }))
    feeYuan.value =
        loaded.service_fee_cents != null
            ? (loaded.service_fee_cents / 100).toFixed(2)
            : ''
    leadUserId.value = loaded.lead_user_id ?? ''
}

async function saveProject(): Promise<void> {
    if (!canEdit.value || saving.value) return
    const id = project.value?.id
    if (!id || id !== projectId.value) return
    saving.value = true
    errorMessage.value = ''
    try {
        apply(
            await projectsApi.update(id, {
                description: description.value,
                stage: stage.value,
                starts_on: startsOn.value || null,
                contract_due_on: dueOn.value || null,
                service_fee_cents: feeYuan.value.trim()
                    ? centsFromYuan(feeYuan.value)
                    : null,
                lead_user_id: leadUserId.value || null,
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
    const id = project.value?.id
    if (!id || id !== projectId.value) return
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
        apply(await projectsApi.replaceMilestones(id, milestones))
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

function beginShift(nodeId: string): void {
    activeNodeId.value = nodeId
    shiftOpen.value = true
}

function beginComplete(nodeId: string): void {
    activeNodeId.value = nodeId
    evidenceIds.value = ['']
    selectedFileIds.value = []
    completeOpen.value = true
    void loadProjectFiles()
}

const activeNode = computed(() =>
    project.value?.nodes?.find((item) => item.id === activeNodeId.value),
)

function materialLabels(node: {
    required_materials?: unknown[]
    photo_required?: boolean
}): string {
    const required = Array.isArray(node.required_materials)
        ? node.required_materials.map((item) => String(item))
        : []
    if (node.photo_required && !required.includes('photo'))
        required.push('photo')
    if (!required.length) return projectsCopy.materialsRequired
    return required
        .map((item) =>
            item === 'photo' ? '照片' : item === 'document' ? '文档' : item,
        )
        .join('、')
}

function evidenceList(
    value: unknown,
): Array<{ filename?: string; kind?: string; file_id?: string }> {
    if (!Array.isArray(value)) return []
    return value.filter((item) => item && typeof item === 'object') as Array<{
        filename?: string
        kind?: string
        file_id?: string
    }>
}

async function loadProjectFiles(): Promise<void> {
    try {
        const folders = await filesApi.listFolders()
        const projectFolder = folders.find(
            (item) => item.name === '项目' && item.visibility === 'ALL',
        )
        if (!projectFolder) {
            availableFiles.value = []
            return
        }
        const listed = await filesApi.listFiles(projectFolder.id)
        availableFiles.value = listed.filter(
            (item) =>
                item.state === 'CLEAN' &&
                (!item.project_id || item.project_id === project.value?.id),
        )
    } catch {
        availableFiles.value = []
    }
}

function addEvidenceField(): void {
    evidenceIds.value = [...evidenceIds.value, '']
}

function collectedEvidence(): string[] {
    const fromFields = evidenceIds.value.flatMap((value) =>
        value.split(/[,，\s]+/).map((item) => item.trim()),
    )
    return [
        ...new Set([...selectedFileIds.value, ...fromFields].filter(Boolean)),
    ]
}

async function applyShift(): Promise<void> {
    if (!activeNodeId.value) return
    const id = project.value?.id
    if (!id || id !== projectId.value) return
    if (!shiftReason.value.trim()) {
        errorMessage.value = projectsCopy.shiftReason
        return
    }
    saving.value = true
    try {
        const preview = await projectsApi.previewShift(
            id,
            activeNodeId.value,
            Number(shiftDays.value) || 0,
        )
        if (preview && typeof preview === 'object' && 'valid' in preview) {
            const payload = preview as {
                valid?: boolean
                violations?: unknown[]
            }
            if (payload.valid === false) {
                const violations = Array.isArray(payload.violations)
                    ? payload.violations
                    : []
                shiftPreview.value = String(violations[0] || projectsCopy.shift)
                errorMessage.value = shiftPreview.value
                return
            }
        }
        apply(
            await projectsApi.applyShift(
                id,
                activeNodeId.value,
                Number(shiftDays.value) || 0,
                shiftReason.value.trim(),
            ),
        )
        shiftOpen.value = false
        shiftReason.value = ''
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function completeActive(): Promise<void> {
    if (!activeNodeId.value) return
    const id = project.value?.id
    if (!id || id !== projectId.value) return
    saving.value = true
    try {
        apply(
            await projectsApi.completeNode(
                id,
                activeNodeId.value,
                collectedEvidence(),
            ),
        )
        completeOpen.value = false
        evidenceIds.value = ['']
        selectedFileIds.value = []
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function applyWorkflow(): Promise<void> {
    if (!canLead.value || !project.value) return
    saving.value = true
    try {
        apply(
            await projectsApi.applyWorkflow(project.value.id, {
                starts_on: startsOn.value || project.value.starts_on,
            }),
        )
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function completeService(): Promise<void> {
    if (!canLead.value || !project.value) return
    saving.value = true
    try {
        apply(await projectsApi.completeService(project.value.id))
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function removeMilestone(index: number): Promise<void> {
    draftMilestones.value.splice(index, 1)
    await saveMilestones()
}

async function confirmDelete(): Promise<void> {
    if (!canEdit.value || saving.value) return
    saving.value = true
    errorMessage.value = ''
    try {
        const id = project.value?.id
        if (!id || id !== projectId.value) return
        await projectsApi.remove(id)
        deleteOpen.value = false
        await router.replace('/projects')
    } catch (error) {
        errorMessage.value = projectErrorMessage(error)
        deleteOpen.value = false
    } finally {
        saving.value = false
    }
}

watch(projectId, () => {
    drawerOpen.value = false
    shiftOpen.value = false
    completeOpen.value = false
    deleteOpen.value = false
    errorMessage.value = ''
    project.value = undefined
    void load()
})
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
            <el-button v-if="canEdit" text @click="deleteOpen = true">{{
                projectsCopy.remove
            }}</el-button>
        </PageHeader>
        <InlineError :message="errorMessage" />
        <div v-loading="loading">
            <p v-if="project" class="meta">
                {{
                    [
                        STAGE_LABEL[project.stage],
                        project.starts_on
                            ? `${projectsCopy.start} ${dateLabel(project.starts_on)}`
                            : '',
                        project.contract_due_on
                            ? `${projectsCopy.due} ${dateLabel(project.contract_due_on)}`
                            : '',
                        project.due_on
                            ? `${projectsCopy.processDue} ${dateLabel(project.due_on)}`
                            : '',
                        `${project.progress_percent}%`,
                        project.service_fee_cents != null
                            ? `${projectsCopy.serviceFee} ${moneyLabel(project.service_fee_cents)}`
                            : '',
                    ]
                        .filter(Boolean)
                        .join(' · ')
                }}
            </p>
            <p v-if="project?.description" class="body">
                {{ project.description }}
            </p>
            <p v-if="rewards && auth.user?.role !== 'STAFF'" class="body">
                节余奖金
                {{ moneyLabel(Number(rewards.surplus_bonus_cents) || 0) }}
                · 执行部抽成
                {{ moneyLabel(Number(rewards.pool_pay_cents) || 0) }}
                · 毛利
                {{ moneyLabel(Number(rewards.gross_cents) || 0) }}
            </p>
            <ul v-if="allocations.length" class="alloc">
                <li
                    v-for="item in allocations"
                    :key="item.person_name + item.kind"
                >
                    {{ item.kind === 'SURPLUS' ? '节余' : '抽成' }}
                    {{ item.person_name }}
                    {{ moneyLabel(item.share_cents) }}
                </li>
            </ul>
            <h2>{{ projectsCopy.nodes }}</h2>
            <p v-if="project?.workflow_pending" class="meta">
                待配置流程
                <el-button v-if="canLead" text @click="applyWorkflow">{{
                    projectsCopy.applyWorkflow
                }}</el-button>
            </p>
            <el-button
                v-if="canLead && project && !project.service_completed_on"
                text
                @click="completeService"
                >{{ projectsCopy.completeService }}</el-button
            >
            <ol class="timeline">
                <li
                    v-for="node in project?.nodes ?? []"
                    :key="node.id"
                    :class="{ done: node.status === 'DONE' }"
                >
                    <span>{{
                        node.planned_end ? dateShort(node.planned_end) : '—'
                    }}</span>
                    <strong>{{ node.title }}</strong>
                    <el-button
                        v-if="canLead && node.status === 'OPEN'"
                        text
                        @click="beginShift(node.id)"
                        >{{ projectsCopy.shift }}</el-button
                    >
                    <el-button
                        v-if="canLead && node.status === 'OPEN'"
                        text
                        @click="beginComplete(node.id)"
                        >{{ projectsCopy.completeStage }}</el-button
                    >
                    <small v-if="node.document_name">{{
                        node.document_name
                    }}</small>
                    <small v-if="node.preparation?.length">{{
                        node.preparation.join('；')
                    }}</small>
                    <small v-if="node.completed_at">
                        {{ dateShort(node.completed_at.slice(0, 10)) }}
                    </small>
                    <small v-if="evidenceList(node.evidence).length">
                        {{ projectsCopy.evidenceLinked }}
                        {{
                            evidenceList(node.evidence)
                                .map(
                                    (item) =>
                                        item.filename ||
                                        item.kind ||
                                        item.file_id,
                                )
                                .filter(Boolean)
                                .join('、')
                        }}
                    </small>
                </li>
            </ol>
            <ol
                v-if="project?.schedule_changes?.length"
                class="timeline changes"
            >
                <li v-for="item in project.schedule_changes" :key="item.id">
                    <span>{{ dateShort(item.created_at.slice(0, 10)) }}</span>
                    <strong
                        >{{ item.days > 0 ? '+' : '' }}{{ item.days }}
                        {{ projectsCopy.shiftDays }}</strong
                    >
                    <span>{{ item.reason }}</span>
                </li>
            </ol>
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
                                    >{{
                                        item.done_at
                                            ? projectsCopy.undone
                                            : projectsCopy.done
                                    }}</el-dropdown-item
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
                <label for="project-fee">{{ projectsCopy.feeYuan }}</label>
                <el-input id="project-fee" v-model="feeYuan" />
                <label for="project-lead">{{ projectsCopy.lead }}</label>
                <el-select id="project-lead" v-model="leadUserId" clearable>
                    <el-option
                        v-for="user in staff.filter(
                            (item) => item.role === 'STAFF',
                        )"
                        :key="user.id"
                        :label="user.display_name"
                        :value="user.id"
                    />
                </el-select>
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="saving"
                    >{{ projectsCopy.save }}</el-button
                >
            </form>
        </el-drawer>
        <el-dialog
            v-model="deleteOpen"
            :title="projectsCopy.remove"
            width="360px"
            :close-on-click-modal="false"
        >
            <p>{{ projectsCopy.deleteConfirm }}</p>
            <template #footer>
                <el-button @click="deleteOpen = false">{{
                    projectsCopy.close
                }}</el-button>
                <el-button
                    type="primary"
                    :loading="saving"
                    @click="confirmDelete"
                    >{{ projectsCopy.confirm }}</el-button
                >
            </template>
        </el-dialog>
        <el-dialog
            v-model="shiftOpen"
            :title="projectsCopy.shift"
            width="360px"
            :close-on-click-modal="false"
        >
            <label>{{ projectsCopy.shiftDays }}</label>
            <el-input-number v-model="shiftDays" :min="-60" :max="60" />
            <label>{{ projectsCopy.shiftReason }}</label>
            <el-input v-model="shiftReason" type="textarea" />
            <template #footer>
                <el-button @click="shiftOpen = false">{{
                    projectsCopy.close
                }}</el-button>
                <el-button
                    type="primary"
                    :loading="saving"
                    @click="applyShift"
                    >{{ projectsCopy.confirm }}</el-button
                >
            </template>
        </el-dialog>
        <el-dialog
            v-model="completeOpen"
            :title="projectsCopy.completeStage"
            width="360px"
            :close-on-click-modal="false"
        >
            <p>
                {{
                    activeNode
                        ? materialLabels(activeNode)
                        : projectsCopy.materialsRequired
                }}
            </p>
            <p class="meta">{{ projectsCopy.materialsHint }}</p>
            <el-checkbox-group
                v-if="availableFiles.length"
                v-model="selectedFileIds"
            >
                <el-checkbox
                    v-for="file in availableFiles"
                    :key="file.id"
                    :label="file.id"
                >
                    {{ file.filename }}
                </el-checkbox>
            </el-checkbox-group>
            <label v-for="(_item, index) in evidenceIds" :key="index">
                <el-input
                    v-model="evidenceIds[index]"
                    :placeholder="projectsCopy.evidenceFileId"
                />
            </label>
            <el-button text @click="addEvidenceField">{{
                projectsCopy.evidenceAdd
            }}</el-button>
            <template #footer>
                <el-button @click="completeOpen = false">{{
                    projectsCopy.close
                }}</el-button>
                <el-button
                    type="primary"
                    :loading="saving"
                    @click="completeActive"
                    >{{ projectsCopy.confirm }}</el-button
                >
            </template>
        </el-dialog>
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
.alloc {
    list-style: none;
    margin: 0 0 24px;
    padding: 0;
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
.drawer-form,
.milestone-edit {
    display: grid;
    gap: 12px;
}
</style>
