import {
    AxiosHeaders,
    AxiosError,
    type AxiosAdapter,
    type AxiosRequestConfig,
    type AxiosRequestTransformer,
    type AxiosResponse,
    type InternalAxiosRequestConfig,
} from 'axios'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import {
    ApiContractError,
    MAX_API_RESPONSE_BYTES,
    createHttpClient,
} from '../src/api/http'

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

function unauthorized(config: InternalAxiosRequestConfig): never {
    const rejected = response(config, 401, {
        detail: 'Authentication required',
    })
    throw new AxiosError(
        'rejected',
        'ERR_BAD_REQUEST',
        config,
        undefined,
        rejected,
    )
}

function setCookie(value: string): void {
    document.cookie = 'XSRF-TOKEN=; Max-Age=0; Path=/'
    if (value) document.cookie = `XSRF-TOKEN=${value}; Path=/`
}

beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    setCookie('')
})

describe('browser HTTP security boundary', () => {
    test('uses the reviewed 256 KiB raw response ceiling', () => {
        expect(MAX_API_RESPONSE_BYTES).toBe(256 * 1024)
    })

    test('uses cookie credentials, never manufactures bearer auth, and attaches decoded CSRF only to unsafe methods', async () => {
        setCookie(encodeURIComponent('csrf value/+=_'))
        localStorage.setItem('access_token', 'attacker-local-access')
        sessionStorage.setItem('refresh_token', 'attacker-session-refresh')
        const seen: AxiosRequestConfig[] = []
        const adapter: AxiosAdapter = async (config) => {
            seen.push(config)
            return response(config, 204, null)
        }
        const getItem = vi.spyOn(Storage.prototype, 'getItem')
        const client = createHttpClient({ adapter })

        await client.get('/projects')
        await client.post('/projects', { name: 'Safe' })
        await client.put('/projects/1', {})
        await client.patch('/projects/1', {})
        await client.delete('/projects/1')

        expect(client.defaults.withCredentials).toBe(true)
        expect(client.defaults.baseURL).toBe('/api/v1')
        expect(client.defaults.responseType).toBe('text')
        expect(client.defaults.xsrfCookieName).toBe('')
        expect(client.defaults.xsrfHeaderName).toBe('')
        expect(seen).toHaveLength(5)
        for (const request of seen) {
            expect(request.headers?.Authorization).toBeUndefined()
        }
        expect(seen[0].headers?.['X-CSRF-Token']).toBeUndefined()
        for (const request of seen.slice(1)) {
            expect(request.headers?.['X-CSRF-Token']).toBe('csrf value/+=_')
        }
        expect(getItem).not.toHaveBeenCalled()
    })

    test('forces caller-requested JSON back to text before the adapter boundary', async () => {
        let seen: AxiosRequestConfig | undefined
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 200, { ok: true })
        }
        const client = createHttpClient({ adapter })

        await client.get('/projects', { responseType: 'json' })

        expect(seen?.responseType).toBe('text')
    })

    test('reasserts every cookie-only same-origin option and strips mixed-case auth headers before the adapter', async () => {
        setCookie(encodeURIComponent('reviewed csrf'))
        let seen: InternalAxiosRequestConfig | undefined
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 204, null)
        }
        const client = createHttpClient({ adapter })

        await client.post('/auth/logout', null, {
            auth: { username: 'attacker', password: 'sentinel' },
            baseURL: 'https://evil.example/api',
            headers: {
                aUtHoRiZaTiOn: 'Basic sentinel',
                'x-CsRf-ToKeN': 'caller-controlled',
            },
            withCredentials: false,
            xsrfCookieName: 'ATTACKER-XSRF',
            xsrfHeaderName: 'X-Attacker-XSRF',
        })

        expect(seen?.url).toBe('/auth/logout')
        expect(seen?.baseURL).toBe('/api/v1')
        expect(seen?.withCredentials).toBe(true)
        expect(seen?.xsrfCookieName).toBe('')
        expect(seen?.xsrfHeaderName).toBe('')
        expect(seen?.auth).toBeUndefined()
        const headers = AxiosHeaders.from(seen?.headers)
        expect(headers.has('Authorization')).toBe(false)
        expect(headers.get('X-CSRF-Token')).toBe('reviewed csrf')
        expect(JSON.stringify(seen)).not.toContain('sentinel')
        expect(JSON.stringify(seen)).not.toContain('evil.example')
    })

    test.each(['single function', 'array'])(
        'replaces a caller %s transformRequest before it can restore auth or alter JSON',
        async (shape) => {
            setCookie(encodeURIComponent('reviewed csrf'))
            let seen: InternalAxiosRequestConfig | undefined
            const adapter: AxiosAdapter = async (config) => {
                seen = config
                return response(config, 201, {
                    id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
                    name: 'Safe project',
                    is_test: true,
                    status: 'ACTIVE',
                })
            }
            const hostile: AxiosRequestTransformer = vi.fn((_data, headers) => {
                headers.set(
                    'aUtHoRiZaTiOn',
                    'Bearer reinserted-after-interceptor',
                )
                headers.set('x-CsRf-ToKeN', 'forged-after-interceptor')
                return JSON.stringify({ name: 'Tampered', is_test: false })
            })
            const transformRequest = shape === 'array' ? [hostile] : hostile
            const client = createHttpClient({ adapter })

            await client.post(
                '/projects',
                { name: 'Safe project', is_test: true },
                { transformRequest },
            )

            expect(hostile).not.toHaveBeenCalled()
            const headers = AxiosHeaders.from(seen?.headers)
            expect(headers.has('Authorization')).toBe(false)
            expect(headers.get('X-CSRF-Token')).toBe('reviewed csrf')
            expect(headers.get('Content-Type')).toMatch(/^application\/json/)
            expect(JSON.parse(String(seen?.data))).toEqual({
                name: 'Safe project',
                is_test: true,
            })
            expect(JSON.stringify(seen)).not.toContain(
                'reinserted-after-interceptor',
            )
            expect(JSON.stringify(seen)).not.toContain('Tampered')
        },
    )

    test('cannot replace the bounded text transform or size options per request', async () => {
        let seen: InternalAxiosRequestConfig | undefined
        const oversized = JSON.stringify({
            pad: 'x'.repeat(MAX_API_RESPONSE_BYTES),
        })
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 200, oversized, {
                'content-type': 'application/json',
            })
        }
        const client = createHttpClient({ adapter })

        await expect(
            client.get('/projects', {
                maxBodyLength: Number.MAX_SAFE_INTEGER,
                maxContentLength: Number.MAX_SAFE_INTEGER,
                responseType: 'json',
                transformResponse: [
                    (data: string) => JSON.parse(data) as unknown,
                ],
            }),
        ).rejects.toBeInstanceOf(ApiContractError)

        expect(seen?.responseType).toBe('text')
        expect(seen?.maxBodyLength).toBe(MAX_API_RESPONSE_BYTES)
        expect(seen?.maxContentLength).toBe(MAX_API_RESPONSE_BYTES)
    })

    test('rejects absolute and protocol-relative targets before cookies can leave the API boundary', async () => {
        let calls = 0
        const adapter: AxiosAdapter = async (config) => {
            calls += 1
            return response(config, 204, null)
        }
        const client = createHttpClient({ adapter })

        for (const target of [
            'https://evil.example/collect',
            '//evil.example/collect',
            'http://127.0.0.1/collect',
        ]) {
            await expect(client.get(target)).rejects.toBeInstanceOf(
                ApiContractError,
            )
        }
        expect(calls).toBe(0)
    })

    test('does not confuse cookie-name prefixes and safely omits malformed percent encoding', async () => {
        document.cookie = 'NOT-XSRF-TOKEN=wrong; Path=/'
        document.cookie = 'XSRF-TOKEN=%E0%A4%A; Path=/'
        let seen: AxiosRequestConfig | undefined
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 204, null)
        }

        await createHttpClient({ adapter }).post('/auth/logout', null, {
            headers: { 'X-CSRF-Token': 'caller-controlled' },
        })

        expect(seen?.headers?.['X-CSRF-Token']).toBeUndefined()
    })

    test('coalesces concurrent 401s into one refresh and retries each original request only once', async () => {
        let refreshCalls = 0
        const attempts = new Map<string, number>()
        const adapter: AxiosAdapter = async (config) => {
            const url = String(config.url)
            if (url === '/auth/refresh') {
                refreshCalls += 1
                await Promise.resolve()
                return response(config, 204, null)
            }
            const count = (attempts.get(url) ?? 0) + 1
            attempts.set(url, count)
            if (count === 1) unauthorized(config)
            return response(config, 200, { url })
        }
        const lost = vi.fn()
        const client = createHttpClient({ adapter, onAuthenticationLost: lost })

        const [first, second] = await Promise.all([
            client.get('/projects'),
            client.get('/auth/me'),
        ])

        expect([first.status, second.status]).toEqual([200, 200])
        expect(refreshCalls).toBe(1)
        expect(attempts).toEqual(
            new Map([
                ['/projects', 2],
                ['/auth/me', 2],
            ]),
        )
        expect(lost).not.toHaveBeenCalled()
    })

    test('waits for one shared session-refresh hook before releasing concurrent business retries', async () => {
        const events: string[] = []
        const attempts = new Map<string, number>()
        const adapter: AxiosAdapter = async (config) => {
            const url = String(config.url)
            if (url === '/auth/refresh') {
                events.push('http-refresh')
                return response(config, 204, null)
            }
            const count = (attempts.get(url) ?? 0) + 1
            attempts.set(url, count)
            if (count === 1) unauthorized(config)
            events.push(`retry:${url}`)
            return response(config, 200, { ok: true })
        }
        const sessionRefreshed = vi.fn(async () => {
            events.push('session-role-refresh')
        })
        const client = createHttpClient({
            adapter,
            onSessionRefreshed: sessionRefreshed,
        } as Parameters<typeof createHttpClient>[0] & {
            onSessionRefreshed: () => Promise<void>
        })

        await Promise.all([
            client.get('/projects/one'),
            client.get('/projects/two'),
        ])

        expect(sessionRefreshed).toHaveBeenCalledTimes(1)
        expect(events).toEqual([
            'http-refresh',
            'session-role-refresh',
            'retry:/projects/one',
            'retry:/projects/two',
        ])
        expect(attempts).toEqual(
            new Map([
                ['/projects/one', 2],
                ['/projects/two', 2],
            ]),
        )
    })

    test('does not recursively refresh when the post-refresh /me probe is unauthorized', async () => {
        let businessCalls = 0
        let refreshCalls = 0
        let meCalls = 0
        const lost = vi.fn()
        const adapter: AxiosAdapter = async (config) => {
            if (config.url === '/auth/refresh') {
                refreshCalls += 1
                return response(config, 204, null)
            }
            if (config.url === '/auth/me') {
                meCalls += 1
                unauthorized(config)
            }
            businessCalls += 1
            if (businessCalls === 1) unauthorized(config)
            return response(config, 200, { ok: true })
        }
        const client = createHttpClient({
            adapter,
            onAuthenticationLost: lost,
            onSessionRefreshed: () => client.get('/auth/me').then(() => {}),
        } as Parameters<typeof createHttpClient>[0] & {
            onSessionRefreshed: () => Promise<void>
        })

        await expect(client.get('/projects')).rejects.toMatchObject({
            response: { status: 401 },
        })

        expect(refreshCalls).toBe(1)
        expect(meCalls).toBe(1)
        expect(businessCalls).toBe(1)
        expect(lost).toHaveBeenCalledTimes(1)
    })

    test('starts a fresh single-flight cycle after a prior refresh has settled', async () => {
        let refreshCalls = 0
        const attempts = new Map<string, number>()
        const adapter: AxiosAdapter = async (config) => {
            const url = String(config.url)
            if (url === '/auth/refresh') {
                refreshCalls += 1
                return response(config, 204, null)
            }
            const count = (attempts.get(url) ?? 0) + 1
            attempts.set(url, count)
            if (count === 1) unauthorized(config)
            return response(config, 200, { url })
        }
        const client = createHttpClient({ adapter })

        await client.get('/projects/first')
        await client.get('/projects/second')

        expect(refreshCalls).toBe(2)
        expect(attempts).toEqual(
            new Map([
                ['/projects/first', 2],
                ['/projects/second', 2],
            ]),
        )
    })

    test.each([
        { status: 200, data: null, label: 'wrong status' },
        { status: 202, data: null, label: 'accepted status' },
        { status: 204, data: { leaked: 'sentinel' }, label: 'non-empty body' },
    ])(
        'rejects a refresh $label before retrying the business request',
        async ({ status, data }) => {
            let businessCalls = 0
            let refreshCalls = 0
            const lost = vi.fn()
            const adapter: AxiosAdapter = async (config) => {
                if (config.url === '/auth/refresh') {
                    refreshCalls += 1
                    return response(config, status, data)
                }
                businessCalls += 1
                if (businessCalls === 1) unauthorized(config)
                return response(config, 200, { ok: true })
            }
            const client = createHttpClient({
                adapter,
                onAuthenticationLost: lost,
            })

            await expect(client.get('/projects')).rejects.toBeInstanceOf(
                ApiContractError,
            )

            expect(refreshCalls).toBe(1)
            expect(businessCalls).toBe(1)
            expect(lost).toHaveBeenCalledTimes(1)
        },
    )

    test('never recursively refreshes refresh itself and clears auth after one failed retry', async () => {
        let refreshCalls = 0
        const lost = vi.fn()
        const adapter: AxiosAdapter = async (config) => {
            if (config.url === '/auth/refresh') refreshCalls += 1
            unauthorized(config)
        }
        const client = createHttpClient({ adapter, onAuthenticationLost: lost })

        await expect(client.get('/projects')).rejects.toMatchObject({
            response: { status: 401 },
        })

        expect(refreshCalls).toBe(1)
        expect(lost).toHaveBeenCalledTimes(1)
    })

    test('fans a failed refresh out to all waiters without retrying business requests', async () => {
        let refreshCalls = 0
        let businessCalls = 0
        const lost = vi.fn()
        const adapter: AxiosAdapter = async (config) => {
            if (config.url === '/auth/refresh') refreshCalls += 1
            else businessCalls += 1
            unauthorized(config)
        }
        const client = createHttpClient({ adapter, onAuthenticationLost: lost })

        const results = await Promise.allSettled([
            client.get('/projects'),
            client.get('/auth/me'),
            client.get('/projects/another'),
        ])

        expect(results.every((result) => result.status === 'rejected')).toBe(
            true,
        )
        expect(refreshCalls).toBe(1)
        expect(businessCalls).toBe(3)
        expect(lost).toHaveBeenCalledTimes(1)
    })

    test('can notify a later auth loss after a new /me session has succeeded', async () => {
        let phase: 'first-loss' | 'restored' | 'second-loss' = 'first-loss'
        const lost = vi.fn()
        const adapter: AxiosAdapter = async (config) => {
            if (phase === 'restored' && config.url === '/auth/me') {
                return response(config, 200, {
                    userid: 'owner-1',
                    role: 'OWNER',
                })
            }
            unauthorized(config)
        }
        const client = createHttpClient({ adapter, onAuthenticationLost: lost })

        await expect(client.get('/projects/first')).rejects.toBeDefined()
        expect(lost).toHaveBeenCalledTimes(1)
        phase = 'restored'
        await client.get('/auth/me')
        phase = 'second-loss'
        await expect(client.get('/projects/second')).rejects.toBeDefined()

        expect(lost).toHaveBeenCalledTimes(2)
    })

    test('bounds raw JSON before parsing and accepts the exact boundary', async () => {
        const adapter: AxiosAdapter = async (config) =>
            response(config, 200, 'x'.repeat(MAX_API_RESPONSE_BYTES + 1), {
                'content-type': 'application/json',
            })
        const client = createHttpClient({ adapter })

        await expect(client.get('/projects')).rejects.toBeInstanceOf(
            ApiContractError,
        )

        const exactAdapter: AxiosAdapter = async (config) =>
            response(
                config,
                200,
                JSON.stringify({
                    pad: 'x'.repeat(MAX_API_RESPONSE_BYTES - 10),
                }),
                {
                    'content-type': 'application/json',
                },
            )
        const exact = createHttpClient({ adapter: exactAdapter })
        await expect(exact.get('/health')).resolves.toMatchObject({
            status: 200,
        })
    })

    test('maps hostile server details to a fixed safe message', () => {
        const error = new AxiosError(
            'raw transport secret',
            'ERR_BAD_RESPONSE',
            undefined,
            undefined,
            {
                config: {},
                data: {
                    error: {
                        code: 'INTERNAL',
                        message: 'postgres password=sentinel',
                    },
                },
                headers: {},
                status: 500,
                statusText: '500',
            } as AxiosResponse,
        )

        expect(ApiContractError.safeMessage(error)).toBe(
            '服务暂时不可用，请稍后重试。',
        )
        expect(ApiContractError.safeMessage(error)).not.toContain('sentinel')
        expect(ApiContractError.safeMessage(error)).not.toContain('password')
    })
})
