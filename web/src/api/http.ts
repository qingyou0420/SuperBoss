import axios, {
    AxiosHeaders,
    type AxiosAdapter,
    type InternalAxiosRequestConfig,
} from 'axios'

const REFRESH_PATH = '/auth/refresh'

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
    patch<T = unknown>(url: string, data?: unknown): Promise<BrowserHttpResponse<T>>
    put<T = unknown>(url: string, data?: unknown): Promise<BrowserHttpResponse<T>>
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
        this.data = data
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
        if (error instanceof HttpClientError && error.status) {
            return `请求失败（${error.status}），请稍后重试。`
        }
        return '服务暂时不可用，请稍后重试。'
    }
}

function readCsrfCookie(): string | undefined {
    if (typeof document === 'undefined') return undefined
    for (const item of document.cookie.split(';')) {
        const separator = item.indexOf('=')
        if (separator < 0 || item.slice(0, separator).trim() !== 'XSRF-TOKEN') continue
        try {
            return decodeURIComponent(item.slice(separator + 1)) || undefined
        } catch {
            return undefined
        }
    }
    return undefined
}

export function createHttpClient(options: HttpClientOptions = {}): BrowserHttpClient {
    let refreshPromise: Promise<void> | null = null
    let authenticationLostNotified = false

    const notifyAuthenticationLost = async (): Promise<void> => {
        if (authenticationLostNotified) return
        authenticationLostNotified = true
        await options.onAuthenticationLost?.()
    }

    const client = axios.create({
        adapter: options.adapter,
        baseURL: '/api/v1',
        timeout: 30_000,
        validateStatus: (status) => status >= 200 && status < 300,
        withCredentials: true,
        transitional: { clarifyTimeoutError: true },
        transformResponse: [(data) => data],
    })

    client.interceptors.request.use((config) => {
        const headers = AxiosHeaders.from(config.headers)
        if (config.method && config.method.toUpperCase() !== 'GET') {
            const csrf = readCsrfCookie()
            if (csrf) headers.set('X-CSRF-Token', csrf)
        }
        config.headers = headers
        return config
    })

    const refresh = (): Promise<void> => {
        if (refreshPromise) return refreshPromise
        const pending = (async () => {
            const response = await client.post(REFRESH_PATH)
            if (response.status !== 204) throw new ApiContractError()
            await options.onSessionRefreshed?.()
            authenticationLostNotified = false
        })()
        refreshPromise = pending.finally(() => {
            if (refreshPromise === pending) refreshPromise = null
        })
        return refreshPromise
    }

    client.interceptors.response.use(
        (response) => response,
        async (error: unknown) => {
            if (!axios.isAxiosError(error) || error.response?.status !== 401 || !error.config) {
                throw error
            }
            const config = error.config as RetriableConfig
            if (config.url === REFRESH_PATH) throw error
            const refreshable =
                AxiosHeaders.from(error.response.headers as AxiosHeaders).get(
                    'X-SuperBoss-Refreshable',
                ) === '1'
            if (!refreshable || config._superbossAuthRetried) {
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

    const parseBody = (data: unknown): unknown => {
        if (typeof data !== 'string' || data === '') return data === '' ? null : data
        try {
            return JSON.parse(data) as unknown
        } catch {
            return data
        }
    }

    const execute = async <T>(
        method: 'delete' | 'get' | 'patch' | 'post' | 'put',
        url: string,
        data?: unknown,
        params?: Readonly<Record<string, string>>,
        idempotencyKey?: string,
    ): Promise<BrowserHttpResponse<T>> => {
        if (!url.startsWith('/') || url.startsWith('//')) {
            throw new ApiContractError('Cross-origin API target rejected')
        }
        try {
            const headers = new AxiosHeaders()
            if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey)
            const response = await client.request({
                data,
                headers,
                method,
                params,
                url,
            })
            return { status: response.status, data: parseBody(response.data) as T }
        } catch (error) {
            if (error instanceof ApiContractError || error instanceof HttpClientError) throw error
            if (axios.isAxiosError(error)) {
                throw new HttpClientError(error.response?.status, parseBody(error.response?.data))
            }
            throw new HttpClientError()
        }
    }

    return {
        delete: <T = unknown>(url: string) => execute<T>('delete', url),
        get: <T = unknown>(url: string, getOptions?: BrowserGetOptions) =>
            execute<T>('get', url, undefined, getOptions?.params),
        post: <T = unknown>(url: string, data?: unknown, postOptions?: BrowserPostOptions) =>
            execute<T>('post', url, data, undefined, postOptions?.idempotencyKey),
        patch: <T = unknown>(url: string, data?: unknown) => execute<T>('patch', url, data),
        put: <T = unknown>(url: string, data?: unknown) => execute<T>('put', url, data),
    }
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
