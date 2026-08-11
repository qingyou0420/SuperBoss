import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { authApi } from '../src/api/auth'
import LoginPage from '../src/pages/LoginPage.vue'
import PasswordChangePage from '../src/pages/PasswordChangePage.vue'

const owner = {
    username: 'owner',
    display_name: 'Owner',
    role: 'OWNER' as const,
    must_change_password: false,
}

function routerFor(path: string) {
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

async function renderAt(path: string) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = routerFor(path)
    await router.isReady()
    const Host = defineComponent({ template: '<router-view />' })
    render(Host, { global: { plugins: [pinia, router, ElementPlus] } })
    return router
}

beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    sessionStorage.clear()
})

describe('LoginPage', () => {
    test('submits local credentials once and restores a safe internal target', async () => {
        let release!: () => void
        vi.spyOn(authApi, 'login').mockImplementation(
            () => new Promise((resolve) => (release = resolve)),
        )
        vi.spyOn(authApi, 'me').mockResolvedValue(owner)
        const router = await renderAt(
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

        expect(authApi.login).toHaveBeenCalledTimes(1)
        expect(authApi.login).toHaveBeenCalledWith({
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
        vi.spyOn(authApi, 'login').mockResolvedValue()
        vi.spyOn(authApi, 'me').mockResolvedValue({
            ...owner,
            must_change_password: true,
        })
        const router = await renderAt('/login?redirect=%2Fowner%2Fprojects')
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
        vi.spyOn(authApi, 'login').mockRejectedValue(
            new Error('password=sentinel database traceback'),
        )
        await renderAt('/login')
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
        vi.spyOn(authApi, 'changePassword').mockResolvedValue()
        vi.spyOn(authApi, 'me').mockResolvedValue(owner)
        const router = await renderAt('/password/change')
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
        expect(authApi.changePassword).not.toHaveBeenCalled()
        expect(
            await screen.findByText('两次输入的新密码不一致。'),
        ).toBeInTheDocument()

        await fireEvent.update(inputs[2], 'replacement local password')
        await fireEvent.click(screen.getByRole('button', { name: '更新密码' }))
        await waitFor(() =>
            expect(router.currentRoute.value.path).toBe('/owner'),
        )
        expect(authApi.changePassword).toHaveBeenCalledWith({
            current_password: 'temporary local password',
            new_password: 'replacement local password',
        })
        expect(document.body.textContent).not.toMatch(
            /temporary local password|replacement local password/,
        )
    })
})
