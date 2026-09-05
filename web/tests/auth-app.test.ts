import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { authApi } from '../src/api/auth'
import * as httpModule from '../src/api/http'
import { HttpClientError } from '../src/api/http'
import { projectsApi } from '../src/api/projects'
import { createAppRouter, safePostLoginPath } from '../src/app/router'
import LoginPage from '../src/pages/LoginPage.vue'
import PasswordChangePage from '../src/pages/PasswordChangePage.vue'
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

vi.mock('../src/api/agent', () => ({
    agentApi: {
        listConversations: vi.fn().mockResolvedValue([]),
        createConversation: vi.fn(),
        listMessages: vi.fn().mockResolvedValue([]),
        listCards: vi.fn().mockResolvedValue([]),
        send: vi.fn(),
        confirm: vi.fn(),
        revise: vi.fn(),
        reject: vi.fn(),
        listSoul: vi.fn().mockResolvedValue([]),
        writeSoul: vi.fn(),
        activateSoul: vi.fn(),
        previewSoul: vi.fn(),
        listMemories: vi.fn().mockResolvedValue([]),
        patchMemory: vi.fn(),
    },
    agentErrorMessage: () => '霜月暂时无法完成操作，请稍后重试。',
}))

vi.mock('../src/api/projects', () => ({
    projectsApi: {
        create: vi.fn(),
        get: vi.fn(),
        list: vi.fn(),
        replaceMilestones: vi.fn(),
        update: vi.fn(),
    },
    projectErrorMessage: vi.fn(() => 'safe project error'),
}))

vi.mock('../src/api/users', () => ({
    usersApi: {
        list: vi.fn().mockResolvedValue([]),
        create: vi.fn(),
        update: vi.fn(),
        replaceProjects: vi.fn(),
        resetPassword: vi.fn(),
    },
    userErrorMessage: () => '员工操作暂时无法完成，请稍后重试。',
}))

vi.mock('../src/api/http', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../src/api/http')>()
    return {
        ...actual,
        setAuthenticationLostHandler: vi.fn(),
        setSessionRefreshedHandler: vi.fn(),
    }
})

const mockedAuth = vi.mocked(authApi)
const mockedProjects = vi.mocked(projectsApi)
const owner = {
    username: 'owner',
    display_name: 'Owner',
    role: 'OWNER' as const,
    must_change_password: false,
}

function unauthorized(): HttpClientError {
    return new HttpClientError(401, { detail: 'Authentication required' })
}

function rejected(status: number): HttpClientError {
    return new HttpClientError(status, { detail: 'private traceback sentinel' })
}

type SessionRefreshedHandler = () => Promise<void>

function sessionRefreshedHandler(): SessionRefreshedHandler {
    const registration = (
        httpModule as typeof httpModule & {
            setSessionRefreshedHandler: (
                handler: SessionRefreshedHandler,
            ) => void
        }
    ).setSessionRefreshedHandler as ReturnType<typeof vi.fn>
    const handler = registration.mock.calls.at(-1)?.[0] as
        SessionRefreshedHandler | undefined
    expect(handler).toBeTypeOf('function')
    return handler as SessionRefreshedHandler
}

function pageRouter(path: string) {
    const Destination = defineComponent({ template: '<p>destination</p>' })
    const router = createRouter({
        history: createMemoryHistory(),
        routes: [
            { path: '/login', name: 'login', component: LoginPage },
            {
                path: '/password/change',
                name: 'password-change',
                component: PasswordChangePage,
            },
            { path: '/owner', component: Destination },
            { path: '/owner/projects', component: Destination },
        ],
    })
    void router.push(path)
    return router
}

async function renderPagesAt(path: string) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = pageRouter(path)
    await router.isReady()
    const Host = defineComponent({ template: '<router-view />' })
    render(Host, { global: { plugins: [pinia, router, ElementPlus] } })
    return router
}

async function renderAnonymous(target: string) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createAppRouter(createMemoryHistory())
    await router.push(target)
    await router.isReady()
    const Host = defineComponent({ template: '<router-view />' })
    render(Host, { global: { plugins: [pinia, router, ElementPlus] } })
    return router
}

