import { HttpClientError, apiClient, type BrowserHttpClient } from './http'

export const MAX_PROJECTS_PER_RESPONSE = 1000

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const EDGE_WHITESPACE = /^[ \t\r\n\u00a0]+|[ \t\r\n\u00a0]+$/g

export interface Project {
    id: string
    name: string
    is_test: boolean
    status: 'ACTIVE' | 'ARCHIVED'
}

export interface ProjectCreate {
    name: string
    is_test: boolean
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

function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
    const actual = Object.keys(value).sort()
    return (
        actual.length === keys.length &&
        actual.every((key, index) => key === keys[index])
    )
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

function parseProject(value: unknown): Project {
    if (
        !isRecord(value) ||
        !exactKeys(value, ['id', 'is_test', 'name', 'status'])
    ) {
        throw new ProjectContractError()
    }
    const name = canonicalName(value.name)
    if (
        typeof value.id !== 'string' ||
        !UUID.test(value.id) ||
        name !== value.name ||
        typeof value.is_test !== 'boolean' ||
        (value.status !== 'ACTIVE' && value.status !== 'ARCHIVED')
    ) {
        throw new ProjectContractError()
    }
    return { id: value.id, name, is_test: value.is_test, status: value.status }
}

function parseProjectList(value: unknown): Project[] {
    if (!Array.isArray(value) || value.length > MAX_PROJECTS_PER_RESPONSE) {
        throw new ProjectContractError()
    }
    return value.map(parseProject)
}

function validatedCreate(value: unknown): ProjectCreate {
    if (!isRecord(value) || !exactKeys(value, ['is_test', 'name'])) {
        throw new ProjectContractError()
    }
    if (typeof value.is_test !== 'boolean') throw new ProjectContractError()
    return { name: canonicalName(value.name), is_test: value.is_test }
}

export function projectErrorMessage(error: unknown): string {
    if (
        !(error instanceof HttpClientError) ||
        error.status !== 409 ||
        !isRecord(error.data)
    ) {
        return '项目操作失败，请稍后重试。'
    }
    const body = error.data
    if (!exactKeys(body, ['error']) || !isRecord(body.error)) {
        return '项目操作失败，请稍后重试。'
    }
    const detail = body.error
    if (!exactKeys(detail, ['code', 'message', 'request_id'])) {
        return '项目操作失败，请稍后重试。'
    }
    if (
        detail.code === 'PROJECT_NAME_CONFLICT' &&
        detail.message === 'A project with this name already exists' &&
        typeof detail.request_id === 'string' &&
        UUID.test(detail.request_id)
    ) {
        return '项目名称已存在。'
    }
    return '项目操作失败，请稍后重试。'
}

export function createProjectsApi(client: BrowserHttpClient) {
    return {
        async list(): Promise<Project[]> {
            const response = await client.get('/projects')
            if (response.status !== 200) throw new ProjectContractError()
            return parseProjectList(response.data)
        },
        async create(command: ProjectCreate): Promise<Project> {
            const canonical = validatedCreate(command)
            const response = await client.post('/projects', canonical)
            if (response.status !== 201) throw new ProjectContractError()
            return parseProject(response.data)
        },
    }
}

export const projectsApi = createProjectsApi(apiClient)
