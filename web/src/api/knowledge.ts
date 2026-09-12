import { errorCopy } from '../copy/errors'
import { apiClient, formatRequestError, type BrowserHttpClient } from './http'
import { isRecord, UUID } from './parse'

export interface KnowledgePoint {
    id: string
    title: string
    body_md: string
    sort_order: number
    source_file_id?: string | null
}

export interface KnowledgeRevision {
    id: string
    version: number
    body_md: string
    change_reason: string
    stage_title?: string
    is_canonical?: boolean
    source_file_id?: string | null
    points_json?: Array<Record<string, unknown>>
    released?: boolean
    points_review?: string
    created_at: string
}

export interface KnowledgeDoc {
    id: string
    title: string
    body_md: string
    tags: string[]
    status: 'DRAFT' | 'PUBLISHED'
    updated_at: string
    points: KnowledgePoint[]
    project_id?: string | null
    stage_title?: string
    change_reason?: string
    is_canonical?: boolean
    source_file_id?: string | null
    published_revision_id?: string | null
    draft_revision_id?: string | null
    revisions?: KnowledgeRevision[]
}

export class KnowledgeContractError extends Error {
    constructor() {
        super('Invalid knowledge data')
        this.name = 'KnowledgeContractError'
    }
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
                      source_file_id:
                          typeof point.source_file_id === 'string'
                              ? point.source_file_id
                              : null,
                  }
              })
            : [],
        project_id:
            typeof value.project_id === 'string' ? value.project_id : null,
        stage_title: String(value.stage_title || ''),
        change_reason: String(value.change_reason || ''),
        is_canonical: Boolean(value.is_canonical),
        source_file_id:
            typeof value.source_file_id === 'string'
                ? value.source_file_id
                : null,
        published_revision_id:
            typeof value.published_revision_id === 'string'
                ? value.published_revision_id
                : null,
        draft_revision_id:
            typeof value.draft_revision_id === 'string'
                ? value.draft_revision_id
                : null,
        revisions: Array.isArray(value.revisions)
            ? value.revisions.map((item) => {
                  if (!isRecord(item)) {
                      throw new KnowledgeContractError()
                  }
                  return {
                      id: String(item.id),
                      version: Number(item.version) || 0,
                      body_md: String(item.body_md || ''),
                      change_reason: String(item.change_reason || ''),
                      stage_title: String(item.stage_title || ''),
                      is_canonical: Boolean(item.is_canonical),
                      source_file_id:
                          typeof item.source_file_id === 'string'
                              ? item.source_file_id
                              : null,
                      points_json: Array.isArray(item.points_json)
                          ? item.points_json.filter(isRecord)
                          : [],
                      released: Boolean(item.released),
                      points_review: String(item.points_review || 'OK'),
                      created_at: String(item.created_at || ''),
                  }
              })
            : [],
    }
}

export function knowledgeErrorMessage(error: unknown): string {
    return formatRequestError(errorCopy.knowledge, error, errorCopy.generic)
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
        async create(
            title: string,
            body_md: string,
            tags: string[] = [],
            extras: {
                project_id?: string | null
                stage_title?: string
                change_reason?: string
                is_canonical?: boolean
            } = {},
        ): Promise<KnowledgeDoc> {
            const response = await client.post('/knowledge', {
                title,
                body_md,
                tags,
                ...extras,
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
        async update(
            id: string,
            patch: {
                title?: string
                body_md?: string
                tags?: string[]
                status?: 'DRAFT' | 'PUBLISHED'
                project_id?: string | null
                stage_title?: string
                change_reason?: string
                is_canonical?: boolean
            },
        ): Promise<KnowledgeDoc> {
            const response = await client.patch(`/knowledge/${id}`, patch)
            if (response.status !== 200) throw new KnowledgeContractError()
            return parseDoc(response.data)
        },
        async reviewPoints(
            docId: string,
            revisionId: string,
            action: 'confirm' | 'clear',
        ): Promise<KnowledgeDoc> {
            const response = await client.post(
                `/knowledge/${docId}/revisions/${revisionId}/points-review`,
                { action },
            )
            if (response.status !== 200) throw new KnowledgeContractError()
            return parseDoc(response.data)
        },
        async sourceDownload(
            id: string,
            fileId?: string,
        ): Promise<{ url: string }> {
            const params = fileId ? { file_id: fileId } : undefined
            const response = await client.get(
                `/knowledge/${id}/source-download`,
                params ? { params } : undefined,
            )
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new KnowledgeContractError()
            }
            return { url: String(response.data.url || '') }
        },
    })
}

export const knowledgeApi = createKnowledgeApi(apiClient)
