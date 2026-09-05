import type {
    AxiosAdapter,
    AxiosResponse,
    InternalAxiosRequestConfig,
} from 'axios'
import { describe, expect, test } from 'vitest'

import { createHttpClient } from '../src/api/http'
import {
    FinanceContractError,
    centsFromYuan,
    createFinanceApi,
    yuanFromCents,
} from '../src/api/finance'

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

const ENTRY_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'

const entry = {
    id: ENTRY_ID,
    kind: 'COST' as const,
    scope: 'PROJECT' as const,
    project_id: PROJECT_ID,
    project_name: '星野合作',
    amount_cents: 1_200_000,
    currency: 'CNY',
    occurred_on: '2026-09-02',
    category: '外包',
    memo: '',
    visibility: 'ALL' as const,
    created_via: 'FORM' as const,
    created_at: '2026-09-02T00:00:00Z',
    adjustments: [],
}

describe('finance API contracts', () => {
    test('converts yuan and cents without floating residue', () => {
        expect(yuanFromCents(800_000)).toBe('8000.00')
        expect(centsFromYuan('8000')).toBe(800_000)
        expect(centsFromYuan('8000.5')).toBe(800_050)
        expect(centsFromYuan('0')).toBeNull()
        expect(centsFromYuan('12.345')).toBeNull()
    })

    test('lists and summarizes with canonical routes', async () => {
        const calls: Array<{ data?: unknown; method?: string; url?: string }> =
            []
        const adapter: AxiosAdapter = async (config) => {
            calls.push({
                data: config.data,
                method: config.method,
                url: config.url,
            })
            if (config.url === '/finance/summary') {
                return response(config, 200, {
                    month: '2026-09',
                    projects: [
                        {
                            project_id: PROJECT_ID,
                            project_name: '星野合作',
                            cost_cents: 1_200_000,
                        },
                    ],
                })
            }
            return response(config, 200, [entry])
        }
        const api = createFinanceApi(createHttpClient({ adapter }))
        await expect(api.list('2026-09')).resolves.toEqual([entry])
        await expect(api.summary('2026-09')).resolves.toEqual({
            month: '2026-09',
            company: null,
            projects: [
                {
                    project_id: PROJECT_ID,
                    project_name: '星野合作',
                    cost_cents: 1_200_000,
                },
            ],
        })
        expect(calls.map((call) => call.url)).toEqual([
            '/finance/entries',
            '/finance/summary',
        ])
    })

    test('creates and adjusts entries', async () => {
        const adapter: AxiosAdapter = async (config) => {
            if (config.method === 'post' && config.url === '/finance/entries') {
                return response(config, 201, entry)
            }
            return response(config, 200, {
                ...entry,
                amount_cents: 900_000,
                adjustments: [
                    {
                        id: PROJECT_ID,
                        field: 'amount_cents',
                        old_value: '1200000',
                        new_value: '900000',
                        reason: '补差',
                        created_at: '2026-09-03T00:00:00Z',
                    },
                ],
            })
        }
        const api = createFinanceApi(createHttpClient({ adapter }))
        await expect(
            api.create({
                kind: 'COST',
                scope: 'PROJECT',
                project_id: PROJECT_ID,
                amount_cents: 1_200_000,
                occurred_on: '2026-09-02',
                category: '外包',
            }),
        ).resolves.toEqual(entry)
        const adjusted = await api.adjust(ENTRY_ID, {
            field: 'amount_cents',
            new_value: '900000',
            reason: '补差',
        })
        expect(adjusted.amount_cents).toBe(900_000)
        expect(adjusted.adjustments).toHaveLength(1)
    })

    test('rejects malformed month and summary income for staff-shaped payloads', async () => {
        const api = createFinanceApi(
            createHttpClient({
                adapter: async (config) => response(config, 200, []),
            }),
        )
        await expect(api.list('2026-13')).rejects.toBeInstanceOf(
            FinanceContractError,
        )
        await expect(
            createFinanceApi(
                createHttpClient({
                    adapter: async (config) =>
                        response(config, 200, {
                            month: '2026-09',
                            company: { cost_cents: 1, income_cents: 'secret' },
                            projects: [],
                        }),
                }),
            ).summary('2026-09'),
        ).rejects.toBeInstanceOf(FinanceContractError)
    })
})
