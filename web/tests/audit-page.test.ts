import { render, screen } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, test, vi } from 'vitest'

import AuditPage from '../src/pages/AuditPage.vue'

vi.mock('../src/api/audit', () => ({
    auditApi: {
        list: vi.fn().mockResolvedValue([
            {
                id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
                actor_kind: 'user',
                actor_id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f811',
                action: 'finance.entry.create',
                object_type: 'finance_entry',
                object_id: null,
                project_id: null,
                outcome: 'SUCCESS',
                metadata_json: {},
                request_id: null,
                created_at: '2026-09-05T00:00:00Z',
            },
        ]),
    },
    auditErrorMessage: () => '审计记录暂时无法加载，请稍后重试。',
}))

describe('audit page', () => {
    test('lists recent audit actions for OWNER', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        render(AuditPage, { global: { plugins: [pinia, ElementPlus] } })
        expect(
            await screen.findByRole('heading', { name: '审计' }),
        ).toBeInTheDocument()
        expect(
            await screen.findByText('finance.entry.create'),
        ).toBeInTheDocument()
    })
})
