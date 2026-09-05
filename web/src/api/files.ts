import {
    apiClient,
    formatRequestError,
    type BrowserHttpClient,
    HttpClientError,
} from './http'

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SHA256 = /^[0-9a-f]{64}$/
const MIME = /^[A-Za-z0-9!#$&^_.+-]+\/[A-Za-z0-9!#$&^_.+-]+$/
const MAX_FILE_BYTES = 100 * 1024 * 1024

export type FolderVisibility = 'ALL' | 'MANAGEMENT' | 'OWNER_ONLY'

export interface DriveFolder {
    readonly id: string
    readonly parent_id: string | null
    readonly name: string
    readonly visibility: FolderVisibility
}

export interface DriveFile {
    readonly id: string
    readonly folder_id: string
    readonly project_id: string | null
    readonly filename: string
    readonly size_bytes: number
    readonly content_type: string
    readonly state: FileUploadCompleted['state'] | 'UPLOADING'
    readonly created_at: string
}

export interface FileUploadStart {
    readonly folder_id: string
    readonly filename: string
    readonly size_bytes: number
    readonly sha256: string
    readonly content_type: string
    readonly project_id?: string | null
}

export interface FileUploadStarted {
    readonly upload_id: string
    readonly file_id: string
}

export interface FilePart {
    readonly part_number: number
    readonly etag: string
}

export interface FileUploadCompleted {
    readonly file_id: string
    readonly state: 'QUARANTINED' | 'SCANNING' | 'CLEAN' | 'INFECTED' | 'FAILED'
}

export class FileContractError extends Error {
    constructor() {
        super('Invalid file data')
        this.name = 'FileContractError'
    }
}

export class FileDownloadUnavailableError extends Error {
    readonly state: 'INFECTED' | 'FAILED'

    constructor(state: 'INFECTED' | 'FAILED') {
        super('File download is unavailable')
        Object.defineProperty(this, 'name', {
            value: 'FileDownloadUnavailableError',
        })
        this.state = state
        Object.freeze(this)
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

function safeText(value: unknown, maximum: number): value is string {
    if (typeof value !== 'string' || !value.trim()) return false
    if ([...value].length > maximum) return false
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

function uuid(value: unknown): value is string {
    return typeof value === 'string' && UUID.test(value)
}

function idempotencyKey(value: unknown): value is string {
    if (typeof value !== 'string' || value.length < 1 || value.length > 255)
        return false
    return [...value].every((character) => {
        const code = character.charCodeAt(0)
        return code >= 33 && code <= 126
    })
}

function canonicalStart(value: unknown): FileUploadStart {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'content_type',
            'filename',
            'folder_id',
            'sha256',
            'size_bytes',
        ]) ||
        !uuid(value.folder_id) ||
        !safeText(value.filename, 1024) ||
        !Number.isSafeInteger(value.size_bytes) ||
        (value.size_bytes as number) < 1 ||
        (value.size_bytes as number) > MAX_FILE_BYTES ||
        typeof value.sha256 !== 'string' ||
        !SHA256.test(value.sha256) ||
        typeof value.content_type !== 'string' ||
        value.content_type.length > 255 ||
        !MIME.test(value.content_type)
    ) {
        throw new FileContractError()
    }
    return {
        content_type: value.content_type,
        filename: value.filename,
        folder_id: value.folder_id,
        sha256: value.sha256,
        size_bytes: value.size_bytes as number,
    }
}

function parseStarted(value: unknown): FileUploadStarted {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, ['file_id', 'upload_id']) ||
        !uuid(value.file_id) ||
        !uuid(value.upload_id)
    ) {
        throw new FileContractError()
    }
    return { file_id: value.file_id, upload_id: value.upload_id }
}

function canonicalParts(value: unknown): FilePart[] {
    if (!Array.isArray(value) || value.length < 1 || value.length > 10_000)
        throw new FileContractError()
    const seen = new Set<number>()
    const parts = value.map((part): FilePart => {
        if (
            !isRecord(part) ||
            !hasRequiredKeys(part, ['etag', 'part_number']) ||
            !Number.isInteger(part.part_number) ||
            (part.part_number as number) < 1 ||
            (part.part_number as number) > 10_000 ||
            !safeText(part.etag, 1024)
        ) {
            throw new FileContractError()
        }
        const partNumber = part.part_number as number
        const etag = (part.etag as string).trim()
        if (!etag || seen.has(partNumber)) throw new FileContractError()
        seen.add(partNumber)
        return { etag, part_number: partNumber }
    })
    return parts.sort((left, right) => left.part_number - right.part_number)
}

function parseCompleted(value: unknown): FileUploadCompleted {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, ['file_id', 'state']) ||
        !uuid(value.file_id) ||
        !['QUARANTINED', 'SCANNING', 'CLEAN', 'INFECTED', 'FAILED'].includes(
            String(value.state),
        )
    ) {
        throw new FileContractError()
    }
    return {
        file_id: value.file_id,
        state: value.state as FileUploadCompleted['state'],
    }
}

function terminalDownloadState(
    failure: unknown,
): FileDownloadUnavailableError['state'] | undefined {
    if (
        !(failure instanceof HttpClientError) ||
        failure.status !== 409 ||
        !isRecord(failure.data) ||
        !isRecord(failure.data.error)
    ) {
        return undefined
    }
    if (failure.data.error.code === 'FILE_INFECTED') return 'INFECTED'
    if (failure.data.error.code === 'FILE_SCAN_FAILED') return 'FAILED'
    return undefined
}

