import type {
    AxiosAdapter,
    AxiosResponse,
    InternalAxiosRequestConfig,
} from 'axios'
import { afterEach, describe, expect, test, vi } from 'vitest'

import { createAgentApi, AgentContractError } from '../src/api/agent'
import { createHttpClient } from '../src/api/http'

function response(
    config: InternalAxiosRequestConfig,
    status: number,
    data: unknown,
): AxiosResponse {
    return {
        config,
        data,
        headers: {},
        status,
        statusText: String(status),
    } as AxiosResponse
}

const ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const CARD = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'
const FILE = '019f2b8e-18f0-7f31-9f42-3e6a76b9f812'

const conversation = {
    id: ID,
    title: '房租',
    summary: '',
    created_at: '2026-09-05T00:00:00Z',
    last_message_at: '2026-09-05T00:00:00Z',
    archived_at: null,
}

const message = {
    id: CARD,
    role: 'assistant',
    content: '请确认',
    card_ids: [CARD],
    created_at: '2026-09-05T00:00:00Z',
}

const card = {
    id: CARD,
    conversation_id: ID,
    message_id: CARD,
    kind: 'finance_entry',
    payload: { amount_cents: 800000, category: '房租' },
    status: 'PROPOSED',
    decided_at: null,
    committed_object_type: null,
    committed_object_id: null,
    error: null,
}

describe('agent API', () => {
    test('creates a conversation and confirms a card', async () => {
        const adapter: AxiosAdapter = async (config) => {
            if (
                config.url === '/agent/conversations' &&
                config.method === 'post'
            ) {
                return response(config, 201, conversation)
            }
            if (String(config.url).includes('/confirm')) {
                return response(config, 200, { ...card, status: 'COMMITTED' })
            }
            return response(config, 200, {
                conversation_id: ID,
                message,
                cards: [card],
                offline: false,
            })
        }
        const api = createAgentApi(createHttpClient({ adapter }))
        await expect(api.createConversation()).resolves.toEqual(conversation)
        const turn = await api.send(ID, '公司房租 8000')
        expect(turn.cards[0]?.kind).toBe('finance_entry')
        await expect(api.confirm(CARD)).resolves.toMatchObject({
            status: 'COMMITTED',
        })
    })

    test('rejects an empty message before network', async () => {
        const api = createAgentApi(
            createHttpClient({
                adapter: async (config) => response(config, 200, {}),
            }),
        )
        await expect(api.send(ID, '  ')).rejects.toBeInstanceOf(
            AgentContractError,
        )
    })

    test('patches a card payload', async () => {
        const adapter: AxiosAdapter = async (config) =>
            response(config, 200, { ...card, payload: { category: '水电' } })
        const api = createAgentApi(createHttpClient({ adapter }))
        await expect(
            api.patch(CARD, { category: '水电' }, '改类别'),
        ).resolves.toMatchObject({
            payload: { category: '水电' },
        })
    })

    test('lists conversations with a search query', async () => {
        const adapter: AxiosAdapter = async (config) => {
            expect(config.params).toEqual({ q: '房租' })
            return response(config, 200, [conversation])
        }
        const api = createAgentApi(createHttpClient({ adapter }))
        await expect(api.listConversations('房租')).resolves.toEqual([
            conversation,
        ])
    })

    test('sends a message with a file attachment', async () => {
        const adapter: AxiosAdapter = async (config) => {
            const body =
                typeof config.data === 'string'
                    ? JSON.parse(config.data)
                    : config.data
            expect(body).toEqual({ content: '', file_id: FILE })
            return response(config, 200, {
                conversation_id: ID,
                message,
                cards: [],
                offline: false,
            })
        }
        const api = createAgentApi(createHttpClient({ adapter }))
        await expect(api.send(ID, '  ', FILE)).resolves.toMatchObject({
            conversation_id: ID,
            offline: false,
        })
    })

    test('streams tokens then parses the done turn', async () => {
        const sse = [
            'event: token\ndata: "请"\n\n',
            'event: token\ndata: "确认"\n\n',
            `event: done\ndata: ${JSON.stringify({
                conversation_id: ID,
                message,
                cards: [card],
                offline: false,
            })}\n\n`,
        ].join('')
        const stream = new ReadableStream({
            start(controller) {
                controller.enqueue(new TextEncoder().encode(sse))
                controller.close()
            },
        })
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            body: stream,
        })
        vi.stubGlobal('fetch', fetchMock)
        document.cookie = 'XSRF-TOKEN=csrf-token'
        const api = createAgentApi(
            createHttpClient({
                adapter: async (config) => response(config, 200, {}),
            }),
        )
        const pieces: string[] = []
        const turn = await api.stream(ID, '房租', (piece) => pieces.push(piece))
        expect(pieces).toEqual(['请', '确认'])
        expect(turn.cards[0]?.kind).toBe('finance_entry')
        expect(fetchMock).toHaveBeenCalledWith(
            `/api/v1/agent/conversations/${ID}/messages/stream`,
            expect.objectContaining({
                method: 'POST',
                credentials: 'include',
                headers: expect.objectContaining({
                    'X-CSRF-Token': 'csrf-token',
                }),
            }),
        )
    })

    test('offline SSE payload is parsed once', async () => {
        const sse = [
            `event: offline\ndata: ${JSON.stringify('霜月暂时离线')}\n\n`,
            `event: done\ndata: ${JSON.stringify({
                conversation_id: ID,
                message: { ...message, content: '霜月暂时离线' },
                cards: [],
                offline: true,
            })}\n\n`,
        ].join('')
        vi.stubGlobal(
            'fetch',
            vi.fn().mockResolvedValue({
                ok: true,
                body: new ReadableStream({
                    start(controller) {
                        controller.enqueue(new TextEncoder().encode(sse))
                        controller.close()
                    },
                }),
            }),
        )
        const api = createAgentApi(
            createHttpClient({
                adapter: async (config) => response(config, 200, {}),
            }),
        )
        const pieces: string[] = []
        const turn = await api.stream(ID, '房租', (piece) => pieces.push(piece))
        expect(pieces).toEqual(['霜月暂时离线'])
        expect(turn.offline).toBe(true)
    })
})

afterEach(() => {
    vi.unstubAllGlobals()
})
