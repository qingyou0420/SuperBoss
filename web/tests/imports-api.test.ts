import { describe, expect, test, vi } from 'vitest'

const MODULE_PATH = '../src/api/imports'
const JOB_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'
const ATTACHMENT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f812'
const FILE_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f813'
const UPLOAD_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f814'

interface ImportsModule {
    ImportContractError: new () => Error
    MAX_OWNER_IMPORTS: number
    createImportsApi(client: unknown): {
        list(limit?: number): Promise<unknown[]>
    }
    importErrorMessage(error: unknown): string
}

async function importsModule(): Promise<ImportsModule> {
    return (await import(/* @vite-ignore */ MODULE_PATH)) as ImportsModule
}

function clientReturning(data: unknown, status = 200) {
    const get = vi.fn(async () => Object.freeze({ data, status }))
    return { client: Object.freeze({ get }), get }
}

const summary = {
    attachments: [
        {
            file_id: FILE_ID,
            file_state: 'SCANNING',
            id: ATTACHMENT_ID,
            kind: 'REVISED',
            upload_id: UPLOAD_ID,
        },
    ],
    created_at: '2026-08-10T02:00:00Z',
    external_document_reference: 'KimiWork/客户方案.docx',
    id: JOB_ID,
    local_task_id: 'kimi-20260810-1754',
    model_label: 'kimi-k3',
    project_id: PROJECT_ID,
    result_code: null,
    status: 'SCANNING',
    submitted_at: '2026-08-10T02:01:00Z',
    updated_at: '2026-08-10T02:02:00Z',
}

describe('strict bounded OWNER import summaries', () => {
    test('calls only the real bounded owner list endpoint and exposes no K3 body', async () => {
        const mod = await importsModule()
        const { client, get } = clientReturning([summary])

        await expect(mod.createImportsApi(client).list(25)).resolves.toEqual([
            {
                ...summary,
                created_at: '2026-08-10T02:00:00.000Z',
                submitted_at: '2026-08-10T02:01:00.000Z',
                updated_at: '2026-08-10T02:02:00.000Z',
            },
        ])
        expect(get).toHaveBeenCalledWith('/owner/import-jobs', {
            params: { limit: '25' },
        })
        expect(
            JSON.stringify((await get.mock.results[0]?.value)?.data),
        ).not.toMatch(
            /k3_result|modification_details|knowledge_points|risks|token/i,
        )
    })

    test('locks the backend limit range before any request', async () => {
        const mod = await importsModule()
        expect(mod.MAX_OWNER_IMPORTS).toBe(100)
        const { client, get } = clientReturning([])
        for (const limit of [0, 101, 1.5, Number.NaN]) {
            await expect(
                mod.createImportsApi(client).list(limit),
            ).rejects.toBeInstanceOf(mod.ImportContractError)
        }
        expect(get).not.toHaveBeenCalled()
    })

    test.each([
        ['UPLOADING', null, 'UPLOADING'],
        ['SCANNING', null, 'SCANNING'],
        ['RECEIVED', null, 'CLEAN'],
        ['REJECTED', 'ATTACHMENT_INFECTED', 'INFECTED'],
        ['CONFLICT', 'BASE_SHA256_MISMATCH', 'CLEAN'],
    ])(
        'accepts exact job/result/file-state semantics for %s',
        async (status, result_code, file_state) => {
            const mod = await importsModule()
            const value = {
                ...summary,
                attachments: [{ ...summary.attachments[0], file_state }],
                result_code,
                status,
                submitted_at:
                    status === 'UPLOADING' ? null : summary.submitted_at,
            }
            await expect(
                mod.createImportsApi(clientReturning([value]).client).list(),
            ).resolves.toHaveLength(1)
        },
    )

    test.each([
        ['UPLOADING', null, null, 'QUARANTINED'],
        ['SCANNING', null, summary.submitted_at, 'CLEAN'],
        ['SCANNING', null, summary.submitted_at, 'INFECTED'],
        ['SCANNING', null, summary.submitted_at, 'FAILED'],
    ])(
        'accepts live attachment snapshot %s/%s independently of job reconciliation',
        async (status, result_code, submitted_at, file_state) => {
            const mod = await importsModule()
            const liveSnapshot = {
                ...summary,
                attachments: [{ ...summary.attachments[0], file_state }],
                result_code,
                status,
                submitted_at,
            }

            await expect(
                mod
                    .createImportsApi(clientReturning([liveSnapshot]).client)
                    .list(),
            ).resolves.toHaveLength(1)
        },
    )

    test('rejects extra/K3/secret fields, malformed time/UUID/state, and oversized rows', async () => {
        const mod = await importsModule()
        const invalid = [
            { ...summary, id: 'not-a-uuid' },
            { ...summary, created_at: '2026-08-10T02:00:00' },
            { ...summary, status: 'DONE' },
            { ...summary, status: 'RECEIVED', result_code: 'SHOULD_BE_NULL' },
            {
                ...summary,
                attachments: [
                    { ...summary.attachments[0], file_state: 'UPLOADING' },
                ],
                status: 'UPLOADING',
                submitted_at: summary.submitted_at,
            },
            { ...summary, status: 'SCANNING', submitted_at: null },
            { ...summary, local_task_id: 'x'.repeat(256) },
            {
                ...summary,
                attachments: Array.from(
                    { length: 4 },
                    () => summary.attachments[0],
                ),
            },
        ]
        for (const value of invalid) {
            await expect(
                mod.createImportsApi(clientReturning([value]).client).list(),
            ).rejects.toBeInstanceOf(mod.ImportContractError)
        }
        await expect(
            mod.createImportsApi(clientReturning([summary], 201).client).list(),
        ).rejects.toBeInstanceOf(mod.ImportContractError)
        await expect(
            mod
                .createImportsApi(
                    clientReturning(
                        Array.from({ length: 101 }, (_, index) => ({
                            ...summary,
                            id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
                        })),
                    ).client,
                )
                .list(),
        ).rejects.toBeInstanceOf(mod.ImportContractError)
    })

    test('maps failures to one safe fixed message', async () => {
        const mod = await importsModule()
        const error = new Error('K3正文 sentinel database traceback')
        expect(mod.importErrorMessage(error)).toBe(
            '导入任务暂时无法加载，请稍后重试。',
        )
        expect(mod.importErrorMessage(error)).not.toMatch(/sentinel|traceback/i)
    })
})
