import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { createAppRouter, homePath } from '../src/app/router'
import AppShell from '../src/layouts/AppShell.vue'
import { useAuthStore } from '../src/stores/auth'

const DRIVE_PATH = '../src/pages/owner/DrivePage.vue'
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const FILE_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'

const mocks = vi.hoisted(() => {
    class FileDownloadUnavailableError extends Error {
        readonly state: 'INFECTED' | 'FAILED'

        constructor(state: 'INFECTED' | 'FAILED') {
            super('File download is unavailable')
            this.state = state
        }
    }
    return {
        FileDownloadUnavailableError,
        filesApi: {
            download: vi.fn(),
            listFolders: vi.fn(),
            listFiles: vi.fn(),
            createFolder: vi.fn(),
            rename: vi.fn(),
            move: vi.fn(),
            remove: vi.fn(),
        },
        projectsApi: { list: vi.fn() },
        upload: vi.fn(),
        clearTray: vi.fn(),
        tray: { value: [] as { name: string; status: string }[] },
    }
})

vi.mock('../src/api/files', () => ({
    FileDownloadUnavailableError: mocks.FileDownloadUnavailableError,
    fileErrorMessage: () => '文件操作失败，请稍后重试。',
    filesApi: mocks.filesApi,
}))
vi.mock('../src/api/projects', () => ({ projectsApi: mocks.projectsApi }))
vi.mock('../src/components/files/useMultipartUpload', async () => {
    const { ref } = await import('vue')
    const tray = ref<{ name: string; status: string }[]>([])
    mocks.tray = tray
    return {
        useMultipartUpload: () => ({
            tray,
            upload: mocks.upload,
            clearTray: mocks.clearTray,
        }),
    }
})

async function chooseHeaderFile(name = '上传.pdf'): Promise<void> {
    const input = document.querySelector('#drive-upload')
    expect(input).toBeInstanceOf(HTMLInputElement)
    const file = new File(['x'], name, { type: 'application/pdf' })
    Object.defineProperty(input, 'files', {
        configurable: true,
        value: [file],
    })
    await fireEvent.change(input as HTMLInputElement)
}

beforeEach(() => {
    vi.clearAllMocks()
    mocks.tray.value = []
    mocks.upload.mockImplementation(async (file: File) => {
        mocks.tray.value = [{ name: file.name, status: '扫描中' }]
        return { file_id: FILE_ID, state: 'QUARANTINED' }
    })
    mocks.clearTray.mockImplementation(() => {
        mocks.tray.value = []
    })
    mocks.filesApi.download.mockReset()
    mocks.filesApi.listFolders.mockResolvedValue([
        {
            id: PROJECT_ID,
            parent_id: null,
            name: '项目',
            visibility: 'ALL',
        },
    ])
    mocks.filesApi.listFiles.mockResolvedValue([])
    localStorage.clear()
    sessionStorage.clear()
    setActivePinia(createPinia())
    mocks.projectsApi.list.mockResolvedValue([
        {
            id: PROJECT_ID,
            name: '客户方案',
            status: 'ACTIVE',
            description: '',
            stage: 'PLANNING',
            progress_percent: 0,
            starts_on: null,
            due_on: null,
            milestones: [],
        },
    ])
})

afterEach(() => {
    vi.restoreAllMocks()
})

