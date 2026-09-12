import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import FinancePage from '../src/pages/FinancePage.vue'
import { useAuthStore } from '../src/stores/auth'

const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'

const mocks = vi.hoisted(() => ({
    financeApi: {
        list: vi.fn(),
        summary: vi.fn(),
        create: vi.fn(),
        adjust: vi.fn(),
        alerts: vi.fn(),
        markPaid: vi.fn(),
        importFile: vi.fn(),
        listImportRows: vi.fn(),
        resolveImportRow: vi.fn(),
    },
    projectsApi: {
        list: vi.fn(),
    },
}))

vi.mock('../src/api/finance', async () => {
    const actual =
        await vi.importActual<typeof import('../src/api/finance')>(
            '../src/api/finance',
        )
    return {
        ...actual,
        financeApi: mocks.financeApi,
        financeErrorMessage: () => '财务操作失败，请稍后重试。',
    }
})

vi.mock('../src/api/projects', () => ({
    projectsApi: mocks.projectsApi,
}))

function setRole(role: 'OWNER' | 'MANAGER' | 'STAFF') {
    const pinia = createPinia()
    setActivePinia(pinia)
    useAuthStore().user = {
        username: role.toLowerCase(),
        display_name: role,
        role,
        must_change_password: false,
    }
    return pinia
}

beforeEach(() => {
    vi.clearAllMocks()
    mocks.projectsApi.list.mockResolvedValue([
        {
            id: PROJECT_ID,
            name: '星野合作',
            description: '',
            status: 'ACTIVE',
            stage: 'PLANNING',
            progress_percent: 0,
            starts_on: null,
            due_on: null,
            milestones: [],
        },
    ])
    mocks.financeApi.alerts.mockResolvedValue([])
    mocks.financeApi.listImportRows.mockResolvedValue({ items: [], total: 0 })
    mocks.financeApi.list.mockResolvedValue([
        {
            id: PROJECT_ID,
            kind: 'COST',
            scope: 'PROJECT',
            project_id: PROJECT_ID,
            project_name: '星野合作',
            amount_cents: 1_200_000,
            currency: 'CNY',
            occurred_on: '2026-09-02',
            category: '外包',
            memo: '',
            visibility: 'ALL',
            created_via: 'FORM',
            created_at: '2026-09-02T00:00:00Z',
            adjustments: [],
        },
    ])
    mocks.financeApi.summary.mockResolvedValue({
        month: '2026-09',
        company: { cost_cents: 800_000, income_cents: 0 },
        projects: [
            {
                project_id: PROJECT_ID,
                project_name: '星野合作',
                cost_cents: 1_200_000,
                income_cents: 0,
            },
        ],
    })
})

