import {
    HttpClientError,
    apiClient,
    formatRequestError,
    type BrowserHttpClient,
} from './http'
import { errorCopy } from '../copy/errors'
import { hasRequiredKeys, isRecord, UUID } from './parse'

export const MAX_PROJECTS_PER_RESPONSE = 1000

const EDGE_WHITESPACE = /^[ \t\r\n\u00a0]+|[ \t\r\n\u00a0]+$/g

export const PROJECT_STAGES = [
    'PLANNING',
    'ACTIVE',
    'DELIVERING',
    'REVIEW',
    'ARCHIVED',
] as const

export type ProjectStage = (typeof PROJECT_STAGES)[number]

export interface Milestone {
    id: string
    title: string
    due_on: string | null
    done_at: string | null
    sort_order: number
}

export interface Project {
    id: string
    name: string
    description: string
    status: 'ACTIVE' | 'ARCHIVED'
    stage: ProjectStage
    progress_percent: number
    starts_on: string | null
    due_on: string | null
    service_fee_cents: number | null
    lead_user_id: string | null
    milestones: Milestone[]
    nodes?: ProjectNode[]
    schedule_changes?: ScheduleChange[]
    workflow_pending?: boolean
    contract_due_on?: string | null
    service_completed_on?: string | null
    template_version_id?: string | null
}

export interface ProjectNode {
    id: string
    sort_order: number
    title: string
    planned_start: string | null
    planned_end: string | null
    status: string
    completed_at: string | null
    preparation: unknown[]
    document_name: string
    photo_required: boolean
    required_materials?: unknown[]
    completed_by?: string | null
    evidence: unknown[]
}

export interface ScheduleChange {
    id: string
    node_id: string | null
    days: number
    reason: string
    created_at: string
}

export interface ProjectCreate {
    name: string
    description?: string
    stage?: ProjectStage
    starts_on?: string | null
    due_on?: string | null
    service_fee_cents?: number | null
    lead_user_id?: string | null
}

export interface ProjectUpdate {
    name?: string
    description?: string
    stage?: ProjectStage
    progress_percent?: number
    starts_on?: string | null
    due_on?: string | null
    contract_due_on?: string | null
    service_fee_cents?: number | null
    lead_user_id?: string | null
}

export interface MilestoneWrite {
    title: string
    due_on: string | null
    done: boolean
    sort_order: number
}

export class ProjectContractError extends Error {
    constructor() {
        super('Invalid project data')
        this.name = 'ProjectContractError'
    }
}

function hasUnsafeText(value: string): boolean {
    for (let index = 0; index < value.length; index += 1) {
        const code = value.charCodeAt(index)
        if (code <= 31 || (code >= 127 && code <= 159)) return true
        if (code >= 0xd800 && code <= 0xdbff) {
            const next = value.charCodeAt(index + 1)
            if (next < 0xdc00 || next > 0xdfff) return true
            index += 1
        } else if (code >= 0xdc00 && code <= 0xdfff) {
            return true
        }
    }
    return false
}

function canonicalName(value: unknown): string {
    if (typeof value !== 'string') throw new ProjectContractError()
    const normalized = value.replace(EDGE_WHITESPACE, '')
    if (
        !normalized ||
        hasUnsafeText(normalized) ||
        [...normalized].length > 255
    ) {
        throw new ProjectContractError()
    }
    return normalized
}

function optionalDate(value: unknown): string | null {
    if (value === null) return null
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
        throw new ProjectContractError()
    }
    return value
}

function parseNode(value: unknown): ProjectNode {
    if (
        !isRecord(value) ||
        typeof value.id !== 'string' ||
        !UUID.test(value.id)
    ) {
        throw new ProjectContractError()
    }
    return {
        id: value.id,
        sort_order: Number(value.sort_order) || 0,
        title: String(value.title || ''),
        planned_start: optionalDate(value.planned_start ?? null),
        planned_end: optionalDate(value.planned_end ?? null),
        status: String(value.status || 'OPEN'),
        completed_at:
            typeof value.completed_at === 'string' ? value.completed_at : null,
        preparation: Array.isArray(value.preparation) ? value.preparation : [],
        document_name: String(value.document_name || ''),
        photo_required: Boolean(value.photo_required),
        evidence: Array.isArray(value.evidence) ? value.evidence : [],
        ...('required_materials' in value
            ? {
                  required_materials: Array.isArray(value.required_materials)
                      ? value.required_materials
                      : [],
              }
            : {}),
        ...('completed_by' in value
            ? {
                  completed_by:
                      typeof value.completed_by === 'string'
                          ? value.completed_by
                          : null,
              }
            : {}),
    }
}

