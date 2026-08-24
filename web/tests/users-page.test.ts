import type {
    AxiosAdapter,
    AxiosResponse,
    InternalAxiosRequestConfig,
} from 'axios'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { createHttpClient } from '../src/api/http'
import { UserContractError, createUsersApi, usersApi } from '../src/api/users'
import UsersPage from '../src/pages/owner/UsersPage.vue'

vi.mock('../src/api/users', async () => {
    const actual =
        await vi.importActual<typeof import('../src/api/users')>(
            '../src/api/users',
        )
    return {
        ...actual,
        usersApi: {
            list: vi.fn(),
            create: vi.fn(),
            update: vi.fn(),
            replaceProjects: vi.fn(),
            resetPassword: vi.fn(),
        },
        userErrorMessage: () => '员工操作暂时无法完成，请稍后重试。',
    }
})
vi.mock('../src/api/projects', () => ({
    projectsApi: {
        list: vi.fn().mockResolvedValue([
            {
                id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
                name: '验收项目',
                is_test: false,
                status: 'ACTIVE',
            },
        ]),
    },
}))

const mockedUsersApi = vi.mocked(usersApi)
const project = {
    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
    name: '验收项目',
}
const staff = {
    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f811',
    username: 'existing-staff',
    display_name: 'Existing Staff',
    role: 'STAFF' as const,
    status: 'ACTIVE' as const,
    last_login_at: '2026-08-09T12:00:00Z',
    projects: [project],
}
const temporaryPassword = 'temporary-password-sentinel'

function response(
    config: InternalAxiosRequestConfig,
    data: unknown,
    status = 200,
): AxiosResponse {
    return {
        config,
        data,
        headers: {},
        status,
        statusText: String(status),
    } as AxiosResponse
}

beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    mockedUsersApi.list.mockResolvedValue([staff])
})

afterEach(() => {
    Reflect.deleteProperty(navigator, 'clipboard')
})

describe('OWNER local user API', () => {
    test('decodes exact one-time create and reset envelopes', async () => {
        const seen: InternalAxiosRequestConfig[] = []
        const adapter: AxiosAdapter = async (config) => {
            seen.push(config)
            if (config.url?.endsWith('/password-reset')) {
                return response(config, {
                    temporary_password: temporaryPassword,
                })
            }
            return response(
                config,
                { user: staff, temporary_password: temporaryPassword },
                201,
            )
        }
        const api = createUsersApi(createHttpClient({ adapter }))

        await expect(
            api.create({
                username: 'existing-staff',
                display_name: 'Existing Staff',
                project_ids: [project.id],
            }),
        ).resolves.toEqual({
            user: staff,
            temporary_password: temporaryPassword,
        })
        await expect(api.resetPassword(staff.id)).resolves.toEqual({
            temporary_password: temporaryPassword,
        })
        expect(JSON.parse(String(seen[0]?.data))).toEqual({
            username: 'existing-staff',
            display_name: 'Existing Staff',
            project_ids: [project.id],
        })
        expect(seen[1]?.url).toBe(`/owner/users/${staff.id}/password-reset`)
    })

    test.each([{ user: staff, temporary_password: '' }])(
        'rejects malformed credential envelopes %#',
        async (body) => {
            const adapter: AxiosAdapter = async (config) =>
                response(
                    config,
                    body,
                    config.url?.endsWith('/password-reset') ? 200 : 201,
                )
            const api = createUsersApi(createHttpClient({ adapter }))
            await expect(
                'user' in body
                    ? api.create({
                          username: 'existing-staff',
                          display_name: 'Existing Staff',
                          project_ids: [],
                      })
                    : api.resetPassword(staff.id),
            ).rejects.toBeInstanceOf(UserContractError)
        },
    )
})

describe('OWNER local user management page', () => {
    test('shows a created temporary password once without storage or clipboard writes', async () => {
        mockedUsersApi.create.mockResolvedValue({
            user: { ...staff, username: 'staff-acceptance' },
            temporary_password: temporaryPassword,
        })
        const clipboard = vi.fn()
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText: clipboard },
        })
        render(UsersPage, { global: { plugins: [ElementPlus] } })
        await screen.findByText('existing-staff')
        await fireEvent.update(
            screen.getByLabelText('用户名'),
            'staff-acceptance',
        )
        await fireEvent.update(screen.getByLabelText('显示名称'), 'Acceptance')
        await fireEvent.click(screen.getByRole('button', { name: '添加员工' }))

        expect(await screen.findByText(temporaryPassword)).toBeInTheDocument()
        expect(mockedUsersApi.create).toHaveBeenCalledWith({
            username: 'staff-acceptance',
            display_name: 'Acceptance',
            project_ids: [],
        })
        expect(clipboard).not.toHaveBeenCalled()
        expect(localStorage).toHaveLength(0)
        expect(sessionStorage).toHaveLength(0)
        await fireEvent.click(screen.getByRole('button', { name: '我已保存' }))
        await waitFor(() =>
            expect(
                screen.queryByText(temporaryPassword),
            ).not.toBeInTheDocument(),
        )
    })

    test('clears a reset password on close and component unmount', async () => {
        mockedUsersApi.resetPassword.mockResolvedValue({
            temporary_password: temporaryPassword,
        })
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
        const rendered = render(UsersPage, {
            global: { plugins: [ElementPlus] },
        })
        await screen.findByText('existing-staff')
        await fireEvent.click(screen.getByRole('button', { name: '重置密码' }))
        expect(await screen.findByText(temporaryPassword)).toBeInTheDocument()
        expect(confirm).toHaveBeenCalledWith(
            '确认重置 existing-staff 的密码吗？',
        )
        rendered.unmount()
        expect(document.body.textContent).not.toContain(temporaryPassword)
    })

    test('confirms disable by username and preserves project assignment behavior', async () => {
        mockedUsersApi.update.mockResolvedValue({
            ...staff,
            status: 'DISABLED',
        })
        mockedUsersApi.replaceProjects.mockResolvedValue(staff)
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
        render(UsersPage, { global: { plugins: [ElementPlus] } })
        await screen.findByText('existing-staff')
        expect(screen.getAllByText('验收项目').length).toBeGreaterThan(0)
        await fireEvent.click(screen.getByRole('button', { name: '禁用' }))
        expect(confirm).toHaveBeenCalledWith('确认禁用 existing-staff 吗？')
        expect(mockedUsersApi.update).toHaveBeenCalledWith(staff.id, {
            status: 'DISABLED',
        })
        expect(
            screen.queryByLabelText(/角色|明文密码/i),
        ).not.toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: /删除/i }),
        ).not.toBeInTheDocument()
    })
})
