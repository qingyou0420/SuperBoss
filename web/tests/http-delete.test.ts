import {
    AxiosError,
    type AxiosAdapter,
    type AxiosHeaders,
    type AxiosResponse,
    type InternalAxiosRequestConfig,
} from 'axios'
import { afterEach, describe, expect, test, vi } from 'vitest'

import { createHttpClient } from '../src/api/http'

function response(
    config: InternalAxiosRequestConfig,
    status: number,
    data: unknown,
    headers: Record<string, string> = {},
): AxiosResponse {
    return {
        config,
        data,
        headers,
        status,
        statusText: String(status),
    } as AxiosResponse
}

interface DeleteFacade {
    delete<T = unknown>(
        url: string,
    ): Promise<{
        readonly status: number
        readonly data: T
    }>
}

interface PostOptionsFacade {
    post<T = unknown>(
        url: string,
        data?: unknown,
        options?: { readonly idempotencyKey?: string },
    ): Promise<{
        readonly status: number
        readonly data: T
    }>
}

function unauthorized(config: InternalAxiosRequestConfig): never {
    throw new AxiosError(
        'sentinel raw unauthorized',
        'ERR_BAD_REQUEST',
        config,
        undefined,
        response(
            config,
            401,
            { detail: 'sentinel' },
            { 'X-SuperBoss-Refreshable': '1' },
        ),
    )
}

afterEach(() => {
    document.cookie = 'XSRF-TOKEN=; Max-Age=0; Path=/'
})

describe('safe browser DELETE facade', () => {
    test('exposes only frozen bounded verb methods', () => {
        const client = createHttpClient({
            adapter: vi.fn() as unknown as AxiosAdapter,
        })

        expect(Object.isFrozen(client)).toBe(true)
        expect(Object.keys(client).sort()).toEqual([
            'delete',
            'get',
            'patch',
            'post',
            'put',
        ])
        expect('request' in client).toBe(false)
        expect('defaults' in client).toBe(false)
        expect('interceptors' in client).toBe(false)
    })

    test('sends a bodyless cookie DELETE with only the exact CSRF header', async () => {
        document.cookie = 'XSRF-TOKEN=csrf-delete; Path=/'
        let seen: InternalAxiosRequestConfig | undefined
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 204, '')
        }
        const client = createHttpClient({
            adapter,
        }) as typeof createHttpClient extends (...args: never[]) => infer Result
            ? Result & DeleteFacade
            : DeleteFacade

        await expect(
            client.delete(
                '/owner/devices/019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
            ),
        ).resolves.toEqual({ status: 204, data: null })

        expect(seen?.method).toBe('delete')
        expect(seen?.baseURL).toBe('/api/v1')
        expect(seen?.withCredentials).toBe(true)
        expect(seen?.data).toBeUndefined()
        const headers = seen?.headers as AxiosHeaders
        expect(headers.get('X-CSRF-Token')).toBe('csrf-delete')
        expect(headers.get('Authorization')).toBeUndefined()
        expect(Object.keys(headers.toJSON()).sort()).toEqual([
            'Accept',
            'X-CSRF-Token',
        ])
    })

    test('ignores hostile runtime arguments and never executes their hooks', async () => {
        document.cookie = 'XSRF-TOKEN=trusted; Path=/'
        let seen: InternalAxiosRequestConfig | undefined
        let hostileCalls = 0
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 204, '')
        }
        const client = createHttpClient({ adapter }) as unknown as DeleteFacade
        const hostile = {
            adapter: () => {
                hostileCalls += 1
            },
            auth: { password: 'sentinel', username: 'sentinel' },
            beforeRedirect: () => {
                hostileCalls += 1
            },
            data: 'sentinel',
            headers: {
                Authorization: 'Bearer sentinel',
                'X-CSRF-Token': 'sentinel',
            },
            transformRequest: () => {
                hostileCalls += 1
            },
        }

        await (
            client.delete as unknown as (...args: unknown[]) => Promise<unknown>
        )(
            '/owner/devices/019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
            hostile,
            hostile,
        )

        expect(hostileCalls).toBe(0)
        expect(seen?.data).toBeUndefined()
        const headers = seen?.headers as AxiosHeaders
        expect(headers.get('Authorization')).toBeUndefined()
        expect(headers.get('X-CSRF-Token')).toBe('trusted')
    })
})

