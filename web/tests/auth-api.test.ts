import type {
    AxiosAdapter,
    AxiosRequestConfig,
    AxiosResponse,
    InternalAxiosRequestConfig,
} from 'axios'
import { describe, expect, test } from 'vitest'

import {
    AuthContractError,
    createAuthApi,
    parseOAuthCallback,
} from '../src/api/auth'
import { createHttpClient } from '../src/api/http'

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

function clientReturning(data: unknown) {
    const adapter: AxiosAdapter = async (config) => response(config, 200, data)
    return createHttpClient({ adapter })
}

function clientResponding(status: number, data: unknown) {
    const adapter: AxiosAdapter = async (config) =>
        response(config, status, data)
    return createHttpClient({ adapter })
}

describe('strict authentication API contracts', () => {
    test('accepts the exact /me schema and rejects role aliases, extra keys, controls, and oversized identities', async () => {
        await expect(
            createAuthApi(
                clientReturning({ userid: 'owner-1', role: 'OWNER' }),
            ).me(),
        ).resolves.toEqual({ userid: 'owner-1', role: 'OWNER' })
        const unicodeUserid = '😀'.repeat(255)
        await expect(
            createAuthApi(
                clientReturning({ userid: unicodeUserid, role: 'STAFF' }),
            ).me(),
        ).resolves.toEqual({ userid: unicodeUserid, role: 'STAFF' })

        for (const invalid of [
            { userid: 'owner-1', role: 'owner' },
            { userid: 'owner-1', role: 'OWNER', token: 'leak' },
            { userid: 'bad\r\nid', role: 'OWNER' },
            { userid: 'x'.repeat(256), role: 'STAFF' },
            null,
            [],
        ]) {
            await expect(
                createAuthApi(clientReturning(invalid)).me(),
            ).rejects.toBeInstanceOf(AuthContractError)
        }
    })

    test('accepts only a safe HTTPS provider URL containing the server state exactly once', async () => {
        const state = 'safe_state-123'
        const valid = {
            state,
            authorization_url: `https://open.work.weixin.qq.com/wwopen/sso/qrConnect?state=${state}`,
        }
        await expect(
            createAuthApi(clientReturning(valid)).startWeCom(),
        ).resolves.toEqual(valid)

        for (const invalid of [
            { ...valid, extra: true },
            { ...valid, authorization_url: 'javascript:alert(1)' },
            {
                ...valid,
                authorization_url: 'http://open.work.weixin.qq.com/authorize',
            },
            {
                ...valid,
                authorization_url: `https://user:pass@example.com/?state=${state}`,
            },
            { ...valid, authorization_url: 'https://example.com/?state=other' },
            {
                ...valid,
                authorization_url: `https://example.com/?state=${state}&state=${state}`,
            },
            { ...valid, state: 'bad\nstate' },
        ]) {
            await expect(
                createAuthApi(clientReturning(invalid)).startWeCom(),
            ).rejects.toBeInstanceOf(AuthContractError)
        }
    })

    test('parses one bounded ASCII code/state and rejects unrecognized redirect parameters', () => {
        expect(parseOAuthCallback('?code=code_1&state=state-1')).toEqual({
            code: 'code_1',
            state: 'state-1',
        })
        expect(
            parseOAuthCallback(
                '?code=code_1&state=state-1&redirect=%2Fowner%2Fprojects',
            ),
        ).toEqual({
            code: 'code_1',
            state: 'state-1',
            redirect: '/owner/projects',
        })

        for (const search of [
            '',
            '?code=x',
            '?state=x',
            '?code=x&code=y&state=z',
            '?code=x&state=y&redirect=%2Fowner&redirect=%2Fowner%2Fprojects',
            '?code=x&state=y&next=https%3A%2F%2Fevil.example',
            '?code=%0D%0Aevil&state=safe',
            `?code=${'x'.repeat(2049)}&state=safe`,
            '?code=中文&state=safe',
        ]) {
            expect(() => parseOAuthCallback(search)).toThrow(AuthContractError)
        }
    })

    test('callback forwards only validated code/state to the fixed same-origin endpoint', async () => {
        let seen: AxiosRequestConfig | undefined
        const adapter: AxiosAdapter = async (config) => {
            seen = config
            return response(config, 204, null)
        }
        const api = createAuthApi(createHttpClient({ adapter }))

        await api.completeWeCom({ code: 'code_1', state: 'state-1' })

        expect(seen?.url).toBe('/auth/wecom/callback')
        expect(seen?.method).toBe('get')
        expect(seen?.params).toEqual({ code: 'code_1', state: 'state-1' })
        expect(JSON.stringify(seen)).not.toContain('redirect')
        expect(JSON.stringify(seen)).not.toContain('Authorization')
    })

    test('accepts exact callback/logout 204 responses only when the body is empty', async () => {
        await expect(
            createAuthApi(clientResponding(204, null)).completeWeCom({
                code: 'code-1',
                state: 'state-1',
            }),
        ).resolves.toBeUndefined()
        await expect(
            createAuthApi(clientResponding(204, null)).logout(),
        ).resolves.toBeUndefined()
    })

    test.each([
        {
            label: 'start wrong status',
            status: 201,
            data: {
                state: 'state-1',
                authorization_url:
                    'https://open.weixin.qq.com/connect/oauth2/authorize?state=state-1',
            },
            operation: 'start' as const,
        },
        {
            label: 'me wrong status',
            status: 201,
            data: { userid: 'owner-1', role: 'OWNER' as const },
            operation: 'me' as const,
        },
        {
            label: 'callback wrong status',
            status: 200,
            data: null,
            operation: 'callback' as const,
        },
        {
            label: 'callback non-empty body',
            status: 204,
            data: { detail: 'sentinel' },
            operation: 'callback' as const,
        },
        {
            label: 'logout wrong status',
            status: 200,
            data: null,
            operation: 'logout' as const,
        },
        {
            label: 'logout non-empty body',
            status: 204,
            data: { detail: 'sentinel' },
            operation: 'logout' as const,
        },
    ])('rejects $label', async ({ status, data, operation }) => {
        const api = createAuthApi(clientResponding(status, data))
        const pending =
            operation === 'start'
                ? api.startWeCom()
                : operation === 'me'
                  ? api.me()
                  : operation === 'callback'
                    ? api.completeWeCom({
                          code: 'code-1',
                          state: 'state-1',
                      })
                    : api.logout()

        await expect(pending).rejects.toBeInstanceOf(AuthContractError)
    })
})
