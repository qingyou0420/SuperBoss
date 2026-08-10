import type {
    FilePart,
    FileUploadCompleted,
    FileUploadStart,
    FileUploadStarted,
} from '../api/files'

export const UPLOAD_PART_SIZE = 8 * 1024 * 1024
export const MAX_PARALLEL_PUTS = 3

const MAX_FILE_BYTES = 100 * 1024 * 1024
const MAX_PARTS = Math.ceil(MAX_FILE_BYTES / UPLOAD_PART_SIZE)
const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SHA256 = /^[0-9a-f]{64}$/
const MIME = /^[A-Za-z0-9!#$&^_.+-]+\/[A-Za-z0-9!#$&^_.+-]+$/
const DATE = /^\d{4}-\d{2}-\d{2}$/
const DATABASE_NAME = 'superboss-upload-progress'
const DATABASE_VERSION = 1
const OBJECT_STORE_NAME = 'active-upload'
const ACTIVE_UPLOAD_KEY = 'active'

export interface UploadCommand {
    readonly category: string
    readonly file: File
    readonly file_date: string
    readonly project_id: string
}

export interface UploadProgressEntry {
    readonly completed_parts: readonly FilePart[]
    readonly fingerprint: string
    readonly upload_id: string
}

export interface UploadHash {
    readonly part_sha256: readonly string[]
    readonly sha256: string
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

interface UploadHasher {
    hash(file: File): Promise<UploadHash>
    hashPart(part: Blob): Promise<string>
}

interface UploadProgressStore {
    delete(): Promise<void>
    load(): Promise<UploadProgressEntry | null>
    save(entry: UploadProgressEntry): Promise<void>
}

interface MultipartDependencies {
    readonly filesApi: FilesApi
    readonly hasher: UploadHasher
    readonly progressStore: UploadProgressStore
    readonly uploadPart: (
        url: string,
        body: Blob,
        signal?: AbortSignal,
    ) => Promise<string>
}

interface WorkerLike {
    onerror: ((event: ErrorEvent) => void) | null
    onmessage: ((event: MessageEvent) => void) | null
    postMessage(message: unknown): void
    terminate(): void
}

interface ProgressDatabase {
    delete(): Promise<void>
    get(): Promise<unknown>
    put(entry: UploadProgressEntry): Promise<void>
}

interface IndexedDbProgressOptions {
    readonly openDatabase?: () => Promise<ProgressDatabase>
}

interface WorkerHasherOptions {
    readonly createWorker?: () => WorkerLike
    readonly partSize?: number
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

function isRecord(value: unknown): value is Record<string, unknown> {
    if (typeof value !== 'object' || value === null || Array.isArray(value))
        return false
    const prototype = Object.getPrototypeOf(value)
    return prototype === Object.prototype || prototype === null
}

function exactKeys(
    value: Record<string, unknown>,
    expected: string[],
): boolean {
    const actual = Object.keys(value).sort()
    return (
        actual.length === expected.length &&
        actual.every((key, index) => key === expected[index])
    )
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
        !exactKeys(value, ['category', 'file', 'file_date', 'project_id']) ||
        !(value.file instanceof File) ||
        value.file.size < 1 ||
        value.file.size > MAX_FILE_BYTES ||
        !isSafeText(value.file.name, 1024) ||
        !Number.isSafeInteger(value.file.lastModified) ||
        value.file.lastModified < 0 ||
        !isSafeText(value.category, 255) ||
        typeof value.file_date !== 'string' ||
        !DATE.test(value.file_date) ||
        typeof value.project_id !== 'string' ||
        !UUID.test(value.project_id)
    ) {
        throw new UploadContractError()
    }
    const contentType = value.file.type || 'application/octet-stream'
    if (contentType.length > 255 || !MIME.test(contentType))
        throw new UploadContractError()
    return {
        category: value.category,
        file: value.file,
        file_date: value.file_date,
        project_id: value.project_id,
    }
}

function canonicalHash(value: unknown, partCount: number): UploadHash {
    if (
        !isRecord(value) ||
        !exactKeys(value, ['part_sha256', 'sha256']) ||
        typeof value.sha256 !== 'string' ||
        !SHA256.test(value.sha256) ||
        !Array.isArray(value.part_sha256) ||
        value.part_sha256.length !== partCount ||
        !value.part_sha256.every(
            (digest) => typeof digest === 'string' && SHA256.test(digest),
        )
    ) {
        throw new UploadContractError()
    }
    return {
        part_sha256: [...value.part_sha256],
        sha256: value.sha256,
    }
}

function canonicalProgress(
    value: unknown,
    maximumParts = 10_000,
): UploadProgressEntry | null {
    if (
        !isRecord(value) ||
        !exactKeys(value, ['completed_parts', 'fingerprint', 'upload_id']) ||
        typeof value.fingerprint !== 'string' ||
        !SHA256.test(value.fingerprint) ||
        typeof value.upload_id !== 'string' ||
        !UUID.test(value.upload_id) ||
        !Array.isArray(value.completed_parts) ||
        value.completed_parts.length > Math.min(MAX_PARTS, maximumParts)
    ) {
        return null
    }
    const completed: FilePart[] = []
    for (let index = 0; index < value.completed_parts.length; index += 1) {
        const part = value.completed_parts[index]
        if (
            !isRecord(part) ||
            !exactKeys(part, ['etag', 'part_number']) ||
            part.part_number !== index + 1 ||
            !isEtag(part.etag)
        ) {
            return null
        }
        completed.push({ etag: part.etag, part_number: index + 1 })
    }
    return {
        completed_parts: completed,
        fingerprint: value.fingerprint,
        upload_id: value.upload_id,
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

async function uploadIdentity(
    command: UploadCommand,
    hash: UploadHash,
): Promise<{ fingerprint: string; idempotencyKey: string }> {
    const contentType = command.file.type || 'application/octet-stream'
    const canonical = JSON.stringify({
        category: command.category,
        content_type: contentType,
        file_date: command.file_date,
        filename: command.file.name,
        last_modified: command.file.lastModified,
        project_id: command.project_id,
        sha256: hash.sha256,
        size_bytes: command.file.size,
    })
    const fingerprint = await digestText(
        `superboss-upload-fingerprint-v1\n${canonical}`,
    )
    const keyDigest = await digestText(
        `superboss-upload-idempotency-v1\n${canonical}\n${fingerprint}`,
    )
    return { fingerprint, idempotencyKey: `file-${keyDigest}` }
}

function aborted(signal: AbortSignal): void {
    if (signal.aborted) throw new UploadContractError()
}

async function runUpload(
    dependencies: MultipartDependencies,
    rawCommand: unknown,
    signal: AbortSignal,
): Promise<FileUploadCompleted> {
    const command = canonicalCommand(rawCommand)
    const partCount = Math.ceil(command.file.size / UPLOAD_PART_SIZE)
    let hash: UploadHash
    try {
        hash = canonicalHash(
            await dependencies.hasher.hash(command.file),
            partCount,
        )
    } catch {
        throw new UploadContractError()
    }
    aborted(signal)
    const { fingerprint, idempotencyKey } = await uploadIdentity(command, hash)

    let progress: UploadProgressEntry | null
    try {
        const loaded = await dependencies.progressStore.load()
        progress = loaded === null ? null : canonicalProgress(loaded, partCount)
        if (loaded !== null && progress === null) {
            await dependencies.progressStore.delete()
        }
        if (progress !== null && progress.fingerprint !== fingerprint) {
            await dependencies.progressStore.delete()
            progress = null
        }
    } catch {
        throw new UploadContractError()
    }
    aborted(signal)

    if (progress === null) {
        let started: FileUploadStarted
        try {
            started = await dependencies.filesApi.start(
                {
                    category: command.category,
                    content_type:
                        command.file.type || 'application/octet-stream',
                    file_date: command.file_date,
                    filename: command.file.name,
                    project_id: command.project_id,
                    sha256: hash.sha256,
                    size_bytes: command.file.size,
                },
                idempotencyKey,
            )
            progress = {
                completed_parts: [],
                fingerprint,
                upload_id: started.upload_id,
            }
            await dependencies.progressStore.save(progress)
        } catch {
            throw new UploadContractError()
        }
    }

    const completed = [...progress.completed_parts]
    let nextPart = completed.length + 1
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
                    progress.upload_id,
                    partNumber,
                )
                const approvedDigest = await dependencies.hasher.hashPart(body)
                if (
                    !SHA256.test(approvedDigest) ||
                    approvedDigest !== hash.part_sha256[partNumber - 1]
                ) {
                    await dependencies.progressStore.delete()
                    throw new UploadContractError()
                }
                aborted(signal)
                prepared.push({ body, partNumber, url })
            }
        } catch (error) {
            if (error instanceof UploadContractError) throw error
            throw new UploadContractError()
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
            progress = {
                completed_parts: [...completed],
                fingerprint,
                upload_id: progress.upload_id,
            }
            try {
                await dependencies.progressStore.save(progress)
            } catch {
                throw new UploadContractError()
            }
        }
        if (failed) throw new UploadContractError()
        nextPart = batchEnd + 1
    }

    aborted(signal)
    try {
        const result = await dependencies.filesApi.complete(
            progress.upload_id,
            completed,
        )
        aborted(signal)
        await dependencies.progressStore.delete()
        return result
    } catch {
        throw new UploadContractError()
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
                .catch(() => {
                    throw new UploadContractError()
                })
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

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
    return new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result)
        request.onerror = () => reject(new Error('IndexedDB request failed'))
    })
}

