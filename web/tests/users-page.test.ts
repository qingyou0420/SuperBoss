import type {
    AxiosAdapter,
    AxiosResponse,
    InternalAxiosRequestConfig,
} from 'axios'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { createHttpClient } from '../src/api/http'
import { createUsersApi, usersApi } from '../src/api/users'
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
        },
        userErrorMessage: () => '员工操作暂时无法完成，请稍后重试。',
    }
})
vi.mock('../src/api/projects', () => ({
    projectsApi: {
        list: vi
            .fn()
            .mockResolvedValue([
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
const project = { id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810', name: '验收项目' }
const staff = {
    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f811',
    wecom_userid: 'existing-staff',
    display_name: 'Existing Staff',
    role: 'STAFF' as const,
    status: 'ACTIVE' as const,
    last_login_at: '2026-08-09T12:00:00Z',
    projects: [project],
}

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
    mockedUsersApi.list.mockResolvedValue([staff])
})

describe('OWNER user management', () => {
    test('creates a STAFF whitelist account through the narrow HTTP facade contract', async () => {
        const adapter: AxiosAdapter = async (config) =>
            response(
                config,
                {
                    ...staff,
                    wecom_userid: 'staff-acceptance',
                    display_name: 'Acceptance',
                },
                201,
            )
        const api = createUsersApi(createHttpClient({ adapter }))

        await expect(
            api.create({
                wecom_userid: 'staff-acceptance',
                display_name: 'Acceptance',
                project_ids: [],
            }),
        ).resolves.toMatchObject({
            wecom_userid: 'staff-acceptance',
            role: 'STAFF',
        })
    })

    test('adds and displays a STAFF user without role, password, or delete-owner controls', async () => {
        mockedUsersApi.create.mockResolvedValue({
            ...staff,
            wecom_userid: 'staff-acceptance',
            display_name: 'Acceptance',
        })
        render(UsersPage, { global: { plugins: [ElementPlus] } })
        await screen.findByText('existing-staff')

        await fireEvent.update(
            screen.getByLabelText('WeCom UserID'),
            'staff-acceptance',
        )
        await fireEvent.update(screen.getByLabelText('显示名称'), 'Acceptance')
        await fireEvent.click(screen.getByRole('button', { name: '添加员工' }))

        await waitFor(() =>
            expect(mockedUsersApi.create).toHaveBeenCalledWith({
                wecom_userid: 'staff-acceptance',
                display_name: 'Acceptance',
                project_ids: [],
            }),
        )
        expect(await screen.findByText('staff-acceptance')).toBeInTheDocument()
        expect(screen.getAllByText('STAFF').length).toBeGreaterThan(0)
        expect(screen.queryByLabelText(/角色|密码/i)).not.toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: /删除/i }),
        ).not.toBeInTheDocument()
    })

    test('requires confirmation before disable and displays projects plus Shanghai last-login time', async () => {
        mockedUsersApi.update.mockResolvedValue({
            ...staff,
            status: 'DISABLED',
        })
        const confirm = vi
            .spyOn(window, 'confirm')
            .mockReturnValueOnce(false)
            .mockReturnValueOnce(true)
        render(UsersPage, { global: { plugins: [ElementPlus] } })
        await screen.findByText('existing-staff')
        expect(screen.getAllByText('验收项目').length).toBeGreaterThan(0)
        expect(screen.getByText(/2026/)).toBeInTheDocument()
        const disable = screen.getByRole('button', { name: '禁用' })
        await fireEvent.click(disable)
        expect(mockedUsersApi.update).not.toHaveBeenCalled()
        await fireEvent.click(disable)
        await waitFor(() =>
            expect(mockedUsersApi.update).toHaveBeenCalledWith(staff.id, {
                status: 'DISABLED',
            }),
        )
        expect(confirm).toHaveBeenCalledTimes(2)
    })
})
