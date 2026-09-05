import type {
    FilePart,
    FileUploadCompleted,
    FileUploadStart,
    FileUploadStarted,
} from '../api/files'
import { HttpClientError } from '../api/http'

export const UPLOAD_PART_SIZE = 8 * 1024 * 1024
export const MAX_PARALLEL_PUTS = 3

const MAX_FILE_BYTES = 100 * 1024 * 1024
const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SHA256 = /^[0-9a-f]{64}$/
const MIME = /^[A-Za-z0-9!#$&^_.+-]+\/[A-Za-z0-9!#$&^_.+-]+$/
export interface UploadCommand {
    readonly file: File
    readonly folder_id: string
}

interface FilesApi {
    start(
        command: FileUploadStart,
        idempotencyKey: string,
    ): Promise<FileUploadStarted>
    partUrl(uploadId: string, partNumber: number): Promise<string>
    complete(
        uploadId: string,
        parts: readonly FilePart[],
    ): Promise<FileUploadCompleted>
}

export interface MultipartDependencies {
    readonly filesApi: FilesApi
    readonly uploadPart: (
        url: string,
        body: Blob,
        signal?: AbortSignal,
    ) => Promise<string>
    readonly hashFile?: (file: File) => Promise<string>
    readonly onProgress?: (uploadedBytes: number, totalBytes: number) => void
}

interface PresignedTransportOptions {
    readonly allowedObjectOrigin: string
    readonly fetch?: (
        input: string,
        init: RequestInit,
    ) => Promise<{
        readonly headers: { get(name: string): string | null }
        readonly status: number
    }>
}

export class UploadContractError extends Error {
    constructor() {
        super('Upload operation failed')
        this.name = 'UploadContractError'
    }
}

export class UploadUserError extends UploadContractError {
    readonly code: 'EMPTY' | 'TOO_LARGE' | 'BAD_TYPE'

    constructor(code: 'EMPTY' | 'TOO_LARGE' | 'BAD_TYPE') {
        super()
        this.name = 'UploadUserError'
        this.code = code
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    if (typeof value !== 'object' || value === null || Array.isArray(value))
        return false
    const prototype = Object.getPrototypeOf(value)
    return prototype === Object.prototype || prototype === null
}

function isSafeText(value: unknown, maximum: number): value is string {
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

function isEtag(value: unknown): value is string {
    return (
        isSafeText(value, 1024) &&
        value.trim() === value &&
        !/[\r\n]/.test(value)
    )
}

function canonicalCommand(value: unknown): UploadCommand {
    if (
        !isRecord(value) ||
        !(value.file instanceof File) ||
        !isSafeText(value.file.name, 1024) ||
        !Number.isSafeInteger(value.file.lastModified) ||
        value.file.lastModified < 0 ||
        typeof value.folder_id !== 'string' ||
        !UUID.test(value.folder_id)
    ) {
        throw new UploadContractError()
    }
    if (value.file.size < 1) throw new UploadUserError('EMPTY')
    if (value.file.size > MAX_FILE_BYTES) throw new UploadUserError('TOO_LARGE')
    const contentType = value.file.type || 'application/octet-stream'
    if (contentType.length > 255 || !MIME.test(contentType))
        throw new UploadUserError('BAD_TYPE')
    return {
        file: value.file,
        folder_id: value.folder_id,
    }
}

function hex(buffer: ArrayBuffer): string {
    return [...new Uint8Array(buffer)]
        .map((byte) => byte.toString(16).padStart(2, '0'))
        .join('')
}

async function digestText(value: string): Promise<string> {
    try {
        return hex(
            await crypto.subtle.digest(
                'SHA-256',
                new TextEncoder().encode(value),
            ),
        )
    } catch {
        throw new UploadContractError()
    }
}

export async function hashFile(file: File): Promise<string> {
    try {
        const digest = hex(
            await crypto.subtle.digest('SHA-256', await file.arrayBuffer()),
        )
        if (!SHA256.test(digest)) throw new UploadContractError()
        return digest
    } catch (error) {
        if (error instanceof UploadContractError) throw error
        throw new UploadContractError()
    }
}

async function uploadIdempotencyKey(
    command: UploadCommand,
    sha256: string,
): Promise<string> {
    const contentType = command.file.type || 'application/octet-stream'
    const canonical = JSON.stringify({
        content_type: contentType,
        filename: command.file.name,
        folder_id: command.folder_id,
        last_modified: command.file.lastModified,
        sha256,
        size_bytes: command.file.size,
    })
    const digest = await digestText(
        `superboss-upload-idempotency-v1\n${canonical}`,
    )
    return `file-${digest}`
}

function aborted(signal: AbortSignal): void {
    if (signal.aborted) throw new UploadContractError()
}

function rethrowUploadFailure(error: unknown): never {
    if (
        error instanceof UploadUserError ||
        error instanceof UploadContractError ||
        error instanceof HttpClientError
    ) {
        throw error
    }
    throw new UploadContractError()
}

async function runUpload(
    dependencies: MultipartDependencies,
    rawCommand: unknown,
    signal: AbortSignal,
): Promise<FileUploadCompleted> {
    const command = canonicalCommand(rawCommand)
    const partCount = Math.ceil(command.file.size / UPLOAD_PART_SIZE)
    let sha256: string
    try {
        sha256 = await (dependencies.hashFile ?? hashFile)(command.file)
        if (!SHA256.test(sha256)) throw new UploadContractError()
    } catch (error) {
        rethrowUploadFailure(error)
    }
    aborted(signal)
    const idempotencyKey = await uploadIdempotencyKey(command, sha256)
    aborted(signal)

    let started: FileUploadStarted
    try {
        started = await dependencies.filesApi.start(
            {
                content_type: command.file.type || 'application/octet-stream',
                filename: command.file.name,
                folder_id: command.folder_id,
                sha256,
                size_bytes: command.file.size,
            },
            idempotencyKey,
        )
    } catch (error) {
        rethrowUploadFailure(error)
    }

    const completed: FilePart[] = []
    dependencies.onProgress?.(0, command.file.size)
    let nextPart = 1
    while (nextPart <= partCount) {
        aborted(signal)
        const batchEnd = Math.min(nextPart + MAX_PARALLEL_PUTS - 1, partCount)
        const prepared: Array<{
            body: Blob
            partNumber: number
            url: string
        }> = []
        try {
            for (
                let partNumber = nextPart;
                partNumber <= batchEnd;
                partNumber += 1
            ) {
                aborted(signal)
                const start = (partNumber - 1) * UPLOAD_PART_SIZE
                const end = Math.min(
                    start + UPLOAD_PART_SIZE,
                    command.file.size,
                )
                const body = command.file.slice(start, end, command.file.type)
                const url = await dependencies.filesApi.partUrl(
                    started.upload_id,
                    partNumber,
                )
                aborted(signal)
                prepared.push({ body, partNumber, url })
            }
        } catch (error) {
            rethrowUploadFailure(error)
        }

        const settled = await Promise.allSettled(
            prepared.map(({ body, url }) =>
                dependencies.uploadPart(url, body, signal),
            ),
        )
        aborted(signal)
        let failed = false
        for (let index = 0; index < settled.length; index += 1) {
            const result = settled[index]
            if (failed || result.status === 'rejected') {
                failed = true
                continue
            }
            const item = prepared[index]
            if (!item || !isEtag(result.value)) {
                failed = true
                continue
            }
            completed.push({ etag: result.value, part_number: item.partNumber })
        }
        if (failed) throw new UploadContractError()
        const uploadedBytes = Math.min(
            completed.length * UPLOAD_PART_SIZE,
            command.file.size,
        )
        dependencies.onProgress?.(uploadedBytes, command.file.size)
        nextPart = batchEnd + 1
    }

    aborted(signal)
    try {
        return await dependencies.filesApi.complete(
            started.upload_id,
            completed,
        )
    } catch (error) {
        rethrowUploadFailure(error)
    }
}

export function createMultipartUploader(dependencies: MultipartDependencies) {
    let active:
        | {
              readonly controller: AbortController
              readonly promise: Promise<FileUploadCompleted>
          }
        | undefined
    return Object.freeze({
        upload(command: UploadCommand): Promise<FileUploadCompleted> {
            if (active) return active.promise
            const controller = new AbortController()
            const promise = runUpload(dependencies, command, controller.signal)
                .catch((error: unknown) => rethrowUploadFailure(error))
                .finally(() => {
                    if (active?.promise === promise) active = undefined
                })
            active = { controller, promise }
            return promise
        },
        cancel(): void {
            active?.controller.abort()
        },
    })
}

function canonicalObjectOrigin(value: unknown): string {
    if (typeof value !== 'string' || value.length > 2048) {
        throw new UploadContractError()
    }
    try {
        const parsed = new URL(value)
        if (
            parsed.protocol !== 'https:' ||
            parsed.username ||
            parsed.password ||
            parsed.pathname !== '/' ||
            parsed.search ||
            parsed.hash ||
            parsed.origin !== value
        ) {
            throw new UploadContractError()
        }
        return parsed.origin
    } catch (error) {
        if (error instanceof UploadContractError) throw error
        throw new UploadContractError()
    }
}

export function createPresignedUploadTransport(
    options: PresignedTransportOptions,
) {
    const allowedOrigin = canonicalObjectOrigin(options.allowedObjectOrigin)
    const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis)
    return Object.freeze({
        async put(
            url: string,
            body: Blob,
            signal?: AbortSignal,
        ): Promise<string> {
            let parsed: URL
            try {
                if (!isSafeText(url, 4096)) throw new UploadContractError()
                parsed = new URL(url)
                if (
                    parsed.protocol !== 'https:' ||
                    parsed.username ||
                    parsed.password ||
                    parsed.hash ||
                    parsed.origin !== allowedOrigin
                ) {
                    throw new UploadContractError()
                }
            } catch (error) {
                if (error instanceof UploadContractError) throw error
                throw new UploadContractError()
            }
            try {
                const response = await fetcher(parsed.href, {
                    body,
                    credentials: 'omit',
                    headers: new Headers(),
                    method: 'PUT',
                    redirect: 'error',
                    signal,
                })
                const etag = response.headers.get('ETag')
                if (response.status !== 200 || !isEtag(etag)) {
                    throw new UploadContractError()
                }
                return etag
            } catch (error) {
                if (error instanceof UploadContractError) throw error
                throw new UploadContractError()
            }
        },
    })
}