function transactionComplete(transaction: IDBTransaction): Promise<void> {
    return new Promise((resolve, reject) => {
        transaction.oncomplete = () => resolve()
        transaction.onabort = () =>
            reject(new Error('IndexedDB transaction failed'))
        transaction.onerror = () =>
            reject(new Error('IndexedDB transaction failed'))
    })
}

function defaultOpenDatabase(): Promise<ProgressDatabase> {
    return new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') {
            reject(new Error('IndexedDB unavailable'))
            return
        }
        const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION)
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains(OBJECT_STORE_NAME)) {
                request.result.createObjectStore(OBJECT_STORE_NAME)
            }
        }
        request.onerror = () => reject(new Error('IndexedDB open failed'))
        request.onsuccess = () => {
            const database = request.result
            resolve({
                async delete(): Promise<void> {
                    const transaction = database.transaction(
                        OBJECT_STORE_NAME,
                        'readwrite',
                    )
                    transaction
                        .objectStore(OBJECT_STORE_NAME)
                        .delete(ACTIVE_UPLOAD_KEY)
                    await transactionComplete(transaction)
                },
                async get(): Promise<unknown> {
                    const transaction = database.transaction(
                        OBJECT_STORE_NAME,
                        'readonly',
                    )
                    const result = await requestResult(
                        transaction
                            .objectStore(OBJECT_STORE_NAME)
                            .get(ACTIVE_UPLOAD_KEY),
                    )
                    await transactionComplete(transaction)
                    return result ?? null
                },
                async put(entry: UploadProgressEntry): Promise<void> {
                    const transaction = database.transaction(
                        OBJECT_STORE_NAME,
                        'readwrite',
                    )
                    transaction
                        .objectStore(OBJECT_STORE_NAME)
                        .put(entry, ACTIVE_UPLOAD_KEY)
                    await transactionComplete(transaction)
                },
            })
        }
    })
}

