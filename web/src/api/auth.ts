import { ApiContractError, apiClient, type BrowserHttpClient } from './http'

const USERNAME = /^[a-z][a-z0-9._-]{2,31}$/
const MAX_PASSWORD_UTF8_BYTES = 512

export type UserRole = 'OWNER' | 'STAFF'

export interface AuthUser {
    username: string
    display_name: string
    role: UserRole
    must_change_password: boolean
}

export interface LoginCredentials {
    username: string
    password: string
}

export interface PasswordChangeCommand {
    current_password: string
    new_password: string
}

export class AuthContractError extends ApiContractError {
    constructor() {
        super('Invalid authentication data')
        this.name = 'AuthContractError'
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(
    value: Record<string, unknown>,
    expected: string[],
): boolean {
    const actual = Object.keys(value).sort()
    const sortedExpected = [...expected].sort()
    return (
        actual.length === sortedExpected.length &&
        actual.every((key, index) => key === sortedExpected[index])
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

function validPassword(value: unknown): value is string {
    if (typeof value !== 'string' || hasUnsafeText(value)) return false
    const codepoints = [...value].length
    return (
        codepoints >= 12 &&
        codepoints <= 128 &&
        new TextEncoder().encode(value).byteLength <= MAX_PASSWORD_UTF8_BYTES
    )
}

function validateCredentials(value: LoginCredentials): void {
    if (!USERNAME.test(value.username) || !validPassword(value.password)) {
        throw new AuthContractError()
    }
}

function validatePasswordChange(value: PasswordChangeCommand): void {
    if (
        !validPassword(value.current_password) ||
        !validPassword(value.new_password)
    ) {
        throw new AuthContractError()
    }
}

function parseUser(data: unknown): AuthUser {
    if (
        !isRecord(data) ||
        !hasExactKeys(data, [
            'display_name',
            'must_change_password',
            'role',
            'username',
        ]) ||
        typeof data.username !== 'string' ||
        !USERNAME.test(data.username) ||
        typeof data.display_name !== 'string' ||
        data.display_name !== data.display_name.trim() ||
        [...data.display_name].length > 255 ||
        hasUnsafeText(data.display_name) ||
        typeof data.must_change_password !== 'boolean' ||
        (data.role !== 'OWNER' && data.role !== 'STAFF')
    ) {
        throw new AuthContractError()
    }
    return {
        username: data.username,
        display_name: data.display_name,
        role: data.role,
        must_change_password: data.must_change_password,
    }
}

export function createAuthApi(client: BrowserHttpClient) {
    const prepareCsrf = async (): Promise<void> => {
        const response = await client.get('/auth/csrf')
        if (response.status !== 204 || response.data !== null) {
            throw new AuthContractError()
        }
    }

    return {
        prepareCsrf,
        async login(credentials: LoginCredentials): Promise<void> {
            validateCredentials(credentials)
            await prepareCsrf()
            const response = await client.post('/auth/login', {
                username: credentials.username,
                password: credentials.password,
            })
            if (response.status !== 204 || response.data !== null) {
                throw new AuthContractError()
            }
        },
        async changePassword(command: PasswordChangeCommand): Promise<void> {
            validatePasswordChange(command)
            const response = await client.post('/auth/password/change', {
                current_password: command.current_password,
                new_password: command.new_password,
            })
            if (response.status !== 204 || response.data !== null) {
                throw new AuthContractError()
            }
        },
        async me(): Promise<AuthUser> {
            const response = await client.get('/auth/me')
            if (response.status !== 200) throw new AuthContractError()
            return parseUser(response.data)
        },
        async logout(): Promise<void> {
            const response = await client.post('/auth/logout')
            if (response.status !== 204 || response.data !== null) {
                throw new AuthContractError()
            }
        },
    }
}

export const authApi = createAuthApi(apiClient)
