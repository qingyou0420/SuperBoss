import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { projectsApi } from '../src/api/projects'
import { projectsCopy } from '../src/copy/pages/projects'
import ProjectsPage from '../src/pages/owner/ProjectsPage.vue'
import { useAuthStore } from '../src/stores/auth'

vi.mock('../src/api/projects', () => ({
    projectsApi: {
        list: vi.fn(),
        create: vi.fn(),
        update: vi.fn(),
        remove: vi.fn(),
    },
}))

const mockedProjects = vi.mocked(projectsApi)
const extras = {
    description: '',
    stage: 'PLANNING' as const,
    progress_percent: 0,
    starts_on: null,
    due_on: null,
    service_fee_cents: null,
    lead_user_id: null,
    milestones: [],
}
const regular = {
    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
    name: '正式项目',
    status: 'ACTIVE' as const,
    ...extras,
}
const acceptance = {
    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f811',
    name: '员工验收沙盒',
    status: 'ACTIVE' as const,
    ...extras,
}

function renderPage() {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = {
        username: 'owner',
        display_name: 'Owner',
        role: 'OWNER',
        must_change_password: false,
    }
    const router = createRouter({
        history: createMemoryHistory(),
        routes: [
            { path: '/projects', component: ProjectsPage },
            { path: '/projects/:projectId', component: ProjectsPage },
        ],
    })
    void router.push('/projects')
    return render(ProjectsPage, {
        global: { plugins: [pinia, router, ElementPlus] },
    })
}

beforeEach(() => {
    vi.clearAllMocks()
    mockedProjects.list.mockResolvedValue([regular, acceptance])
})

describe('OWNER project management page', () => {
    test('lists OWNER-visible projects', async () => {
        renderPage()

        expect(await screen.findByText('正式项目')).toBeInTheDocument()
        expect(screen.getByText('员工验收沙盒')).toBeInTheDocument()
        expect(screen.queryByText('验收测试')).not.toBeInTheDocument()
        expect(mockedProjects.list).toHaveBeenCalledTimes(1)
    })

    test('creates a project from accessible form controls', async () => {
        mockedProjects.create.mockResolvedValue({
            ...acceptance,
            name: '新验收项目',
        })
        renderPage()
        await screen.findByText('正式项目')
        await fireEvent.click(screen.getByRole('button', { name: '新建' }))

        await fireEvent.update(
            screen.getByLabelText('项目名称'),
            ' 新验收项目 ',
        )
        await fireEvent.click(screen.getByRole('button', { name: '保存' }))

        await waitFor(() =>
            expect(mockedProjects.create).toHaveBeenCalledWith({
                name: '新验收项目',
                description: '',
                stage: 'PLANNING',
                starts_on: null,
                due_on: null,
            }),
        )
        expect(await screen.findByText('新验收项目')).toBeInTheDocument()
    })

    test('does not let a stale initial list overwrite a project created while loading', async () => {
        let releaseList!: () => void
        mockedProjects.list.mockImplementationOnce(
            () =>
                new Promise(
                    (resolve) => (releaseList = () => resolve([regular])),
                ),
        )
        mockedProjects.create.mockResolvedValue({
            ...acceptance,
            name: 'race-created',
        })
        renderPage()
        await fireEvent.click(
            await screen.findByRole('button', { name: '新建' }),
        )

        await fireEvent.update(
            await screen.findByLabelText('项目名称'),
            'race-created',
        )
        await fireEvent.click(screen.getByRole('button', { name: '保存' }))
        expect(await screen.findByText('race-created')).toBeInTheDocument()

        releaseList()
        expect(await screen.findByText('正式项目')).toBeInTheDocument()
        expect(screen.getByText('race-created')).toBeInTheDocument()
    })

    test('accepts 255 supplementary Unicode code points without a UTF-16 maxlength barrier', async () => {
        mockedProjects.create.mockResolvedValue({
            ...acceptance,
            name: '\u{1f600}'.repeat(255),
        })
        renderPage()
        await screen.findByText('正式项目')
        await fireEvent.click(screen.getByRole('button', { name: '新建' }))
        const input = screen.getByLabelText('项目名称') as HTMLInputElement

        expect(input.maxLength).toBe(-1)
        const boundary = '\u{1f600}'.repeat(255)
        await fireEvent.update(input, boundary)
        await fireEvent.click(screen.getByRole('button', { name: '保存' }))

        await waitFor(() =>
            expect(mockedProjects.create).toHaveBeenCalledWith({
                name: boundary,
                description: '',
                stage: 'PLANNING',
                starts_on: null,
                due_on: null,
            }),
        )
    })

    test('rejects 256 Unicode code points locally with a safe length prompt', async () => {
        mockedProjects.create.mockResolvedValue(acceptance)
        renderPage()
        await screen.findByText('正式项目')
        await fireEvent.click(screen.getByRole('button', { name: '新建' }))

        await fireEvent.update(
            screen.getByLabelText('项目名称'),
            '\u{1f600}'.repeat(256),
        )
        await fireEvent.click(screen.getByRole('button', { name: '保存' }))

        expect(mockedProjects.create).not.toHaveBeenCalled()
        expect(screen.getByRole('alert')).toHaveTextContent(/255/)
        expect(screen.getByRole('alert')).not.toHaveTextContent(/sentinel/i)
    })

    test('does not submit blank names or duplicate clicks while a create is pending', async () => {
        let release!: () => void
        mockedProjects.create.mockImplementation(
            () =>
                new Promise((resolve) => (release = () => resolve(acceptance))),
        )
        renderPage()
        await screen.findByText('正式项目')
        await fireEvent.click(screen.getByRole('button', { name: '新建' }))

        const button = screen.getByRole('button', { name: '保存' })
        await fireEvent.click(button)
        expect(mockedProjects.create).not.toHaveBeenCalled()

        await fireEvent.update(screen.getByLabelText('项目名称'), '验收沙盒')
        await fireEvent.click(button)
        await fireEvent.click(button)
        expect(mockedProjects.create).toHaveBeenCalledTimes(1)
        expect(button).toBeDisabled()
        release()
    })

    test('shows a fixed safe error and never renders backend/provider details', async () => {
        mockedProjects.list.mockRejectedValue(
            new Error('postgres://admin:sentinel@db/internal traceback'),
        )
        renderPage()

        expect(
            await screen.findByText('项目列表加载失败。'),
        ).toBeInTheDocument()
        expect(
            screen.queryByText(/sentinel|postgres|traceback/i),
        ).not.toBeInTheDocument()
    })

    test('deletes a project after confirm', async () => {
        mockedProjects.remove.mockResolvedValue(undefined)
        mockedProjects.list
            .mockResolvedValueOnce([regular, acceptance])
            .mockResolvedValueOnce([acceptance])
        renderPage()
        expect(await screen.findByText('正式项目')).toBeInTheDocument()
        await fireEvent.click(screen.getAllByRole('button', { name: '···' })[0])
        await fireEvent.click(
            (
                await screen.findAllByRole('menuitem', {
                    name: projectsCopy.remove,
                })
            )[0],
        )
        expect(
            await screen.findByText(projectsCopy.deleteConfirm),
        ).toBeInTheDocument()
        await fireEvent.click(
            screen.getByRole('button', { name: projectsCopy.confirm }),
        )
        await waitFor(() =>
            expect(mockedProjects.remove).toHaveBeenCalledWith(regular.id),
        )
    })
})
