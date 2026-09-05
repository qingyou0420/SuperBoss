import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { projectsApi } from '../src/api/projects'
import ProjectsPage from '../src/pages/owner/ProjectsPage.vue'
import { useAuthStore } from '../src/stores/auth'

vi.mock('../src/api/projects', () => ({
    projectsApi: {
        list: vi.fn(),
        create: vi.fn(),
    },
}))

const mockedProjects = vi.mocked(projectsApi)
const extras = {
    description: '',
    stage: 'PLANNING' as const,
    progress_percent: 0,
    starts_on: null,
    due_on: null,
    milestones: [],
}
const regular = {
    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
    name: '正式项目',
    is_test: false,
    status: 'ACTIVE' as const,
    ...extras,
}
const acceptance = {
    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f811',
    name: '员工验收沙盒',
    is_test: true,
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
    test('lists OWNER-visible projects and marks test projects with an explicit acceptance label', async () => {
        renderPage()

        expect(await screen.findByText('正式项目')).toBeInTheDocument()
        expect(screen.getByText('员工验收沙盒')).toBeInTheDocument()
        expect(screen.getByText('验收测试')).toBeInTheDocument()
        expect(screen.queryAllByText('验收测试')).toHaveLength(1)
        expect(mockedProjects.list).toHaveBeenCalledTimes(1)
    })

    test('creates an is_test project from accessible form controls and immediately labels it', async () => {
        mockedProjects.create.mockResolvedValue({
            ...acceptance,
            name: '新验收项目',
        })
        renderPage()
        await screen.findByText('正式项目')

        await fireEvent.update(
            screen.getByLabelText('项目名称'),
            ' 新验收项目 ',
        )
        await fireEvent.click(screen.getByLabelText('设为验收测试项目'))
        await fireEvent.click(screen.getByRole('button', { name: '创建项目' }))

        await waitFor(() =>
            expect(mockedProjects.create).toHaveBeenCalledWith({
                name: '新验收项目',
                is_test: true,
            }),
        )
        expect(await screen.findByText('新验收项目')).toBeInTheDocument()
        expect(screen.getAllByText('验收测试')).toHaveLength(2)
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

        await fireEvent.update(
            await screen.findByRole('textbox'),
            'race-created',
        )
        await fireEvent.click(screen.getByLabelText('设为验收测试项目'))
        await fireEvent.click(screen.getByRole('button', { name: '创建项目' }))
        expect(await screen.findByText('race-created')).toBeInTheDocument()

        releaseList()
        expect(await screen.findByText('正式项目')).toBeInTheDocument()
        expect(screen.getByText('race-created')).toBeInTheDocument()
        expect(screen.getByText('验收测试')).toBeInTheDocument()
    })

    test('accepts 255 supplementary Unicode code points without a UTF-16 maxlength barrier', async () => {
        mockedProjects.create.mockResolvedValue({
            ...acceptance,
            name: '\u{1f600}'.repeat(255),
        })
        renderPage()
        await screen.findByText('正式项目')
        const input = screen.getByLabelText('项目名称') as HTMLInputElement

        expect(input.maxLength).toBe(-1)
        const boundary = '\u{1f600}'.repeat(255)
        await fireEvent.update(input, boundary)
        await fireEvent.click(screen.getByRole('button', { name: '创建项目' }))

        await waitFor(() =>
            expect(mockedProjects.create).toHaveBeenCalledWith({
                name: boundary,
                is_test: false,
            }),
        )
    })

    test('rejects 256 Unicode code points locally with a safe length prompt', async () => {
        mockedProjects.create.mockResolvedValue(acceptance)
        renderPage()
        await screen.findByText('正式项目')

        await fireEvent.update(
            screen.getByLabelText('项目名称'),
            '\u{1f600}'.repeat(256),
        )
        await fireEvent.click(screen.getByRole('button', { name: '创建项目' }))

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

        const button = screen.getByRole('button', { name: '创建项目' })
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
            await screen.findByText('项目列表暂时无法加载，请稍后重试。'),
        ).toBeInTheDocument()
        expect(
            screen.queryByText(/sentinel|postgres|traceback/i),
        ).not.toBeInTheDocument()
    })
})
