import { AxiosError, type AxiosResponse } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { authApi } from '../src/api/auth'
import * as httpModule from '../src/api/http'
import { createAppRouter, safePostLoginPath } from '../src/app/router'
import OwnerProjectsPage from '../src/pages/owner/ProjectsPage.vue'

vi.mock('../src/api/auth', () => ({
    authApi: {
        me: vi.fn(),
        logout: vi.fn(),
        startWeCom: vi.fn(),
        completeWeCom: vi.fn(),
    },
    parseOAuthCallback: vi.fn(),
}))

const mockedAuth = vi.mocked(authApi)

function unauthorized(): AxiosError {
    return new AxiosError('unsafe', 'ERR_BAD_REQUEST', undefined, undefined, {
        config: {},
        data: { detail: 'Authentication required' },
        headers: {},
        status: 401,
        statusText: '401',
    } as AxiosResponse)
}

beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    document.cookie = 'access_token=; Max-Age=0; Path=/'
})

describe('authoritative route guards', () => {
    test('waits for /me and sends an anonymous browser to login with an internal return path', async () => {
        mockedAuth.me.mockRejectedValue(unauthorized())
        const router = createAppRouter(createMemoryHistory())

        await router.push('/owner/projects?view=all')

        expect(mockedAuth.me).toHaveBeenCalledTimes(1)
        expect(router.currentRoute.value.name).toBe('login')
        expect(router.currentRoute.value.query).toEqual({
            redirect: '/owner/projects?view=all',
        })
    })

    test('does not infer login from readable cookies and does not require cookie visibility', async () => {
        document.cookie = 'access_token=forged-owner; Path=/'
        mockedAuth.me.mockRejectedValueOnce(unauthorized())
        const forged = createAppRouter(createMemoryHistory())
        await forged.push('/owner')
        expect(forged.currentRoute.value.name).toBe('login')

        document.cookie = 'access_token=; Max-Age=0; Path=/'
        setActivePinia(createPinia())
        mockedAuth.me.mockResolvedValueOnce({
            userid: 'owner-1',
            role: 'OWNER',
        })
        const httpOnly = createAppRouter(createMemoryHistory())
        await httpOnly.push('/owner')
        expect(httpOnly.currentRoute.value.name).toBe('owner-home')
    })

    test('allows OWNER routes and sends authenticated STAFF away from OWNER pages', async () => {
        mockedAuth.me.mockResolvedValueOnce({
            userid: 'owner-1',
            role: 'OWNER',
        })
        const owner = createAppRouter(createMemoryHistory())
        await owner.push('/owner/projects')
        expect(owner.currentRoute.value.name).toBe('owner-projects')
        const configured =
            owner.currentRoute.value.matched.at(-1)?.components?.default
        const resolved =
            typeof configured === 'function'
                ? await (configured as () => unknown)()
                : configured
        expect(
            (resolved as { default?: unknown } | undefined)?.default ??
                resolved,
        ).toBe(OwnerProjectsPage)
        await owner.push('/owner')
        expect(owner.currentRoute.value.name).toBe('owner-home')
        expect(mockedAuth.me).toHaveBeenCalledTimes(1)

        setActivePinia(createPinia())
        mockedAuth.me.mockResolvedValueOnce({
            userid: 'staff-1',
            role: 'STAFF',
        })
        const staff = createAppRouter(createMemoryHistory())
        await staff.push('/owner/projects')
        expect(staff.currentRoute.value.name).toBe('forbidden')
    })

    test('an exhausted refresh clears protected navigation without starting another bootstrap loop', async () => {
        const registration = vi.spyOn(
            httpModule,
            'setAuthenticationLostHandler',
        )
        mockedAuth.me.mockResolvedValueOnce({
            userid: 'owner-1',
            role: 'OWNER',
        })
        const router = createAppRouter(createMemoryHistory())
        await router.push('/owner/projects')
        const handler = registration.mock.calls.at(-1)?.[0]
        expect(handler).toBeTypeOf('function')

        await handler?.()

        expect(router.currentRoute.value.name).toBe('login')
        expect(router.currentRoute.value.query).toEqual({
            redirect: '/owner/projects',
        })
        expect(mockedAuth.me).toHaveBeenCalledTimes(1)
    })

    test('keeps login and callback public but redirects an authenticated OWNER away from login', async () => {
        mockedAuth.me.mockResolvedValue({ userid: 'owner-1', role: 'OWNER' })
        const router = createAppRouter(createMemoryHistory())

        await router.push('/login')
        expect(router.currentRoute.value.name).toBe('owner-home')

        await router.push('/auth/callback?code=code_1&state=state-1')
        expect(router.currentRoute.value.name).toBe('auth-callback')
    })

    test('accepts only same-origin absolute-path post-login destinations', () => {
        expect(safePostLoginPath('/owner/projects?view=all')).toBe(
            '/owner/projects?view=all',
        )
        for (const unsafe of [
            '//evil.example/path',
            'https://evil.example/path',
            '/\\evil.example',
            '/%5cevil.example',
            '/%2e%2e//evil.example/path',
            '/%2e%2e/%2e%2e//evil.example/path',
            '/%252e%252e/%255cevil.example',
            '/auth/callback?code=secret',
            '/login',
            'javascript:alert(1)',
            '',
        ]) {
            expect(safePostLoginPath(unsafe)).toBe('/owner')
        }
    })
})
