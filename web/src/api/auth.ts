import { ApiContractError, apiClient, type BrowserHttpClient } from './http'

const OAUTH_VALUE = /^[A-Za-z0-9._~-]{1,2048}$/

export type UserRole = 'OWNER' | 'STAFF'

export interface AuthUser {
    userid: string
    role: UserRole
}

export interface AuthStart {
    state: string
    authorization_url: string
}

export interface OAuthCallback {
    code: string
    state: string
    redirect?: string
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
    return (
        actual.length === expected.length &&
        actual.every((key, index) => key === expected[index])
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

function safeUserid(value: unknown): value is string {
    return (
        typeof value === 'string' &&
        !hasUnsafeText(value) &&
        [...value].length >= 1 &&
        [...value].length <= 255 &&
        value.trim() === value
    )
}

function validateProviderUrl(value: unknown, state?: string): string {
    if (
        typeof value !== 'string' ||
        value.length > 8192 ||
        hasUnsafeText(value)
    ) {
        throw new AuthContractError()
    }
    let parsed: URL
    try {
        parsed = new URL(value)
    } catch {
        throw new AuthContractError()
    }
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) {
        throw new AuthContractError()
    }
    if (state !== undefined) {
        const states = parsed.searchParams.getAll('state')
        if (states.length !== 1 || states[0] !== state)
            throw new AuthContractError()
    } else {
        const states = parsed.searchParams.getAll('state')
        if (states.length !== 1 || !OAUTH_VALUE.test(states[0])) {
            throw new AuthContractError()
        }
    }
    return parsed.href
}

function parseUser(data: unknown): AuthUser {
    if (!isRecord(data) || !hasExactKeys(data, ['role', 'userid'])) {
        throw new AuthContractError()
    }
    if (
        !safeUserid(data.userid) ||
        (data.role !== 'OWNER' && data.role !== 'STAFF')
    ) {
        throw new AuthContractError()
    }
    return { userid: data.userid, role: data.role }
}

function parseStart(data: unknown): AuthStart {
    if (
        !isRecord(data) ||
        !hasExactKeys(data, ['authorization_url', 'state'])
    ) {
        throw new AuthContractError()
    }
    if (typeof data.state !== 'string' || !OAUTH_VALUE.test(data.state)) {
        throw new AuthContractError()
    }
    return {
        state: data.state,
        authorization_url: validateProviderUrl(
            data.authorization_url,
            data.state,
        ),
    }
}

export function parseOAuthCallback(search: string): OAuthCallback {
    const query = new URLSearchParams(
        search.startsWith('?') ? search.slice(1) : search,
    )
    const allowed = new Set(['code', 'state', 'redirect'])
    if ([...query.keys()].some((key) => !allowed.has(key)))
        throw new AuthContractError()
    const codes = query.getAll('code')
    const states = query.getAll('state')
    const redirects = query.getAll('redirect')
    if (
        codes.length !== 1 ||
        states.length !== 1 ||
        redirects.length > 1 ||
        !OAUTH_VALUE.test(codes[0]) ||
        !OAUTH_VALUE.test(states[0])
    ) {
        throw new AuthContractError()
    }
    const result: OAuthCallback = { code: codes[0], state: states[0] }
    if (redirects.length === 1) {
        const redirect = redirects[0]
        if (!redirect || redirect.length > 2048 || hasUnsafeText(redirect)) {
            throw new AuthContractError()
        }
        result.redirect = redirect
    }
    return result
}

export function navigateToAuthorization(
    url: string,
    navigate: (target: string) => void,
): void {
    navigate(validateProviderUrl(url))
}

export function createAuthApi(client: BrowserHttpClient) {
    return {
        async startWeCom(): Promise<AuthStart> {
            const response = await client.get('/auth/wecom/start')
            if (response.status !== 200) throw new AuthContractError()
            return parseStart(response.data)
        },
        async completeWeCom(
            callback: Pick<OAuthCallback, 'code' | 'state'>,
        ): Promise<void> {
            if (
                !OAUTH_VALUE.test(callback.code) ||
                !OAUTH_VALUE.test(callback.state)
            ) {
                throw new AuthContractError()
            }
            const response = await client.get('/auth/wecom/callback', {
                params: { code: callback.code, state: callback.state },
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
