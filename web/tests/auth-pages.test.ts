import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import type { AxiosAdapter, AxiosResponse } from 'axios'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import * as authModule from '../src/api/auth'
import {
    authApi,
    createAuthApi,
    navigateToAuthorization,
} from '../src/api/auth'
import { createHttpClient } from '../src/api/http'
import AppLayout from '../src/layouts/AppLayout.vue'
import AuthCallbackPage from '../src/pages/AuthCallbackPage.vue'
import LoginPage from '../src/pages/LoginPage.vue'
import { useAuthStore } from '../src/stores/auth'

const owner = { userid: 'owner-1', role: 'OWNER' as const }
const safeStart = {
    state: 'state-1',
    authorization_url:
        'https://open.weixin.qq.com/connect/oauth2/authorize?state=state-1#wechat_redirect',
}

async function createValidatedStart() {
    const adapter: AxiosAdapter = async (config) =>
        ({
            config,
            data: safeStart,
            headers: {},
            status: 200,
            statusText: '200',
        }) as AxiosResponse
    return createAuthApi(createHttpClient({ adapter })).startWeCom()
}

function callbackRouter(path: string): Router {
    const Empty = defineComponent({ template: '<div>destination</div>' })
    const router = createRouter({
        history: createMemoryHistory(),
        routes: [
            { path: '/auth/callback', component: AuthCallbackPage },
            { path: '/owner', component: Empty },
            { path: '/owner/projects', component: Empty },
        ],
    })
    void router.push(path)
    return router
}

async function renderCallback(path: string) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = callbackRouter(path)
    await router.isReady()
    const rendered = render(AuthCallbackPage, {
        global: { plugins: [pinia, router, ElementPlus] },
    })
    return { ...rendered, router }
}

function renderLogin(navigate: (url: string) => void) {
    const pinia = createPinia()
    setActivePinia(pinia)
    return render(LoginPage, {
        props: { navigate },
        global: { plugins: [pinia, ElementPlus] },
    })
}

async function renderAuthenticatedLayout() {
    const pinia = createPinia()
    setActivePinia(pinia)
    vi.spyOn(authApi, 'me').mockResolvedValue(owner)
    const store = useAuthStore()
    await store.bootstrap(true)
    const Empty = defineComponent({ template: '<div>owner content</div>' })
    const RouterHost = defineComponent({ template: '<router-view />' })
    const router = createRouter({
        history: createMemoryHistory(),
        routes: [
            {
                path: '/owner',
                component: AppLayout,
                children: [{ path: '', component: Empty }],
            },
            { path: '/login', component: LoginPage },
        ],
    })
    await router.push('/owner')
    await router.isReady()
    render(RouterHost, {
        global: { plugins: [pinia, router, ElementPlus] },
    })
    return { router, store }
}

beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    sessionStorage.clear()
})

describe('LoginPage', () => {
    test('starts OAuth only after the user acts and navigates to the validated provider URL', async () => {
        const start = vi
            .spyOn(authApi, 'startWeCom')
            .mockResolvedValue(safeStart)
        const validatedNavigation = vi.spyOn(
            authModule,
            'navigateToAuthorization',
        )
        const navigate = vi.fn()
        const getItem = vi.spyOn(Storage.prototype, 'getItem')
        const setItem = vi.spyOn(Storage.prototype, 'setItem')
        renderLogin(navigate)

        expect(start).not.toHaveBeenCalled()
        expect(navigate).not.toHaveBeenCalled()
        await fireEvent.click(
            screen.getByRole('button', { name: '企业微信登录' }),
        )

        await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
        expect(validatedNavigation).toHaveBeenCalledWith(
            safeStart.authorization_url,
            navigate,
        )
        expect(navigate).toHaveBeenCalledWith(safeStart.authorization_url)
        expect(getItem).not.toHaveBeenCalled()
        expect(setItem).not.toHaveBeenCalled()
        expect(document.body.textContent).not.toMatch(
            /state-1|authorization_url/i,
        )
    })

    test('the navigation helper accepts an Auth API-validated URL and rejects unvalidated targets', async () => {
        const navigate = vi.fn()
        const validated = await createValidatedStart()
        navigateToAuthorization(validated.authorization_url, navigate)
        expect(navigate).toHaveBeenCalledWith(safeStart.authorization_url)

        for (const unsafe of [
            'javascript:alert(1)',
            'http://open.weixin.qq.com/authorize?state=x',
            'https://user:pass@example.com/authorize?state=x',
            '//evil.example/authorize',
            'https://open.weixin.qq.com/authorize',
            'https://open.weixin.qq.com/authorize?state=one&state=two',
        ]) {
            expect(() => navigateToAuthorization(unsafe, navigate)).toThrow()
        }
        expect(navigate).toHaveBeenCalledTimes(1)
    })

    test('shows fixed safe text on start failure without navigating or leaking details', async () => {
        vi.spyOn(authApi, 'startWeCom').mockRejectedValue(
            new Error('provider client_secret=sentinel traceback'),
        )
        const navigate = vi.fn()
        renderLogin(navigate)

        await fireEvent.click(
            screen.getByRole('button', { name: '企业微信登录' }),
        )

        expect(
            await screen.findByText('登录暂时不可用，请稍后重试。'),
        ).toBeInTheDocument()
        expect(
            screen.queryByText(/sentinel|client_secret|traceback/i),
        ).not.toBeInTheDocument()
        expect(navigate).not.toHaveBeenCalled()
    })

    test('shows a prior logout warning and clears it when a new login starts successfully', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        const store = useAuthStore()
        store.errorMessage = '退出请求未完成，本机已退出。'
        vi.spyOn(authApi, 'startWeCom').mockResolvedValue(safeStart)
        const navigate = vi.fn()
        render(LoginPage, {
            props: { navigate },
            global: { plugins: [pinia, ElementPlus] },
        })

        expect(
            screen.getByText('退出请求未完成，本机已退出。'),
        ).toBeInTheDocument()
        await fireEvent.click(
            screen.getByRole('button', { name: '企业微信登录' }),
        )

        await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1))
        expect(
            screen.queryByText('退出请求未完成，本机已退出。'),
        ).not.toBeInTheDocument()
        expect(store.errorMessage).toBe('')
    })
})

