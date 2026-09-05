import { apiClient, formatRequestError, type BrowserHttpClient } from './http'

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const DATE = /^\d{4}-\d{2}-\d{2}$/
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/
const EDGE = /^[ \t\r\n\u00a0]+|[ \t\r\n\u00a0]+$/g

export const FINANCE_KINDS = ['COST', 'INCOME'] as const
export const FINANCE_SCOPES = ['COMPANY', 'PROJECT'] as const
export const FINANCE_VISIBILITIES = ['ALL', 'MANAGEMENT', 'OWNER_ONLY'] as const

export type FinanceKind = (typeof FINANCE_KINDS)[number]
export type FinanceScope = (typeof FINANCE_SCOPES)[number]
export type FinanceVisibility = (typeof FINANCE_VISIBILITIES)[number]

export interface FinanceAdjustment {
    id: string
    field: string
    old_value: string
    new_value: string
    reason: string
    created_at: string
}

export interface FinanceEntry {
    id: string
    kind: FinanceKind
    scope: FinanceScope
    project_id: string | null
    project_name: string | null
    amount_cents: number
    currency: string
    occurred_on: string
    category: string
    memo: string
    visibility: FinanceVisibility
    created_via: 'FORM' | 'CARD'
    created_at: string
    adjustments: FinanceAdjustment[]
}

export interface FinanceEntryCreate {
    kind: FinanceKind
    scope: FinanceScope
    project_id?: string | null
    amount_cents: number
    occurred_on: string
    category: string
    memo?: string
    visibility?: FinanceVisibility
}

export interface FinanceAdjustmentCreate {
    field: 'amount_cents' | 'occurred_on' | 'category' | 'memo' | 'visibility'
    new_value: string
    reason: string
}

export interface CompanyTotals {
    cost_cents: number
    income_cents: number
}

export interface ProjectTotals {
    project_id: string
    project_name: string
    cost_cents: number
    income_cents?: number
}

export interface FinanceSummary {
    month: string
    company: CompanyTotals | null
    projects: ProjectTotals[]
}

export class FinanceContractError extends Error {
    constructor() {
        super('Invalid finance data')
        this.name = 'FinanceContractError'
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasRequiredKeys(
    value: Record<string, unknown>,
    required: readonly string[],
): boolean {
    return required.every((key) => key in value)
}

function uuid(value: unknown): value is string {
    return typeof value === 'string' && UUID.test(value)
}

function cents(value: unknown): value is number {
    return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function parseAdjustment(value: unknown): FinanceAdjustment {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'created_at',
            'field',
            'id',
            'new_value',
            'old_value',
            'reason',
        ]) ||
        !uuid(value.id) ||
        typeof value.field !== 'string' ||
        typeof value.old_value !== 'string' ||
        typeof value.new_value !== 'string' ||
        typeof value.reason !== 'string' ||
        typeof value.created_at !== 'string'
    ) {
        throw new FinanceContractError()
    }
    return {
        id: value.id,
        field: value.field,
        old_value: value.old_value,
        new_value: value.new_value,
        reason: value.reason,
        created_at: value.created_at,
    }
}

function parseEntry(value: unknown): FinanceEntry {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'adjustments',
            'amount_cents',
            'category',
            'created_at',
            'created_via',
            'currency',
            'id',
            'kind',
            'memo',
            'occurred_on',
            'project_id',
            'project_name',
            'scope',
            'visibility',
        ]) ||
        !uuid(value.id) ||
        !FINANCE_KINDS.includes(value.kind as FinanceKind) ||
        !FINANCE_SCOPES.includes(value.scope as FinanceScope) ||
        !FINANCE_VISIBILITIES.includes(value.visibility as FinanceVisibility) ||
        (value.project_id !== null && !uuid(value.project_id)) ||
        (value.project_name !== null &&
            typeof value.project_name !== 'string') ||
        !cents(value.amount_cents) ||
        value.currency !== 'CNY' ||
        typeof value.occurred_on !== 'string' ||
        !DATE.test(value.occurred_on) ||
        typeof value.category !== 'string' ||
        typeof value.memo !== 'string' ||
        (value.created_via !== 'FORM' && value.created_via !== 'CARD') ||
        typeof value.created_at !== 'string' ||
        !Array.isArray(value.adjustments)
    ) {
        throw new FinanceContractError()
    }
    return {
        id: value.id,
        kind: value.kind as FinanceKind,
        scope: value.scope as FinanceScope,
        project_id: value.project_id,
        project_name: value.project_name,
        amount_cents: value.amount_cents,
        currency: value.currency,
        occurred_on: value.occurred_on,
        category: value.category,
        memo: value.memo,
        visibility: value.visibility as FinanceVisibility,
        created_via: value.created_via,
        created_at: value.created_at,
        adjustments: value.adjustments.map(parseAdjustment),
    }
}