async function renderAt(path: string): Promise<Router> {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createAppRouter(createMemoryHistory())
    await router.push(path)
    await router.isReady()
    const RouterHost = defineComponent({ template: '<router-view />' })
    render(RouterHost, {
        global: { plugins: [pinia, router, ElementPlus] },
    })
    return router
}

async function submitLogin(password = 'correct horse battery staple') {
    await fireEvent.update(screen.getByLabelText('用户名'), 'owner')
    await fireEvent.update(screen.getByLabelText('密码'), password)
    await fireEvent.click(screen.getByRole('button', { name: '登录' }))
}

beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    mockedProjects.list.mockResolvedValue([])
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

describe('local-auth route guards', () => {
    test('sends anonymous protected navigation to login with an internal return path', async () => {
        mockedAuth.me.mockRejectedValue(unauthorized())
        const router = createAppRouter(createMemoryHistory())
        await router.push('/owner/projects?view=all')
        expect(router.currentRoute.value.name).toBe('login')
        expect(router.currentRoute.value.query).toEqual({
            redirect: '/projects?view=all',
        })
    })

    test('forces password change ahead of role authorization', async () => {
        mockedAuth.me.mockResolvedValue({
            ...owner,
            must_change_password: true,
        })
        const router = createAppRouter(createMemoryHistory())
        await router.push('/owner/projects')
        expect(router.currentRoute.value.name).toBe('password-change')
        expect(router.currentRoute.value.query).toEqual({
            redirect: '/projects',
        })
        await router.push('/forbidden')
        expect(router.currentRoute.value.name).toBe('password-change')
    })

    test('keeps password change reachable and sends a completed OWNER away from it', async () => {
        mockedAuth.me.mockResolvedValue(owner)
        const router = createAppRouter(createMemoryHistory())
        await router.push('/password/change')
        expect(router.currentRoute.value.name).toBe('chat')
        expect(
            router.getRoutes().some((route) => route.path === '/auth/callback'),
        ).toBe(false)
    })

    test('an exhausted refresh clears protected navigation without another bootstrap', async () => {
        const registration = vi.spyOn(
            httpModule,
            'setAuthenticationLostHandler',
        )
        mockedAuth.me.mockResolvedValue(owner)
        const router = createAppRouter(createMemoryHistory())
        await router.push('/owner/projects')
        const handler = registration.mock.calls.at(-1)?.[0]
        await handler?.()
        expect(router.currentRoute.value.name).toBe('login')
        expect(mockedAuth.me).toHaveBeenCalledTimes(1)
    })

    test('accepts only same-origin business destinations', () => {
        expect(safePostLoginPath('/owner/projects?view=all')).toBe(
            '/owner/projects?view=all',
        )
        for (const unsafe of [
            '//evil.example/path',
            'https://evil.example/path',
            '/\\evil.example',
            '/%2e%2e//evil.example/path',
            '/auth/callback?code=secret',
            '/password/change',
            '/login',
            '',
        ]) {
            expect(safePostLoginPath(unsafe)).toBe('/projects')
        }
    })
})

