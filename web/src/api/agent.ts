import { apiClient, formatRequestError, type BrowserHttpClient } from './http'

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export type CardKind =
    | 'finance_entry'
    | 'finance_adjust'
    | 'project_create'
    | 'project_update'
    | 'milestone_change'
    | 'file_move'
    | 'memory'
    | 'knowledge_ingest'

export type CardStatus =
    'PROPOSED' | 'CONFIRMED' | 'COMMITTED' | 'REVISED' | 'REJECTED' | 'FAILED'

export interface AgentConversation {
    id: string
    title: string
    summary: string
    created_at: string
    last_message_at: string
    archived_at: string | null
}

export interface AgentMessage {
    id: string
    role: 'user' | 'assistant' | 'tool' | 'system'
    content: string
    card_ids: string[]
    created_at: string
}

export interface AgentCard {
    id: string
    conversation_id: string
    message_id: string | null
    kind: CardKind
    payload: Record<string, unknown>
    status: CardStatus
    decided_at: string | null
    committed_object_type: string | null
    committed_object_id: string | null
    error: string | null
}

export interface ChatTurn {
    conversation_id: string
    message: AgentMessage
    cards: AgentCard[]
    offline: boolean
}

export interface SoulVersion {
    id: string
    content: string
    note: string
    created_at: string
    is_active: boolean
}

export interface AgentMemory {
    id: string
    kind: string
    content: string
    importance: number
    pinned: boolean
    status: 'ACTIVE' | 'ARCHIVED'
    created_at: string
    recall_count: number
}

