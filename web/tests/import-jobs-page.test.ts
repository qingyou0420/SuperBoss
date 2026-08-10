import { fireEvent, render, screen } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, test, vi } from 'vitest'

const PAGE_PATH = '../src/pages/owner/ImportJobsPage.vue'
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'

const mocks = vi.hoisted(() => ({
    importsApi: { list: vi.fn() },
}))

vi.mock('../src/api/imports', () => ({
    importErrorMessage: () => '导入任务暂时无法加载，请稍后重试。',
    importsApi: mocks.importsApi,
}))

async function renderPage() {
    const module = await import(/* @vite-ignore */ PAGE_PATH)
    return render(module.default, { global: { plugins: [ElementPlus] } })
}

function job(
    suffix: number,
    status: string,
    result_code: string | null = null,
) {
    const fileState = {
        CONFLICT: 'CLEAN',
        RECEIVED: 'CLEAN',
        REJECTED: 'INFECTED',
        SCANNING: 'SCANNING',
        UPLOADING: 'UPLOADING',
    }[status]
    return {
        attachments: [
            {
                file_id: `00000000-0000-4000-8000-${String(suffix + 100).padStart(12, '0')}`,
                file_state: fileState,
                id: `00000000-0000-4000-8000-${String(suffix + 200).padStart(12, '0')}`,
                kind: 'REVISED',
                upload_id: `00000000-0000-4000-8000-${String(suffix + 300).padStart(12, '0')}`,
            },
        ],
        created_at: '2026-08-10T02:00:00.000Z',
        external_document_reference: `KimiWork/job-${suffix}.docx`,
        id: `00000000-0000-4000-8000-${String(suffix).padStart(12, '0')}`,
        local_task_id: `kimi-task-${suffix}`,
        model_label: 'kimi-k3',
        project_id: PROJECT_ID,
        result_code,
        status,
        submitted_at: '2026-08-10T02:01:00.000Z',
        updated_at: '2026-08-10T02:02:00.000Z',
    }
}

beforeEach(() => {
    vi.clearAllMocks()
    mocks.importsApi.list.mockResolvedValue([
        job(1, 'UPLOADING'),
        job(2, 'SCANNING'),
        job(3, 'RECEIVED'),
        job(4, 'REJECTED', 'ATTACHMENT_INFECTED'),
        job(5, 'CONFLICT', 'BASE_SHA256_MISMATCH'),
    ])
})

describe('OWNER import job summaries', () => {
    test('renders all five frozen states as distinct Chinese labels', async () => {
        await renderPage()

        for (const label of [
            '上传中',
            '扫描中',
            '已接收',
            '已拒绝',
            '有冲突',
        ]) {
            expect(await screen.findByText(label)).toBeInTheDocument()
        }
        expect(mocks.importsApi.list).toHaveBeenCalledWith(100)
    })

    test('uses the selected bounded summary as detail without requesting K3 content', async () => {
        await renderPage()
        await fireEvent.click(await screen.findByText('kimi-task-4'))

        expect(screen.getByText(PROJECT_ID)).toBeInTheDocument()
        expect(screen.getByText('kimi-k3')).toBeInTheDocument()
        expect(screen.getByText('REVISED')).toBeInTheDocument()
        expect(screen.getByText('INFECTED')).toBeInTheDocument()
        expect(
            screen.getByText('附件检出风险，导入已拒绝。'),
        ).toBeInTheDocument()
        expect(mocks.importsApi.list).toHaveBeenCalledTimes(1)
        expect(Object.keys(mocks.importsApi)).toEqual(['list'])
    })

    test('uses a fixed reason for unknown result codes and never renders K3 or secrets', async () => {
        mocks.importsApi.list.mockResolvedValue([
            {
                ...job(6, 'REJECTED', 'UNKNOWN_SAFE_CODE'),
                modification_details: ['K3正文 sentinel'],
                refresh_token: 'token sentinel',
            },
        ])
        await renderPage()

        expect(
            await screen.findByText('任务未完成，请联系管理员查看审计记录。'),
        ).toBeInTheDocument()
        expect(
            screen.queryByText(/K3正文|refresh_token|sentinel/i),
        ).not.toBeInTheDocument()
    })

    test('shows only a fixed safe list failure', async () => {
        mocks.importsApi.list.mockRejectedValue(
            new Error('K3正文 token=sentinel postgres traceback'),
        )
        await renderPage()

        expect(
            await screen.findByText('导入任务暂时无法加载，请稍后重试。'),
        ).toBeInTheDocument()
        expect(
            screen.queryByText(/K3正文|sentinel|traceback/i),
        ).not.toBeInTheDocument()
    })
})
