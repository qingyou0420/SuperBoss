import { errorCopy } from '../copy/errors'
import { apiClient, formatRequestError, type BrowserHttpClient } from './http'
import { hasRequiredKeys, isRecord, uuid } from './parse'

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
    batch_key?: string
    paid_on?: string | null
    paid_cents?: number | null
    voucher?: string
    paid_total_cents?: number
    unpaid_cents?: number
    payments?: Array<{
        id: string
        paid_on: string
        amount_cents: number
        created_at: string
    }>
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

export interface FinanceOverview {
    active_count: number
    completed_count: number
    completed_fee_cents: number
    completed_gross_cents: number
    received_cents: number
    paid_cents: number
    unpaid_cents: number
    net_inflow_cents: number
    has_opening_balance: boolean
    opening_balance_cents?: number
    opening_as_of?: string
    available_cents?: number | null
    placeholder?: boolean
    payroll_same_day?: string
    months: Array<{
        month: string
        cost_cents: number
        income_cents: number
        company_fixed_cents: number
    }>
    receivables: Array<{
        project_id: string
        project_name: string
        fee_cents: number
        received_cents: number
        outstanding_cents: number
    }>
    pending_rewards: Array<{
        project_id: string
        project_name: string
        surplus_bonus_cents: number
        pool_pay_cents: number
        payroll_on: string | null
    }>
    pipeline: { commissioned: number; talking: number }
}

export interface FinanceImportResult {
    batch_key: string
    inserted: number
    skipped: number
    unresolved: unknown[]
    parse_unresolved?: unknown[]
    replayed: boolean
    entries?: unknown[]
}

export interface FinanceImportRowRecord {
    id: string
    batch_key: string
    row_index: number
    status: string
    reason: string
    fingerprint: string
    entry_id: string | null
    payload: Record<string, unknown>
}

