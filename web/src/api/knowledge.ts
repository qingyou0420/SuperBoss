import { apiClient, formatRequestError, type BrowserHttpClient } from './http'

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export interface KnowledgePoint {
    id: string
    title: string
    body_md: string
    sort_order: number
}

export interface KnowledgeDoc {
    id: string
    title: string
    body_md: string
    tags: string[]
    status: 'DRAFT' | 'PUBLISHED'
    updated_at: string
    points: KnowledgePoint[]
}

export class KnowledgeContractError extends Error {
    constructor() {
        super('Invalid knowledge data')
        this.name = 'KnowledgeContractError'
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseDoc(value: unknown): KnowledgeDoc {
    if (
        !isRecord(value) ||
        typeof value.id !== 'string' ||
        !UUID.test(value.id)
    ) {
        throw new KnowledgeContractError()
    }
    return {
        id: value.id,
        title: String(value.title || ''),
        body_md: String(value.body_md || ''),
        tags: Array.isArray(value.tags) ? value.tags.map(String) : [],
        status: value.status === 'PUBLISHED' ? 'PUBLISHED' : 'DRAFT',
        updated_at: String(value.updated_at || ''),
        points: Array.isArray(value.points)
            ? value.points.map((point) => {
                  if (!isRecord(point)) throw new KnowledgeContractError()
                  return {
                      id: String(point.id),
                      title: String(point.title || ''),
                      body_md: String(point.body_md || ''),
                      sort_order: Number(point.sort_order) || 0,
                  }
              })
            : [],
    }
}

export function knowledgeErrorMessage(error: unknown): string {
    return formatRequestError(
        '知识库暂时无法加载',
        error,
        '知识库暂时无法加载，请稍后重试。',
    )
}

export function createKnowledgeApi(client: BrowserHttpClient) {
    return Object.freeze({
        async list(query?: string): Promise<KnowledgeDoc[]> {
            const params = query ? { q: query } : undefined
            const response = await client.get(
                '/knowledge',
                params ? { params } : undefined,
            )
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new KnowledgeContractError()
            }
            return response.data.map(parseDoc)
        },
        async create(title: string, body_md: string): Promise<KnowledgeDoc> {
            const response = await client.post('/knowledge', {
                title,
                body_md,
                tags: [],
            })
            if (response.status !== 201) throw new KnowledgeContractError()
            return parseDoc(response.data)
        },
        async publish(id: string): Promise<KnowledgeDoc> {
            const response = await client.patch(`/knowledge/${id}`, {
                status: 'PUBLISHED',
            })
            if (response.status !== 200) throw new KnowledgeContractError()
            return parseDoc(response.data)
        },
    })
}

export const knowledgeApi = createKnowledgeApi(apiClient)
