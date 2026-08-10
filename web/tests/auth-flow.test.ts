import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, type Router } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import * as authModule from '../src/api/auth'
import { authApi } from '../src/api/auth'
import { HttpClientError } from '../src/api/http'
import { projectsApi } from '../src/api/projects'
import { createAppRouter } from '../src/app/router'

const owner = { userid: 'owner-1', role: 'OWNER' as const }
const safeStart = {
    state: 'state-1',
    authorization_url:
        'https://open.weixin.qq.com/connect/oauth2/authorize?state=state-1',
}

function unauthorized(): HttpClientError {
    return new HttpClientError(401, {
        detail: 'Authentication required',
    })
}

async function renderAnonymousFlow(initialPath: string): Promise<Router> {
    const pinia = createPinia()
    setActivePinia(pinia)
    vi.spyOn(authApi, 'me')
        .mockRejectedValueOnce(unauthorized())
        .mockResolvedValue(owner)
    vi.spyOn(authApi, 'startWeCom').mockResolvedValue(safeStart)
    vi.spyOn(authApi, 'completeWeCom').mockResolvedValue()
    vi.spyOn(projectsApi, 'list').mockResolvedValue([])
    vi.spyOn(authModule, 'navigateToAuthorization').mockImplementation(() => {})
    const router = createAppRouter(createMemoryHistory())
    await router.push(initialPath)
    await router.isReady()
    const RouterHost = defineComponent({ template: '<router-view />' })
    render(RouterHost, {
        global: { plugins: [pinia, router, ElementPlus] },
    })
    return router
}

async function beginLogin(): Promise<void> {
    await fireEvent.click(screen.getByRole('button', { name: '企业微信登录' }))
    await waitFor(() => expect(authApi.startWeCom).toHaveBeenCalled())
}

function storedReturnTarget(): { key: string; value: string } {
    expect(localStorage).toHaveLength(0)
    expect(sessionStorage).toHaveLength(1)
    const key = sessionStorage.key(0)
    expect(key).not.toBeNull()
    const value = sessionStorage.getItem(String(key))
    expect(value).not.toBeNull()
    return { key: String(key), value: String(value) }
}

beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    sessionStorage.clear()
})

