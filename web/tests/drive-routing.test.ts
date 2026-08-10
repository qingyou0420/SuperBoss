import { fireEvent, render, screen } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { createAppRouter } from '../src/app/router'
import AppLayout from '../src/layouts/AppLayout.vue'

const DRIVE_PATH = '../src/pages/owner/DrivePage.vue'
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'

const mocks = vi.hoisted(() => ({
    projectsApi: { list: vi.fn() },
}))

vi.mock('../src/api/projects', () => ({ projectsApi: mocks.projectsApi }))

beforeEach(() => {
    vi.clearAllMocks()
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
})
