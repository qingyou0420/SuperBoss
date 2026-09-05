import { apiClient, formatRequestError, type BrowserHttpClient } from './http'

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export interface AuditEvent {
    id: string
    actor_kind: string
    actor_id: string | null
    action: string
    object_type: string
    object_id: string | null
    project_id: string | null
    outcome: string
    metadata_json: Record<string, unknown>
    request_id: string | null
    created_at: string
}

export class AuditContractError extends Error {
    constructor() {
        super('Invalid audit data')
        this.name = 'AuditContractError'
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseEvent(value: unknown): AuditEvent {
    if (
        !isRecord(value) ||
        typeof value.id !== 'string' ||
        !UUID.test(value.id) ||
        typeof value.action !== 'string' ||
        typeof value.outcome !== 'string' ||
        typeof value.created_at !== 'string'
    ) {
        throw new AuditContractError()
    }
    return {
        id: value.id,
        actor_kind: String(value.actor_kind || ''),
        actor_id: typeof value.actor_id === 'string' ? value.actor_id : null,
        action: value.action,
        object_type: String(value.object_type || ''),
        object_id: typeof value.object_id === 'string' ? value.object_id : null,
        project_id:
            typeof value.project_id === 'string' ? value.project_id : null,
        outcome: value.outcome,
        metadata_json: isRecord(value.metadata_json) ? value.metadata_json : {},
        request_id:
            typeof value.request_id === 'string' ? value.request_id : null,
        created_at: value.created_at,
    }
}

export function auditErrorMessage(error: unknown): string {
    return formatRequestError(
        '审计记录暂时无法加载',
        error,
        '审计记录暂时无法加载，请稍后重试。',
    )
}

export function createAuditApi(client: BrowserHttpClient) {
    return Object.freeze({
        async list(limit = 50, action?: string): Promise<AuditEvent[]> {
            const params: Record<string, string> = { limit: String(limit) }
            if (action) params.action = action
            const response = await client.get('/audit', { params })
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new AuditContractError()
            }
            return response.data.map(parseEvent)
        },
    })
}

export const auditApi = createAuditApi(apiClient)
