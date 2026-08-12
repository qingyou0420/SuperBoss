import { fireEvent, render, screen } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { createAppRouter } from '../src/app/router'
import AppLayout from '../src/layouts/AppLayout.vue'

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
        filesApi: { download: vi.fn() },
        projectsApi: { list: vi.fn() },
    }
})

vi.mock('../src/api/files', () => ({
    FileDownloadUnavailableError: mocks.FileDownloadUnavailableError,
    fileErrorMessage: () => '文件操作失败，请稍后重试。',
    filesApi: mocks.filesApi,
}))
vi.mock('../src/api/projects', () => ({ projectsApi: mocks.projectsApi }))

beforeEach(() => {
    vi.clearAllMocks()
    mocks.filesApi.download.mockReset()
    localStorage.clear()
    sessionStorage.clear()
    setActivePinia(createPinia())
    mocks.projectsApi.list.mockResolvedValue([
        {
            id: PROJECT_ID,
            is_test: false,
            name: '客户方案',
            status: 'ACTIVE',
        },
    ])
})

afterEach(() => {
    vi.restoreAllMocks()
})

describe('Task13 OWNER navigation and Drive integration', () => {
    test.each([
        ['/owner/drive', 'owner-drive'],
        ['/owner/devices', 'owner-devices'],
        ['/owner/import-jobs', 'owner-import-jobs'],
    ])('registers %s under the OWNER-only layout', (path, name) => {
        const router = createAppRouter(createMemoryHistory())
        const resolved = router.resolve(path)

        expect(resolved.name).toBe(name)
        expect(resolved.matched).toHaveLength(2)
        expect(resolved.matched[0]?.path).toBe('/owner')
        expect(resolved.matched[0]?.meta).toMatchObject({
            requiresAuth: true,
            roles: ['OWNER'],
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
        render(AppLayout, {
            global: { plugins: [createPinia(), router, ElementPlus] },
        })

        for (const label of ['项目', '文件上传', '设备', '导入任务']) {
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
        const MultipartStub = defineComponent({
            emits: ['completed'],
            props: ['allowedObjectOrigin', 'projectId'],
            template:
                "<div><span data-testid=\"upload-boundary\">{{ allowedObjectOrigin }}|{{ projectId }}</span><button @click=\"$emit('completed', { file_id: 'file-1', state: 'QUARANTINED' })\">完成上传</button></div>",
        })
        render(module.default, {
            props: { allowedObjectOrigin: 'https://objects.example' },
            global: {
                plugins: [ElementPlus],
                stubs: { MultipartUploader: MultipartStub },
            },
        })

        expect(
            screen.getByRole('heading', { name: '文件上传' }),
        ).toBeInTheDocument()
        expect(await screen.findByTestId('upload-boundary')).toHaveTextContent(
            `https://objects.example|${PROJECT_ID}`,
        )
        expect(screen.queryByText(/历史文件|全部文件/)).not.toBeInTheDocument()
        await fireEvent.click(screen.getByRole('button', { name: '完成上传' }))
        expect(screen.getByText('扫描中')).toBeInTheDocument()
        expect(screen.getByText('file-1')).toBeInTheDocument()
    })

    test('rechecks only the current quarantined file until its download becomes ready', async () => {
        const module = await import(/* @vite-ignore */ DRIVE_PATH)
        const storageWrite = vi.spyOn(Storage.prototype, 'setItem')
        const consoleWrite = vi
            .spyOn(console, 'log')
            .mockImplementation(() => undefined)
        mocks.filesApi.download
            .mockRejectedValueOnce(
                new Error('FILE_NOT_CLEAN provider sentinel signed-url'),
            )
            .mockResolvedValueOnce(
                'https://objects.example/download/current?signature=secret',
            )
        const MultipartStub = defineComponent({
            emits: ['completed'],
            template:
                "<button @click=\"$emit('completed', { file_id: '" +
                FILE_ID +
                "', state: 'QUARANTINED' })\">完成上传</button>",
        })
        render(module.default, {
            props: { allowedObjectOrigin: 'https://objects.example' },
            global: {
                plugins: [ElementPlus],
                stubs: { MultipartUploader: MultipartStub },
            },
        })
        await screen.findByText('客户方案')
        await fireEvent.click(screen.getByRole('button', { name: '完成上传' }))

        const check = screen.getByRole('button', {
            name: '检查并获取下载',
        })
        await fireEvent.click(check)
        expect(
            await screen.findByText('文件仍在扫描中，请稍后重试。'),
        ).toBeInTheDocument()
        expect(
            screen.queryByRole('link', { name: '下载本次文件' }),
        ).not.toBeInTheDocument()
        expect(check).toBeInTheDocument()

        await fireEvent.click(check)
        const link = await screen.findByRole('link', {
            name: '下载本次文件',
        })
        expect(link).toHaveAttribute(
            'href',
            'https://objects.example/download/current?signature=secret',
        )
        expect(mocks.filesApi.download).toHaveBeenNthCalledWith(1, FILE_ID)
        expect(mocks.filesApi.download).toHaveBeenNthCalledWith(2, FILE_ID)
        expect(storageWrite).not.toHaveBeenCalled()
        expect(localStorage).toHaveLength(0)
        expect(sessionStorage).toHaveLength(0)
        expect(consoleWrite).not.toHaveBeenCalled()
        expect(screen.queryByText(/历史文件|全部文件/)).not.toBeInTheDocument()
    })

    test.each([
        ['INFECTED', '检测到风险，文件不可下载'],
        ['FAILED', '扫描失败，文件不可下载，请重新上传'],
    ] as const)(
        'renders direct terminal completion %s without a download action',
        async (state, message) => {
            const module = await import(/* @vite-ignore */ DRIVE_PATH)
            const MultipartStub = defineComponent({
                emits: ['completed'],
                template:
                    "<button @click=\"$emit('completed', { file_id: '" +
                    FILE_ID +
                    "', state: '" +
                    state +
                    '\' })">完成上传</button>',
            })
            render(module.default, {
                props: { allowedObjectOrigin: 'https://objects.example' },
                global: {
                    plugins: [ElementPlus],
                    stubs: { MultipartUploader: MultipartStub },
                },
            })
            await screen.findByText('客户方案')
            await fireEvent.click(
                screen.getByRole('button', { name: '完成上传' }),
            )

            expect(screen.getByText(message)).toBeInTheDocument()
            expect(
                screen.queryByRole('button', {
                    name: '检查并获取下载',
                }),
            ).not.toBeInTheDocument()
            expect(
                screen.queryByRole('link', { name: '下载本次文件' }),
            ).not.toBeInTheDocument()
        },
    )

    test.each([
        ['INFECTED', '检测到风险，文件不可下载'],
        ['FAILED', '扫描失败，文件不可下载，请重新上传'],
    ] as const)(
        'converges download probe to terminal state %s',
        async (state, message) => {
            const module = await import(/* @vite-ignore */ DRIVE_PATH)
            mocks.filesApi.download.mockRejectedValueOnce(
                new mocks.FileDownloadUnavailableError(state),
            )
            const MultipartStub = defineComponent({
                emits: ['completed'],
                template:
                    "<button @click=\"$emit('completed', { file_id: '" +
                    FILE_ID +
                    "', state: 'QUARANTINED' })\">完成上传</button>",
            })
            render(module.default, {
                props: { allowedObjectOrigin: 'https://objects.example' },
                global: {
                    plugins: [ElementPlus],
                    stubs: { MultipartUploader: MultipartStub },
                },
            })
            await screen.findByText('客户方案')
            await fireEvent.click(
                screen.getByRole('button', { name: '完成上传' }),
            )
            await fireEvent.click(
                screen.getByRole('button', {
                    name: '检查并获取下载',
                }),
            )

            expect(await screen.findByText(message)).toBeInTheDocument()
            expect(mocks.filesApi.download).toHaveBeenCalledOnce()
            expect(mocks.filesApi.download).toHaveBeenCalledWith(FILE_ID)
            expect(
                screen.queryByRole('button', {
                    name: '检查并获取下载',
                }),
            ).not.toBeInTheDocument()
            expect(
                screen.queryByRole('link', { name: '下载本次文件' }),
            ).not.toBeInTheDocument()
        },
    )
})