describe('narrow idempotent POST option', () => {
    const key = `file-${'a'.repeat(64)}`

    test('sets one exact printable Idempotency-Key without changing facade surface', async () => {
        document.cookie = 'XSRF-TOKEN=csrf-post; Path=/'
        let seen: InternalAxiosRequestConfig | undefined
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 201, { ok: true })
        }
        const client = createHttpClient({
            adapter,
        }) as unknown as PostOptionsFacade

        await client.post(
            '/files/uploads',
            { safe: true },
            { idempotencyKey: key },
        )

        const headers = seen?.headers as AxiosHeaders
        expect(headers.get('Idempotency-Key')).toBe(key)
        expect(headers.get('X-CSRF-Token')).toBe('csrf-post')
        expect(headers.get('Authorization')).toBeUndefined()
        expect(Object.keys(client).sort()).toEqual([
            'delete',
            'get',
            'patch',
            'post',
            'put',
        ])
    })

    test.each([
        '',
        ' leading',
        'trailing ',
        'embedded space',
        'bad\nkey',
        'bad\u007fkey',
        'non-ascii-é',
        'x'.repeat(256),
    ])(
        'rejects invalid raw key %j before the adapter',
        async (idempotencyKey) => {
            const adapter = vi.fn(async (config: InternalAxiosRequestConfig) =>
                response(config, 201, { ok: true }),
            ) as unknown as AxiosAdapter
            const client = createHttpClient({
                adapter,
            }) as unknown as PostOptionsFacade

            await expect(
                client.post('/files/uploads', {}, { idempotencyKey }),
            ).rejects.toMatchObject({ name: 'ApiContractError' })
            expect(adapter).not.toHaveBeenCalled()
        },
    )

    test('reads only an own data idempotency property and ignores hostile hooks', async () => {
        let hostileCalls = 0
        const seen: InternalAxiosRequestConfig[] = []
        const adapter: AxiosAdapter = async (config) => {
            seen.push(config)
            return response(config, 201, { ok: true })
        }
        const client = createHttpClient({
            adapter,
        }) as unknown as PostOptionsFacade
        const symbol = Symbol('hostile')
        const hostile = Object.create({ idempotencyKey: 'inherited-sentinel' })
        Object.defineProperties(hostile, {
            headers: {
                enumerable: true,
                get: () => {
                    hostileCalls += 1
                    return { Authorization: 'Bearer sentinel' }
                },
            },
            idempotencyKey: {
                enumerable: true,
                value: key,
            },
            transformRequest: {
                enumerable: true,
                get: () => {
                    hostileCalls += 1
                    return () => 'sentinel'
                },
            },
        })
        Object.defineProperty(hostile, symbol, {
            get: () => {
                hostileCalls += 1
                return 'sentinel'
            },
        })

        await client.post(
            '/files/uploads',
            { safe: true },
            hostile as { idempotencyKey?: string },
        )
        const inheritedOnly = Object.create({ idempotencyKey: key })
        await client.post(
            '/projects',
            { safe: true },
            inheritedOnly as { idempotencyKey?: string },
        )

        expect(hostileCalls).toBe(0)
        expect((seen[0]?.headers as AxiosHeaders).get('Idempotency-Key')).toBe(
            key,
        )
        expect(
            (seen[0]?.headers as AxiosHeaders).get('Authorization'),
        ).toBeUndefined()
        expect(
            (seen[1]?.headers as AxiosHeaders).get('Idempotency-Key'),
        ).toBeUndefined()
    })

    test('replays the exact same key once after a 401 refresh', async () => {
        const businessKeys: unknown[] = []
        let businessCalls = 0
        const adapter: AxiosAdapter = async (config) => {
            if (config.url === '/auth/refresh') {
                return response(config, 204, '')
            }
            businessCalls += 1
            businessKeys.push(config.headers.get('Idempotency-Key'))
            if (businessCalls === 1) unauthorized(config)
            return response(config, 201, { ok: true })
        }
        const client = createHttpClient({
            adapter,
        }) as unknown as PostOptionsFacade

        await expect(
            client.post(
                '/files/uploads',
                { safe: true },
                { idempotencyKey: key },
            ),
        ).resolves.toEqual({ data: { ok: true }, status: 201 })
        expect(businessCalls).toBe(2)
        expect(businessKeys).toEqual([key, key])
    })
})