describe('finance page by role', () => {
    test('OWNER can record an entry and sees company totals', async () => {
        const pinia = setRole('OWNER')
        mocks.financeApi.create.mockResolvedValue({})
        render(FinancePage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('公司运营成本')).toBeInTheDocument()
        expect(await screen.findByText('外包')).toBeInTheDocument()
        await fireEvent.click(screen.getByRole('button', { name: '记一笔' }))
        expect(screen.getByRole('button', { name: '保存' })).toBeInTheDocument()
        await fireEvent.update(screen.getByLabelText('类别'), '房租')
        await fireEvent.update(screen.getByLabelText('金额（元）'), '8000')
        await fireEvent.click(screen.getByRole('button', { name: '保存' }))
        expect(mocks.financeApi.create).toHaveBeenCalledWith(
            expect.objectContaining({
                kind: 'COST',
                scope: 'COMPANY',
                amount_cents: 800_000,
                category: '房租',
            }),
        )
    })

    function processedRow(status: string, index: number) {
        return {
            id: `${status}-${index}`,
            batch_key: 'batch-a',
            row_index: index,
            status,
            reason: '',
            fingerprint: '',
            entry_id: `${status}-entry-${index}`,
            payload: { amount_cents: 1, source_row: index },
        }
    }

    function processedEntryLinkCount() {
        return document.querySelectorAll('.import-review a').length
    }

    test('reloading processed sources replaces the previous snapshot', async () => {
        const pinia = setRole('OWNER')
        const first = {
            id: 'row-1',
            batch_key: 'batch-a',
            row_index: 1,
            status: 'INSERTED',
            reason: '',
            fingerprint: '',
            entry_id: PROJECT_ID,
            payload: { occurred_on: '2026-09-02', amount_cents: 100 },
        }
        const second = {
            ...first,
            id: 'row-2',
            row_index: 2,
        }
        let inserted = [first]
        mocks.financeApi.listImportRows.mockImplementation(async (status) => {
            if (status === 'INSERTED') {
                return { items: inserted, total: inserted.length }
            }
            return { items: [], total: 0 }
        })
        render(FinancePage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText(/已入账行/)).toBeInTheDocument()
        expect(screen.getAllByRole('link', { name: '打开账目' })).toHaveLength(
            1,
        )
        inserted = [first, second]
        await fireEvent.click(screen.getByRole('button', { name: '›' }))
        await waitFor(() => {
            expect(
                screen.getAllByRole('link', { name: '打开账目' }),
            ).toHaveLength(2)
        })
    })

    test('processed source pages stay unique after in-flight double click', async () => {
        const pinia = setRole('OWNER')
        const inserted = Array.from({ length: 404 }, (_, index) =>
            processedRow('INSERTED', index),
        )
        const linked = Array.from({ length: 405 }, (_, index) =>
            processedRow('LINKED', index),
        )
        let resumeSecondPage: (() => void) | undefined
        const secondPageGate = new Promise<void>((resolve) => {
            resumeSecondPage = resolve
        })
        mocks.financeApi.listImportRows.mockImplementation(
            async (status, paging) => {
                const offset = paging?.offset ?? 0
                const limit = paging?.limit ?? 200
                const rows =
                    status === 'INSERTED'
                        ? inserted
                        : status === 'LINKED'
                          ? linked
                          : []
                if (offset > 0) await secondPageGate
                return {
                    items: rows.slice(offset, offset + limit),
                    total: rows.length,
                }
            },
        )
        render(FinancePage, { global: { plugins: [pinia, ElementPlus] } })
        const more = await screen.findByRole('button', {
            name: '查看更多已入账来源',
        })
        expect(processedEntryLinkCount()).toBe(400)
        await fireEvent.click(more)
        await fireEvent.click(more)
        resumeSecondPage?.()
        await waitFor(() => {
            expect(processedEntryLinkCount()).toBe(800)
        })
        expect(
            screen.getByRole('button', { name: '查看更多已入账来源' }),
        ).toBeInTheDocument()
        await fireEvent.click(
            screen.getByRole('button', { name: '查看更多已入账来源' }),
        )
        await waitFor(() => {
            expect(processedEntryLinkCount()).toBe(809)
        })
        expect(
            screen.queryByRole('button', { name: '查看更多已入账来源' }),
        ).not.toBeInTheDocument()
    })

    test('sequential processed paging keeps 405 inserted and 405 linked unique', async () => {
        const pinia = setRole('OWNER')
        const inserted = Array.from({ length: 405 }, (_, index) =>
            processedRow('INSERTED', index),
        )
        const linked = Array.from({ length: 405 }, (_, index) =>
            processedRow('LINKED', index),
        )
        mocks.financeApi.listImportRows.mockImplementation(
            async (status, paging) => {
                const offset = paging?.offset ?? 0
                const limit = paging?.limit ?? 200
                const rows =
                    status === 'INSERTED'
                        ? inserted
                        : status === 'LINKED'
                          ? linked
                          : []
                return {
                    items: rows.slice(offset, offset + limit),
                    total: rows.length,
                }
            },
        )
        render(FinancePage, { global: { plugins: [pinia, ElementPlus] } })
        expect(
            await screen.findByRole('button', {
                name: '查看更多已入账来源',
            }),
        ).toBeInTheDocument()
        await fireEvent.click(
            screen.getByRole('button', { name: '查看更多已入账来源' }),
        )
        await waitFor(() => {
            expect(processedEntryLinkCount()).toBe(800)
        })
        await fireEvent.click(
            screen.getByRole('button', { name: '查看更多已入账来源' }),
        )
        await waitFor(() => {
            expect(processedEntryLinkCount()).toBe(810)
        })
    })

    test('STAFF does not see ledger amounts', async () => {
        const pinia = setRole('STAFF')
        mocks.financeApi.summary.mockResolvedValue({
            month: '2026-09',
            company: null,
            projects: [],
        })
        mocks.financeApi.list.mockResolvedValue([])
        render(FinancePage, { global: { plugins: [pinia, ElementPlus] } })
        expect(
            await screen.findByText(
                '财务明细仅老板与股东可见。已确定的项目金额在项目页查看。',
            ),
        ).toBeInTheDocument()
        expect(screen.queryByText('外包')).not.toBeInTheDocument()
        expect(screen.queryByText('公司运营成本')).not.toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: '保存' }),
        ).not.toBeInTheDocument()
    })
})
