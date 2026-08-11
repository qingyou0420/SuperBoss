import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { authApi } from '../src/api/auth'
import * as httpModule from '../src/api/http'
import { HttpClientError } from '../src/api/http'
import { createAppRouter, safePostLoginPath } from '../src/app/router'

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

function unauthorized(): HttpClientError {
    return new HttpClientError(401, { detail: 'Authentication required' })
}

beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
})

describe('local-auth route guards', () => {
    test('sends anonymous protected navigation to login with an internal return path', async () => {
        mockedAuth.me.mockRejectedValue(unauthorized())
        const router = createAppRouter(createMemoryHistory())
        await router.push('/owner/projects?view=all')
        expect(router.currentRoute.value.name).toBe('login')
        expect(router.currentRoute.value.query).toEqual({
            redirect: '/owner/projects?view=all',
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
            redirect: '/owner/projects',
        })
        await router.push('/forbidden')
        expect(router.currentRoute.value.name).toBe('password-change')
    })

    test('keeps password change reachable and sends a completed OWNER away from it', async () => {
        mockedAuth.me.mockResolvedValue(owner)
        const router = createAppRouter(createMemoryHistory())
        await router.push('/password/change')
        expect(router.currentRoute.value.name).toBe('owner-home')
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
            expect(safePostLoginPath(unsafe)).toBe('/owner')
        }
    })
})
