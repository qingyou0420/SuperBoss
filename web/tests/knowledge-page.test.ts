import { render, screen } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, test, vi } from 'vitest'

import KnowledgePage from '../src/pages/KnowledgePage.vue'
import { useAuthStore } from '../src/stores/auth'

vi.mock('../src/api/knowledge', () => ({
    knowledgeApi: {
        list: vi.fn().mockResolvedValue([
            {
                id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
                title: '星野合作',
                body_md: '默认三个里程碑',
                tags: [],
                status: 'PUBLISHED',
                updated_at: '2026-09-05T00:00:00Z',
                points: [],
            },
        ]),
        create: vi.fn(),
        publish: vi.fn(),
    },
    knowledgeErrorMessage: () => '知识库暂时无法加载，请稍后重试。',
}))

describe('knowledge page', () => {
    test('STAFF can read published documents', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'staff',
            display_name: 'Staff',
            role: 'STAFF',
            must_change_password: false,
        }
        render(KnowledgePage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('星野合作')).toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: '保存草稿' }),
        ).not.toBeInTheDocument()
    })
})
