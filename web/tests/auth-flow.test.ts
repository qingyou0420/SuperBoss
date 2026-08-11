import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { authApi } from '../src/api/auth'
import { HttpClientError } from '../src/api/http'
import { projectsApi } from '../src/api/projects'
import { createAppRouter } from '../src/app/router'

const owner = {
    username: 'owner',
    display_name: 'Owner',
    role: 'OWNER' as const,
    must_change_password: false,
}

function unauthorized(): HttpClientError {
    return new HttpClientError(401, { detail: 'Authentication required' })
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

async function submitLogin(password = 'correct horse battery staple') {
    await fireEvent.update(screen.getByLabelText('用户名'), 'owner')
    await fireEvent.update(screen.getByLabelText('密码'), password)
    await fireEvent.click(screen.getByRole('button', { name: '登录' }))
}

beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    vi.spyOn(projectsApi, 'list').mockResolvedValue([])
})

describe('complete local-auth browser flow', () => {
    test('returns to the original protected path without OAuth state or browser token storage', async () => {
        vi.spyOn(authApi, 'me')
            .mockRejectedValueOnce(unauthorized())
            .mockResolvedValue(owner)
        vi.spyOn(authApi, 'login').mockResolvedValue()
        const target = '/owner/projects?view=all#acceptance'
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
        vi.spyOn(authApi, 'me')
            .mockRejectedValueOnce(unauthorized())
            .mockResolvedValue(owner)
        vi.spyOn(authApi, 'login').mockResolvedValue()
        const router = await renderAnonymous(
            '/login?redirect=%2F%2Fevil.example%2Fpath',
        )
        await submitLogin()
        await waitFor(() =>
            expect(router.currentRoute.value.fullPath).toBe('/owner'),
        )
    })

    test('completes the mandatory password change before restoring the target', async () => {
        vi.spyOn(authApi, 'me')
            .mockRejectedValueOnce(unauthorized())
            .mockResolvedValueOnce({ ...owner, must_change_password: true })
            .mockResolvedValueOnce(owner)
        vi.spyOn(authApi, 'login').mockResolvedValue()
        vi.spyOn(authApi, 'changePassword').mockResolvedValue()
        const router = await renderAnonymous('/owner/projects')
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
            expect(router.currentRoute.value.fullPath).toBe('/owner/projects'),
        )
        expect(document.body.textContent).not.toMatch(
            /temporary local password|replacement local password/,
        )
    })
})
