import axios, {
    AxiosHeaders,
    type AxiosAdapter,
    type InternalAxiosRequestConfig,
} from 'axios'

export const MAX_API_RESPONSE_BYTES = 256 * 1024

const MAX_JSON_DEPTH = 64
const MAX_JSON_NODES = 20_000
const REFRESH_PATH = '/auth/refresh'
const stringifyJson = JSON.stringify.bind(JSON)

type RetriableConfig = InternalAxiosRequestConfig & {
    _superbossAuthRetried?: boolean
}

export interface HttpClientOptions {
    adapter?: AxiosAdapter
    onAuthenticationLost?: () => void | Promise<void>
    onSessionRefreshed?: () => void | Promise<void>
}

export interface BrowserHttpResponse<T = unknown> {
    readonly status: number
    readonly data: T
}

export interface BrowserGetOptions {
    readonly params?: Readonly<Record<string, string>>
}

export interface BrowserPostOptions {
    readonly idempotencyKey?: string
}

export interface BrowserHttpClient {
    delete<T = unknown>(url: string): Promise<BrowserHttpResponse<T>>
    get<T = unknown>(
        url: string,
        options?: BrowserGetOptions,
    ): Promise<BrowserHttpResponse<T>>
    post<T = unknown>(
        url: string,
        data?: unknown,
        options?: BrowserPostOptions,
    ): Promise<BrowserHttpResponse<T>>
}

export class HttpClientError extends Error {
    readonly status: number | undefined
    readonly data: unknown

    constructor(status?: number, data?: unknown) {
        super('HTTP request failed')
        this.name = 'HttpClientError'
        this.status =
            Number.isInteger(status) && status! >= 100 && status! <= 599
                ? status
                : undefined
        this.data = safeErrorData(data)
        Object.freeze(this)
    }
}

export class ApiContractError extends Error {
    constructor(message = 'Invalid API response') {
        super(message)
        this.name = 'ApiContractError'
    }

    static safeMessage(error: unknown): string {
        if (responseStatus(error) === 401) {
            return '登录状态已失效，请重新登录。'
        }
        return '服务暂时不可用，请稍后重试。'
    }
}

function readCsrfCookie(): string | undefined {
    if (typeof document === 'undefined') return undefined
    for (const item of document.cookie.split(';')) {
        const separator = item.indexOf('=')
        if (separator < 0 || item.slice(0, separator).trim() !== 'XSRF-TOKEN')
            continue
        try {
            const value = decodeURIComponent(item.slice(separator + 1))
            return value || undefined
        } catch {
            return undefined
        }
    }
    return undefined
}

function utf8Size(value: string): number {
    return new TextEncoder().encode(value).byteLength
}

function parseBoundedResponse(data: unknown): unknown {
    if (typeof data !== 'string') return data
    if (utf8Size(data) > MAX_API_RESPONSE_BYTES) {
        throw new ApiContractError('API response is too large')
    }
    if (!data) return null
    try {
        return JSON.parse(data) as unknown
    } catch {
        throw new ApiContractError()
    }
}

function isSafeRelativeTarget(url: string | undefined): boolean {
    return Boolean(
        url?.startsWith('/') && !url.startsWith('//') && !url.includes('\\'),
    )
}

function isRefresh(config: InternalAxiosRequestConfig | undefined): boolean {
    return config?.url === REFRESH_PATH
}

function isPlainObject(value: object): boolean {
    const prototype = Object.getPrototypeOf(value)
    return prototype === Object.prototype || prototype === null
}

interface CopyState {
    nodes: number
    readonly active: WeakSet<object>
}