describe('LoginPage', () => {
    test('submits local credentials once and restores a safe internal target', async () => {
        let release!: () => void
        mockedAuth.login.mockImplementation(
            () => new Promise((resolve) => (release = resolve)),
        )
        mockedAuth.me.mockResolvedValue(owner)
        const router = await renderPagesAt(
            '/login?redirect=%2Fowner%2Fprojects%3Fview%3Dall',
        )
        const username = screen.getByLabelText('用户名')
        const password = screen.getByLabelText('密码')
        expect(password).toHaveAttribute('type', 'password')
        expect(password).toHaveAttribute('autocomplete', 'current-password')
        await fireEvent.update(username, 'owner')
        await fireEvent.update(password, 'correct horse battery staple')
        await fireEvent.click(screen.getByRole('button', { name: '登录' }))
        await fireEvent.click(screen.getByRole('button', { name: '登录' }))

        expect(mockedAuth.login).toHaveBeenCalledTimes(1)
        expect(mockedAuth.login).toHaveBeenCalledWith({
            username: 'owner',
            password: 'correct horse battery staple',
        })
        expect(document.body.textContent).not.toContain(
            'correct horse battery staple',
        )
        expect(localStorage).toHaveLength(0)
        expect(sessionStorage).toHaveLength(0)
        release()
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe(
                '/owner/projects?view=all',
            ),
        )
    })

    test('routes a first-login account to password change before owner pages', async () => {
        mockedAuth.login.mockResolvedValue()
        mockedAuth.me.mockResolvedValue({
            ...owner,
            must_change_password: true,
        })
        const router = await renderPagesAt(
            '/login?redirect=%2Fowner%2Fprojects',
        )
        await fireEvent.update(screen.getByLabelText('用户名'), 'owner')
        await fireEvent.update(
            screen.getByLabelText('密码'),
            'temporary local password',
        )
        await fireEvent.click(screen.getByRole('button', { name: '登录' }))
        await waitFor(() =>
            expect(router.currentRoute.value.path).toBe('/password/change'),
        )
    })

    test('uses a fixed safe failure without password or transport details', async () => {
        mockedAuth.login.mockRejectedValue(
            new Error('password=sentinel database traceback'),
        )
        await renderPagesAt('/login')
        await fireEvent.update(screen.getByLabelText('用户名'), 'owner')
        await fireEvent.update(
            screen.getByLabelText('密码'),
            'correct horse battery staple',
        )
        await fireEvent.click(screen.getByRole('button', { name: '登录' }))
        expect(
            await screen.findByText('用户名或密码错误，请重试。'),
        ).toBeInTheDocument()
        expect(document.body.textContent).not.toMatch(/sentinel|traceback/i)
    })
})

describe('PasswordChangePage', () => {
    test('validates confirmation locally and replaces the forced-change session', async () => {
        mockedAuth.changePassword.mockResolvedValue()
        mockedAuth.me.mockResolvedValue(owner)
        const router = await renderPagesAt('/password/change')
        const inputs = [
            screen.getByLabelText('当前密码'),
            screen.getByLabelText('新密码', { exact: true }),
            screen.getByLabelText('确认新密码'),
        ]
        for (const input of inputs) {
            expect(input).toHaveAttribute('type', 'password')
        }
        expect(inputs[0]).toHaveAttribute('autocomplete', 'current-password')
        expect(inputs[1]).toHaveAttribute('autocomplete', 'new-password')
        expect(inputs[2]).toHaveAttribute('autocomplete', 'new-password')
        await fireEvent.update(inputs[0], 'temporary local password')
        await fireEvent.update(inputs[1], 'replacement local password')
        await fireEvent.update(inputs[2], 'different replacement password')
        await fireEvent.click(screen.getByRole('button', { name: '更新密码' }))
        expect(mockedAuth.changePassword).not.toHaveBeenCalled()
        expect(
            await screen.findByText('两次输入的新密码不一致。'),
        ).toBeInTheDocument()

        await fireEvent.update(inputs[2], 'replacement local password')
        await fireEvent.click(screen.getByRole('button', { name: '更新密码' }))
        await waitFor(() =>
            expect(router.currentRoute.value.path).toBe('/chat'),
        )
        expect(mockedAuth.changePassword).toHaveBeenCalledWith({
            current_password: 'temporary local password',
            new_password: 'replacement local password',
        })
        expect(document.body.textContent).not.toMatch(
            /temporary local password|replacement local password/,
        )
    })
})

