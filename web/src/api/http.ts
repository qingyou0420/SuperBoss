import axios, {
    AxiosHeaders,
    type AxiosAdapter,
    type AxiosError,
    type AxiosInstance,
    type InternalAxiosRequestConfig,
} from 'axios'

export const MAX_API_RESPONSE_BYTES = 256 * 1024

const UNSAFE_METHODS = new Set(['post', 'put', 'patch', 'delete'])
const REFRESH_PATH = '/auth/refresh'

type RetriableConfig = InternalAxiosRequestConfig & {
    _superbossAuthRetried?: boolean
}

export interface HttpClientOptions {
    adapter?: AxiosAdapter
    onAuthenticationLost?: () => void | Promise<void>
}

export class ApiContractError extends Error {
    constructor(message = 'Invalid API response') {
        super(message)
        this.name = 'ApiContractError'
    }

    static safeMessage(error: unknown): string {
        if (axios.isAxiosError(error) && error.response?.status === 401) {
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

function parseBoundedResponse(data: unknown): unknown {
    if (typeof data !== 'string') return data
    if (new TextEncoder().encode(data).byteLength > MAX_API_RESPONSE_BYTES) {
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

export function createHttpClient(
    options: HttpClientOptions = {},
): AxiosInstance {
    const client = axios.create({
        adapter: options.adapter,
        baseURL: '/api/v1',
        maxBodyLength: MAX_API_RESPONSE_BYTES,
        maxContentLength: MAX_API_RESPONSE_BYTES,
        responseType: 'text',
        transformResponse: [parseBoundedResponse],
        withCredentials: true,
        xsrfCookieName: '',
        xsrfHeaderName: '',
    })
    let refreshPromise: Promise<void> | null = null
    let authenticationLostNotified = false

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
        config.responseType = 'text'
        headers.delete('Authorization')
        headers.delete('X-CSRF-Token')
        if (UNSAFE_METHODS.has(config.method?.toLowerCase() ?? '')) {
            const csrf = readCsrfCookie()
            if (csrf) headers.set('X-CSRF-Token', csrf)
        }
        config.headers = headers
        return config
    })

    const refresh = (): Promise<void> => {
        if (refreshPromise) return refreshPromise
        const pending = client.post(REFRESH_PATH).then(() => undefined)
        refreshPromise = pending
        const clear = (): void => {
            if (refreshPromise === pending) refreshPromise = null
        }
        void pending.then(clear, clear)
        return pending
    }

    client.interceptors.response.use(
        (response) => {
            if (
                response.config.url === '/auth/me' ||
                isRefresh(response.config)
            ) {
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

    return client
}

let authenticationLostHandler: (() => void | Promise<void>) | undefined

export function setAuthenticationLostHandler(
    handler: (() => void | Promise<void>) | undefined,
): void {
    authenticationLostHandler = handler
}

export const apiClient = createHttpClient({
    onAuthenticationLost: () => authenticationLostHandler?.(),
})

export function responseStatus(error: unknown): number | undefined {
    return axios.isAxiosError(error)
        ? (error as AxiosError).response?.status
        : undefined
}