function safeJsonCopy(
    value: unknown,
    state: CopyState,
    depth: number,
): unknown {
    state.nodes += 1
    if (state.nodes > MAX_JSON_NODES || depth > MAX_JSON_DEPTH) {
        throw new ApiContractError('JSON value is too complex')
    }
    if (
        value === null ||
        typeof value === 'boolean' ||
        typeof value === 'string'
    ) {
        return value
    }
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) throw new ApiContractError()
        return value
    }
    if (typeof value !== 'object') throw new ApiContractError()
    if (state.active.has(value)) throw new ApiContractError()
    state.active.add(value)
    try {
        if (Array.isArray(value)) {
            const descriptors = Object.getOwnPropertyDescriptors(value)
            const keys = Reflect.ownKeys(descriptors)
            if (
                keys.some(
                    (key) =>
                        typeof key !== 'string' ||
                        (key !== 'length' && !/^(0|[1-9][0-9]*)$/.test(key)),
                )
            ) {
                throw new ApiContractError()
            }
            const result: unknown[] = []
            Object.defineProperty(result, 'toJSON', {
                configurable: false,
                enumerable: false,
                value: undefined,
                writable: false,
            })
            for (let index = 0; index < value.length; index += 1) {
                const descriptor = descriptors[String(index)]
                if (!descriptor?.enumerable || !('value' in descriptor)) {
                    throw new ApiContractError()
                }
                result.push(safeJsonCopy(descriptor.value, state, depth + 1))
            }
            return Object.freeze(result)
        }
        if (!isPlainObject(value)) throw new ApiContractError()
        const result = Object.create(null) as Record<string, unknown>
        const descriptors = Object.getOwnPropertyDescriptors(value)
        for (const key of Reflect.ownKeys(descriptors)) {
            if (typeof key !== 'string') throw new ApiContractError()
            const descriptor = descriptors[key]
            if (!descriptor.enumerable || !('value' in descriptor)) {
                throw new ApiContractError()
            }
            Object.defineProperty(result, key, {
                configurable: false,
                enumerable: true,
                value: safeJsonCopy(descriptor.value, state, depth + 1),
                writable: false,
            })
        }
        return Object.freeze(result)
    } catch (error) {
        if (error instanceof ApiContractError) throw error
        throw new ApiContractError()
    } finally {
        state.active.delete(value)
    }
}

function copySafeJson(value: unknown): unknown {
    return safeJsonCopy(value, { nodes: 0, active: new WeakSet() }, 0)
}

interface EncodedRequestBody {
    readonly data: string | undefined
    readonly contentType: string | undefined
}

const encodeJsonRequest = Object.freeze(
    (value: unknown): EncodedRequestBody => {
        if (value === undefined || value === null) {
            return Object.freeze({ data: undefined, contentType: undefined })
        }
        const safe = copySafeJson(value)
        let data: string
        try {
            data = stringifyJson(safe)
        } catch {
            throw new ApiContractError()
        }
        if (utf8Size(data) > MAX_API_RESPONSE_BYTES) {
            throw new ApiContractError('API request is too large')
        }
        return Object.freeze({
            data,
            contentType: 'application/json',
        })
    },
)

function copySafeParams(options: unknown): Readonly<Record<string, string>> {
    if (options === undefined) return Object.freeze({})
    if (
        typeof options !== 'object' ||
        options === null ||
        !isPlainObject(options)
    ) {
        throw new ApiContractError('Invalid query parameters')
    }
    const optionDescriptor = Object.getOwnPropertyDescriptor(options, 'params')
    if (!optionDescriptor || !('value' in optionDescriptor)) {
        return Object.freeze({})
    }
    const params = optionDescriptor.value
    if (
        typeof params !== 'object' ||
        params === null ||
        !isPlainObject(params)
    ) {
        throw new ApiContractError('Invalid query parameters')
    }
    const result: Record<string, string> = {}
    for (const key of Reflect.ownKeys(params)) {
        if (typeof key !== 'string') throw new ApiContractError()
        const descriptor = Object.getOwnPropertyDescriptor(params, key)
        if (
            !descriptor?.enumerable ||
            !('value' in descriptor) ||
            typeof descriptor.value !== 'string'
        ) {
            throw new ApiContractError('Invalid query parameters')
        }
        Object.defineProperty(result, key, {
            configurable: false,
            enumerable: true,
            value: descriptor.value,
            writable: false,
        })
    }
    return Object.freeze(result)
}

