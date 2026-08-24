import { describe, expect, test, vi } from 'vitest'

import { HttpClientError } from '../src/api/http'

const MODULE_PATH = '../src/api/files'
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const UPLOAD_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'
const FILE_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f812'

interface FilesModule {
    FileContractError: new () => Error
    FileDownloadUnavailableError: new (
        state: 'INFECTED' | 'FAILED',
    ) => Error & { readonly state: 'INFECTED' | 'FAILED' }
    createFilesApi(client: unknown): {
        start(command: unknown, idempotencyKey: string): Promise<unknown>
        partUrl(uploadId: string, partNumber: number): Promise<string>
        complete(uploadId: string, parts: unknown[]): Promise<unknown>
        download(fileId: string): Promise<string>
    }
    fileErrorMessage(error: unknown): string
}

async function filesModule(): Promise<FilesModule> {
    return (await import(/* @vite-ignore */ MODULE_PATH)) as FilesModule
}

function clientWith(steps: Array<{ data: unknown; status: number }>) {
    const calls: Array<{
        data?: unknown
        method: string
        options?: unknown
        url: string
    }> = []
    const take = (
        method: string,
        url: string,
        data?: unknown,
        options?: unknown,
    ) => {
        calls.push({ data, method, options, url })
        const next = steps.shift()
        if (!next) throw new Error('unexpected request')
        return Promise.resolve(Object.freeze(next))
    }
    return {
        calls,
        client: Object.freeze({
            get: vi.fn((url: string) => take('get', url)),
            post: vi.fn((url: string, data?: unknown, options?: unknown) =>
                take('post', url, data, options),
            ),
        }),
    }
}

const startCommand = {
    category: '客户方案',
    content_type:
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    file_date: '2026-08-10',
    filename: '客户方案.docx',
    project_id: PROJECT_ID,
    sha256: 'a'.repeat(64),
    size_bytes: 8_388_609,
}
const idempotencyKey = `file-${'a'.repeat(64)}`