function parseProjectTotals(value: unknown): ProjectTotals {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, ['cost_cents', 'project_id', 'project_name']) ||
        !uuid(value.project_id) ||
        typeof value.project_name !== 'string' ||
        !cents(value.cost_cents)
    ) {
        throw new FinanceContractError()
    }
    const totals: ProjectTotals = {
        project_id: value.project_id,
        project_name: value.project_name,
        cost_cents: value.cost_cents,
    }
    if ('income_cents' in value) {
        if (!cents(value.income_cents)) throw new FinanceContractError()
        totals.income_cents = value.income_cents
    }
    return totals
}

function parseSummary(value: unknown): FinanceSummary {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, ['month', 'projects']) ||
        typeof value.month !== 'string' ||
        !MONTH.test(value.month) ||
        !Array.isArray(value.projects)
    ) {
        throw new FinanceContractError()
    }
    let company: CompanyTotals | null = null
    if (value.company !== undefined && value.company !== null) {
        if (
            !isRecord(value.company) ||
            !cents(value.company.cost_cents) ||
            !cents(value.company.income_cents)
        ) {
            throw new FinanceContractError()
        }
        company = {
            cost_cents: value.company.cost_cents,
            income_cents: value.company.income_cents,
        }
    }
    return {
        month: value.month,
        company,
        projects: value.projects.map(parseProjectTotals),
    }
}

export function financeErrorMessage(error: unknown): string {
    return formatRequestError(
        '财务操作失败',
        error,
        '财务操作失败，请稍后重试。',
    )
}

export function yuanFromCents(centsValue: number): string {
    return (centsValue / 100).toFixed(2)
}

export function centsFromYuan(value: string): number | null {
    const normalized = value.replace(EDGE, '')
    if (!/^\d+(\.\d{1,2})?$/.test(normalized)) return null
    const centsValue = Math.round(Number(normalized) * 100)
    if (!Number.isInteger(centsValue) || centsValue < 1) return null
    return centsValue
}

export function createFinanceApi(client: BrowserHttpClient) {
    return Object.freeze({
        async list(month: string, projectId?: string): Promise<FinanceEntry[]> {
            if (
                !MONTH.test(month) ||
                (projectId !== undefined && !uuid(projectId))
            ) {
                throw new FinanceContractError()
            }
            const params: Record<string, string> = { month }
            if (projectId) params.project_id = projectId
            const response = await client.get('/finance/entries', { params })
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new FinanceContractError()
            }
            return response.data.map(parseEntry)
        },
        async summary(month: string): Promise<FinanceSummary> {
            if (!MONTH.test(month)) throw new FinanceContractError()
            const response = await client.get('/finance/summary', {
                params: { month },
            })
            if (response.status !== 200) throw new FinanceContractError()
            return parseSummary(response.data)
        },
        async create(command: FinanceEntryCreate): Promise<FinanceEntry> {
            if (
                !FINANCE_KINDS.includes(command.kind) ||
                !FINANCE_SCOPES.includes(command.scope) ||
                !DATE.test(command.occurred_on) ||
                !Number.isInteger(command.amount_cents) ||
                command.amount_cents < 1
            ) {
                throw new FinanceContractError()
            }
            const response = await client.post('/finance/entries', command)
            if (response.status !== 201) throw new FinanceContractError()
            return parseEntry(response.data)
        },
        async adjust(
            entryId: string,
            command: FinanceAdjustmentCreate,
        ): Promise<FinanceEntry> {
            if (!uuid(entryId) || !command.reason.replace(EDGE, '')) {
                throw new FinanceContractError()
            }
            const response = await client.post(
                `/finance/entries/${entryId}/adjustments`,
                command,
            )
            if (response.status !== 200) throw new FinanceContractError()
            return parseEntry(response.data)
        },
    })
}

export const financeApi = createFinanceApi(apiClient)
