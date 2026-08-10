import { Blob as NodeBlob } from 'node:buffer'

import { afterEach, describe, expect, test, vi } from 'vitest'

const MODULE_PATH = '../src/uploads/multipart'
const PART_SIZE = 8 * 1024 * 1024
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const UPLOAD_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'
const FILE_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f812'
const FILE_SHA = 'a'.repeat(64)
const PART_SHA = 'b'.repeat(64)

interface ProgressEntry {
    completed_parts: Array<{ etag: string; part_number: number }>
    fingerprint: string
    upload_id: string
}

interface MultipartModule {
    MAX_PARALLEL_PUTS: number
    UPLOAD_PART_SIZE: number
    UploadContractError: new () => Error
    createMultipartUploader(dependencies: Record<string, unknown>): {
        upload(command: Record<string, unknown>): Promise<unknown>
        cancel(): void
    }
    createPresignedUploadTransport(options: Record<string, unknown>): {
        put(url: string, body: Blob, signal?: AbortSignal): Promise<string>
    }
    createIndexedDbProgressStore(options: Record<string, unknown>): {
        delete(): Promise<void>
        load(): Promise<ProgressEntry | null>
        save(entry: ProgressEntry): Promise<void>
    }
    createWorkerHasher(options: Record<string, unknown>): {
        hash(file: File): Promise<{
            part_sha256: string[]
            sha256: string
        }>
        hashPart(part: Blob): Promise<string>
    }
}

async function multipartModule(): Promise<MultipartModule> {
    return (await import(/* @vite-ignore */ MODULE_PATH)) as MultipartModule
}

