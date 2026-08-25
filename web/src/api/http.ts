import axios, {
    AxiosHeaders,
    type AxiosAdapter,
    type InternalAxiosRequestConfig,
} from 'axios'

const REFRESH_PATH = '/auth/refresh'

export type BrowserHttpResponse<T = unknown> = {
    readonly status: number
    readonly data: T
}

export type BrowserHttpClient = {
    delete<T = unknown>(url: string): Promise<BrowserHttpResponse<T>>
    get<T = unknown>(
        url: string,
        options?: { readonly params?: Readonly<Record<string, string>> },
    ): Promise<BrowserHttpResponse<T>>
    post<T = unknown>(
        url: string,
        data?: unknown,
        options?: { readonly idempotencyKey?: string },
    ): Promise<BrowserHttpResponse<T>>
    patch<T = unknown>(
        url: string,
        data?: unknown,
    ): Promise<BrowserHttpResponse<T>>
    put<T = unknown>(
        url: string,
        data?: unknown,
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
        this.data = data
    }
}

export class ApiContractError extends Error {
    constructor(message = 'Invalid API response') {
        super(message)
        this.name = 'ApiContractError'
    }
    static safeMessage(error: unknown): string {
        return formatRequestError(
            '请求失败',
            error,
            '服务暂时不可用，请稍后重试。',
            {
                unauthorized: '登录状态已失效，请重新登录。',
            },
        )
    }
}

export function responseStatus(error: unknown): number | undefined {
    return error instanceof HttpClientError ? error.status : undefined
}

export function requestIdFromError(error: unknown): string | undefined {
    if (
        !(error instanceof HttpClientError) ||
        typeof error.data !== 'object' ||
        !error.data
    ) {
        return undefined
    }
    const detail = (error.data as { error?: { request_id?: unknown } }).error
    const id = detail?.request_id
    return typeof id === 'string' && id.length >= 1 && id.length <= 128
        ? id
        : undefined
}

export function formatRequestError(
    prefix: string,
    error: unknown,
    fallback: string,
    extra?: { unauthorized?: string },
): string {
    if (responseStatus(error) === 401 && extra?.unauthorized)
        return extra.unauthorized
    const status = responseStatus(error)
    if (!status) return fallback
    const id = requestIdFromError(error)
    return id ? `${prefix}（${status}，${id}）` : `${prefix}（${status}）`
}

function csrfToken(): string | undefined {
    if (typeof document === 'undefined') return undefined
    const match = document.cookie
        .split(';')
        .map((item) => item.trim())
        .find((item) => item.startsWith('XSRF-TOKEN='))
    if (!match) return undefined
    try {
        return (
            decodeURIComponent(match.slice('XSRF-TOKEN='.length)) || undefined
        )
    } catch {
        return undefined
    }
}

function parseBody(data: unknown): unknown {
    if (typeof data !== 'string' || data === '')
        return data === '' ? null : data
    try {
        return JSON.parse(data) as unknown
    } catch {
        return data
    }
}

export function createHttpClient(
    options: {
        adapter?: AxiosAdapter
        onAuthenticationLost?: () => void | Promise<void>
        onSessionRefreshed?: () => void | Promise<void>
    } = {},
): BrowserHttpClient {
    let refreshPromise: Promise<void> | null = null
    let lostNotified = false
    const notifyLost = async () => {
        if (lostNotified) return
        lostNotified = true
        await options.onAuthenticationLost?.()
    }
    const client = axios.create({
        adapter: options.adapter,
        baseURL: '/api/v1',
        timeout: 30_000,
        validateStatus: (status) => status >= 200 && status < 300,
        withCredentials: true,
        transformResponse: [(data) => data],
    })
    client.interceptors.request.use((config) => {
        const headers = AxiosHeaders.from(config.headers)
        if (config.method && config.method.toUpperCase() !== 'GET') {
            const csrf = csrfToken()
            if (csrf) headers.set('X-CSRF-Token', csrf)
        }
        config.headers = headers
        return config
    })
    const refresh = () => {
        if (refreshPromise) return refreshPromise
        const pending = (async () => {
            const response = await client.post(REFRESH_PATH)
            if (response.status !== 204) throw new ApiContractError()
            await options.onSessionRefreshed?.()
            lostNotified = false
        })()
        refreshPromise = pending.finally(() => {
            if (refreshPromise === pending) refreshPromise = null
        })
        return refreshPromise
    }
    client.interceptors.response.use(
        (response) => response,
        async (error: unknown) => {
            if (
                !axios.isAxiosError(error) ||
                error.response?.status !== 401 ||
                !error.config
            ) {
                throw error
            }
            const config = error.config as InternalAxiosRequestConfig & {
                _retried?: boolean
            }
            if (config.url === REFRESH_PATH) throw error
            const refreshable =
                AxiosHeaders.from(error.response.headers as AxiosHeaders).get(
                    'X-SuperBoss-Refreshable',
                ) === '1'
            if (!refreshable || config._retried) {
                await notifyLost()
                throw error
            }
            config._retried = true
            try {
                await refresh()
                return await client.request(config)
            } catch (refreshError) {
                await notifyLost()
                throw refreshError
            }
        },
    )
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
            return {
                status: response.status,
                data: parseBody(response.data) as T,
            }
        } catch (error) {
            if (
                error instanceof ApiContractError ||
                error instanceof HttpClientError
            )
                throw error
            if (axios.isAxiosError(error)) {
                throw new HttpClientError(
                    error.response?.status,
                    parseBody(error.response?.data),
                )
            }
            throw new HttpClientError()
        }
    }
    return {
        delete: (url) => execute('delete', url),
        get: (url, options) => execute('get', url, undefined, options?.params),
        post: (url, data, options) =>
            execute('post', url, data, undefined, options?.idempotencyKey),
        patch: (url, data) => execute('patch', url, data),
        put: (url, data) => execute('put', url, data),
    }
}

let lostHandler: (() => void | Promise<void>) | undefined
let refreshedHandler: (() => void | Promise<void>) | undefined
export const setAuthenticationLostHandler = (handler: typeof lostHandler) => {
    lostHandler = handler
}
export const setSessionRefreshedHandler = (
    handler: typeof refreshedHandler,
) => {
    refreshedHandler = handler
}
export const apiClient = createHttpClient({
    onAuthenticationLost: () => lostHandler?.(),
    onSessionRefreshed: () => refreshedHandler?.(),
})