describe('strict browser file API', () => {
    test('uses exact frozen routes, statuses, and canonical bodies', async () => {
        const mod = await filesModule()
        const { calls, client } = clientWith([
            { data: { upload_id: UPLOAD_ID, file_id: FILE_ID }, status: 201 },
            {
                data: { url: 'https://objects.example/upload/part-1' },
                status: 200,
            },
            { data: { file_id: FILE_ID, state: 'QUARANTINED' }, status: 200 },
            {
                data: { url: 'https://objects.example/download/file' },
                status: 200,
            },
        ])
        const api = mod.createFilesApi(client)

        expect(Object.keys(api).sort()).toEqual([
            'complete',
            'download',
            'partUrl',
            'start',
        ])

        await expect(api.start(startCommand, idempotencyKey)).resolves.toEqual({
            file_id: FILE_ID,
            upload_id: UPLOAD_ID,
        })
        await expect(api.partUrl(UPLOAD_ID, 1)).resolves.toBe(
            'https://objects.example/upload/part-1',
        )
        await expect(
            api.complete(UPLOAD_ID, [{ etag: 'etag-1', part_number: 1 }]),
        ).resolves.toEqual({ file_id: FILE_ID, state: 'QUARANTINED' })
        await expect(api.download(FILE_ID)).resolves.toBe(
            'https://objects.example/download/file',
        )

        expect(calls).toEqual([
            {
                data: startCommand,
                method: 'post',
                options: { idempotencyKey },
                url: '/files/uploads',
            },
            {
                data: undefined,
                method: 'post',
                options: undefined,
                url: `/files/uploads/${UPLOAD_ID}/parts/1`,
            },
            {
                data: { parts: [{ etag: 'etag-1', part_number: 1 }] },
                method: 'post',
                options: undefined,
                url: `/files/uploads/${UPLOAD_ID}/complete`,
            },
            {
                data: undefined,
                method: 'get',
                options: undefined,
                url: `/files/${FILE_ID}/download`,
            },
        ])
    })

    test('accepts extra fields on a valid upload start response', async () => {
        const mod = await filesModule()
        const api = mod.createFilesApi(
            clientWith([
                {
                    data: {
                        upload_id: UPLOAD_ID,
                        file_id: FILE_ID,
                        token: 'sentinel',
                    },
                    status: 201,
                },
            ]).client,
        )
        await expect(api.start(startCommand, idempotencyKey)).resolves.toEqual({
            file_id: FILE_ID,
            upload_id: UPLOAD_ID,
        })
    })

    test.each([
        { data: { upload_id: 'bad', file_id: FILE_ID }, status: 201 },
        { data: { upload_id: UPLOAD_ID, file_id: FILE_ID }, status: 200 },
    ])('rejects malformed or wrong-status upload start %#', async (reply) => {
        const mod = await filesModule()
        const api = mod.createFilesApi(clientWith([reply]).client)
        await expect(
            api.start(startCommand, idempotencyKey),
        ).rejects.toBeInstanceOf(mod.FileContractError)
    })

    test('rejects local bounds before network and strict URL/ETag/state responses', async () => {
        const mod = await filesModule()
        const idle = clientWith([])
        const api = mod.createFilesApi(idle.client)

        const invalidStarts: Array<{ command: unknown; key: string }> = [
            {
                command: { ...startCommand, size_bytes: 0 },
                key: idempotencyKey,
            },
            {
                command: {
                    ...startCommand,
                    size_bytes: 100 * 1024 * 1024 + 1,
                },
                key: idempotencyKey,
            },
            {
                command: { ...startCommand, sha256: 'A'.repeat(64) },
                key: idempotencyKey,
            },
            {
                command: { ...startCommand, filename: 'bad\rname.docx' },
                key: idempotencyKey,
            },
            {
                command: { ...startCommand, content_type: 'not-mime' },
                key: idempotencyKey,
            },
            {
                command: { ...startCommand, file_date: '2026-02-31' },
                key: idempotencyKey,
            },
            { command: startCommand, key: '' },
            { command: startCommand, key: 'bad key' },
            { command: startCommand, key: 'é' },
        ]
        for (const { command, key } of invalidStarts) {
            await expect(api.start(command, key)).rejects.toBeInstanceOf(
                mod.FileContractError,
            )
        }
        expect(idle.calls).toHaveLength(0)

        for (const reply of [
            { data: { url: 'https://objects.example/u\r\nX:x' }, status: 200 },
            {
                data: { url: `https://objects.example/${'x'.repeat(4097)}` },
                status: 200,
            },
        ]) {
            await expect(
                mod
                    .createFilesApi(clientWith([reply]).client)
                    .partUrl(UPLOAD_ID, 1),
            ).rejects.toBeInstanceOf(mod.FileContractError)
        }

        await expect(
            mod
                .createFilesApi(
                    clientWith([
                        {
                            data: {
                                url: 'https://objects.example/u',
                                extra: true,
                            },
                            status: 200,
                        },
                    ]).client,
                )
                .partUrl(UPLOAD_ID, 1),
        ).resolves.toBe('https://objects.example/u')

        for (const reply of [
            { data: { file_id: FILE_ID, state: 'UPLOADING' }, status: 200 },
            { data: { file_id: FILE_ID, state: 'SCANNING' }, status: 201 },
        ]) {
            await expect(
                mod
                    .createFilesApi(clientWith([reply]).client)
                    .complete(UPLOAD_ID, [{ etag: 'etag', part_number: 1 }]),
            ).rejects.toBeInstanceOf(mod.FileContractError)
        }
    })

    test('maps transport failures to a safe message with status and request id', async () => {
        const mod = await filesModule()
        const detail = 's3://secret@internal provider traceback sentinel'
        expect(mod.fileErrorMessage(new Error(detail))).toBe(
            '文件操作失败，请稍后重试。',
        )
        expect(mod.fileErrorMessage(new Error(detail))).not.toContain(
            'sentinel',
        )
        expect(
            mod.fileErrorMessage(
                new HttpClientError(503, {
                    error: {
                        code: 'FILE_COMPLETION_PENDING',
                        message: detail,
                        request_id: 'bba39a39-47ba-4ac5-9250-ccdba1d7f25e',
                    },
                }),
            ),
        ).toBe('文件操作失败（503，bba39a39-47ba-4ac5-9250-ccdba1d7f25e）')
        expect(
            mod.fileErrorMessage(new HttpClientError(502, { error: {} })),
        ).toBe('文件操作失败（502）')
    })

    test.each([
        ['FILE_INFECTED', 'File did not pass security scanning', 'INFECTED'],
        ['FILE_SCAN_FAILED', 'File scanning did not complete', 'FAILED'],
    ] as const)(
        'maps exact terminal download error %s to fixed state %s',
        async (code, message, state) => {
            const mod = await filesModule()
            const failure = new HttpClientError(409, {
                error: {
                    code,
                    message,
                    request_id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f813',
                },
            })
            const client = Object.freeze({
                get: vi.fn().mockRejectedValue(failure),
                post: vi.fn(),
            })

            const caught = await mod
                .createFilesApi(client)
                .download(FILE_ID)
                .catch((error: unknown) => error)

            expect(caught).toBeInstanceOf(mod.FileDownloadUnavailableError)
            expect(caught).toMatchObject({ state })
            expect(Object.keys(caught as object).sort()).toEqual(['state'])
            expect(String(caught)).not.toContain(code)
            expect(String(caught)).not.toContain('019f2b8e')
        },
    )

    test.each([
        new HttpClientError(400, {
            error: {
                code: 'FILE_INFECTED',
                message: 'File did not pass security scanning',
                request_id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f813',
            },
        }),
        new HttpClientError(409, {
            error: {
                code: 'FILE_UNKNOWN',
                message: 'wrong message',
                request_id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f813',
            },
        }),
    ])(
        'does not trust malformed terminal error envelope %#',
        async (failure) => {
            const mod = await filesModule()
            const client = Object.freeze({
                get: vi.fn().mockRejectedValue(failure),
                post: vi.fn(),
            })

            await expect(
                mod.createFilesApi(client).download(FILE_ID),
            ).rejects.toBe(failure)
        },
    )
})
