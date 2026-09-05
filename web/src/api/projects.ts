import {
    HttpClientError,
    apiClient,
    formatRequestError,
    type BrowserHttpClient,
} from './http'

export const MAX_PROJECTS_PER_RESPONSE = 1000

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
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
    is_test: boolean
    status: 'ACTIVE' | 'ARCHIVED'
    stage: ProjectStage
    progress_percent: number
    starts_on: string | null
    due_on: string | null
    milestones: Milestone[]
}

export interface ProjectCreate {
    name: string
    is_test: boolean
    description?: string
    stage?: ProjectStage
}

export interface ProjectUpdate {
    name?: string
    description?: string
    stage?: ProjectStage
    progress_percent?: number
    starts_on?: string | null
    due_on?: string | null
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

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasRequiredKeys(
    value: Record<string, unknown>,
    required: readonly string[],
): boolean {
    return required.every((key) => key in value)
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
            'is_test',
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
        typeof value.is_test !== 'boolean' ||
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
        is_test: value.is_test,
        status: value.status,
        stage: value.stage as ProjectStage,
        progress_percent: value.progress_percent,
        starts_on: optionalDate(value.starts_on),
        due_on: optionalDate(value.due_on),
        milestones: value.milestones.map(parseMilestone),
    }
}

function parseProjectList(value: unknown): Project[] {
    if (!Array.isArray(value) || value.length > MAX_PROJECTS_PER_RESPONSE) {
        throw new ProjectContractError()
    }
    return value.map(parseProject)
}

function validatedCreate(value: unknown): ProjectCreate {
    if (!isRecord(value) || !hasRequiredKeys(value, ['is_test', 'name'])) {
        throw new ProjectContractError()
    }
    if (typeof value.is_test !== 'boolean') throw new ProjectContractError()
    return { name: canonicalName(value.name), is_test: value.is_test }
}

export function projectErrorMessage(error: unknown): string {
    if (
        error instanceof HttpClientError &&
        error.status === 409 &&
        isRecord(error.data) &&
        isRecord(error.data.error) &&
        error.data.error.code === 'PROJECT_NAME_CONFLICT'
    ) {
        return '项目名称已存在。'
    }
    return formatRequestError(
        '项目操作失败',
        error,
        '项目操作失败，请稍后重试。',
    )
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