function parseScheduleChange(value: unknown): ScheduleChange {
    if (
        !isRecord(value) ||
        typeof value.id !== 'string' ||
        !UUID.test(value.id)
    ) {
        throw new ProjectContractError()
    }
    return {
        id: value.id,
        node_id:
            typeof value.node_id === 'string' && UUID.test(value.node_id)
                ? value.node_id
                : null,
        days: Number(value.days) || 0,
        reason: String(value.reason || ''),
        created_at: String(value.created_at || ''),
    }
}

function parseMilestone(value: unknown): Milestone {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'id',
            'title',
            'due_on',
            'done_at',
            'sort_order',
        ])
    ) {
        throw new ProjectContractError()
    }
    const title = canonicalName(value.title)
    if (
        typeof value.id !== 'string' ||
        !UUID.test(value.id) ||
        title !== value.title ||
        (value.due_on !== null && typeof value.due_on !== 'string') ||
        (value.done_at !== null && typeof value.done_at !== 'string') ||
        typeof value.sort_order !== 'number'
    ) {
        throw new ProjectContractError()
    }
    return {
        id: value.id,
        title,
        due_on: optionalDate(value.due_on),
        done_at: value.done_at,
        sort_order: value.sort_order,
    }
}

function parseProject(value: unknown): Project {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'description',
            'due_on',
            'id',
            'milestones',
            'name',
            'progress_percent',
            'stage',
            'starts_on',
            'status',
        ])
    ) {
        throw new ProjectContractError()
    }
    const name = canonicalName(value.name)
    if (
        typeof value.id !== 'string' ||
        !UUID.test(value.id) ||
        name !== value.name ||
        typeof value.description !== 'string' ||
        (value.status !== 'ACTIVE' && value.status !== 'ARCHIVED') ||
        !PROJECT_STAGES.includes(value.stage as ProjectStage) ||
        typeof value.progress_percent !== 'number' ||
        value.progress_percent < 0 ||
        value.progress_percent > 100 ||
        !Array.isArray(value.milestones)
    ) {
        throw new ProjectContractError()
    }
    return {
        id: value.id,
        name,
        description: value.description,
        status: value.status,
        stage: value.stage as ProjectStage,
        progress_percent: value.progress_percent,
        starts_on: optionalDate(value.starts_on),
        due_on: optionalDate(value.due_on),
        service_fee_cents:
            value.service_fee_cents === null ||
            value.service_fee_cents === undefined
                ? null
                : typeof value.service_fee_cents === 'number'
                  ? value.service_fee_cents
                  : (() => {
                        throw new ProjectContractError()
                    })(),
        lead_user_id:
            value.lead_user_id === null || value.lead_user_id === undefined
                ? null
                : typeof value.lead_user_id === 'string' &&
                    UUID.test(value.lead_user_id)
                  ? value.lead_user_id
                  : (() => {
                        throw new ProjectContractError()
                    })(),
        milestones: value.milestones.map(parseMilestone),
        ...(Array.isArray(value.nodes)
            ? { nodes: value.nodes.map(parseNode) }
            : {}),
        ...(Array.isArray(value.schedule_changes)
            ? {
                  schedule_changes:
                      value.schedule_changes.map(parseScheduleChange),
              }
            : {}),
        ...('workflow_pending' in value
            ? { workflow_pending: Boolean(value.workflow_pending) }
            : {}),
        ...('contract_due_on' in value
            ? { contract_due_on: optionalDate(value.contract_due_on ?? null) }
            : {}),
        ...('service_completed_on' in value
            ? {
                  service_completed_on: optionalDate(
                      value.service_completed_on ?? null,
                  ),
              }
            : {}),
        ...('template_version_id' in value
            ? {
                  template_version_id:
                      typeof value.template_version_id === 'string' &&
                      UUID.test(value.template_version_id)
                          ? value.template_version_id
                          : null,
              }
            : {}),
    }
}

function parseProjectList(value: unknown): Project[] {
    if (!Array.isArray(value) || value.length > MAX_PROJECTS_PER_RESPONSE) {
        throw new ProjectContractError()
    }
    return value.map(parseProject)
}