function copyIdempotencyKey(options: unknown): string | undefined {
    if (options === undefined) return undefined
    if (typeof options !== 'object' || options === null) {
        throw new ApiContractError('Invalid idempotency key')
    }
    const descriptor = Object.getOwnPropertyDescriptor(
        options,
        'idempotencyKey',
    )
    if (!descriptor || !('value' in descriptor)) return undefined
    const value = descriptor.value
    if (
        typeof value !== 'string' ||
        value.length < 1 ||
        value.length > 255 ||
        [...value].some((character) => {
            const code = character.charCodeAt(0)
            return code < 33 || code > 126
        })
    ) {
        throw new ApiContractError('Invalid idempotency key')
    }
    return value
}

function safeResponseData(data: unknown): unknown {
    if (data === undefined) return undefined
    const safe = copySafeJson(data)
    let serialized: string
    try {
        serialized = stringifyJson(safe)
    } catch {
        throw new ApiContractError()
    }
    if (utf8Size(serialized) > MAX_API_RESPONSE_BYTES) {
        throw new ApiContractError('API response is too large')
    }
    return safe
}

function safeErrorData(data: unknown): unknown {
    try {
        return safeResponseData(data)
    } catch {
        return undefined
    }
}

function approvedAdapter(adapter: AxiosAdapter | undefined): AxiosAdapter {
    const selected = adapter ?? axios.getAdapter(axios.defaults.adapter)
    return Object.freeze((config: InternalAxiosRequestConfig) =>
        Reflect.apply(selected, undefined, [config]),
    )
}