export function createIndexedDbProgressStore(
    options: IndexedDbProgressOptions = {},
): UploadProgressStore {
    let databasePromise: Promise<ProgressDatabase> | undefined
    const database = (): Promise<ProgressDatabase> => {
        databasePromise ??= (options.openDatabase ?? defaultOpenDatabase)()
        return databasePromise
    }
    return Object.freeze({
        async delete(): Promise<void> {
            try {
                await (await database()).delete()
            } catch {
                throw new UploadContractError()
            }
        },
        async load(): Promise<UploadProgressEntry | null> {
            try {
                const store = await database()
                const raw = await store.get()
                if (raw === null || raw === undefined) return null
                const progress = canonicalProgress(raw)
                if (progress !== null) return progress
                await store.delete()
                return null
            } catch {
                throw new UploadContractError()
            }
        },
        async save(entry: UploadProgressEntry): Promise<void> {
            const progress = canonicalProgress(entry)
            if (progress === null) throw new UploadContractError()
            try {
                await (await database()).put(progress)
            } catch {
                throw new UploadContractError()
            }
        },
    })
}

function defaultWorker(): WorkerLike {
    return new Worker(new URL('./hash.worker.ts', import.meta.url), {
        type: 'module',
    })
}

function runWorker<T>(
    createWorker: () => WorkerLike,
    message: unknown,
    parse: (value: unknown) => T,
): Promise<T> {
    return new Promise((resolve, reject) => {
        let worker: WorkerLike
        try {
            worker = createWorker()
        } catch {
            reject(new UploadContractError())
            return
        }
        let finished = false
        const finish = (callback: () => void): void => {
            if (finished) return
            finished = true
            worker.terminate()
            callback()
        }
        worker.onerror = () => finish(() => reject(new UploadContractError()))
        worker.onmessage = (event) => {
            try {
                const result = parse(event.data)
                finish(() => resolve(result))
            } catch {
                finish(() => reject(new UploadContractError()))
            }
        }
        try {
            worker.postMessage(message)
        } catch {
            finish(() => reject(new UploadContractError()))
        }
    })
}

export function createWorkerHasher(
    options: WorkerHasherOptions = {},
): UploadHasher {
    const createWorker = options.createWorker ?? defaultWorker
    const partSize = options.partSize ?? UPLOAD_PART_SIZE
    if (
        !Number.isSafeInteger(partSize) ||
        partSize < 1 ||
        partSize > MAX_FILE_BYTES
    )
        throw new UploadContractError()
    return Object.freeze({
        hash(file: File): Promise<UploadHash> {
            const partCount = Math.ceil(file.size / partSize)
            return runWorker(
                createWorker,
                { file, part_size: partSize, type: 'hash-file' },
                (value) => {
                    if (
                        !isRecord(value) ||
                        !exactKeys(value, ['part_sha256', 'sha256', 'type']) ||
                        value.type !== 'hash-result'
                    ) {
                        throw new UploadContractError()
                    }
                    return canonicalHash(
                        {
                            part_sha256: value.part_sha256,
                            sha256: value.sha256,
                        },
                        partCount,
                    )
                },
            )
        },
        hashPart(part: Blob): Promise<string> {
            return runWorker(
                createWorker,
                { part, type: 'hash-part' },
                (value) => {
                    if (
                        !isRecord(value) ||
                        !exactKeys(value, ['sha256', 'type']) ||
                        value.type !== 'hash-part-result' ||
                        typeof value.sha256 !== 'string' ||
                        !SHA256.test(value.sha256)
                    ) {
                        throw new UploadContractError()
                    }
                    return value.sha256
                },
            )
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
            } catch {
                throw new UploadContractError()
            }
        },
    })
}
