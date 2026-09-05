import { Blob as NodeBlob } from 'node:buffer'

import { afterEach, describe, expect, test, vi } from 'vitest'

const MODULE_PATH = '../src/uploads/multipart'
const PART_SIZE = 8 * 1024 * 1024
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const UPLOAD_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'
const FILE_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f812'
const FILE_SHA = 'a'.repeat(64)

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

function uploaderFixture(options?: { fileSize?: number }) {
    const file = fileOfSize(options?.fileSize ?? 2 * PART_SIZE + 1)
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
    const hashFile = vi.fn(async () => FILE_SHA)
    const uploadPart = vi.fn(async (url: string, body: Blob) => {
        void url
        void body
        return 'etag'
    })
    return { file, filesApi, hashFile, uploadPart }
}

function uploadCommand(file: File) {
    return {
        file,
        folder_id: PROJECT_ID,
    }
}

afterEach(() => {
    vi.restoreAllMocks()
})

describe('multipart upload orchestration', () => {
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
            hashFile: fixture.hashFile,
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
        expect(fixture.hashFile).toHaveBeenCalledTimes(1)
    })

    test('rejects a malformed folder id before hashing or network', async () => {
        const mod = await multipartModule()
        const fixture = uploaderFixture({ fileSize: 1 })
        const uploader = mod.createMultipartUploader({
            filesApi: fixture.filesApi,
            hashFile: fixture.hashFile,
            uploadPart: fixture.uploadPart,
        })

        await expect(
            uploader.upload({
                ...uploadCommand(fixture.file),
                folder_id: 'not-a-folder',
            }),
        ).rejects.toBeInstanceOf(mod.UploadContractError)
        expect(fixture.hashFile).not.toHaveBeenCalled()
        expect(fixture.filesApi.start).not.toHaveBeenCalled()
        expect(fixture.filesApi.partUrl).not.toHaveBeenCalled()
        expect(fixture.uploadPart).not.toHaveBeenCalled()
    })

    test('derives a stable idempotency key from the full upload intent', async () => {
        const mod = await multipartModule()
        const first = uploaderFixture({ fileSize: 1 })
        first.filesApi.start.mockRejectedValueOnce(new Error('response lost'))
        const firstUploader = mod.createMultipartUploader({
            filesApi: first.filesApi,
            hashFile: first.hashFile,
            uploadPart: first.uploadPart,
        })
        await expect(
            firstUploader.upload(uploadCommand(first.file)),
        ).rejects.toBeInstanceOf(mod.UploadContractError)
        const firstKey = first.filesApi.start.mock.calls[0]?.[1]

        const retry = uploaderFixture({ fileSize: 1 })
        const retryUploader = mod.createMultipartUploader({
            filesApi: retry.filesApi,
            hashFile: retry.hashFile,
            uploadPart: retry.uploadPart,
        })
        await retryUploader.upload(uploadCommand(retry.file))
        const retryKey = retry.filesApi.start.mock.calls[0]?.[1]

        const changed = uploaderFixture({ fileSize: 1 })
        const changedUploader = mod.createMultipartUploader({
            filesApi: changed.filesApi,
            hashFile: changed.hashFile,
            uploadPart: changed.uploadPart,
        })
        await changedUploader.upload({
            ...uploadCommand(changed.file),
            folder_id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f899',
        })
        const changedKey = changed.filesApi.start.mock.calls[0]?.[1]

        expect(firstKey).toBe(retryKey)
        expect(firstKey).toMatch(/^[!-~]{64,255}$/)
        expect(firstKey).not.toBe(FILE_SHA)
        expect(changedKey).not.toBe(firstKey)
    })

    test('reports uploaded bytes after each completed batch', async () => {
        const mod = await multipartModule()
        const fixture = uploaderFixture({ fileSize: PART_SIZE + 17 })
        const progress: Array<[number, number]> = []
        const uploader = mod.createMultipartUploader({
            filesApi: fixture.filesApi,
            hashFile: fixture.hashFile,
            onProgress: (uploaded: number, total: number) => {
                progress.push([uploaded, total])
            },
            uploadPart: fixture.uploadPart,
        })
        await uploader.upload(uploadCommand(fixture.file))
        expect(progress[0]).toEqual([0, PART_SIZE + 17])
        expect(progress.at(-1)).toEqual([PART_SIZE + 17, PART_SIZE + 17])
    })

    test.each(['hash', 'start', 'presign', 'put', 'complete'])(
        'fails closed on %s without leaking provider details',
        async (stage) => {
            const mod = await multipartModule()
            const fixture = uploaderFixture({ fileSize: 1 })
            const sentinel = new Error(`${stage} secret provider sentinel`)
            if (stage === 'hash') fixture.hashFile.mockRejectedValue(sentinel)
            if (stage === 'start')
                fixture.filesApi.start.mockRejectedValue(sentinel)
            if (stage === 'presign')
                fixture.filesApi.partUrl.mockRejectedValue(sentinel)
            if (stage === 'put') fixture.uploadPart.mockRejectedValue(sentinel)
            if (stage === 'complete')
                fixture.filesApi.complete.mockRejectedValue(sentinel)
            const uploader = mod.createMultipartUploader({
                filesApi: fixture.filesApi,
                hashFile: fixture.hashFile,
                uploadPart: fixture.uploadPart,
            })

            await expect(
                uploader.upload(uploadCommand(fixture.file)),
            ).rejects.toBeInstanceOf(mod.UploadContractError)
            if (stage === 'hash') {
                expect(fixture.filesApi.start).not.toHaveBeenCalled()
            }
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
            hashFile: fixture.hashFile,
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

describe('isolated presigned PUT boundaries', () => {
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