export function createHttpClient(
    options: HttpClientOptions = {},
): BrowserHttpClient {
    const transport = approvedAdapter(options.adapter)
    const client = axios.create({
        adapter: transport,
        baseURL: '/api/v1',
        maxBodyLength: MAX_API_RESPONSE_BYTES,
        maxContentLength: MAX_API_RESPONSE_BYTES,
        responseType: 'text',
        transformRequest: [],
        transformResponse: [parseBoundedResponse],
        withCredentials: true,
        xsrfCookieName: '',
        xsrfHeaderName: '',
    })
    let refreshPromise: Promise<void> | null = null
    let authenticationLostNotified = false
    let sessionRefreshHookRunning = false

    const notifyAuthenticationLost = async (): Promise<void> => {
        if (authenticationLostNotified) return
        authenticationLostNotified = true
        await options.onAuthenticationLost?.()
    }

    client.interceptors.request.use((config) => {
        if (!isSafeRelativeTarget(config.url)) {
            throw new ApiContractError('Cross-origin API target rejected')
        }
        const headers = AxiosHeaders.from(config.headers)
        config.adapter = transport
        config.baseURL = '/api/v1'
        config.withCredentials = true
        config.xsrfCookieName = ''
        config.xsrfHeaderName = ''
        delete config.auth
        config.transformRequest = []
        config.responseType = 'text'
        config.transformResponse = [parseBoundedResponse]
        config.maxBodyLength = MAX_API_RESPONSE_BYTES
        config.maxContentLength = MAX_API_RESPONSE_BYTES
        delete config.beforeRedirect
        delete config.env
        delete config.fetchOptions
        delete config.transport
        headers.delete('Authorization')
        headers.delete('X-CSRF-Token')
        if (
            ['delete', 'patch', 'post', 'put'].includes(
                config.method?.toLowerCase() ?? '',
            )
        ) {
            const csrf = readCsrfCookie()
            if (csrf) headers.set('X-CSRF-Token', csrf)
        }
        config.headers = headers
        return config
    })

    const refresh = (): Promise<void> => {
        if (refreshPromise) return refreshPromise
        const pending = (async (): Promise<void> => {
            const response = await client.post(REFRESH_PATH)
            if (response.status !== 204 || response.data !== null) {
                throw new ApiContractError()
            }
            sessionRefreshHookRunning = true
            try {
                await options.onSessionRefreshed?.()
                authenticationLostNotified = false
            } finally {
                sessionRefreshHookRunning = false
            }
        })()
        refreshPromise = pending
        const clear = (): void => {
            if (refreshPromise === pending) refreshPromise = null
        }
        void pending.then(clear, clear)
        return pending
    }

    client.interceptors.response.use(
        (response) => {
            if (response.config.url === '/auth/me') {
                authenticationLostNotified = false
            }
            return response
        },
        async (error: unknown) => {
            if (
                !axios.isAxiosError(error) ||
                error.response?.status !== 401 ||
                !error.config
            ) {
                throw error
            }
            const config = error.config as RetriableConfig
            if (isRefresh(config)) throw error
            if (sessionRefreshHookRunning && config.url === '/auth/me') {
                await notifyAuthenticationLost()
                throw error
            }
            if (config._superbossAuthRetried) {
                await notifyAuthenticationLost()
                throw error
            }
            config._superbossAuthRetried = true
            try {
                await refresh()
                return await client.request(config)
            } catch (refreshError) {
                await notifyAuthenticationLost()
                throw refreshError
            }
        },
    )

    const execute = async <T>(
        method: 'delete' | 'get' | 'post',
        url: string,
        data?: unknown,
        params?: Readonly<Record<string, string>>,
        idempotencyKey?: string,
    ): Promise<BrowserHttpResponse<T>> => {
        if (!isSafeRelativeTarget(url)) {
            throw new ApiContractError('Cross-origin API target rejected')
        }
        try {
            const encoded =
                method === 'post' ? encodeJsonRequest(data) : undefined
            const headers = new AxiosHeaders()
            if (encoded?.contentType)
                headers.set('Content-Type', encoded.contentType)
            if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey)
            const response = await client.request({
                data: encoded?.data,
                headers,
                method,
                params,
                url,
            })
            return Object.freeze({
                status: response.status,
                data: safeResponseData(response.data) as T,
            })
        } catch (error) {
            if (
                error instanceof ApiContractError ||
                error instanceof HttpClientError
            ) {
                throw error
            }
            if (axios.isAxiosError(error)) {
                throw new HttpClientError(
                    error.response?.status,
                    error.response?.data,
                )
            }
            throw new HttpClientError()
        }
    }

    return Object.freeze({
        delete: <T = unknown>(url: string) => execute<T>('delete', url),
        get: <T = unknown>(url: string, getOptions?: BrowserGetOptions) =>
            execute<T>('get', url, undefined, copySafeParams(getOptions)),
        post: async <T = unknown>(
            url: string,
            data?: unknown,
            postOptions?: BrowserPostOptions,
        ) =>
            await execute<T>(
                'post',
                url,
                data,
                undefined,
                copyIdempotencyKey(postOptions),
            ),
    })
}

let authenticationLostHandler: (() => void | Promise<void>) | undefined
let sessionRefreshedHandler: (() => void | Promise<void>) | undefined

export function setAuthenticationLostHandler(
    handler: (() => void | Promise<void>) | undefined,
): void {
    authenticationLostHandler = handler
}

export function setSessionRefreshedHandler(
    handler: (() => void | Promise<void>) | undefined,
): void {
    sessionRefreshedHandler = handler
}

export const apiClient = createHttpClient({
    onAuthenticationLost: () => authenticationLostHandler?.(),
    onSessionRefreshed: () => sessionRefreshedHandler?.(),
})

export function responseStatus(error: unknown): number | undefined {
    return error instanceof HttpClientError ? error.status : undefined
}