describe('complete protected-route OAuth return flow', () => {
    test('restores the safe protected fullPath through real login and callback components', async () => {
        const target = '/owner/projects?view=all#acceptance'
        const router = await renderAnonymousFlow(target)

        expect(router.currentRoute.value.name).toBe('login')
        expect(router.currentRoute.value.query).toEqual({ redirect: target })
        await beginLogin()

        const stored = storedReturnTarget()
        expect(stored.value).toBe(target)
        expect(`${stored.key}:${stored.value}`).not.toMatch(
            /access|refresh|code|state|token/i,
        )

        await router.push('/auth/callback?code=code_1&state=state-1')
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe(target),
        )

        expect(authApi.completeWeCom).toHaveBeenCalledWith({
            code: 'code_1',
            state: 'state-1',
        })
        expect(sessionStorage).toHaveLength(0)
        expect(document.body.textContent).not.toMatch(/code_1|state-1/)
        expect(
            screen.getByRole('button', { name: '创建项目' }),
        ).toBeInTheDocument()
    })

    test('a newer login replaces the prior target and successful callback consumes it once', async () => {
        const router = await renderAnonymousFlow('/owner/projects?view=old')
        await beginLogin()
        expect(storedReturnTarget().value).toBe('/owner/projects?view=old')

        await router.push('/login?redirect=%2Fowner%2Fprojects%3Fview%3Dnew')
        await beginLogin()
        expect(storedReturnTarget().value).toBe('/owner/projects?view=new')

        await router.push('/auth/callback?code=code_2&state=state-2')
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe(
                '/owner/projects?view=new',
            ),
        )
        expect(sessionStorage).toHaveLength(0)
    })

    test('a failed new-target write deletes the old target and callback falls back safely', async () => {
        const router = await renderAnonymousFlow('/owner/projects?view=old')
        await beginLogin()
        const stored = storedReturnTarget()
        expect(stored.value).toBe('/owner/projects?view=old')

        await router.push('/login?redirect=%2Fowner%2Fprojects%3Fview%3Dnew')
        const setItem = vi
            .spyOn(Storage.prototype, 'setItem')
            .mockImplementation(() => {
                throw new DOMException(
                    'quota provider_token=sentinel',
                    'QuotaExceededError',
                )
            })
        await beginLogin()
        setItem.mockRestore()

        await router.push('/auth/callback?code=code_3&state=state-3')
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe('/owner'),
        )
        expect(sessionStorage).toHaveLength(0)
        expect(document.body.textContent).not.toMatch(
            /sentinel|provider_token|quota/i,
        )
    })

    test('a failed write and failed cleanup cannot revive an old target after storage recovers', async () => {
        const router = await renderAnonymousFlow('/owner/projects?view=old')
        await beginLogin()
        storedReturnTarget()

        await router.push('/login?redirect=%2Fowner%2Fprojects%3Fview%3Dnew')
        const setItem = vi
            .spyOn(Storage.prototype, 'setItem')
            .mockImplementation(() => {
                throw new Error('set refresh_token=sentinel')
            })
        const removeItem = vi
            .spyOn(Storage.prototype, 'removeItem')
            .mockImplementation(() => {
                throw new Error('remove code=sentinel')
            })
        await beginLogin()
        expect(document.body.textContent).not.toMatch(
            /sentinel|refresh_token|code=/i,
        )
        setItem.mockRestore()
        removeItem.mockRestore()

        await router.push('/auth/callback?code=code_4&state=state-4')
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe('/owner'),
        )
        expect(sessionStorage).toHaveLength(0)
    })

    test.each(['getItem', 'removeItem'] as const)(
        'a callback %s fault falls back and best-effort consumes the pending target',
        async (method) => {
            const router = await renderAnonymousFlow('/owner/projects?view=old')
            await beginLogin()
            storedReturnTarget()
            vi.spyOn(Storage.prototype, method).mockImplementationOnce(() => {
                throw new Error(`${method} state=sentinel`)
            })

            await router.push('/auth/callback?code=code_5&state=state-5')
            await waitFor(() =>
                expect(router.currentRoute.value.fullPath).toBe('/owner'),
            )

            expect(sessionStorage).toHaveLength(0)
            expect(document.body.textContent).not.toMatch(/sentinel|state=/i)
        },
    )

    test.each([
        '/login?redirect=%2F%2Fevil.example%2Fpath',
        '/login?redirect=%2F%252e%252e%2F%2Fevil.example%2Fpath',
        '/login?redirect=%2Fowner%2Fprojects&redirect=%2Fowner',
        '/login?redirect=%2Fowner%255c..%255cadmin',
    ])('falls back for hostile or ambiguous login target %s', async (path) => {
        const router = await renderAnonymousFlow(path)
        await beginLogin()
        await router.push('/auth/callback?code=code_1&state=state-1')

        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe('/owner'),
        )
        expect(sessionStorage).toHaveLength(0)
    })

    test.each(['invalid callback', 'provider failure'])(
        'clears the pending return target after %s',
        async (failure) => {
            const router = await renderAnonymousFlow('/owner/projects')
            await beginLogin()
            storedReturnTarget()
            if (failure === 'provider failure') {
                vi.mocked(authApi.completeWeCom).mockRejectedValueOnce(
                    new Error('provider token=sentinel traceback'),
                )
                await router.push('/auth/callback?code=code_1&state=state-1')
                expect(
                    await screen.findByText('登录未完成，请重新登录。'),
                ).toBeInTheDocument()
            } else {
                await router.push(
                    '/auth/callback?code=code_1&state=one&state=two',
                )
                expect(
                    await screen.findByText('登录回调无效，请重新登录。'),
                ).toBeInTheDocument()
            }

            expect(sessionStorage).toHaveLength(0)
            expect(document.body.textContent).not.toMatch(
                /sentinel|traceback|code_1|state-1/i,
            )
        },
    )
})
