import {
    AxiosError,
    type AxiosAdapter,
    type AxiosResponse,
    type InternalAxiosRequestConfig,
} from 'axios'
import { describe, expect, test } from 'vitest'

import { createHttpClient, HttpClientError } from '../src/api/http'

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

describe('browser HTTP client', () => {
    test('attaches CSRF to mutating requests and retries a refreshable 401 once', async () => {
        document.cookie = 'XSRF-TOKEN=csrf-token; Path=/'
        const seen: string[] = []
        let calls = 0
        const adapter: AxiosAdapter = async (config) => {
            calls += 1
            seen.push(`${config.method}:${config.url}:${config.headers?.['X-CSRF-Token'] ?? ''}`)
            if (config.url === '/auth/refresh') {
                return response(config, 204, '')
            }
            if (calls === 1) {
                const rejected = response(
                    config,
                    401,
                    '{"detail":"Authentication required"}',
                    { 'X-SuperBoss-Refreshable': '1' },
                )
                throw new AxiosError(
                    'rejected',
                    'ERR_BAD_REQUEST',
                    config,
                    undefined,
                    rejected,
                )
            }
            return response(config, 204, '')
        }
        const client = createHttpClient({ adapter })
        const result = await client.post('/projects', { name: 'x' })
        expect(result.status).toBe(204)
        expect(seen.some((item) => item.startsWith('post:/auth/refresh'))).toBe(true)
        expect(seen[0]).toContain('csrf-token')
    })

    test('wraps non-refreshable failures as HttpClientError', async () => {
        const adapter: AxiosAdapter = async (config) => {
            const rejected = response(config, 503, '{"error":{"code":"DOWN"}}')
            throw new AxiosError('rejected', 'ERR_BAD_REQUEST', config, undefined, rejected)
        }
        await expect(createHttpClient({ adapter }).get('/health')).rejects.toBeInstanceOf(
            HttpClientError,
        )
    })
})
