import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import ChatPage from '../src/pages/ChatPage.vue'
import { useAuthStore } from '../src/stores/auth'

const ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const CARD = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'

const mocks = vi.hoisted(() => ({
    agentApi: {
        listConversations: vi.fn(),
        createConversation: vi.fn(),
        listMessages: vi.fn(),
        listCards: vi.fn(),
        send: vi.fn(),
        stream: vi.fn(),
        patch: vi.fn(),
        confirm: vi.fn(),
        revise: vi.fn(),
        reject: vi.fn(),
    },
}))

vi.mock('../src/api/files', () => ({
    filesApi: {
        listFolders: vi.fn().mockResolvedValue([]),
    },
}))

vi.mock('../src/api/agent', async () => {
    const actual =
        await vi.importActual<typeof import('../src/api/agent')>(
            '../src/api/agent',
        )
    return {
        ...actual,
        agentApi: mocks.agentApi,
        agentErrorMessage: () => '失败',
    }
})

beforeEach(() => {
    vi.clearAllMocks()
    const pinia = createPinia()
    setActivePinia(pinia)
    useAuthStore().user = {
        username: 'owner',
        display_name: '清游',
        role: 'OWNER',
        must_change_password: false,
    }
    mocks.agentApi.listConversations.mockResolvedValue([
        {
            id: ID,
            title: '房租',
            summary: '',
            created_at: '2026-09-05T00:00:00Z',
            last_message_at: '2026-09-05T00:00:00Z',
            archived_at: null,
        },
    ])
    mocks.agentApi.listMessages.mockResolvedValue([
        {
            id: CARD,
            role: 'assistant',
            content: '请确认房租。',
            card_ids: [CARD],
            created_at: '2026-09-05T00:00:00Z',
        },
    ])
    mocks.agentApi.listCards.mockResolvedValue([
        {
            id: CARD,
            conversation_id: ID,
            message_id: CARD,
            kind: 'finance_entry',
            payload: { category: '房租', amount_cents: 800000 },
            status: 'PROPOSED',
            decided_at: null,
            committed_object_type: null,
            committed_object_id: null,
            error: null,
        },
    ])
    mocks.agentApi.confirm.mockResolvedValue({
        id: CARD,
        conversation_id: ID,
        message_id: CARD,
        kind: 'finance_entry',
        payload: { category: '房租' },
        status: 'COMMITTED',
        decided_at: '2026-09-05T00:00:01Z',
        committed_object_type: 'finance_entry',
        committed_object_id: CARD,
        error: null,
    })
})

describe('chat page', () => {
    test('OWNER can confirm a proposed finance card', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: '清游',
            role: 'OWNER',
            must_change_password: false,
        }
        render(ChatPage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('请确认房租。')).toBeInTheDocument()
        await fireEvent.click(screen.getByRole('button', { name: '确认入库' }))
        expect(mocks.agentApi.confirm).toHaveBeenCalledWith(CARD)
        expect(await screen.findByText(/已入库/)).toBeInTheDocument()
    })

    test('OWNER can edit card fields inline before confirming', async () => {
        mocks.agentApi.patch.mockResolvedValue({
            id: CARD,
            conversation_id: ID,
            message_id: CARD,
            kind: 'finance_entry',
            payload: { category: '水电', amount_cents: 800000 },
            status: 'PROPOSED',
            decided_at: null,
            committed_object_type: null,
            committed_object_id: null,
            error: null,
        })
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: '清游',
            role: 'OWNER',
            must_change_password: false,
        }
        render(ChatPage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('请确认房租。')).toBeInTheDocument()
        await fireEvent.update(screen.getByDisplayValue('房租'), '水电')
        await fireEvent.click(screen.getByRole('button', { name: '保存修改' }))
        expect(mocks.agentApi.patch).toHaveBeenCalledWith(
            CARD,
            expect.objectContaining({ category: '水电' }),
            '',
        )
    })

    test('searches conversations by title', async () => {
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: '清游',
            role: 'OWNER',
            must_change_password: false,
        }
        render(ChatPage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('房租')).toBeInTheDocument()
        await fireEvent.update(screen.getByLabelText('搜索会话'), '房租')
        await fireEvent.click(screen.getByRole('button', { name: '查找' }))
        expect(mocks.agentApi.listConversations).toHaveBeenLastCalledWith(
            '房租',
        )
    })

    test('send uses stream then reloads the thread', async () => {
        mocks.agentApi.stream.mockResolvedValue({
            conversation_id: ID,
            message: {
                id: CARD,
                role: 'assistant',
                content: '请确认房租。',
                card_ids: [CARD],
                created_at: '2026-09-05T00:00:00Z',
            },
            cards: [],
            offline: false,
        })
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: '清游',
            role: 'OWNER',
            must_change_password: false,
        }
        render(ChatPage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('请确认房租。')).toBeInTheDocument()
        await fireEvent.update(screen.getByLabelText('给霜月'), '这个月房租')
        await fireEvent.click(screen.getByRole('button', { name: '发送' }))
        await waitFor(() => {
            expect(mocks.agentApi.stream).toHaveBeenCalledWith(
                ID,
                '这个月房租',
                expect.any(Function),
                undefined,
            )
        })
        expect(mocks.agentApi.send).not.toHaveBeenCalled()
    })

    test('falls back to send when stream fails', async () => {
        mocks.agentApi.stream.mockRejectedValue(new Error('sse'))
        mocks.agentApi.send.mockResolvedValue({
            conversation_id: ID,
            message: {
                id: CARD,
                role: 'assistant',
                content: '请确认房租。',
                card_ids: [CARD],
                created_at: '2026-09-05T00:00:00Z',
            },
            cards: [],
            offline: false,
        })
        const pinia = createPinia()
        setActivePinia(pinia)
        useAuthStore().user = {
            username: 'owner',
            display_name: '清游',
            role: 'OWNER',
            must_change_password: false,
        }
        render(ChatPage, { global: { plugins: [pinia, ElementPlus] } })
        expect(await screen.findByText('请确认房租。')).toBeInTheDocument()
        await fireEvent.update(screen.getByLabelText('给霜月'), '这个月房租')
        await fireEvent.click(screen.getByRole('button', { name: '发送' }))
        await waitFor(() => {
            expect(mocks.agentApi.send).toHaveBeenCalledWith(
                ID,
                '这个月房租',
                undefined,
            )
        })
    })
})