describe('Task13 OWNER navigation and Drive integration', () => {
    test('OWNER lands on chat and other roles land on projects', () => {
        expect(homePath('OWNER')).toBe('/chat')
        expect(homePath('MANAGER')).toBe('/overview')
        expect(homePath('STAFF')).toBe('/workbench')
    })

    test.each([
        ['/drive', 'drive'],
        ['/finance', 'finance'],
        ['/chat', 'chat'],
        ['/map', 'map'],
    ])('registers %s for signed-in roles', (path, name) => {
        const router = createAppRouter(createMemoryHistory())
        const resolved = router.resolve(path)

        expect(resolved.name).toBe(name)
        expect(resolved.matched).toHaveLength(2)
        expect(resolved.matched[0]?.path).toBe('/')
        expect(resolved.matched[0]?.meta).toMatchObject({
            requiresAuth: true,
            roles: ['OWNER', 'MANAGER', 'STAFF'],
        })
    })

    test('shows exact OWNER navigation without inventing a historical file list', async () => {
        const router = createRouter({
            history: createMemoryHistory(),
            routes: [
                {
                    component: defineComponent({ template: '<p>home</p>' }),
                    path: '/owner',
                },
            ],
        })
        await router.push('/owner')
        await router.isReady()
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: '清游',
            role: 'OWNER',
            must_change_password: false,
        }
        render(AppShell, {
            global: { plugins: [pinia, router, ElementPlus] },
        })

        for (const label of [
            '霜月',
            '工作台',
            '业务地图',
            '经营总览',
            '财务',
            '会务项目',
            '网盘',
        ]) {
            expect(
                screen.getByRole('link', { name: label }),
            ).toBeInTheDocument()
        }
        expect(
            screen.queryByRole('link', { name: /历史文件/ }),
        ).not.toBeInTheDocument()
    })

    test('Drive displays scanning after completion and only the current result', async () => {
        const module = await import(/* @vite-ignore */ DRIVE_PATH)
        mocks.filesApi.listFiles.mockResolvedValue([])
        render(module.default, {
            props: { allowedObjectOrigin: 'https://objects.example' },
            global: { plugins: [ElementPlus] },
        })

        expect(
            screen.getByRole('heading', { name: '网盘' }),
        ).toBeInTheDocument()
        expect(
            await screen.findByRole('button', { name: '上传' }),
        ).toBeInTheDocument()
        expect(screen.getAllByRole('button', { name: '上传' })).toHaveLength(1)
        expect(
            (document.querySelector('#drive-upload') as HTMLInputElement | null)
                ?.tabIndex,
        ).toBe(-1)
        expect(
            document.querySelector('.page-header__actions [role="status"]'),
        ).not.toBeInTheDocument()
        expect(screen.queryByText(/历史文件|全部文件/)).not.toBeInTheDocument()
        mocks.filesApi.listFiles.mockResolvedValue([
            {
                id: FILE_ID,
                folder_id: PROJECT_ID,
                project_id: null,
                filename: '上传.pdf',
                size_bytes: 12,
                content_type: 'application/pdf',
                state: 'SCANNING',
                created_at: '2026-09-05T00:00:00Z',
                uploader_name: '',
            },
        ])
        await chooseHeaderFile()
        expect(await screen.findByText('处理中')).toBeInTheDocument()
        expect(mocks.upload).toHaveBeenCalled()
        expect(mocks.clearTray).toHaveBeenCalled()
        expect(
            document.querySelector('.page-header__actions [role="status"]'),
        ).not.toBeInTheDocument()
        expect(mocks.filesApi.listFiles.mock.calls.length).toBeGreaterThan(1)
    })

    test('refreshes the file list after upload and downloads from the row menu', async () => {
        const module = await import(/* @vite-ignore */ DRIVE_PATH)
        mocks.filesApi.listFiles.mockResolvedValueOnce([]).mockResolvedValue([
            {
                id: FILE_ID,
                folder_id: PROJECT_ID,
                project_id: null,
                filename: '上传.pdf',
                size_bytes: 12,
                content_type: 'application/pdf',
                state: 'CLEAN',
                created_at: '2026-09-05T00:00:00Z',
                uploader_name: '',
            },
        ])
        mocks.filesApi.download.mockResolvedValue(
            'https://objects.example/download/current?signature=secret',
        )
        render(module.default, {
            props: { allowedObjectOrigin: 'https://objects.example' },
            global: { plugins: [ElementPlus] },
        })
        await screen.findByRole('button', { name: '上传' })
        await chooseHeaderFile()
        expect(await screen.findByText('上传.pdf')).toBeInTheDocument()
        await fireEvent.click(screen.getByRole('button', { name: '···' }))
        await fireEvent.click(
            await screen.findByRole('menuitem', { name: '下载' }),
        )
        expect(mocks.filesApi.download).toHaveBeenCalledWith(FILE_ID)
        expect(mocks.filesApi.listFiles.mock.calls.length).toBeGreaterThan(1)
    })

    test.each(['INFECTED', 'FAILED'] as const)(
        'shows terminal scan state %s on the file row',
        async (state) => {
            const module = await import(/* @vite-ignore */ DRIVE_PATH)
            mocks.filesApi.listFiles.mockResolvedValue([
                {
                    id: FILE_ID,
                    folder_id: PROJECT_ID,
                    project_id: null,
                    filename: '风险.pdf',
                    size_bytes: 12,
                    content_type: 'application/pdf',
                    state,
                    created_at: '2026-09-05T00:00:00Z',
                    uploader_name: '',
                },
            ])
            render(module.default, {
                props: { allowedObjectOrigin: 'https://objects.example' },
                global: { plugins: [ElementPlus] },
            })
            expect(await screen.findByText('风险.pdf')).toBeInTheDocument()
            expect(screen.getByText('未通过')).toBeInTheDocument()
            expect(
                screen.queryByRole('link', { name: '下载' }),
            ).not.toBeInTheDocument()
        },
    )

    test('OWNER can create folders and rename, move, or delete files', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: 'Owner',
            role: 'OWNER',
            must_change_password: false,
        }
        const destId = '019f2b8e-18f0-7f31-9f42-3e6a76b9f813'
        mocks.filesApi.listFolders.mockResolvedValue([
            {
                id: PROJECT_ID,
                parent_id: null,
                name: '项目',
                visibility: 'ALL',
            },
            {
                id: destId,
                parent_id: null,
                name: '公司',
                visibility: 'MANAGEMENT',
            },
        ])
        const driveFile = {
            id: FILE_ID,
            folder_id: PROJECT_ID,
            project_id: null,
            filename: '方案.pdf',
            size_bytes: 12,
            content_type: 'application/pdf',
            state: 'CLEAN',
            created_at: '2026-09-05T00:00:00Z',
        }
        mocks.filesApi.listFiles.mockResolvedValue([driveFile])
        mocks.filesApi.createFolder.mockResolvedValue({
            id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f814',
            parent_id: PROJECT_ID,
            name: '子目录',
            visibility: 'ALL',
        })
        mocks.filesApi.rename.mockResolvedValue({
            ...driveFile,
            filename: '新方案.pdf',
        })
        mocks.filesApi.move.mockResolvedValue({
            ...driveFile,
            folder_id: destId,
        })
        const module = await import(/* @vite-ignore */ DRIVE_PATH)
        render(module.default, {
            props: { allowedObjectOrigin: 'https://objects.example' },
            global: { plugins: [pinia, ElementPlus] },
        })

        expect(await screen.findByText('方案.pdf')).toBeInTheDocument()
        const menus = screen.getAllByRole('button', { name: '···' })
        await fireEvent.click(menus[0])
        await fireEvent.click(
            await screen.findByRole('menuitem', { name: '新建子目录' }),
        )
        await fireEvent.update(await screen.findByLabelText('名称'), '子目录')
        await fireEvent.click(screen.getByRole('button', { name: '创建' }))
        expect(mocks.filesApi.createFolder).toHaveBeenCalledWith(
            PROJECT_ID,
            '子目录',
        )
        expect(await screen.findByText('子目录')).toBeInTheDocument()

        await fireEvent.click(screen.getAllByRole('button', { name: '···' })[1])
        await fireEvent.click(
            await screen.findByRole('menuitem', { name: '重命名' }),
        )
        await fireEvent.update(
            screen.getByDisplayValue('方案.pdf'),
            '新方案.pdf',
        )
        await fireEvent.click(screen.getByRole('button', { name: '确定' }))
        expect(mocks.filesApi.rename).toHaveBeenCalledWith(
            FILE_ID,
            '新方案.pdf',
        )
        expect(await screen.findByText('新方案.pdf')).toBeInTheDocument()

        await fireEvent.click(screen.getAllByRole('button', { name: '···' })[1])
        await fireEvent.click(
            await screen.findByRole('menuitem', { name: '移动' }),
        )
        await fireEvent.click(
            screen.getByRole('combobox', { name: '目标目录' }),
        )
        await fireEvent.click(
            await screen.findByRole('option', { name: '公司' }),
        )
        await fireEvent.click(screen.getByRole('button', { name: '放到这里' }))
        expect(mocks.filesApi.move).toHaveBeenCalledWith(FILE_ID, destId)
        expect(screen.queryByText('新方案.pdf')).not.toBeInTheDocument()
    })

    test('OWNER confirms before deleting a file', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: 'Owner',
            role: 'OWNER',
            must_change_password: false,
        }
        mocks.filesApi.listFiles.mockResolvedValue([
            {
                id: FILE_ID,
                folder_id: PROJECT_ID,
                project_id: null,
                filename: '方案.pdf',
                size_bytes: 12,
                content_type: 'application/pdf',
                state: 'CLEAN',
                created_at: '2026-09-05T00:00:00Z',
                uploader_name: '',
            },
        ])
        mocks.filesApi.remove.mockResolvedValue(undefined)
        const module = await import(/* @vite-ignore */ DRIVE_PATH)
        render(module.default, {
            props: { allowedObjectOrigin: 'https://objects.example' },
            global: { plugins: [pinia, ElementPlus] },
        })
        expect(await screen.findByText('方案.pdf')).toBeInTheDocument()
        await fireEvent.click(screen.getAllByRole('button', { name: '···' })[1])
        await fireEvent.click(
            await screen.findByRole('menuitem', { name: '删除' }),
        )
        expect(mocks.filesApi.remove).not.toHaveBeenCalled()
        await fireEvent.click(screen.getByRole('button', { name: '确定' }))
        expect(mocks.filesApi.remove).toHaveBeenCalledWith(FILE_ID)
    })

    test('closes the delete dialog and shows an error when remove fails', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: 'Owner',
            role: 'OWNER',
            must_change_password: false,
        }
        mocks.filesApi.listFiles.mockResolvedValue([
            {
                id: FILE_ID,
                folder_id: PROJECT_ID,
                project_id: null,
                filename: '方案.pdf',
                size_bytes: 12,
                content_type: 'application/pdf',
                state: 'CLEAN',
                created_at: '2026-09-05T00:00:00Z',
                uploader_name: '',
            },
        ])
        mocks.filesApi.remove.mockRejectedValue(new Error('nope'))
        const module = await import(/* @vite-ignore */ DRIVE_PATH)
        render(module.default, {
            props: { allowedObjectOrigin: 'https://objects.example' },
            global: { plugins: [pinia, ElementPlus] },
        })
        expect(await screen.findByText('方案.pdf')).toBeInTheDocument()
        await fireEvent.click(screen.getAllByRole('button', { name: '···' })[1])
        await fireEvent.click(
            await screen.findByRole('menuitem', { name: '删除' }),
        )
        await fireEvent.click(screen.getByRole('button', { name: '确定' }))
        expect(await screen.findByText('无法删除文件。')).toBeInTheDocument()
        await waitFor(() =>
            expect(
                screen.queryByRole('dialog', { name: '删除' }),
            ).not.toBeInTheDocument(),
        )
    })

    test('STAFF can download but cannot manage folders or files', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'staff',
            display_name: 'Staff',
            role: 'STAFF',
            must_change_password: false,
        }
        mocks.filesApi.listFiles.mockResolvedValue([
            {
                id: FILE_ID,
                folder_id: PROJECT_ID,
                project_id: null,
                filename: '方案.pdf',
                size_bytes: 12,
                content_type: 'application/pdf',
                state: 'CLEAN',
                created_at: '2026-09-05T00:00:00Z',
            },
        ])
        const module = await import(/* @vite-ignore */ DRIVE_PATH)
        render(module.default, {
            props: { allowedObjectOrigin: 'https://objects.example' },
            global: { plugins: [pinia, ElementPlus] },
        })

        expect(await screen.findByText('方案.pdf')).toBeInTheDocument()
        await fireEvent.click(screen.getByRole('button', { name: '···' }))
        expect(
            await screen.findByRole('menuitem', { name: '下载' }),
        ).toBeInTheDocument()
        expect(
            screen.queryByRole('menuitem', { name: '重命名' }),
        ).not.toBeInTheDocument()
        expect(
            screen.queryByRole('menuitem', { name: '移动' }),
        ).not.toBeInTheDocument()
        expect(
            screen.queryByRole('menuitem', { name: '删除' }),
        ).not.toBeInTheDocument()
        expect(
            screen.queryByRole('menuitem', { name: '新建子目录' }),
        ).not.toBeInTheDocument()
    })

    test.each([
        ['INFECTED', '检测到风险，文件不可下载'],
        ['FAILED', '扫描失败，文件不可下载，请重新上传'],
        ['SCANNING', '文件仍在扫描中。'],
    ] as const)(
        'downloadFile surfaces %s as a safe message',
        async (state, message) => {
            const module = await import(/* @vite-ignore */ DRIVE_PATH)
            mocks.filesApi.listFiles.mockResolvedValue([
                {
                    id: FILE_ID,
                    folder_id: PROJECT_ID,
                    project_id: null,
                    filename: '方案.pdf',
                    size_bytes: 12,
                    content_type: 'application/pdf',
                    state: 'CLEAN',
                    created_at: '2026-09-05T00:00:00Z',
                    uploader_name: '',
                },
            ])
            if (state === 'SCANNING') {
                mocks.filesApi.download.mockRejectedValueOnce(new Error('busy'))
            } else {
                mocks.filesApi.download.mockRejectedValueOnce(
                    new mocks.FileDownloadUnavailableError(state),
                )
            }
            render(module.default, {
                props: { allowedObjectOrigin: 'https://objects.example' },
                global: { plugins: [ElementPlus] },
            })
            expect(await screen.findByText('方案.pdf')).toBeInTheDocument()
            await fireEvent.click(screen.getByRole('button', { name: '···' }))
            await fireEvent.click(
                await screen.findByRole('menuitem', { name: '下载' }),
            )
            expect(await screen.findByText(message)).toBeInTheDocument()
        },
    )
})
