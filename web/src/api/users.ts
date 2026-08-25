import {
    HttpClientError,
    apiClient,
    formatRequestError,
    type BrowserHttpClient,
} from './http'

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const USERNAME = /^[a-z][a-z0-9._-]{2,31}$/
const MAX_USERS_PER_RESPONSE = 1000

export interface UserProject {
    id: string
    name: string
}

export interface OwnerUser {
    id: string
    username: string
    display_name: string
    role: 'OWNER' | 'STAFF'
    status: 'ACTIVE' | 'DISABLED'
    last_login_at: string | null
    projects: UserProject[]
}

export interface StaffCreate {
    username: string
    display_name: string
    project_ids: string[]
}

export interface StaffCreateResult {
    user: OwnerUser
    temporary_password: string
}

export interface PasswordResetResult {
    temporary_password: string
}

export interface StaffUpdate {
    display_name?: string
    status?: 'ACTIVE' | 'DISABLED'
}

export interface UsersApi {
    list(): Promise<OwnerUser[]>
    create(command: StaffCreate): Promise<StaffCreateResult>
    resetPassword(userId: string): Promise<PasswordResetResult>
    update(userId: string, command: StaffUpdate): Promise<OwnerUser>
    replaceProjects(userId: string, projectIds: string[]): Promise<OwnerUser>
}

export class UserContractError extends Error {
    constructor() {
        super('Invalid user data')
        this.name = 'UserContractError'
    }
}

function record(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasRequiredKeys(
    value: Record<string, unknown>,
    required: readonly string[],
): boolean {
    return required.every((key) => key in value)
}

function text(value: unknown, max = 255): value is string {
    if (typeof value !== 'string' || value.length < 1 || value.length > max)
        return false
    return [...value].every((character) => {
        const code = character.charCodeAt(0)
        return code > 31 && (code < 127 || code > 159)
    })
}

function username(value: unknown): value is string {
    return typeof value === 'string' && USERNAME.test(value)
}

function temporaryPassword(value: unknown): value is string {
    return (
        typeof value === 'string' &&
        [...value].length >= 12 &&
        [...value].length <= 128 &&
        new TextEncoder().encode(value).length <= 512 &&
        [...value].every((character) => {
            const code = character.codePointAt(0) ?? 0
            return code > 31 && code !== 127 && (code < 128 || code > 159)
        })
    )
}

function project(value: unknown): UserProject {
    if (
        !record(value) ||
        !hasRequiredKeys(value, ['id', 'name']) ||
        typeof value.id !== 'string' ||
        !UUID.test(value.id) ||
        !text(value.name)
    )
        throw new UserContractError()
    return { id: value.id, name: value.name }
}

function user(value: unknown): OwnerUser {
    if (
        !record(value) ||
        !hasRequiredKeys(value, [
            'display_name',
            'id',
            'last_login_at',
            'projects',
            'role',
            'status',
            'username',
        ])
    )
        throw new UserContractError()
    if (
        typeof value.id !== 'string' ||
        !UUID.test(value.id) ||
        !username(value.username) ||
        !text(value.display_name) ||
        (value.role !== 'OWNER' && value.role !== 'STAFF') ||
        (value.status !== 'ACTIVE' && value.status !== 'DISABLED') ||
        (value.last_login_at !== null &&
            (typeof value.last_login_at !== 'string' ||
                Number.isNaN(Date.parse(value.last_login_at)))) ||
        !Array.isArray(value.projects)
    )
        throw new UserContractError()
    return {
        id: value.id,
        username: value.username,
        display_name: value.display_name,
        role: value.role,
        status: value.status,
        last_login_at: value.last_login_at,
        projects: value.projects.map(project),
    }
}

function ids(value: unknown): string[] {
    if (
        !Array.isArray(value) ||
        value.length > MAX_USERS_PER_RESPONSE ||
        value.some((id) => typeof id !== 'string' || !UUID.test(id)) ||
        new Set(value).size !== value.length
    )
        throw new UserContractError()
    return value
}

function createCommand(value: StaffCreate): StaffCreate {
    if (
        !record(value) ||
        !hasRequiredKeys(value, ['display_name', 'project_ids', 'username']) ||
        !username(value.username) ||
        !text(value.display_name)
    )
        throw new UserContractError()
    return {
        username: value.username,
        display_name: value.display_name,
        project_ids: ids(value.project_ids),
    }
}

function createResult(value: unknown): StaffCreateResult {
    if (
        !record(value) ||
        !hasRequiredKeys(value, ['temporary_password', 'user'])
    )
        throw new UserContractError()
    if (!temporaryPassword(value.temporary_password))
        throw new UserContractError()
    return {
        user: user(value.user),
        temporary_password: value.temporary_password,
    }
}

function passwordResetResult(value: unknown): PasswordResetResult {
    if (!record(value) || !hasRequiredKeys(value, ['temporary_password']))
        throw new UserContractError()
    if (!temporaryPassword(value.temporary_password))
        throw new UserContractError()
    return { temporary_password: value.temporary_password }
}

function update(value: StaffUpdate): StaffUpdate {
    if (!record(value) || Object.keys(value).length !== 1)
        throw new UserContractError()
    if ('display_name' in value && !text(value.display_name))
        throw new UserContractError()
    if (
        'status' in value &&
        value.status !== 'ACTIVE' &&
        value.status !== 'DISABLED'
    )
        throw new UserContractError()
    return value
}

export function userErrorMessage(error: unknown): string {
    if (error instanceof HttpClientError && error.status === 409)
        return '员工状态与现有记录冲突，请刷新后重试。'
    return formatRequestError(
        '员工操作暂时无法完成',
        error,
        '员工操作暂时无法完成，请稍后重试。',
    )
}

export function createUsersApi(client: BrowserHttpClient): UsersApi {
    return {
        async list(): Promise<OwnerUser[]> {
            const response = await client.get('/owner/users')
            if (
                response.status !== 200 ||
                !Array.isArray(response.data) ||
                response.data.length > MAX_USERS_PER_RESPONSE
            )
                throw new UserContractError()
            return response.data.map(user)
        },
        async create(command: StaffCreate): Promise<StaffCreateResult> {
            const response = await client.post(
                '/owner/users',
                createCommand(command),
            )
            if (response.status !== 201) throw new UserContractError()
            return createResult(response.data)
        },
        async resetPassword(userId: string): Promise<PasswordResetResult> {
            if (!UUID.test(userId)) throw new UserContractError()
            const response = await client.post(
                `/owner/users/${userId}/password-reset`,
            )
            if (response.status !== 200) throw new UserContractError()
            return passwordResetResult(response.data)
        },
        async update(userId: string, command: StaffUpdate): Promise<OwnerUser> {
            if (!UUID.test(userId)) throw new UserContractError()
            const response = await client.patch(
                `/owner/users/${userId}`,
                update(command),
            )
            if (response.status !== 200) throw new UserContractError()
            return user(response.data)
        },
        async replaceProjects(
            userId: string,
            projectIds: string[],
        ): Promise<OwnerUser> {
            if (!UUID.test(userId)) throw new UserContractError()
            const response = await client.put(
                `/owner/users/${userId}/projects`,
                { project_ids: ids(projectIds) },
            )
            if (response.status !== 200) throw new UserContractError()
            return user(response.data)
        },
    }
}

export const usersApi = createUsersApi(apiClient)
