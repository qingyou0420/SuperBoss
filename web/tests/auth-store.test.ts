import { AxiosError, type AxiosResponse } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { authApi } from '../src/api/auth'
import { useAuthStore } from '../src/stores/auth'

vi.mock('../src/api/auth', () => ({
    authApi: {
        me: vi.fn(),
        logout: vi.fn(),
    },
}))

const mockedAuth = vi.mocked(authApi)

function rejected(status: number): AxiosError {
    return new AxiosError(
        'unsafe detail',
        'ERR_BAD_REQUEST',
        undefined,
        undefined,
        {
            config: {},
            data: { detail: 'provider traceback sentinel' },
            headers: {},
            status,
            statusText: String(status),
        } as AxiosResponse,
    )
}

beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
})

describe('auth store lifecycle', () => {
    test('single-flights bootstrap and trusts only the server /me result', async () => {
        let release!: () => void
        mockedAuth.me.mockImplementation(
            () =>
                new Promise(
                    (resolve) =>
                        (release = () =>
                            resolve({ userid: 'owner-1', role: 'OWNER' })),
                ),
        )
        const store = useAuthStore()

        const first = store.bootstrap()
        const second = store.bootstrap()
        expect(mockedAuth.me).toHaveBeenCalledTimes(1)
        release()
        await Promise.all([first, second])

        expect(store.user).toEqual({ userid: 'owner-1', role: 'OWNER' })
        expect(store.isAuthenticated).toBe(true)
        expect(store.isBootstrapped).toBe(true)
    })

    test('treats /me 401 as anonymous and ignores browser token storage', async () => {
        localStorage.setItem('access_token', 'forged-owner')
        sessionStorage.setItem('refresh_token', 'forged-refresh')
        const getItem = vi.spyOn(Storage.prototype, 'getItem')
        const setItem = vi.spyOn(Storage.prototype, 'setItem')
        mockedAuth.me.mockRejectedValue(rejected(401))
        const store = useAuthStore()

        await store.bootstrap()

        expect(store.user).toBeNull()
        expect(store.isAuthenticated).toBe(false)
        expect(getItem).not.toHaveBeenCalled()
        expect(setItem).not.toHaveBeenCalled()
    })

    test('fails closed on malformed or non-auth bootstrap failures without exposing raw details', async () => {
        mockedAuth.me.mockRejectedValue(rejected(500))
        const store = useAuthStore()

        await expect(store.bootstrap()).resolves.toBeUndefined()

        expect(store.user).toBeNull()
        expect(store.isAuthenticated).toBe(false)
        expect(store.errorMessage).toBe('服务暂时不可用，请稍后重试。')
        expect(store.errorMessage).not.toContain('sentinel')
    })

    test('logout clears local auth state even when the remote logout fails', async () => {
        mockedAuth.me.mockResolvedValue({ userid: 'owner-1', role: 'OWNER' })
        mockedAuth.logout.mockRejectedValue(rejected(503))
        const store = useAuthStore()
        await store.bootstrap()

        await expect(store.logout()).resolves.toBeUndefined()

        expect(mockedAuth.logout).toHaveBeenCalledTimes(1)
        expect(store.user).toBeNull()
        expect(store.isAuthenticated).toBe(false)
        expect(store.errorMessage).toBe('退出请求未完成，本机已退出。')
    })
})