export class AgentContractError extends Error {
    constructor() {
        super('Invalid agent data')
        this.name = 'AgentContractError'
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function uuid(value: unknown): value is string {
    return typeof value === 'string' && UUID.test(value)
}

function parseConversation(value: unknown): AgentConversation {
    if (
        !isRecord(value) ||
        !uuid(value.id) ||
        typeof value.title !== 'string' ||
        typeof value.summary !== 'string' ||
        typeof value.created_at !== 'string' ||
        typeof value.last_message_at !== 'string'
    ) {
        throw new AgentContractError()
    }
    return {
        id: value.id,
        title: value.title,
        summary: value.summary,
        created_at: value.created_at,
        last_message_at: value.last_message_at,
        archived_at:
            typeof value.archived_at === 'string' ? value.archived_at : null,
    }
}

function parseMessage(value: unknown): AgentMessage {
    if (
        !isRecord(value) ||
        !uuid(value.id) ||
        typeof value.role !== 'string' ||
        typeof value.content !== 'string' ||
        !Array.isArray(value.card_ids) ||
        typeof value.created_at !== 'string'
    ) {
        throw new AgentContractError()
    }
    return {
        id: value.id,
        role: value.role as AgentMessage['role'],
        content: value.content,
        card_ids: value.card_ids.filter((item): item is string => uuid(item)),
        created_at: value.created_at,
    }
}

function parseCard(value: unknown): AgentCard {
    if (
        !isRecord(value) ||
        !uuid(value.id) ||
        !uuid(value.conversation_id) ||
        typeof value.kind !== 'string' ||
        typeof value.status !== 'string' ||
        !isRecord(value.payload)
    ) {
        throw new AgentContractError()
    }
    return {
        id: value.id,
        conversation_id: value.conversation_id,
        message_id: uuid(value.message_id) ? value.message_id : null,
        kind: value.kind as CardKind,
        payload: value.payload,
        status: value.status as CardStatus,
        decided_at:
            typeof value.decided_at === 'string' ? value.decided_at : null,
        committed_object_type:
            typeof value.committed_object_type === 'string'
                ? value.committed_object_type
                : null,
        committed_object_id: uuid(value.committed_object_id)
            ? value.committed_object_id
            : null,
        error: typeof value.error === 'string' ? value.error : null,
    }
}

function parseSseText(raw: string): string {
    try {
        const parsed: unknown = JSON.parse(raw)
        return typeof parsed === 'string' ? parsed : raw
    } catch {
        return raw
    }
}

function parseTurn(value: unknown): ChatTurn {
    if (
        !isRecord(value) ||
        !uuid(value.conversation_id) ||
        !Array.isArray(value.cards)
    ) {
        throw new AgentContractError()
    }
    return {
        conversation_id: value.conversation_id,
        message: parseMessage(value.message),
        cards: value.cards.map(parseCard),
        offline: value.offline === true,
    }
}

export function agentErrorMessage(error: unknown): string {
    return formatRequestError(
        '霜月暂时无法完成操作',
        error,
        '霜月暂时无法完成操作，请稍后重试。',
    )
}

export function createAgentApi(client: BrowserHttpClient) {
    return Object.freeze({
        async listConversations(query?: string): Promise<AgentConversation[]> {
            const params = query ? { q: query } : undefined
            const response = await client.get(
                '/agent/conversations',
                params ? { params } : undefined,
            )
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new AgentContractError()
            }
            return response.data.map(parseConversation)
        },
        async createConversation(): Promise<AgentConversation> {
            const response = await client.post('/agent/conversations', {})
            if (response.status !== 201) throw new AgentContractError()
            return parseConversation(response.data)
        },
        async listMessages(id: string): Promise<AgentMessage[]> {
            if (!uuid(id)) throw new AgentContractError()
            const response = await client.get(
                `/agent/conversations/${id}/messages`,
            )
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new AgentContractError()
            }
            return response.data.map(parseMessage)
        },
        async listCards(id: string): Promise<AgentCard[]> {
            if (!uuid(id)) throw new AgentContractError()
            const response = await client.get(
                `/agent/conversations/${id}/cards`,
            )
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new AgentContractError()
            }
            return response.data.map(parseCard)
        },
        async send(
            id: string,
            content: string,
            fileId?: string,
        ): Promise<ChatTurn> {
            if (!uuid(id) || (!content.trim() && !fileId)) {
                throw new AgentContractError()
            }
            if (fileId && !uuid(fileId)) throw new AgentContractError()
            const response = await client.post(
                `/agent/conversations/${id}/messages`,
                {
                    content: content.trim(),
                    file_id: fileId ?? null,
                },
            )
            if (response.status !== 200) throw new AgentContractError()
            return parseTurn(response.data)
        },
        async patch(
            cardId: string,
            payload: Record<string, unknown>,
            note = '',
        ): Promise<AgentCard> {
            if (!uuid(cardId)) throw new AgentContractError()
            const response = await client.patch(`/agent/cards/${cardId}`, {
                payload,
                note,
            })
            if (response.status !== 200) throw new AgentContractError()
            return parseCard(response.data)
        },
        async stream(
            id: string,
            content: string,
            onToken: (piece: string) => void,
            fileId?: string,
        ): Promise<ChatTurn> {
            if (!uuid(id) || (!content.trim() && !fileId)) {
                throw new AgentContractError()
            }
            const csrf = document.cookie
                .split(';')
                .map((item) => item.trim())
                .find((item) => item.startsWith('XSRF-TOKEN='))
            let token: string | undefined
            try {
                token = csrf
                    ? decodeURIComponent(csrf.slice('XSRF-TOKEN='.length))
                    : undefined
            } catch {
                token = undefined
            }
            const headers: Record<string, string> = {
                'Content-Type': 'application/json',
            }
            if (token) headers['X-CSRF-Token'] = token
            const response = await fetch(
                `/api/v1/agent/conversations/${id}/messages/stream`,
                {
                    method: 'POST',
                    credentials: 'include',
                    headers,
                    body: JSON.stringify({
                        content: content.trim(),
                        file_id: fileId ?? null,
                    }),
                },
            )
            if (!response.ok || !response.body) throw new AgentContractError()
            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            let buffer = ''
            let turn: ChatTurn | undefined
            const dispatch = (block: string) => {
                const lines = block.split('\n')
                let event = 'message'
                const data: string[] = []
                for (const line of lines) {
                    if (line.startsWith('event:')) event = line.slice(6).trim()
                    if (line.startsWith('data:'))
                        data.push(line.slice(5).trim())
                }
                if (!data.length) return
                const raw = data.join('\n')
                if (event === 'token' || event === 'offline') {
                    onToken(parseSseText(raw))
                    return
                }
                if (event === 'done') {
                    turn = parseTurn(JSON.parse(raw))
                }
            }
            while (true) {
                const { done, value } = await reader.read()
                buffer += decoder.decode(value || new Uint8Array(), {
                    stream: !done,
                })
                const parts = buffer.split('\n\n')
                buffer = parts.pop() ?? ''
                for (const part of parts) dispatch(part)
                if (done) {
                    if (buffer.trim()) dispatch(buffer)
                    break
                }
            }
            if (!turn) throw new AgentContractError()
            return turn
        },
        async confirm(cardId: string): Promise<AgentCard> {
            if (!uuid(cardId)) throw new AgentContractError()
            const response = await client.post(`/agent/cards/${cardId}/confirm`)
            if (response.status !== 200) throw new AgentContractError()
            return parseCard(response.data)
        },
        async revise(cardId: string, instruction: string): Promise<ChatTurn> {
            if (!uuid(cardId) || !instruction.trim())
                throw new AgentContractError()
            const response = await client.post(
                `/agent/cards/${cardId}/revise`,
                {
                    instruction: instruction.trim(),
                },
            )
            if (response.status !== 200) throw new AgentContractError()
            return parseTurn(response.data)
        },
        async reject(cardId: string): Promise<AgentCard> {
            if (!uuid(cardId)) throw new AgentContractError()
            const response = await client.post(`/agent/cards/${cardId}/reject`)
            if (response.status !== 200) throw new AgentContractError()
            return parseCard(response.data)
        },
        async listSoul(): Promise<SoulVersion[]> {
            const response = await client.get('/agent/soul')
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new AgentContractError()
            }
            return response.data.map((item) => {
                if (
                    !isRecord(item) ||
                    !uuid(item.id) ||
                    typeof item.content !== 'string'
                ) {
                    throw new AgentContractError()
                }
                return {
                    id: item.id,
                    content: item.content,
                    note: String(item.note || ''),
                    created_at: String(item.created_at || ''),
                    is_active: item.is_active === true,
                }
            })
        },
        async writeSoul(content: string, note: string): Promise<SoulVersion> {
            const response = await client.post('/agent/soul', { content, note })
            if (response.status !== 201) throw new AgentContractError()
            return (
                (await this.listSoul()).find((item) => item.is_active) ??
                (await this.listSoul())[0]
            )
        },
        async activateSoul(id: string): Promise<SoulVersion> {
            if (!uuid(id)) throw new AgentContractError()
            const response = await client.post(`/agent/soul/${id}/activate`)
            if (response.status !== 200) throw new AgentContractError()
            return (
                (await this.listSoul()).find((item) => item.id === id) ??
                (await this.listSoul())[0]
            )
        },
        async previewSoul(): Promise<string> {
            const response = await client.get('/agent/soul/preview')
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new AgentContractError()
            }
            if (typeof response.data.prompt !== 'string')
                throw new AgentContractError()
            return response.data.prompt
        },
        async listMemories(): Promise<AgentMemory[]> {
            const response = await client.get('/agent/memories')
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new AgentContractError()
            }
            return response.data.map((item) => {
                if (
                    !isRecord(item) ||
                    !uuid(item.id) ||
                    typeof item.content !== 'string'
                ) {
                    throw new AgentContractError()
                }
                return {
                    id: item.id,
                    kind: String(item.kind),
                    content: item.content,
                    importance: Number(item.importance) || 1,
                    pinned: item.pinned === true,
                    status: item.status === 'ARCHIVED' ? 'ARCHIVED' : 'ACTIVE',
                    created_at: String(item.created_at || ''),
                    recall_count: Number(item.recall_count) || 0,
                }
            })
        },
        async patchMemory(
            id: string,
            patch: {
                content?: string
                pinned?: boolean
                status?: 'ACTIVE' | 'ARCHIVED'
            },
        ): Promise<AgentMemory> {
            if (!uuid(id)) throw new AgentContractError()
            const response = await client.patch(`/agent/memories/${id}`, patch)
            if (
                response.status !== 200 ||
                !isRecord(response.data) ||
                !uuid(response.data.id)
            ) {
                throw new AgentContractError()
            }
            return {
                id: response.data.id,
                kind: String(response.data.kind),
                content: String(response.data.content),
                importance: Number(response.data.importance) || 1,
                pinned: response.data.pinned === true,
                status:
                    response.data.status === 'ARCHIVED' ? 'ARCHIVED' : 'ACTIVE',
                created_at: String(response.data.created_at || ''),
                recall_count: Number(response.data.recall_count) || 0,
            }
        },
    })
}

export const agentApi = createAgentApi(apiClient)