function validatedCreate(value: unknown): ProjectCreate {
    if (!isRecord(value) || !hasRequiredKeys(value, ['name'])) {
        throw new ProjectContractError()
    }
    const created: ProjectCreate = { name: canonicalName(value.name) }
    if (typeof value.description === 'string')
        created.description = value.description
    if (typeof value.stage === 'string')
        created.stage = value.stage as ProjectStage
    if (value.starts_on === null || typeof value.starts_on === 'string') {
        created.starts_on = value.starts_on
    }
    if (value.due_on === null || typeof value.due_on === 'string') {
        created.due_on = value.due_on
    }
    if (
        typeof value.service_fee_cents === 'number' ||
        value.service_fee_cents === null
    ) {
        created.service_fee_cents = value.service_fee_cents
    }
    if (typeof value.lead_user_id === 'string' || value.lead_user_id === null) {
        created.lead_user_id = value.lead_user_id
    }
    return created
}

export function projectErrorMessage(error: unknown): string {
    if (
        error instanceof HttpClientError &&
        error.status === 409 &&
        isRecord(error.data) &&
        isRecord(error.data.error)
    ) {
        if (error.data.error.code === 'PROJECT_NAME_CONFLICT') {
            return errorCopy.projectNameConflict
        }
        if (error.data.error.code === 'PROJECT_HAS_ENTRIES') {
            return errorCopy.projectHasEntries
        }
    }
    return formatRequestError(errorCopy.projects, error, errorCopy.generic)
}

export function createProjectsApi(client: BrowserHttpClient) {
    return {
        async list(): Promise<Project[]> {
            const response = await client.get('/projects')
            if (response.status !== 200) throw new ProjectContractError()
            return parseProjectList(response.data)
        },
        async get(projectId: string): Promise<Project> {
            if (!UUID.test(projectId)) throw new ProjectContractError()
            const response = await client.get(`/projects/${projectId}`)
            if (response.status !== 200) throw new ProjectContractError()
            return parseProject(response.data)
        },
        async create(command: ProjectCreate): Promise<Project> {
            const canonical = validatedCreate(command)
            const response = await client.post('/projects', canonical)
            if (response.status !== 201) throw new ProjectContractError()
            return parseProject(response.data)
        },
        async update(
            projectId: string,
            command: ProjectUpdate,
        ): Promise<Project> {
            if (!UUID.test(projectId)) throw new ProjectContractError()
            const response = await client.patch(
                `/projects/${projectId}`,
                command,
            )
            if (response.status !== 200) throw new ProjectContractError()
            return parseProject(response.data)
        },
        async previewShift(
            projectId: string,
            nodeId: string,
            days: number,
        ): Promise<unknown> {
            const response = await client.post(
                `/projects/${projectId}/schedule-preview`,
                { node_id: nodeId, days },
            )
            if (response.status !== 200) throw new ProjectContractError()
            return response.data
        },
        async applyShift(
            projectId: string,
            nodeId: string,
            days: number,
            reason: string,
        ): Promise<Project> {
            const response = await client.post(
                `/projects/${projectId}/schedule`,
                {
                    node_id: nodeId,
                    days,
                    reason,
                },
            )
            if (response.status !== 200) throw new ProjectContractError()
            return parseProject(response.data)
        },
        async applyWorkflow(
            projectId: string,
            command: { starts_on?: string | null } = {},
        ): Promise<Project> {
            if (!UUID.test(projectId)) throw new ProjectContractError()
            const response = await client.post(
                `/projects/${projectId}/apply-workflow`,
                command,
            )
            if (response.status !== 200) throw new ProjectContractError()
            return parseProject(response.data)
        },
        async completeService(projectId: string): Promise<Project> {
            if (!UUID.test(projectId)) throw new ProjectContractError()
            const response = await client.post(
                `/projects/${projectId}/complete-service`,
                {},
            )
            if (response.status !== 200) throw new ProjectContractError()
            return parseProject(response.data)
        },
        async completeNode(
            projectId: string,
            nodeId: string,
            evidence: string[],
        ): Promise<Project> {
            const response = await client.post(
                `/projects/${projectId}/nodes/${nodeId}/complete`,
                { evidence },
            )
            if (response.status !== 200) throw new ProjectContractError()
            return parseProject(response.data)
        },
        async remove(projectId: string): Promise<void> {
            if (!UUID.test(projectId)) throw new ProjectContractError()
            const response = await client.delete(`/projects/${projectId}`)
            if (response.status !== 204) throw new ProjectContractError()
        },
        async replaceMilestones(
            projectId: string,
            milestones: MilestoneWrite[],
        ): Promise<Project> {
            if (!UUID.test(projectId)) throw new ProjectContractError()
            const response = await client.put(
                `/projects/${projectId}/milestones`,
                {
                    milestones,
                },
            )
            if (response.status !== 200) throw new ProjectContractError()
            return parseProject(response.data)
        },
    }
}

export const projectsApi = createProjectsApi(apiClient)
