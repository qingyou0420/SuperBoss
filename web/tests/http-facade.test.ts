import axios, {
    AxiosError,
    AxiosHeaders,
    type AxiosAdapter,
    type AxiosRequestTransformer,
    type AxiosResponse,
    type InternalAxiosRequestConfig,
} from 'axios'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import { createHttpClient, responseStatus } from '../src/api/http'
import { projectErrorMessage } from '../src/api/projects'

const safeProject = {
    name: 'Safe project',
    is_test: true,
}

class BrowserRequest extends Request {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(
            typeof input === 'string'
                ? new URL(input, 'https://app.example').href
                : input,
            init,
        )
    }
}

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

function setCookie(value: string): void {
    document.cookie = 'XSRF-TOKEN=; Max-Age=0; Path=/'
    if (value) document.cookie = `XSRF-TOKEN=${value}; Path=/`
}

function containsCallable(value: unknown, seen = new Set<unknown>()): boolean {
    if (typeof value === 'function') return true
    if (typeof value !== 'object' || value === null || seen.has(value)) {
        return false
    }
    seen.add(value)
    return Object.values(value).some((item) => containsCallable(item, seen))
}

beforeEach(() => {
    setCookie('')
})

describe('narrow browser HTTP facade', () => {
    test('is frozen and exposes only get and post rather than the Axios execution surface', () => {
        const client = createHttpClient({
            adapter: async (config) => response(config, 200, {}),
        })

        expect(Object.isFrozen(client)).toBe(true)
        expect(Object.keys(client).sort()).toEqual(['get', 'post'])
        for (const rawSurface of [
            'defaults',
            'interceptors',
            'request',
            'put',
            'patch',
            'delete',
            'getUri',
        ]) {
            expect(client).not.toHaveProperty(rawSurface)
        }
    })

    test('returns only a frozen status/data view with no internal callable or raw config', async () => {
        const client = createHttpClient({
            adapter: async (config) => response(config, 200, { ok: true }),
        })

        const result = await client.get('/projects')

        expect(Object.keys(result).sort()).toEqual(['data', 'status'])
        expect(Object.isFrozen(result)).toBe(true)
        expect(result).toEqual({ status: 200, data: { ok: true } })
        expect(containsCallable(result)).toBe(false)
        expect(result).not.toHaveProperty('config')
        expect(result).not.toHaveProperty('request')
        expect(result).not.toHaveProperty('headers')
    })

    test('maps an Axios failure to a frozen safe error while preserving bounded project conflict data', async () => {
        const conflict = {
            error: {
                code: 'PROJECT_NAME_CONFLICT',
                message: 'A project with this name already exists',
                request_id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f810',
            },
        }
        const adapter: AxiosAdapter = async (config) => {
            const rejected = response(config, 409, conflict)
            throw new AxiosError(
                'transport client_secret=sentinel',
                'ERR_BAD_REQUEST',
                config,
                { execute: vi.fn() },
                rejected,
            )
        }
        const client = createHttpClient({ adapter })

        let caught: unknown
        try {
            await client.post('/projects', safeProject)
        } catch (error) {
            caught = error
        }

        expect(caught).toBeInstanceOf(Error)
        expect(Object.isFrozen(caught)).toBe(true)
        expect(responseStatus(caught)).toBe(409)
        expect(projectErrorMessage(caught)).toBe('项目名称已存在。')
        expect(caught).not.toHaveProperty('config')
        expect(caught).not.toHaveProperty('request')
        expect(caught).not.toHaveProperty('response')
        expect(caught).not.toHaveProperty('toJSON')
        expect(containsCallable(caught)).toBe(false)
        expect(String(caught)).not.toMatch(/sentinel|client_secret|transport/i)
    })

    test('cannot be changed through the public Axios default transform array or callable properties', async () => {
        setCookie(encodeURIComponent('reviewed csrf'))
        const originalDefaults = axios.defaults.transformRequest
        const original = (
            Array.isArray(originalDefaults)
                ? originalDefaults[0]
                : originalDefaults
        ) as AxiosRequestTransformer
        const callDescriptor = Object.getOwnPropertyDescriptor(original, 'call')
        const applyDescriptor = Object.getOwnPropertyDescriptor(
            original,
            'apply',
        )
        const hostileCall = vi.fn((_context, _data, headers: AxiosHeaders) => {
            headers.set('Authorization', 'Bearer mutated-function-call')
            headers.set('X-CSRF-Token', 'forged-by-mutated-call')
            return JSON.stringify({ tampered: true })
        })
        const hostileApply = vi.fn()
        const hostileArrayTransform = vi.fn()
        let seen: InternalAxiosRequestConfig | undefined
        const client = createHttpClient({
            adapter: async (config) => {
                seen = config
                return response(config, 201, {})
            },
        })

        try {
            axios.defaults.transformRequest = [hostileArrayTransform]
            Object.defineProperty(original, 'call', {
                configurable: true,
                value: hostileCall,
            })
            Object.defineProperty(original, 'apply', {
                configurable: true,
                value: hostileApply,
            })

            await client.post('/projects', safeProject)

            expect(hostileArrayTransform).not.toHaveBeenCalled()
            expect(hostileCall).not.toHaveBeenCalled()
            expect(hostileApply).not.toHaveBeenCalled()
            const headers = AxiosHeaders.from(seen?.headers)
            expect(headers.has('Authorization')).toBe(false)
            expect(headers.get('X-CSRF-Token')).toBe('reviewed csrf')
            expect(headers.get('Content-Type')).toMatch(/^application\/json/)
            expect(JSON.parse(String(seen?.data))).toEqual(safeProject)
        } finally {
            axios.defaults.transformRequest = originalDefaults
            if (callDescriptor) {
                Object.defineProperty(original, 'call', callDescriptor)
            } else {
                delete (
                    original as AxiosRequestTransformer & { call?: unknown }
                ).call
            }
            if (applyDescriptor) {
                Object.defineProperty(original, 'apply', applyDescriptor)
            } else {
                delete (
                    original as AxiosRequestTransformer & { apply?: unknown }
                ).apply
            }
        }
    })

    test('a response cannot expose a mutable transform wrapper that changes a later request', async () => {
        setCookie(encodeURIComponent('reviewed csrf'))
        const seen: InternalAxiosRequestConfig[] = []
        const client = createHttpClient({
            adapter: async (config) => {
                seen.push(config)
                return response(config, 201, {})
            },
        })
        const first = await client.post('/projects', safeProject)
        const exposed = (
            first as unknown as {
                config?: { transformRequest?: AxiosRequestTransformer[] }
            }
        ).config?.transformRequest?.[0]
        const callDescriptor = exposed
            ? Object.getOwnPropertyDescriptor(exposed, 'call')
            : undefined
        const hostileCall = vi.fn((_context, _data, headers: AxiosHeaders) => {
            headers.set('Authorization', 'Bearer response-wrapper')
            return JSON.stringify({ tampered: true })
        })

        try {
            if (exposed) {
                Object.defineProperty(exposed, 'call', {
                    configurable: true,
                    value: hostileCall,
                })
            }
            await client.post('/projects', safeProject)

            expect(hostileCall).not.toHaveBeenCalled()
            const second = seen[1]
            expect(
                AxiosHeaders.from(second?.headers).has('Authorization'),
            ).toBe(false)
            expect(JSON.parse(String(second?.data))).toEqual(safeProject)
        } finally {
            if (exposed) {
                if (callDescriptor) {
                    Object.defineProperty(exposed, 'call', callDescriptor)
                } else {
                    delete (
                        exposed as AxiosRequestTransformer & { call?: unknown }
                    ).call
                }
            }
        }
    })

    test('ignores a hostile per-request custom adapter and keeps the construction adapter', async () => {
        const trusted = vi.fn(async (config: InternalAxiosRequestConfig) =>
            response(config, 201, {}),
        )
        const hostile = vi.fn(async (config: InternalAxiosRequestConfig) =>
            response(config, 201, { tampered: true }),
        )
        const client = createHttpClient({ adapter: trusted })

        await (client.post as (...args: unknown[]) => Promise<unknown>)(
            '/projects',
            safeProject,
            { adapter: hostile },
        )

        expect(trusted).toHaveBeenCalledTimes(1)
        expect(hostile).not.toHaveBeenCalled()
    })

    test('rebuilds GET options from params and removes every caller execution hook', async () => {
        let seen: InternalAxiosRequestConfig | undefined
        const trusted: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 200, {})
        }
        const hostileFetch = vi.fn()
        const hostileRequest = vi.fn()
        const hostileResponse = vi.fn()
        const hostileTransport = { request: vi.fn() }
        const hostileBeforeRedirect = vi.fn()
        const hostileValidateStatus = vi.fn()
        const hostileRequestTransform = vi.fn()
        const hostileResponseTransform = vi.fn()
        const client = createHttpClient({ adapter: trusted })

        await (client.get as (...args: unknown[]) => Promise<unknown>)(
            '/projects',
            {
                params: { view: 'all' },
                auth: { username: 'attacker', password: 'sentinel' },
                beforeRedirect: hostileBeforeRedirect,
                env: {
                    fetch: hostileFetch,
                    Request: hostileRequest,
                    Response: hostileResponse,
                },
                fetchOptions: {
                    credentials: 'omit',
                    headers: { Authorization: 'Bearer late' },
                    method: 'DELETE',
                },
                headers: {
                    Authorization: 'Bearer caller',
                    'X-CSRF-Token': 'forged',
                },
                transport: hostileTransport,
                transformRequest: hostileRequestTransform,
                transformResponse: hostileResponseTransform,
                validateStatus: hostileValidateStatus,
            },
        )

        expect(seen?.params).toEqual({ view: 'all' })
        expect(seen?.auth).toBeUndefined()
        expect(seen?.fetchOptions).toBeUndefined()
        expect(seen?.transport).toBeUndefined()
        expect(seen?.beforeRedirect).toBeUndefined()
        expect(seen?.validateStatus).not.toBe(hostileValidateStatus)
        expect(seen?.env?.fetch).not.toBe(hostileFetch)
        expect(seen?.env?.Request).not.toBe(hostileRequest)
        expect(seen?.env?.Response).not.toBe(hostileResponse)
        expect(seen?.transformRequest).not.toContain(hostileRequestTransform)
        expect(seen?.transformResponse).not.toContain(hostileResponseTransform)
        for (const hostile of [
            hostileFetch,
            hostileRequest,
            hostileResponse,
            hostileBeforeRedirect,
            hostileValidateStatus,
            hostileRequestTransform,
            hostileResponseTransform,
            hostileTransport.request,
        ]) {
            expect(hostile).not.toHaveBeenCalled()
        }
    })

    test('ignores a per-request built-in fetch adapter and its late fetchOptions merge', async () => {
        const trusted = vi.fn(async (config: InternalAxiosRequestConfig) =>
            response(config, 201, {}),
        )
        const hostileFetch = vi.fn(async () =>
            Promise.resolve(
                new Response('{}', {
                    status: 200,
                    headers: { 'content-type': 'application/json' },
                }),
            ),
        )
        const client = createHttpClient({ adapter: trusted })

        await (client.post as (...args: unknown[]) => Promise<unknown>)(
            '/projects',
            safeProject,
            {
                adapter: 'fetch',
                env: { fetch: hostileFetch, Request: BrowserRequest, Response },
                fetchOptions: {
                    body: JSON.stringify({ tampered: true }),
                    credentials: 'omit',
                    headers: { Authorization: 'Bearer fetch-options-late' },
                    method: 'PUT',
                },
            },
        )

        expect(trusted).toHaveBeenCalledTimes(1)
        expect(hostileFetch).not.toHaveBeenCalled()
    })

    test('pins a construction-time built-in fetch adapter to the final safe WHATWG Request', async () => {
        setCookie(encodeURIComponent('reviewed csrf'))
        const requests: Request[] = []
        const observedFetch = vi.fn(async (input: RequestInfo | URL) => {
            const request = input as Request
            requests.push(request)
            return new Response('{}', {
                status: 201,
                headers: { 'content-type': 'application/json' },
            })
        })
        const fetchAdapter = (
            axios.getAdapter as unknown as (
                adapters: string,
                config: unknown,
            ) => AxiosAdapter
        )('fetch', {
            env: {
                fetch: observedFetch,
                Request: BrowserRequest,
                Response,
            },
        })
        const client = createHttpClient({ adapter: fetchAdapter })

        await client.post('/projects', safeProject)

        expect(observedFetch).toHaveBeenCalledTimes(1)
        expect(requests).toHaveLength(1)
        const request = requests[0]
        expect(new URL(request.url).pathname).toBe('/api/v1/projects')
        expect(request.method).toBe('POST')
        expect(request.credentials).toBe('include')
        expect(request.headers.has('Authorization')).toBe(false)
        expect(request.headers.get('X-CSRF-Token')).toBe('reviewed csrf')
        expect(request.headers.get('Content-Type')).toMatch(
            /^application\/json/,
        )
        await expect(request.clone().json()).resolves.toEqual(safeProject)
    })
})