function parseFolder(value: unknown): DriveFolder {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, ['id', 'name', 'parent_id', 'visibility']) ||
        !uuid(value.id) ||
        !safeText(value.name, 128) ||
        (value.parent_id !== null && !uuid(value.parent_id)) ||
        (value.visibility !== 'ALL' &&
            value.visibility !== 'MANAGEMENT' &&
            value.visibility !== 'OWNER_ONLY')
    ) {
        throw new FileContractError()
    }
    return {
        id: value.id,
        parent_id: value.parent_id,
        name: value.name,
        visibility: value.visibility,
    }
}

function parseDriveFile(value: unknown): DriveFile {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'content_type',
            'created_at',
            'filename',
            'folder_id',
            'id',
            'project_id',
            'size_bytes',
            'state',
        ]) ||
        !uuid(value.id) ||
        !uuid(value.folder_id) ||
        (value.project_id !== null && !uuid(value.project_id)) ||
        !safeText(value.filename, 1024) ||
        !Number.isSafeInteger(value.size_bytes) ||
        typeof value.content_type !== 'string' ||
        typeof value.created_at !== 'string'
    ) {
        throw new FileContractError()
    }
    return {
        id: value.id,
        folder_id: value.folder_id,
        project_id: value.project_id,
        filename: value.filename,
        size_bytes: value.size_bytes as number,
        content_type: value.content_type,
        state: value.state as DriveFile['state'],
        created_at: value.created_at,
    }
}

function parseUrl(value: unknown): string {
    if (!isRecord(value) || !hasRequiredKeys(value, ['url']))
        throw new FileContractError()
    if (!safeText(value.url, 4096)) throw new FileContractError()
    try {
        const parsed = new URL(value.url)
        if (
            parsed.protocol !== 'https:' ||
            parsed.username ||
            parsed.password ||
            !parsed.hostname
        ) {
            throw new FileContractError()
        }
    } catch (error) {
        if (error instanceof FileContractError) throw error
        throw new FileContractError()
    }
    return value.url
}

export function fileErrorMessage(error: unknown): string {
    return formatRequestError(
        '文件操作失败',
        error,
        '文件操作失败，请稍后重试。',
    )
}

export function createFilesApi(client: BrowserHttpClient) {
    return Object.freeze({
        async start(
            command: FileUploadStart,
            key: string,
        ): Promise<FileUploadStarted> {
            const canonical = canonicalStart(command)
            if (!idempotencyKey(key)) throw new FileContractError()
            const response = await client.post('/files/uploads', canonical, {
                idempotencyKey: key,
            })
            if (response.status !== 201) throw new FileContractError()
            return parseStarted(response.data)
        },
        async partUrl(uploadId: string, partNumber: number): Promise<string> {
            if (
                !uuid(uploadId) ||
                !Number.isInteger(partNumber) ||
                partNumber < 1 ||
                partNumber > 10_000
            ) {
                throw new FileContractError()
            }
            const response = await client.post(
                `/files/uploads/${uploadId}/parts/${partNumber}`,
            )
            if (response.status !== 200) throw new FileContractError()
            return parseUrl(response.data)
        },
        async complete(
            uploadId: string,
            parts: readonly FilePart[],
        ): Promise<FileUploadCompleted> {
            if (!uuid(uploadId)) throw new FileContractError()
            const canonical = canonicalParts(parts)
            const response = await client.post(
                `/files/uploads/${uploadId}/complete`,
                { parts: canonical },
            )
            if (response.status !== 200) throw new FileContractError()
            return parseCompleted(response.data)
        },
        async download(fileId: string): Promise<string> {
            if (!uuid(fileId)) throw new FileContractError()
            let response
            try {
                response = await client.get(`/files/${fileId}/download`)
            } catch (error) {
                const state = terminalDownloadState(error)
                if (state) throw new FileDownloadUnavailableError(state)
                throw error
            }
            if (response.status !== 200) throw new FileContractError()
            return parseUrl(response.data)
        },
        async listFolders(): Promise<DriveFolder[]> {
            const response = await client.get('/folders')
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new FileContractError()
            }
            return response.data.map(parseFolder)
        },
        async createFolder(parentId: string, name: string): Promise<DriveFolder> {
            if (!uuid(parentId) || !safeText(name, 128)) throw new FileContractError()
            const response = await client.post('/folders', {
                parent_id: parentId,
                name,
            })
            if (response.status !== 201) throw new FileContractError()
            return parseFolder(response.data)
        },
        async listFiles(folderId: string): Promise<DriveFile[]> {
            if (!uuid(folderId)) throw new FileContractError()
            const response = await client.get('/files', {
                params: { folder_id: folderId },
            })
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new FileContractError()
            }
            return response.data.map(parseDriveFile)
        },
        async rename(fileId: string, filename: string): Promise<DriveFile> {
            if (!uuid(fileId) || !safeText(filename, 1024)) {
                throw new FileContractError()
            }
            const response = await client.patch(`/files/${fileId}`, { filename })
            if (response.status !== 200) throw new FileContractError()
            return parseDriveFile(response.data)
        },
        async move(fileId: string, folderId: string): Promise<DriveFile> {
            if (!uuid(fileId) || !uuid(folderId)) throw new FileContractError()
            const response = await client.patch(`/files/${fileId}`, {
                folder_id: folderId,
            })
            if (response.status !== 200) throw new FileContractError()
            return parseDriveFile(response.data)
        },
        async remove(fileId: string): Promise<void> {
            if (!uuid(fileId)) throw new FileContractError()
            const response = await client.delete(`/files/${fileId}`)
            if (response.status !== 204) throw new FileContractError()
        },
    })
}

export const filesApi = createFilesApi(apiClient)