describe('AppLayout logout handoff', () => {
    test('preserves a safe warning on LoginPage when remote logout fails while clearing the local user', async () => {
        vi.spyOn(authApi, 'logout').mockRejectedValue(
            new Error('cookie revoke failed postgres password=sentinel'),
        )
        const { router, store } = await renderAuthenticatedLayout()

        await fireEvent.click(screen.getByRole('button', { name: '退出登录' }))

        expect(
            await screen.findByText('退出请求未完成，本机已退出。'),
        ).toBeInTheDocument()
        expect(router.currentRoute.value.path).toBe('/login')
        expect(store.user).toBeNull()
        expect(document.body.textContent).not.toMatch(
            /sentinel|postgres|password/i,
        )
    })

    test('does not carry a stale logout warning after successful remote logout', async () => {
        vi.spyOn(authApi, 'logout').mockResolvedValue()
        const { router, store } = await renderAuthenticatedLayout()
        store.errorMessage = '退出请求未完成，本机已退出。'

        await fireEvent.click(screen.getByRole('button', { name: '退出登录' }))

        await waitFor(() =>
            expect(router.currentRoute.value.path).toBe('/login'),
        )
        expect(
            screen.queryByText('退出请求未完成，本机已退出。'),
        ).not.toBeInTheDocument()
        expect(store.errorMessage).toBe('')
    })
})

describe('AuthCallbackPage', () => {
    test('validates the query, completes the fixed callback, bootstraps /me, and uses only a safe internal redirect', async () => {
        const complete = vi.spyOn(authApi, 'completeWeCom').mockResolvedValue()
        const me = vi.spyOn(authApi, 'me').mockResolvedValue(owner)
        const parse = vi.spyOn(authModule, 'parseOAuthCallback')
        const getItem = vi.spyOn(Storage.prototype, 'getItem')
        const setItem = vi.spyOn(Storage.prototype, 'setItem')
        const { router } = await renderCallback(
            '/auth/callback?code=code_1&state=state-1&redirect=%2Fowner%2Fprojects',
        )

        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe('/owner/projects'),
        )

        expect(complete).toHaveBeenCalledWith({
            code: 'code_1',
            state: 'state-1',
        })
        expect(parse).toHaveBeenCalledTimes(1)
        expect(me).toHaveBeenCalledTimes(1)
        expect(useAuthStore().user).toEqual(owner)
        expect(getItem).not.toHaveBeenCalled()
        expect(setItem).not.toHaveBeenCalled()
        expect(document.body.textContent).not.toContain('code_1')
        expect(document.body.textContent).not.toContain('state-1')
    })

    test('falls back to OWNER home instead of following a cross-origin redirect', async () => {
        vi.spyOn(authApi, 'completeWeCom').mockResolvedValue()
        vi.spyOn(authApi, 'me').mockResolvedValue(owner)
        const { router } = await renderCallback(
            '/auth/callback?code=code_1&state=state-1&redirect=%2F%2Fevil.example',
        )

        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe('/owner'),
        )
    })

    test.each([
        '/auth/callback?state=state-1',
        '/auth/callback?code=code_1',
        '/auth/callback?code=one&code=two&state=state-1',
        '/auth/callback?code=code_1&state=one&state=two',
        '/auth/callback?code=code_1&state=state-1&redirect=%2Fowner&redirect=%2Fowner%2Fprojects',
        '/auth/callback?code=%0D%0Aevil&state=state-1',
    ])(
        'rejects invalid callback parameters before any callback request: %s',
        async (path) => {
            const complete = vi
                .spyOn(authApi, 'completeWeCom')
                .mockResolvedValue()
            const parse = vi.spyOn(authModule, 'parseOAuthCallback')
            await renderCallback(path)

            expect(
                await screen.findByText('登录回调无效，请重新登录。'),
            ).toBeInTheDocument()
            expect(complete).not.toHaveBeenCalled()
            expect(parse).toHaveBeenCalledTimes(1)
            expect(document.body.textContent).not.toMatch(/code_1|state-1|evil/)
        },
    )

    test('keeps provider and transport details out of callback failure UI and storage', async () => {
        vi.spyOn(authApi, 'completeWeCom').mockRejectedValue(
            new Error('WeCom provider access_token=sentinel traceback'),
        )
        const getItem = vi.spyOn(Storage.prototype, 'getItem')
        const setItem = vi.spyOn(Storage.prototype, 'setItem')
        const { router } = await renderCallback(
            '/auth/callback?code=code_1&state=state-1',
        )

        expect(
            await screen.findByText('登录未完成，请重新登录。'),
        ).toBeInTheDocument()
        expect(router.currentRoute.value.path).toBe('/auth/callback')
        expect(document.body.textContent).not.toMatch(
            /sentinel|access_token|traceback|code_1|state-1/i,
        )
        expect(getItem).not.toHaveBeenCalled()
        expect(setItem).not.toHaveBeenCalled()
    })
})
