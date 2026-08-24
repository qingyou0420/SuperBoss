import { beforeEach, describe, expect, test, vi } from 'vitest'

import {
    AuthContractError,
    createAuthApi,
    type AuthUser,
} from '../src/api/auth'
import type { BrowserHttpClient, BrowserHttpResponse } from '../src/api/http'

const owner: AuthUser = {
    username: 'owner',
    display_name: 'Owner',
    role: 'OWNER',
    must_change_password: false,
}

function response<T>(status: number, data: T): BrowserHttpResponse<T> {
    return Object.freeze({ status, data })
}

function client(): BrowserHttpClient {
    return {
        delete: vi.fn(),
        get: vi.fn(),
        patch: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
    }
}

beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    sessionStorage.clear()
})

describe('local auth API', () => {
    test('prepares CSRF before sending the exact local login body', async () => {
        const http = client()
        vi.mocked(http.get).mockResolvedValue(response(204, null))
        vi.mocked(http.post).mockResolvedValue(response(204, null))
        const api = createAuthApi(http)

        await api.login({
            username: 'owner',
            password: 'correct horse battery staple',
        })

        expect(http.get).toHaveBeenCalledWith('/auth/csrf')
        expect(http.post).toHaveBeenCalledWith('/auth/login', {
            username: 'owner',
            password: 'correct horse battery staple',
        })
        expect(vi.mocked(http.get).mock.invocationCallOrder[0]).toBeLessThan(
            vi.mocked(http.post).mock.invocationCallOrder[0],
        )
    })

    test('sends exact password-change data and accepts only empty 204', async () => {
        const http = client()
        vi.mocked(http.post).mockResolvedValue(response(204, null))
        const api = createAuthApi(http)

        await api.changePassword({
            current_password: 'temporary local password',
            new_password: 'replacement local password',
        })

        expect(http.post).toHaveBeenCalledWith('/auth/password/change', {
            current_password: 'temporary local password',
            new_password: 'replacement local password',
        })
    })

    test('decodes the exact local identity shape', async () => {
        const http = client()
        vi.mocked(http.get).mockResolvedValue(response(200, owner))

        await expect(createAuthApi(http).me()).resolves.toEqual(owner)
        expect(http.get).toHaveBeenCalledWith('/auth/me')
    })

    test('accepts extra /me fields without using them', async () => {
        const http = client()
        vi.mocked(http.get).mockResolvedValue(
            response(200, { ...owner, userid: 'legacy-wecom' }),
        )
        await expect(createAuthApi(http).me()).resolves.toEqual(owner)
    })

    test.each([
        { ...owner, must_change_password: 'false' },
        { username: 'owner', role: 'OWNER' },
    ])('rejects malformed /me data %#', async (body) => {
        const http = client()
        vi.mocked(http.get).mockResolvedValue(response(200, body))
        await expect(createAuthApi(http).me()).rejects.toBeInstanceOf(
            AuthContractError,
        )
    })

    test('sends short login passwords to the server', async () => {
        const http = client()
        vi.mocked(http.get).mockResolvedValue(response(204, null))
        vi.mocked(http.post).mockResolvedValue(response(204, null))
        await createAuthApi(http).login({
            username: 'owner',
            password: 'short',
        })
        expect(http.post).toHaveBeenCalled()
    })

    test.each([
        { username: 'Owner', password: 'correct horse battery staple' },
        { username: 'ab', password: 'correct horse battery staple' },
        { username: 'owner', password: 'line\nbreak password' },
    ])(
        'rejects invalid credentials before CSRF or login I/O %#',
        async (input) => {
            const http = client()
            const api = createAuthApi(http)
            await expect(api.login(input)).rejects.toBeInstanceOf(
                AuthContractError,
            )
            expect(http.get).not.toHaveBeenCalled()
            expect(http.post).not.toHaveBeenCalled()
        },
    )

    test.each([
        ['csrf', 200, null],
        ['login', 200, null],
        ['login-body', 204, { token: 'secret' }],
        ['change', 200, null],
        ['logout', 200, null],
    ] as const)(
        'rejects wrong success contract for %s',
        async (kind, status, data) => {
            const http = client()
            vi.mocked(http.get).mockResolvedValue(response(status, data))
            vi.mocked(http.post).mockResolvedValue(response(status, data))
            const api = createAuthApi(http)
            const operation =
                kind === 'csrf'
                    ? api.prepareCsrf()
                    : kind === 'change'
                      ? api.changePassword({
                            current_password: 'temporary local password',
                            new_password: 'replacement local password',
                        })
                      : kind === 'logout'
                        ? api.logout()
                        : api.login({
                              username: 'owner',
                              password: 'correct horse battery staple',
                          })
            await expect(operation).rejects.toBeInstanceOf(AuthContractError)
        },
    )
})
