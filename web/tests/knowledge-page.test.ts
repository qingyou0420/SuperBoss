import { fireEvent, render, screen } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, test, vi } from 'vitest'

import { knowledgeApi } from '../src/api/knowledge'
import KnowledgePage from '../src/pages/KnowledgePage.vue'
import { useAuthStore } from '../src/stores/auth'

const ids = vi.hoisted(() => ({
    sourceA: '019f2b8e-18f0-7f31-9f42-3e6a76b9f801',
    sourceB: '019f2b8e-18f0-7f31-9f42-3e6a76b9f802',
    doc: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
    rev2: '019f2b8e-18f0-7f31-9f42-3e6a76b9f822',
}))

vi.mock('../src/api/knowledge', () => ({
    knowledgeApi: {
        list: vi.fn().mockResolvedValue([
            {
                id: ids.doc,
                title: '星野合作',
                body_md: '默认三个里程碑',
                tags: [],
                status: 'PUBLISHED',
                updated_at: '2026-09-05T00:00:00Z',
                source_file_id: ids.sourceA,
                published_revision_id: ids.rev2,
                draft_revision_id: ids.rev2,
                points: [
                    {
                        id: 'p1',
                        title: '来源A要点',
                        body_md: 'A',
                        sort_order: 0,
                        source_file_id: ids.sourceA,
                    },
                    {
                        id: 'p2',
                        title: '来源B要点',
                        body_md: 'B',
                        sort_order: 1,
                        source_file_id: ids.sourceB,
                    },
                ],
                revisions: [
                    {
                        id: ids.rev2,
                        version: 2,
                        body_md: '追加后的正文',
                        change_reason: '追加知识点',
                        source_file_id: ids.sourceA,
                        released: true,
                        created_at: '2026-09-05T00:00:00Z',
                        points_json: [
                            {
                                id: 'p1',
                                title: '来源A要点',
                                body_md: 'A',
                                sort_order: 0,
                                source_file_id: ids.sourceA,
                            },
                            {
                                id: 'p2',
                                title: '来源B要点',
                                body_md: 'B',
                                sort_order: 1,
                                source_file_id: ids.sourceB,
                            },
                        ],
                    },
                ],
            },
        ]),
        create: vi.fn(),
        publish: vi.fn(),
        update: vi.fn(),
        reviewPoints: vi.fn(),
        sourceDownload: vi.fn().mockResolvedValue({
            url: 'https://files.example/b',
        }),
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
        expect(
            await screen.findByRole('heading', { name: '星野合作' }),
        ).toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: '保存草稿' }),
        ).not.toBeInTheDocument()
    })

    test('OWNER can confirm a revision waiting for points review', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: 'Owner',
            role: 'OWNER',
            must_change_password: false,
        }
        vi.mocked(knowledgeApi.list).mockResolvedValueOnce([
            {
                id: ids.doc,
                title: '待核对文档',
                body_md: '正文',
                tags: [],
                status: 'PUBLISHED',
                updated_at: '2026-09-05T00:00:00Z',
                points: [],
                revisions: [
                    {
                        id: ids.rev2,
                        version: 1,
                        body_md: '正文',
                        change_reason: '历史快照',
                        released: true,
                        points_review: 'NEEDS_REVIEW',
                        created_at: '2026-09-05T00:00:00Z',
                        points_json: [],
                    },
                ],
            },
        ])
        vi.mocked(knowledgeApi.reviewPoints).mockResolvedValue({
            id: ids.doc,
            title: '待核对文档',
            body_md: '正文',
            tags: [],
            status: 'PUBLISHED',
            updated_at: '2026-09-05T00:00:00Z',
            points: [],
            revisions: [],
        })
        render(KnowledgePage, { global: { plugins: [pinia, ElementPlus] } })
        const confirm = await screen.findByRole('button', {
            name: '确认知识点可开放',
        })
        await fireEvent.click(confirm)
        expect(knowledgeApi.reviewPoints).toHaveBeenCalledWith(
            ids.doc,
            ids.rev2,
            'confirm',
        )
    })

    test('selected revision exposes every point source download', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: 'Owner',
            role: 'OWNER',
            must_change_password: false,
        }
        render(KnowledgePage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('来源B要点')).toBeInTheDocument()
        const downloads = await screen.findAllByRole('button', {
            name: '下载参考定稿',
        })
        expect(downloads.length).toBeGreaterThan(1)
    })
})
