import { apiClient, type BrowserHttpClient } from './http'

export const MAX_OWNER_IMPORTS = 100

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const RESULT_CODE = /^[A-Z][A-Z0-9_]{0,63}$/
const JOB_STATUSES = [
    'UPLOADING',
    'SCANNING',
    'RECEIVED',
    'REJECTED',
    'CONFLICT',
] as const
const FILE_STATES = [
    'UPLOADING',
    'QUARANTINED',
    'SCANNING',
    'CLEAN',
    'INFECTED',
    'FAILED',
] as const
const ATTACHMENT_KINDS = ['ORIGINAL', 'REVISED', 'K3_RAW'] as const

type JobStatus = (typeof JOB_STATUSES)[number]
type FileState = (typeof FILE_STATES)[number]
type AttachmentKind = (typeof ATTACHMENT_KINDS)[number]

export interface OwnerImportAttachment {
    readonly id: string
    readonly file_id: string
    readonly upload_id: string
    readonly kind: AttachmentKind
    readonly file_state: FileState
}

export interface OwnerImportSummary {
    readonly id: string
    readonly project_id: string
    readonly local_task_id: string
    readonly external_document_reference: string | null
    readonly model_label: string
    readonly status: JobStatus
    readonly result_code: string | null
    readonly submitted_at: string | null
    readonly created_at: string
    readonly updated_at: string
    readonly attachments: readonly OwnerImportAttachment[]
}

export class ImportContractError extends Error {
    constructor() {
        super('Invalid import data')
        this.name = 'ImportContractError'
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    if (typeof value !== 'object' || value === null || Array.isArray(value))
        return false
    const prototype = Object.getPrototypeOf(value)
    return prototype === Object.prototype || prototype === null
}

function hasRequiredKeys(
    value: Record<string, unknown>,
    required: readonly string[],
): boolean {
    return required.every((key) => key in value)
}

function uuid(value: unknown): value is string {
    return typeof value === 'string' && UUID.test(value)
}

function safeText(value: unknown, maximum: number): value is string {
    if (
        typeof value !== 'string' ||
        !value.trim() ||
        [...value].length > maximum
    )
        return false
    for (let index = 0; index < value.length; index += 1) {
        const code = value.charCodeAt(index)
        if (code <= 31 || (code >= 127 && code <= 159)) return false
        if (code >= 0xd800 && code <= 0xdbff) {
            const next = value.charCodeAt(index + 1)
            if (next < 0xdc00 || next > 0xdfff) return false
            index += 1
        } else if (code >= 0xdc00 && code <= 0xdfff) return false
    }
    return true
}

function timestamp(value: unknown): string {
    if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) {
        throw new ImportContractError()
    }
    const milliseconds = Date.parse(value)
    if (!Number.isFinite(milliseconds)) throw new ImportContractError()
    return new Date(milliseconds).toISOString()
}

function nullableTimestamp(value: unknown): string | null {
    return value === null ? null : timestamp(value)
}

function parseAttachment(value: unknown): OwnerImportAttachment {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'file_id',
            'file_state',
            'id',
            'kind',
            'upload_id',
        ]) ||
        !uuid(value.id) ||
        !uuid(value.file_id) ||
        !uuid(value.upload_id) ||
        !ATTACHMENT_KINDS.includes(value.kind as AttachmentKind) ||
        !FILE_STATES.includes(value.file_state as FileState)
    ) {
        throw new ImportContractError()
    }
    return {
        file_id: value.file_id,
        file_state: value.file_state as FileState,
        id: value.id,
        kind: value.kind as AttachmentKind,
        upload_id: value.upload_id,
    }
}

function validStateSemantics(
    status: JobStatus,
    resultCode: string | null,
    submittedAt: string | null,
): boolean {
    if (status === 'UPLOADING')
        return resultCode === null && submittedAt === null
    if (status === 'SCANNING' || status === 'RECEIVED')
        return resultCode === null && submittedAt !== null
    return resultCode !== null && submittedAt !== null
}

function parseSummary(value: unknown): OwnerImportSummary {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'attachments',
            'created_at',
            'external_document_reference',
            'id',
            'local_task_id',
            'model_label',
            'project_id',
            'result_code',
            'status',
            'submitted_at',
            'updated_at',
        ]) ||
        !uuid(value.id) ||
        !uuid(value.project_id) ||
        !safeText(value.local_task_id, 255) ||
        !safeText(value.model_label, 128) ||
        (value.external_document_reference !== null &&
            !safeText(value.external_document_reference, 1024)) ||
        !JOB_STATUSES.includes(value.status as JobStatus) ||
        (value.result_code !== null &&
            (typeof value.result_code !== 'string' ||
                !RESULT_CODE.test(value.result_code))) ||
        !Array.isArray(value.attachments) ||
        value.attachments.length < 1 ||
        value.attachments.length > 3
    ) {
        throw new ImportContractError()
    }
    const attachments = value.attachments.map(parseAttachment)
    const ids = new Set(attachments.map((attachment) => attachment.id))
    const fileIds = new Set(attachments.map((attachment) => attachment.file_id))
    const uploadIds = new Set(
        attachments.map((attachment) => attachment.upload_id),
    )
    const kinds = new Set(attachments.map((attachment) => attachment.kind))
    if (
        [ids, fileIds, uploadIds, kinds].some(
            (values) => values.size !== attachments.length,
        )
    ) {
        throw new ImportContractError()
    }
    const status = value.status as JobStatus
    const resultCode = value.result_code as string | null
    const createdAt = timestamp(value.created_at)
    const submittedAt = nullableTimestamp(value.submitted_at)
    const updatedAt = timestamp(value.updated_at)
    if (
        !validStateSemantics(status, resultCode, submittedAt) ||
        createdAt > updatedAt ||
        (submittedAt !== null &&
            (submittedAt < createdAt || submittedAt > updatedAt))
    ) {
        throw new ImportContractError()
    }
    return {
        attachments,
        created_at: createdAt,
        external_document_reference: value.external_document_reference as
            string | null,
        id: value.id,
        local_task_id: value.local_task_id,
        model_label: value.model_label,
        project_id: value.project_id,
        result_code: resultCode,
        status,
        submitted_at: submittedAt,
        updated_at: updatedAt,
    }
}

export function importErrorMessage(_error: unknown): string {
    void _error
    return '\u5bfc\u5165\u4efb\u52a1\u6682\u65f6\u65e0\u6cd5\u52a0\u8f7d\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002'
}

export function createImportsApi(client: BrowserHttpClient) {
    return Object.freeze({
        async list(limit = MAX_OWNER_IMPORTS): Promise<OwnerImportSummary[]> {
            if (
                !Number.isInteger(limit) ||
                limit < 1 ||
                limit > MAX_OWNER_IMPORTS
            )
                throw new ImportContractError()
            const response = await client.get('/owner/import-jobs', {
                params: { limit: String(limit) },
            })
            if (
                response.status !== 200 ||
                !Array.isArray(response.data) ||
                response.data.length > MAX_OWNER_IMPORTS
            ) {
                throw new ImportContractError()
            }
            return response.data.map(parseSummary)
        },
    })
}

export const importsApi = createImportsApi(apiClient)
