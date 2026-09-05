import { fireEvent, render, screen } from '@testing-library/vue'
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
            is_test: false,
            status: 'ACTIVE',
            stage: 'PLANNING',
            progress_percent: 0,
            starts_on: null,
            due_on: null,
            milestones: [],
        },
    ])
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
        expect(
            await screen.findByRole('heading', { name: '公司' }),
        ).toBeInTheDocument()
        expect(await screen.findByText('外包')).toBeInTheDocument()
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

    test('STAFF sees project costs only and cannot write', async () => {
        const pinia = setRole('STAFF')
        mocks.financeApi.summary.mockResolvedValue({
            month: '2026-09',
            company: null,
            projects: [
                {
                    project_id: PROJECT_ID,
                    project_name: '星野合作',
                    cost_cents: 1_200_000,
                },
            ],
        })
        render(FinancePage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('外包')).toBeInTheDocument()
        expect(screen.getAllByText(/12000.00 元/).length).toBeGreaterThan(0)
        expect(
            screen.queryByRole('button', { name: '保存' }),
        ).not.toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: '调整' }),
        ).not.toBeInTheDocument()
        expect(
            screen.queryByRole('heading', { name: '公司' }),
        ).not.toBeInTheDocument()
        expect(screen.queryByText(/收入/)).not.toBeInTheDocument()
    })
})