export class FinanceContractError extends Error {
    constructor() {
        super('Invalid finance data')
        this.name = 'FinanceContractError'
    }
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
        ...('batch_key' in value
            ? { batch_key: String(value.batch_key || '') }
            : {}),
        ...('paid_on' in value
            ? {
                  paid_on:
                      typeof value.paid_on === 'string' ? value.paid_on : null,
              }
            : {}),
        ...('paid_cents' in value
            ? {
                  paid_cents:
                      typeof value.paid_cents === 'number'
                          ? value.paid_cents
                          : null,
              }
            : {}),
        ...('voucher' in value
            ? {
                  voucher:
                      typeof value.voucher === 'string' ? value.voucher : '',
              }
            : {}),
        ...('paid_total_cents' in value
            ? {
                  paid_total_cents:
                      typeof value.paid_total_cents === 'number'
                          ? value.paid_total_cents
                          : 0,
              }
            : {}),
        ...('unpaid_cents' in value
            ? {
                  unpaid_cents:
                      typeof value.unpaid_cents === 'number'
                          ? value.unpaid_cents
                          : 0,
              }
            : {}),
        ...('payments' in value && Array.isArray(value.payments)
            ? {
                  payments: value.payments
                      .filter((item): item is Record<string, unknown> =>
                          isRecord(item),
                      )
                      .map((item) => ({
                          id: String(item.id || ''),
                          paid_on: String(item.paid_on || ''),
                          amount_cents:
                              typeof item.amount_cents === 'number'
                                  ? item.amount_cents
                                  : 0,
                          created_at: String(item.created_at || ''),
                      })),
              }
            : {}),
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
    return formatRequestError(errorCopy.finance, error, errorCopy.generic)
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
        async alerts(): Promise<{ project_id: string; message: string }[]> {
            const response = await client.get('/finance/alerts')
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new FinanceContractError()
            }
            return response.data.map((item) => {
                if (!isRecord(item) || typeof item.message !== 'string') {
                    throw new FinanceContractError()
                }
                return {
                    project_id: String(item.project_id || ''),
                    message: item.message,
                }
            })
        },
        async summary(month: string): Promise<FinanceSummary> {
            if (!MONTH.test(month)) throw new FinanceContractError()
            const response = await client.get('/finance/summary', {
                params: { month },
            })
            if (response.status !== 200) throw new FinanceContractError()
            return parseSummary(response.data)
        },
        async overview(): Promise<FinanceOverview> {
            const response = await client.get('/finance/overview')
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new FinanceContractError()
            }
            const data = response.data
            return {
                active_count: Number(data.active_count) || 0,
                completed_count: Number(data.completed_count) || 0,
                completed_fee_cents: Number(data.completed_fee_cents) || 0,
                completed_gross_cents: Number(data.completed_gross_cents) || 0,
                received_cents: Number(data.received_cents) || 0,
                paid_cents: Number(data.paid_cents) || 0,
                unpaid_cents: Number(data.unpaid_cents) || 0,
                net_inflow_cents: Number(data.net_inflow_cents) || 0,
                has_opening_balance: Boolean(data.has_opening_balance),
                opening_balance_cents: Number(data.opening_balance_cents) || 0,
                opening_as_of: String(data.opening_as_of || ''),
                available_cents:
                    data.available_cents === null ||
                    data.available_cents === undefined
                        ? null
                        : Number(data.available_cents) || 0,
                placeholder: Boolean(data.placeholder),
                payroll_same_day: String(data.payroll_same_day || ''),
                months: Array.isArray(data.months)
                    ? (data.months as FinanceOverview['months'])
                    : [],
                receivables: Array.isArray(data.receivables)
                    ? (data.receivables as FinanceOverview['receivables'])
                    : [],
                pending_rewards: Array.isArray(data.pending_rewards)
                    ? (data.pending_rewards as FinanceOverview['pending_rewards'])
                    : [],
                pipeline: isRecord(data.pipeline)
                    ? {
                          commissioned: Number(data.pipeline.commissioned) || 0,
                          talking: Number(data.pipeline.talking) || 0,
                      }
                    : { commissioned: 0, talking: 0 },
            }
        },
        async importBatch(
            batchKey: string,
            rows: Array<Record<string, unknown>>,
        ): Promise<FinanceImportResult> {
            const response = await client.post('/finance/import', {
                batch_key: batchKey,
                rows,
            })
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new FinanceContractError()
            }
            return {
                batch_key: String(response.data.batch_key || batchKey),
                inserted: Number(response.data.inserted) || 0,
                skipped: Number(response.data.skipped) || 0,
                unresolved: Array.isArray(response.data.unresolved)
                    ? response.data.unresolved
                    : [],
                replayed: Boolean(response.data.replayed),
                entries: Array.isArray(response.data.entries)
                    ? response.data.entries
                    : [],
            }
        },
        async importFile(
            file: File,
            batchKey?: string,
        ): Promise<FinanceImportResult> {
            const body = new FormData()
            body.append('file', file)
            const suffix = batchKey
                ? `?batch_key=${encodeURIComponent(batchKey)}`
                : ''
            const response = await client.post(
                `/finance/imports${suffix}`,
                body,
            )
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new FinanceContractError()
            }
            return {
                batch_key: String(response.data.batch_key || ''),
                inserted: Number(response.data.inserted) || 0,
                skipped: Number(response.data.skipped) || 0,
                unresolved: Array.isArray(response.data.unresolved)
                    ? response.data.unresolved
                    : [],
                parse_unresolved: Array.isArray(response.data.parse_unresolved)
                    ? response.data.parse_unresolved
                    : [],
                replayed: Boolean(response.data.replayed),
                entries: Array.isArray(response.data.entries)
                    ? response.data.entries
                    : [],
            }
        },
        async listImportRows(
            status = 'UNRESOLVED',
            paging?: { offset?: number; limit?: number },
        ): Promise<{
            items: FinanceImportRowRecord[]
            total: number
        }> {
            const response = await client.get('/finance/import-rows', {
                params: {
                    status,
                    offset: String(paging?.offset ?? 0),
                    limit: String(paging?.limit ?? 200),
                },
            })
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new FinanceContractError()
            }
            const items = Array.isArray(response.data.items)
                ? response.data.items
                : []
            return {
                items: items.filter(isRecord).map((item) => ({
                    id: String(item.id || ''),
                    batch_key: String(item.batch_key || ''),
                    row_index: Number(item.row_index) || 0,
                    status: String(item.status || ''),
                    reason: String(item.reason || ''),
                    fingerprint: String(item.fingerprint || ''),
                    entry_id:
                        typeof item.entry_id === 'string'
                            ? item.entry_id
                            : null,
                    payload: isRecord(item.payload) ? item.payload : {},
                })),
                total: Number(response.data.total) || 0,
            }
        },
        async resolveImportRow(
            batchKey: string,
            rowIndex: number,
            action: 'link_voucher' | 'insert_independent',
            entryId?: string,
        ): Promise<FinanceImportRowRecord> {
            const response = await client.post(
                `/finance/imports/${encodeURIComponent(batchKey)}/rows/${rowIndex}/resolve`,
                { action, entry_id: entryId || null },
            )
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new FinanceContractError()
            }
            return {
                id: String(response.data.id || ''),
                batch_key: String(response.data.batch_key || batchKey),
                row_index: Number(response.data.row_index) || rowIndex,
                status: String(response.data.status || ''),
                reason: String(response.data.reason || ''),
                fingerprint: String(response.data.fingerprint || ''),
                entry_id:
                    typeof response.data.entry_id === 'string'
                        ? response.data.entry_id
                        : null,
                payload: isRecord(response.data.payload)
                    ? response.data.payload
                    : {},
            }
        },
        async markPaid(
            entryId: string,
            paidOn: string,
            paidCents?: number,
            idempotencyKey?: string,
        ): Promise<FinanceEntry> {
            if (!uuid(entryId) || !DATE.test(paidOn)) {
                throw new FinanceContractError()
            }
            const response = await client.post(
                `/finance/entries/${entryId}/pay`,
                {
                    paid_on: paidOn,
                    paid_cents: paidCents,
                    idempotency_key: idempotencyKey || undefined,
                },
            )
            if (response.status !== 200) throw new FinanceContractError()
            return parseEntry(response.data)
        },
        async upsertMonthCost(
            month: string,
            amountCents: number,
        ): Promise<{ month: string; amount_cents: number }> {
            if (!MONTH.test(month)) throw new FinanceContractError()
            const response = await client.put(`/finance/month-costs/${month}`, {
                amount_cents: amountCents,
            })
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new FinanceContractError()
            }
            return {
                month: String(response.data.month),
                amount_cents: Number(response.data.amount_cents) || 0,
            }
        },
        async rewards(projectId: string): Promise<Record<string, unknown>> {
            if (!uuid(projectId)) throw new FinanceContractError()
            const response = await client.get(`/finance/rewards/${projectId}`)
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new FinanceContractError()
            }
            return response.data
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