describe('complete local-auth browser flow', () => {
    test('returns to the original protected path without OAuth state or browser token storage', async () => {
        mockedAuth.me
            .mockRejectedValueOnce(unauthorized())
            .mockResolvedValue(owner)
        mockedAuth.login.mockResolvedValue()
        const target = '/projects?view=all#acceptance'
        const router = await renderAnonymous(target)
        expect(router.currentRoute.value.name).toBe('login')

        await submitLogin()
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe(target),
        )

        expect(localStorage).toHaveLength(0)
        expect(sessionStorage).toHaveLength(0)
        expect(document.body.textContent).not.toMatch(
            /correct horse battery staple|oauth|wecom|code=|state=/i,
        )
    })

    test('falls back safely for a hostile login redirect', async () => {
        mockedAuth.me
            .mockRejectedValueOnce(unauthorized())
            .mockResolvedValue(owner)
        mockedAuth.login.mockResolvedValue()
        const router = await renderAnonymous(
            '/login?redirect=%2F%2Fevil.example%2Fpath',
        )
        await submitLogin()
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe('/projects'),
        )
    })

    test('completes the mandatory password change before restoring the target', async () => {
        mockedAuth.me
            .mockRejectedValueOnce(unauthorized())
            .mockResolvedValueOnce({ ...owner, must_change_password: true })
            .mockResolvedValueOnce(owner)
        mockedAuth.login.mockResolvedValue()
        mockedAuth.changePassword.mockResolvedValue()
        const router = await renderAnonymous('/projects')
        await submitLogin('temporary local password')
        await waitFor(() =>
            expect(router.currentRoute.value.name).toBe('password-change'),
        )

        const inputs = [
            screen.getByLabelText('当前密码'),
            screen.getByLabelText('新密码', { exact: true }),
            screen.getByLabelText('确认新密码'),
        ]
        await fireEvent.update(inputs[0], 'temporary local password')
        await fireEvent.update(inputs[1], 'replacement local password')
        await fireEvent.update(inputs[2], 'replacement local password')
        await fireEvent.click(screen.getByRole('button', { name: '更新密码' }))
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe('/projects'),
        )
        expect(document.body.textContent).not.toMatch(
            /temporary local password|replacement local password/,
        )
    })
})

describe('server-authoritative role refresh routing', () => {
    test('OWNER to STAFF refresh removes owner UI and redirects the mounted protected route', async () => {
        mockedAuth.me
            .mockResolvedValueOnce({
                username: 'person-1',
                display_name: 'Person',
                role: 'OWNER',
                must_change_password: false,
            })
            .mockResolvedValueOnce({
                username: 'person-1',
                display_name: 'Person',
                role: 'STAFF',
                must_change_password: false,
            })
        const router = await renderAt('/users')
        expect(
            await screen.findByRole('heading', { name: '账号管理' }),
        ).toBeInTheDocument()

        await sessionRefreshedHandler()()

        expect(useAuthStore().user).toEqual({
            username: 'person-1',
            display_name: 'Person',
            role: 'STAFF',
            must_change_password: false,
        })
        await waitFor(() =>
            expect(router.currentRoute.value.name).toBe('forbidden'),
        )
        expect(
            screen.queryByRole('heading', { name: '账号管理' }),
        ).not.toBeInTheDocument()
    })

    test('STAFF to OWNER refresh updates the store and leaves forbidden for owner UI', async () => {
        mockedAuth.me
            .mockResolvedValueOnce({
                username: 'person-1',
                display_name: 'Person',
                role: 'STAFF',
                must_change_password: false,
            })
            .mockResolvedValueOnce({
                username: 'person-1',
                display_name: 'Person',
                role: 'OWNER',
                must_change_password: false,
            })
        const router = await renderAt('/users')
        expect(router.currentRoute.value.name).toBe('forbidden')

        await sessionRefreshedHandler()()

        expect(useAuthStore().user).toEqual({
            username: 'person-1',
            display_name: 'Person',
            role: 'OWNER',
            must_change_password: false,
        })
        await waitFor(() => expect(router.currentRoute.value.name).toBe('chat'))
        expect(screen.getByText('Person')).toBeInTheDocument()
    })

    test('a refreshed forced-change flag removes mounted owner UI immediately', async () => {
        mockedAuth.me
            .mockResolvedValueOnce({
                username: 'owner',
                display_name: 'Owner',
                role: 'OWNER',
                must_change_password: false,
            })
            .mockResolvedValueOnce({
                username: 'owner',
                display_name: 'Owner',
                role: 'OWNER',
                must_change_password: true,
            })
        const router = await renderAt('/owner/projects')
        await sessionRefreshedHandler()()
        await waitFor(() =>
            expect(router.currentRoute.value.name).toBe('password-change'),
        )
        expect(
            screen.queryByRole('button', { name: '创建项目' }),
        ).not.toBeInTheDocument()
    })
})
