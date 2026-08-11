import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { authApi } from '../src/api/auth'
import { HttpClientError } from '../src/api/http'
import { useAuthStore } from '../src/stores/auth'

vi.mock('../src/api/auth', () => ({
    authApi: {
        changePassword: vi.fn(),
        login: vi.fn(),
        logout: vi.fn(),
        me: vi.fn(),
        prepareCsrf: vi.fn(),
    },
}))

const mockedAuth = vi.mocked(authApi)
const owner = {
    username: 'owner',
    display_name: 'Owner',
    role: 'OWNER' as const,
    must_change_password: false,
}

function rejected(status: number): HttpClientError {
    return new HttpClientError(status, { detail: 'private traceback sentinel' })
}

beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    localStorage.clear()
    sessionStorage.clear()
})

describe('local auth store lifecycle', () => {
    test('single-flights bootstrap and trusts only the exact /me result', async () => {
        let release!: () => void
        mockedAuth.me.mockImplementation(
            () => new Promise((resolve) => (release = () => resolve(owner))),
        )
        const store = useAuthStore()
        const first = store.bootstrap()
        const second = store.bootstrap()
        expect(mockedAuth.me).toHaveBeenCalledTimes(1)
        release()
        await Promise.all([first, second])
        expect(store.user).toEqual(owner)
        expect(store.isAuthenticated).toBe(true)
    })

    test('logs in, reloads server identity, and never touches browser storage', async () => {
        mockedAuth.login.mockResolvedValue()
        mockedAuth.me.mockResolvedValue(owner)
        const getItem = vi.spyOn(Storage.prototype, 'getItem')
        const setItem = vi.spyOn(Storage.prototype, 'setItem')
        const store = useAuthStore()

        await store.login({
            username: 'owner',
            password: 'correct horse battery staple',
        })

        expect(mockedAuth.login).toHaveBeenCalledWith({
            username: 'owner',
            password: 'correct horse battery staple',
        })
        expect(mockedAuth.me).toHaveBeenCalledTimes(1)
        expect(store.user).toEqual(owner)
        expect(getItem).not.toHaveBeenCalled()
        expect(setItem).not.toHaveBeenCalled()
    })

    test('changes the password, reloads /me, and clears the forced-change flag', async () => {
        mockedAuth.me
            .mockResolvedValueOnce({ ...owner, must_change_password: true })
            .mockResolvedValueOnce(owner)
        mockedAuth.changePassword.mockResolvedValue()
        const store = useAuthStore()
        await store.bootstrap()

        await store.changePassword({
            current_password: 'temporary local password',
            new_password: 'replacement local password',
        })

        expect(mockedAuth.changePassword).toHaveBeenCalledTimes(1)
        expect(store.user?.must_change_password).toBe(false)
    })

    test('single-flights a server-authoritative refresh', async () => {
        mockedAuth.me.mockResolvedValueOnce(owner)
        const store = useAuthStore()
        await store.bootstrap()
        let release!: () => void
        mockedAuth.me.mockImplementationOnce(
            () =>
                new Promise(
                    (resolve) =>
                        (release = () =>
                            resolve({ ...owner, role: 'STAFF' as const })),
                ),
        )
        const first = store.refresh()
        const second = store.refresh()
        await Promise.resolve()
        expect(mockedAuth.me).toHaveBeenCalledTimes(2)
        release()
        await Promise.all([first, second])
        expect(store.user?.role).toBe('STAFF')
    })

    test('treats /me 401 as anonymous and does not expose raw errors', async () => {
        mockedAuth.me.mockRejectedValue(rejected(401))
        const store = useAuthStore()
        await store.bootstrap()
        expect(store.user).toBeNull()
        expect(store.errorMessage).toBe('')
    })

    test('logout clears local state even when remote revocation fails', async () => {
        mockedAuth.me.mockResolvedValue(owner)
        mockedAuth.logout.mockRejectedValue(rejected(503))
        const store = useAuthStore()
        await store.bootstrap()
        await store.logout()
        expect(store.user).toBeNull()
        expect(store.errorMessage).toBe('退出请求未完成，本机已退出。')
        expect(store.errorMessage).not.toContain('sentinel')
    })
})
