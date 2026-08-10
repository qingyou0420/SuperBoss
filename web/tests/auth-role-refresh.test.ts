import { render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, type Router } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { authApi } from '../src/api/auth'
import * as httpModule from '../src/api/http'
import { projectsApi } from '../src/api/projects'
import { createAppRouter } from '../src/app/router'
import { useAuthStore } from '../src/stores/auth'

vi.mock('../src/api/auth', () => ({
    authApi: {
        completeWeCom: vi.fn(),
        logout: vi.fn(),
        me: vi.fn(),
        startWeCom: vi.fn(),
    },
    parseOAuthCallback: vi.fn(),
}))

vi.mock('../src/api/projects', () => ({
    projectsApi: {
        create: vi.fn(),
        list: vi.fn(),
    },
    projectErrorMessage: vi.fn(() => 'safe project error'),
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

beforeEach(() => {
    vi.clearAllMocks()
    mockedProjects.list.mockResolvedValue([])
})

describe('server-authoritative role refresh routing', () => {
    test('OWNER to STAFF refresh removes owner UI and redirects the mounted protected route', async () => {
        mockedAuth.me
            .mockResolvedValueOnce({ userid: 'person-1', role: 'OWNER' })
            .mockResolvedValueOnce({ userid: 'person-1', role: 'STAFF' })
        const router = await renderAt('/owner/projects')
        expect(
            await screen.findByRole('button', { name: '创建项目' }),
        ).toBeInTheDocument()

        await sessionRefreshedHandler()()

        expect(useAuthStore().user).toEqual({
            userid: 'person-1',
            role: 'STAFF',
        })
        await waitFor(() =>
            expect(router.currentRoute.value.name).toBe('forbidden'),
        )
        expect(
            screen.queryByRole('button', { name: '创建项目' }),
        ).not.toBeInTheDocument()
    })

    test('STAFF to OWNER refresh updates the store and leaves forbidden for owner UI', async () => {
        mockedAuth.me
            .mockResolvedValueOnce({ userid: 'person-1', role: 'STAFF' })
            .mockResolvedValueOnce({ userid: 'person-1', role: 'OWNER' })
        const router = await renderAt('/owner/projects')
        expect(router.currentRoute.value.name).toBe('forbidden')

        await sessionRefreshedHandler()()

        expect(useAuthStore().user).toEqual({
            userid: 'person-1',
            role: 'OWNER',
        })
        await waitFor(() =>
            expect(router.currentRoute.value.name).toBe('owner-home'),
        )
        expect(screen.getByText('person-1')).toBeInTheDocument()
    })
})
