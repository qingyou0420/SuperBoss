import type {
    AxiosAdapter,
    AxiosRequestConfig,
    AxiosResponse,
    InternalAxiosRequestConfig,
} from 'axios'
import { describe, expect, test } from 'vitest'

import { HttpClientError, createHttpClient } from '../src/api/http'
import {
    MAX_PROJECTS_PER_RESPONSE,
    ProjectContractError,
    createProjectsApi,
    projectErrorMessage,
} from '../src/api/projects'

function response(
    config: InternalAxiosRequestConfig,
    status: number,
    data: unknown,
): AxiosResponse {
    return {
        config,
        data,
        headers: {},
        status,
        statusText: String(status),
    } as AxiosResponse
}

function clientReturning(data: unknown, status = 200) {
    const adapter: AxiosAdapter = async (config) =>
        response(config, status, data)
    return createHttpClient({ adapter })
}

const project = {
    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
    name: '客户方案',
    is_test: true,
    status: 'ACTIVE' as const,
}

const projectConflictBody = {
    error: {
        code: 'PROJECT_NAME_CONFLICT',
        message: 'A project with this name already exists',
        request_id: 'bba39a39-47ba-4ac5-9250-ccdba1d7f25e',
    },
}

describe('strict project API contracts', () => {
    test('uses a finite M1 list ceiling', () => {
        expect(MAX_PROJECTS_PER_RESPONSE).toBe(1000)
    })

    test('accepts only exact bounded ProjectRead objects', async () => {
        await expect(
            createProjectsApi(clientReturning([project])).list(),
        ).resolves.toEqual([project])
        const unicodeName = '😀'.repeat(255)
        await expect(
            createProjectsApi(
                clientReturning([{ ...project, name: unicodeName }]),
            ).list(),
        ).resolves.toEqual([{ ...project, name: unicodeName }])

        for (const invalid of [
            [{ ...project, secret: 'leak' }],
            [{ ...project, id: 'not-a-uuid' }],
            [{ ...project, name: '' }],
            [{ ...project, name: ' x' }],
            [{ ...project, name: 'x'.repeat(256) }],
            [{ ...project, name: 'bad\r\nname' }],
            [{ ...project, is_test: 'true' }],
            [{ ...project, status: 'DELETED' }],
            { items: [project] },
        ]) {
            await expect(
                createProjectsApi(clientReturning(invalid)).list(),
            ).rejects.toBeInstanceOf(ProjectContractError)
        }
    })

    test('bounds the project list count before exposing it to the UI', async () => {
        const oversized = Array.from(
            { length: MAX_PROJECTS_PER_RESPONSE + 1 },
            (_, index) => ({
                ...project,
                id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
            }),
        )

        await expect(
            createProjectsApi(clientReturning(oversized)).list(),
        ).rejects.toBeInstanceOf(ProjectContractError)
    })

    test('sends the exact canonical create body including strict is_test', async () => {
        let seen: AxiosRequestConfig | undefined
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 201, { ...project, name: '验收沙盒' })
        }
        const api = createProjectsApi(createHttpClient({ adapter }))

        await expect(
            api.create({ name: '  验收沙盒\u00a0', is_test: true }),
        ).resolves.toMatchObject({
            name: '验收沙盒',
            is_test: true,
        })

        expect(seen?.url).toBe('/projects')
        expect(seen?.method).toBe('post')
        expect(JSON.parse(String(seen?.data))).toEqual({
            name: '验收沙盒',
            is_test: true,
        })
    })

    test.each([
        { operation: 'list' as const, status: 201 },
        { operation: 'create' as const, status: 200 },
        { operation: 'create' as const, status: 202 },
    ])(
        'rejects $operation success payloads with status $status',
        async ({ operation, status }) => {
            const data = operation === 'list' ? [project] : project
            const api = createProjectsApi(clientReturning(data, status))
            const pending =
                operation === 'list'
                    ? api.list()
                    : api.create({ name: project.name, is_test: true })

            await expect(pending).rejects.toBeInstanceOf(ProjectContractError)
        },
    )

    test('rejects invalid create values locally before any request', async () => {
        let calls = 0
        const adapter: AxiosAdapter = async (config) => {
            calls += 1
            return response(config, 500, null)
        }
        const api = createProjectsApi(createHttpClient({ adapter }))

        for (const invalid of [
            { name: '', is_test: false },
            { name: ' '.repeat(3), is_test: false },
            { name: 'x'.repeat(256), is_test: false },
            { name: 'bad\u0000name', is_test: false },
            { name: 'valid', is_test: 'false' },
        ]) {
            await expect(api.create(invalid as never)).rejects.toBeInstanceOf(
                ProjectContractError,
            )
        }
        expect(calls).toBe(0)
    })

    test('maps only a strict project error envelope and never renders its server message', () => {
        const conflict = new HttpClientError(409, projectConflictBody)
        expect(projectErrorMessage(conflict)).toBe('项目名称已存在。')

        for (const data of [
            {
                error: {
                    code: 'PROJECT_NAME_CONFLICT',
                    message: 'sentinel secret',
                    request_id: 'id',
                    internal: 'postgres password',
                },
            },
            { detail: 'sentinel secret' },
            {
                error: {
                    code: 'PROJECT_NAME_CONFLICT',
                    message: 'sentinel secret',
                    request_id: 'bba39a39-47ba-4ac5-9250-ccdba1d7f25e',
                },
            },
            {
                error: {
                    code: 'UNKNOWN',
                    message: 'sentinel secret',
                    request_id: 'id',
                },
            },
        ]) {
            const malformed = new HttpClientError(500, data)
            expect(projectErrorMessage(malformed)).toBe(
                '项目操作失败，请稍后重试。',
            )
            expect(projectErrorMessage(malformed)).not.toContain('sentinel')
        }
    })

    test.each([
        { label: 'client error', status: 400 },
        { label: 'server error', status: 500 },
        { label: 'missing status', status: undefined },
    ])(
        'does not map the exact conflict body with $label to a name conflict',
        ({ status }) => {
            const error = new HttpClientError(status, projectConflictBody)

            expect(projectErrorMessage(error)).toBe(
                '项目操作失败，请稍后重试。',
            )
        },
    )
})