function fileOfSize(size: number, name = '客户方案.docx'): File {
    return new File([new Uint8Array(size)], name, {
        lastModified: 1_786_313_400_000,
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
}

function progressStore(initial: ProgressEntry | null = null) {
    let current = initial
    return {
        delete: vi.fn(async () => {
            current = null
        }),
        load: vi.fn(async () => current),
        save: vi.fn(async (value: ProgressEntry) => {
            current = structuredClone(value)
        }),
        value: () => current,
    }
}

function uploaderFixture(options?: {
    fileSize?: number
    progress?: ProgressEntry | null
}) {
    const file = fileOfSize(options?.fileSize ?? 2 * PART_SIZE + 1)
    const partCount = Math.ceil(file.size / PART_SIZE)
    const store = progressStore(options?.progress)
    const filesApi = {
        complete: vi.fn(async () => ({
            file_id: FILE_ID,
            state: 'QUARANTINED',
        })),
        partUrl: vi.fn(
            async (_uploadId: string, partNumber: number) =>
                `https://objects.example/upload/${partNumber}?signature=sentinel`,
        ),
        start: vi.fn(async (command: unknown, idempotencyKey: string) => {
            void command
            void idempotencyKey
            return { file_id: FILE_ID, upload_id: UPLOAD_ID }
        }),
    }
    const hasher = {
        hash: vi.fn(async () => ({
            part_sha256: Array.from({ length: partCount }, () => PART_SHA),
            sha256: FILE_SHA,
        })),
        hashPart: vi.fn(async () => PART_SHA),
    }
    const uploadPart = vi.fn(async (url: string, body: Blob) => {
        void url
        void body
        return 'etag'
    })
    return { file, filesApi, hasher, store, uploadPart }
}

function uploadCommand(file: File) {
    return {
        category: '客户方案',
        file,
        file_date: '2026-08-10',
        project_id: PROJECT_ID,
    }
}

afterEach(() => {
    vi.restoreAllMocks()
})

describe('supervised multipart upload orchestration', () => {
    test('locks 8MiB parts, one-at-a-time presign, and at most three PUTs', async () => {
        const mod = await multipartModule()
        expect(mod.UPLOAD_PART_SIZE).toBe(PART_SIZE)
        expect(mod.MAX_PARALLEL_PUTS).toBe(3)
        const fixture = uploaderFixture({ fileSize: 3 * PART_SIZE + 17 })
        let activePresign = 0
        let maxPresign = 0
        fixture.filesApi.partUrl.mockImplementation(async (_id, partNumber) => {
            activePresign += 1
            maxPresign = Math.max(maxPresign, activePresign)
            await Promise.resolve()
            activePresign -= 1
            return `https://objects.example/upload/${partNumber}`
        })
        let activePuts = 0
        let maxPuts = 0
        const sizes: number[] = []
        fixture.uploadPart.mockImplementation(async (_url, body) => {
            activePuts += 1
            maxPuts = Math.max(maxPuts, activePuts)
            sizes.push(body.size)
            await new Promise((resolve) => setTimeout(resolve, 5))
            activePuts -= 1
            return `etag-${sizes.length}`
        })

        const uploader = mod.createMultipartUploader({
            filesApi: fixture.filesApi,
            hasher: fixture.hasher,
            progressStore: fixture.store,
            uploadPart: fixture.uploadPart,
        })
        await uploader.upload(uploadCommand(fixture.file))

        expect(maxPresign).toBe(1)
        expect(maxPuts).toBeLessThanOrEqual(3)
        expect(maxPuts).toBe(3)
        expect(sizes.sort((left, right) => left - right)).toEqual([
            17,
            PART_SIZE,
            PART_SIZE,
            PART_SIZE,
        ])
        expect(
            fixture.filesApi.partUrl.mock.calls.map((call) => call[1]),
        ).toEqual([1, 2, 3, 4])
    })

    test('persists only upload id, opaque fingerprint, and completed ETags', async () => {
        const mod = await multipartModule()
        const fixture = uploaderFixture({ fileSize: PART_SIZE + 1 })
        const uploader = mod.createMultipartUploader({
            filesApi: fixture.filesApi,
            hasher: fixture.hasher,
            progressStore: fixture.store,
            uploadPart: fixture.uploadPart,
        })
        await uploader.upload(uploadCommand(fixture.file))

        expect(fixture.store.save).toHaveBeenCalled()
        for (const [entry] of fixture.store.save.mock.calls) {
            expect(Object.keys(entry).sort()).toEqual([
                'completed_parts',
                'fingerprint',
                'upload_id',
            ])
            expect(entry.fingerprint).toMatch(/^[0-9a-f]{64}$/)
            expect(JSON.stringify(entry)).not.toMatch(
                /https?:|signature|cookie|csrf|authorization|token|sentinel/i,
            )
        }
        expect(fixture.store.delete).toHaveBeenCalledTimes(1)
        expect(fixture.store.value()).toBeNull()
    })

    test('rejects an impossible calendar date before hashing, storage, or network', async () => {
        const mod = await multipartModule()
        const fixture = uploaderFixture({ fileSize: 1 })
        const uploader = mod.createMultipartUploader({
            filesApi: fixture.filesApi,
            hasher: fixture.hasher,
            progressStore: fixture.store,
            uploadPart: fixture.uploadPart,
        })

        await expect(
            uploader.upload({
                ...uploadCommand(fixture.file),
                file_date: '2026-02-31',
            }),
        ).rejects.toBeInstanceOf(mod.UploadContractError)
        expect(fixture.hasher.hash).not.toHaveBeenCalled()
        expect(fixture.store.load).not.toHaveBeenCalled()
        expect(fixture.filesApi.start).not.toHaveBeenCalled()
        expect(fixture.filesApi.partUrl).not.toHaveBeenCalled()
        expect(fixture.uploadPart).not.toHaveBeenCalled()
    })

    test('deterministically derives a stable high-entropy key from the full upload intent', async () => {
        const mod = await multipartModule()
        const first = uploaderFixture({ fileSize: 1 })
        first.filesApi.start.mockRejectedValueOnce(new Error('response lost'))
        const firstUploader = mod.createMultipartUploader({
            filesApi: first.filesApi,
            hasher: first.hasher,
            progressStore: first.store,
            uploadPart: first.uploadPart,
        })
        await expect(
            firstUploader.upload(uploadCommand(first.file)),
        ).rejects.toBeInstanceOf(mod.UploadContractError)
        const firstKey = first.filesApi.start.mock.calls[0]?.[1]

        const retry = uploaderFixture({ fileSize: 1 })
        const retryUploader = mod.createMultipartUploader({
            filesApi: retry.filesApi,
            hasher: retry.hasher,
            progressStore: retry.store,
            uploadPart: retry.uploadPart,
        })
        await retryUploader.upload(uploadCommand(retry.file))
        const retryKey = retry.filesApi.start.mock.calls[0]?.[1]

        const changed = uploaderFixture({ fileSize: 1 })
        const changedUploader = mod.createMultipartUploader({
            filesApi: changed.filesApi,
            hasher: changed.hasher,
            progressStore: changed.store,
            uploadPart: changed.uploadPart,
        })
        await changedUploader.upload({
            ...uploadCommand(changed.file),
            category: '另一分类',
        })
        const changedKey = changed.filesApi.start.mock.calls[0]?.[1]

        expect(firstKey).toBe(retryKey)
        expect(firstKey).toMatch(/^[!-~]{64,255}$/)
        expect(firstKey).not.toBe(FILE_SHA)
        expect(changedKey).not.toBe(firstKey)
        for (const entry of retry.store.save.mock.calls.map(
            (call) => call[0],
        )) {
            expect(entry).not.toHaveProperty('idempotency_key')
        }
    })

    test('reload resumes at the first unfinished part without starting again', async () => {
        const mod = await multipartModule()
        const first = uploaderFixture({ fileSize: 3 * PART_SIZE })
        let putCalls = 0
        const firstUploader = mod.createMultipartUploader({
            filesApi: first.filesApi,
            hasher: first.hasher,
            progressStore: first.store,
            uploadPart: vi.fn(async (_url: string, body: Blob) => {
                putCalls += 1
                if (body.size === PART_SIZE && putCalls === 2) {
                    throw new Error('provider unavailable')
                }
                return 'etag-1'
            }),
        })
        await expect(
            firstUploader.upload(uploadCommand(first.file)),
        ).rejects.toBeInstanceOf(mod.UploadContractError)
        const saved = first.store.value()
        expect(saved).not.toBeNull()

        const resumed = uploaderFixture({
            fileSize: 3 * PART_SIZE,
            progress: saved,
        })
        const uploader = mod.createMultipartUploader({
            filesApi: resumed.filesApi,
            hasher: resumed.hasher,
            progressStore: resumed.store,
            uploadPart: resumed.uploadPart,
        })
        await uploader.upload(uploadCommand(resumed.file))

        expect(resumed.filesApi.start).not.toHaveBeenCalled()
        expect(resumed.filesApi.partUrl.mock.calls[0]?.[1]).toBe(2)
        expect(resumed.filesApi.complete).toHaveBeenCalledWith(UPLOAD_ID, [
            { etag: 'etag-1', part_number: 1 },
            { etag: 'etag', part_number: 2 },
            { etag: 'etag', part_number: 3 },
        ])
        expect(resumed.store.delete).toHaveBeenCalledTimes(1)
    })

    test('reload acknowledges a terminal completion after the first response is lost', async () => {
        const mod = await multipartModule()
        const first = uploaderFixture({ fileSize: 1 })
        first.filesApi.complete.mockRejectedValueOnce(
            new Error('completion response lost after server commit'),
        )
        const firstUploader = mod.createMultipartUploader({
            filesApi: first.filesApi,
            hasher: first.hasher,
            progressStore: first.store,
            uploadPart: first.uploadPart,
        })

        await expect(
            firstUploader.upload(uploadCommand(first.file)),
        ).rejects.toBeInstanceOf(mod.UploadContractError)
        const saved = first.store.value()
        expect(saved).not.toBeNull()
        if (saved === null) throw new Error('expected resumable progress')
        expect(Object.keys(saved).sort()).toEqual([
            'completed_parts',
            'fingerprint',
            'upload_id',
        ])

        const resumed = uploaderFixture({ fileSize: 1, progress: saved })
        resumed.filesApi.complete.mockResolvedValueOnce({
            file_id: FILE_ID,
            state: 'CLEAN',
        })
        const resumedUploader = mod.createMultipartUploader({
            filesApi: resumed.filesApi,
            hasher: resumed.hasher,
            progressStore: resumed.store,
            uploadPart: resumed.uploadPart,
        })

        await expect(
            resumedUploader.upload(uploadCommand(resumed.file)),
        ).resolves.toEqual({ file_id: FILE_ID, state: 'CLEAN' })
        expect(resumed.filesApi.start).not.toHaveBeenCalled()
        expect(resumed.filesApi.partUrl).not.toHaveBeenCalled()
        expect(resumed.uploadPart).not.toHaveBeenCalled()
        expect(resumed.filesApi.complete).toHaveBeenCalledWith(UPLOAD_ID, [
            { etag: 'etag', part_number: 1 },
        ])
        expect(resumed.store.delete).toHaveBeenCalledTimes(1)
        expect(resumed.store.value()).toBeNull()
    })

    test('discards a fingerprint mismatch before creating a fresh upload', async () => {
        const mod = await multipartModule()
        const fixture = uploaderFixture({
            progress: {
                completed_parts: [{ etag: 'stale', part_number: 1 }],
                fingerprint: 'f'.repeat(64),
                upload_id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f899',
            },
        })
        const uploader = mod.createMultipartUploader({
            filesApi: fixture.filesApi,
            hasher: fixture.hasher,
            progressStore: fixture.store,
            uploadPart: fixture.uploadPart,
        })
        await uploader.upload(uploadCommand(fixture.file))

        expect(fixture.store.delete).toHaveBeenCalled()
        expect(fixture.filesApi.start).toHaveBeenCalledTimes(1)
        expect(fixture.filesApi.partUrl.mock.calls[0]?.[0]).toBe(UPLOAD_ID)
    })

    test('detects a changed part before PUT and abandons stale progress', async () => {
        const mod = await multipartModule()
        const fixture = uploaderFixture()
        fixture.hasher.hashPart.mockResolvedValueOnce('c'.repeat(64))
        const uploader = mod.createMultipartUploader({
            filesApi: fixture.filesApi,
            hasher: fixture.hasher,
            progressStore: fixture.store,
            uploadPart: fixture.uploadPart,
        })

        await expect(
            uploader.upload(uploadCommand(fixture.file)),
        ).rejects.toBeInstanceOf(mod.UploadContractError)
        expect(fixture.uploadPart).not.toHaveBeenCalled()
        expect(fixture.filesApi.complete).not.toHaveBeenCalled()
        expect(fixture.store.delete).toHaveBeenCalled()
    })

    test.each(['hash', 'start', 'presign', 'put', 'progress', 'complete'])(
        'fails safely and keeps only resumable non-secret state on %s failure',
        async (stage) => {
            const mod = await multipartModule()
            const fixture = uploaderFixture({ fileSize: 1 })
            const sentinel = new Error(`${stage} secret provider sentinel`)
            if (stage === 'hash')
                fixture.hasher.hash.mockRejectedValue(sentinel)
            if (stage === 'start')
                fixture.filesApi.start.mockRejectedValue(sentinel)
            if (stage === 'presign')
                fixture.filesApi.partUrl.mockRejectedValue(sentinel)
            if (stage === 'put') fixture.uploadPart.mockRejectedValue(sentinel)
            if (stage === 'progress')
                fixture.store.save.mockRejectedValue(sentinel)
            if (stage === 'complete')
                fixture.filesApi.complete.mockRejectedValue(sentinel)
            const uploader = mod.createMultipartUploader({
                filesApi: fixture.filesApi,
                hasher: fixture.hasher,
                progressStore: fixture.store,
                uploadPart: fixture.uploadPart,
            })

            await expect(
                uploader.upload(uploadCommand(fixture.file)),
            ).rejects.toBeInstanceOf(mod.UploadContractError)
            if (stage === 'hash') {
                expect(fixture.filesApi.start).not.toHaveBeenCalled()
                expect(fixture.store.save).not.toHaveBeenCalled()
            }
            expect(JSON.stringify(fixture.store.value())).not.toMatch(
                /https?:|secret|provider|sentinel|cookie|csrf|authorization|token/i,
            )
        },
    )

    test('deduplicates a double submit and cancel prevents complete', async () => {
        const mod = await multipartModule()
        const fixture = uploaderFixture({ fileSize: 1 })
        let release!: () => void
        fixture.uploadPart.mockImplementation(
            () => new Promise((resolve) => (release = () => resolve('etag'))),
        )
        const uploader = mod.createMultipartUploader({
            filesApi: fixture.filesApi,
            hasher: fixture.hasher,
            progressStore: fixture.store,
            uploadPart: fixture.uploadPart,
        })

        const first = uploader.upload(uploadCommand(fixture.file))
        const second = uploader.upload(uploadCommand(fixture.file))
        await vi.waitFor(() =>
            expect(fixture.uploadPart).toHaveBeenCalledTimes(1),
        )
        uploader.cancel()
        release()

        await expect(first).rejects.toBeInstanceOf(mod.UploadContractError)
        await expect(second).rejects.toBeInstanceOf(mod.UploadContractError)
        expect(fixture.filesApi.start).toHaveBeenCalledTimes(1)
        expect(fixture.filesApi.complete).not.toHaveBeenCalled()
    })
})

describe('worker hash and isolated presigned PUT boundaries', () => {
    test('uses an injected IndexedDB boundary and rejects corrupted or failed local state', async () => {
        const mod = await multipartModule()
        let value: unknown = null
        const database = {
            delete: vi.fn(async () => {
                value = null
            }),
            get: vi.fn(async () => value),
            put: vi.fn(async (entry: unknown) => {
                value = structuredClone(entry)
            }),
        }
        const store = mod.createIndexedDbProgressStore({
            openDatabase: async () => database,
        })
        const entry: ProgressEntry = {
            completed_parts: [{ etag: 'etag-1', part_number: 1 }],
            fingerprint: 'a'.repeat(64),
            upload_id: UPLOAD_ID,
        }

        await store.save(entry)
        await expect(store.load()).resolves.toEqual(entry)
        expect(JSON.stringify(value)).not.toMatch(
            /https?:|cookie|csrf|authorization|token|signature/i,
        )
        value = { ...entry, signed_url: 'https://objects.example/secret' }
        await expect(store.load()).resolves.toBeNull()
        expect(database.delete).toHaveBeenCalled()

        database.get.mockRejectedValueOnce(new Error('indexeddb sentinel'))
        await expect(store.load()).rejects.toBeInstanceOf(
            mod.UploadContractError,
        )
    })

    test('requests file and per-part SHA-256 from a dedicated worker at 8MiB', async () => {
        const mod = await multipartModule()
        const messages: unknown[] = []
        const worker = {
            onerror: null as ((event: ErrorEvent) => void) | null,
            onmessage: null as ((event: MessageEvent) => void) | null,
            postMessage(message: unknown) {
                messages.push(message)
                queueMicrotask(() =>
                    this.onmessage?.(
                        new MessageEvent('message', {
                            data: {
                                part_sha256: [PART_SHA, PART_SHA],
                                sha256: FILE_SHA,
                                type: 'hash-result',
                            },
                        }),
                    ),
                )
            },
            terminate: vi.fn(),
        }
        const hasher = mod.createWorkerHasher({
            createWorker: () => worker,
            partSize: PART_SIZE,
        })
        const file = fileOfSize(PART_SIZE + 1)

        await expect(hasher.hash(file)).resolves.toEqual({
            part_sha256: [PART_SHA, PART_SHA],
            sha256: FILE_SHA,
        })
        expect(messages).toEqual([
            { file, part_size: PART_SIZE, type: 'hash-file' },
        ])
        expect(worker.terminate).toHaveBeenCalledTimes(1)
    })

    test('turns worker failure into a safe upload error and terminates it', async () => {
        const mod = await multipartModule()
        const worker = {
            onerror: null as ((event: ErrorEvent) => void) | null,
            onmessage: null as ((event: MessageEvent) => void) | null,
            postMessage() {
                queueMicrotask(() =>
                    this.onerror?.(
                        new ErrorEvent('error', { message: 'sentinel' }),
                    ),
                )
            },
            terminate: vi.fn(),
        }
        const hasher = mod.createWorkerHasher({ createWorker: () => worker })

        await expect(hasher.hash(fileOfSize(1))).rejects.toBeInstanceOf(
            mod.UploadContractError,
        )
        expect(worker.terminate).toHaveBeenCalledTimes(1)
    })

    test('uses an exact injected HTTPS origin and omits browser credentials', async () => {
        const mod = await multipartModule()
        const finalRequests: Request[] = []
        const fetcher = vi.fn(async (input: string, options?: RequestInit) => {
            finalRequests.push(new Request(input, options))
            return {
                arrayBuffer: () => {
                    throw new Error('response body must not be read')
                },
                headers: new Headers({ ETag: '"etag-1"' }),
                status: 200,
                text: () => {
                    throw new Error('response body must not be read')
                },
            }
        })
        const transport = mod.createPresignedUploadTransport({
            allowedObjectOrigin: 'https://objects.example',
            fetch: fetcher,
        })
        const body = new NodeBlob(['part'], {
            type: 'application/pdf',
        }) as Blob

        await expect(
            transport.put('https://objects.example/upload/1?signature=x', body),
        ).resolves.toBe('"etag-1"')
        const options = fetcher.mock.calls[0]?.[1]
        expect(options).toMatchObject({
            body,
            credentials: 'omit',
            method: 'PUT',
            redirect: 'error',
        })
        expect(finalRequests).toHaveLength(1)
        const finalRequest = finalRequests[0]
        expect(finalRequest?.credentials).toBe('omit')
        expect(finalRequest?.redirect).toBe('error')
        expect(finalRequest?.headers.get('Content-Type')).toBe(
            'application/pdf',
        )
        expect(finalRequest?.headers.has('Authorization')).toBe(false)
        expect(finalRequest?.headers.has('Cookie')).toBe(false)
        expect(finalRequest?.headers.has('X-CSRF-Token')).toBe(false)
    })

    test.each([
        'http://objects.example',
        'https://user:pass@objects.example',
        'https://objects.example/path',
        'https://objects.example?query=x',
        'https://objects.example#fragment',
    ])('rejects malformed allowed origin %s before fetch', async (origin) => {
        const mod = await multipartModule()
        const fetcher = vi.fn()
        expect(() =>
            mod.createPresignedUploadTransport({
                allowedObjectOrigin: origin,
                fetch: fetcher,
            }),
        ).toThrow(mod.UploadContractError)
        expect(fetcher).not.toHaveBeenCalled()
    })

    test.each([
        'https://objects.example.evil/upload',
        'https://user@objects.example/upload',
        'http://objects.example/upload',
        'https://objects.example:444/upload',
    ])('rejects cross-origin presigned URL %s before fetch', async (url) => {
        const mod = await multipartModule()
        const fetcher = vi.fn()
        const transport = mod.createPresignedUploadTransport({
            allowedObjectOrigin: 'https://objects.example',
            fetch: fetcher,
        })
        await expect(
            transport.put(url, new Blob(['x'])),
        ).rejects.toBeInstanceOf(mod.UploadContractError)
        expect(fetcher).not.toHaveBeenCalled()
    })

    test.each([
        { etag: '', status: 200 },
        { etag: 'bad\r\netag', status: 200 },
        { etag: 'x'.repeat(1025), status: 200 },
        { etag: 'valid', status: 201 },
    ])(
        'rejects unsafe provider response %# without reading body',
        async (reply) => {
            const mod = await multipartModule()
            let bodyReads = 0
            const fetcher = vi.fn(async () => ({
                headers: {
                    get: (name: string) =>
                        name.toLowerCase() === 'etag' ? reply.etag : null,
                },
                status: reply.status,
                text: () => {
                    bodyReads += 1
                    return Promise.resolve('provider sentinel')
                },
            }))
            const transport = mod.createPresignedUploadTransport({
                allowedObjectOrigin: 'https://objects.example',
                fetch: fetcher,
            })
            await expect(
                transport.put(
                    'https://objects.example/upload',
                    new Blob(['x']),
                ),
            ).rejects.toBeInstanceOf(mod.UploadContractError)
            expect(bodyReads).toBe(0)
        },
    )
})
