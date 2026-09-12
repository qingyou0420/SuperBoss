<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import {
    centsFromYuan,
    financeApi,
    financeErrorMessage,
    type FinanceEntry,
    type FinanceImportRowRecord,
    type FinanceKind,
    type FinanceScope,
    type FinanceSummary,
    type FinanceVisibility,
} from '../api/finance'
import { isRecord } from '../api/parse'
import { moneyLabel } from '../api/parse'
import { errorCopy } from '../copy/errors'
import { projectsApi, type Project } from '../api/projects'
import DateText from '../components/ui/DateText.vue'
import InlineError from '../components/ui/InlineError.vue'
import Money from '../components/ui/Money.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import {
    FIELD_LABEL,
    FINANCE_KIND_LABEL,
    FINANCE_SCOPE_LABEL,
    VISIBILITY_LABEL,
} from '../copy/glossary'
import { financeCopy, financeMonthLabel } from '../copy/pages/finance'
import { useAuthStore } from '../stores/auth'

function currentMonth(): string {
    const now = new Date()
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function todayLocal(): string {
    const now = new Date()
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

function rowLabel(value: unknown): string {
    if (!isRecord(value)) return String(value ?? '')
    const row = value.row ?? value.source_row ?? value.row_index
    const reason = value.reason
    const name = value.project_name
    return [row != null ? `第${row}行` : '', reason, name]
        .filter(Boolean)
        .join(' ')
}

type PendingPay = { key: string; cents: number; paidOn: string }

const pendingPays = new Map<string, PendingPay>()

function pendingPayStorageKey(entryId: string): string {
    return `superboss:pay-pending:${entryId}`
}

function readPendingPay(entryId: string): PendingPay | null {
    const memory = pendingPays.get(entryId)
    if (memory) return memory
    try {
        const raw = sessionStorage.getItem(pendingPayStorageKey(entryId))
        if (!raw) return null
        const parsed = JSON.parse(raw) as PendingPay
        if (!parsed?.key) return null
        pendingPays.set(entryId, parsed)
        return parsed
    } catch {
        return null
    }
}

function writePendingPay(entryId: string, payload: PendingPay): void {
    pendingPays.set(entryId, payload)
    try {
        sessionStorage.setItem(
            pendingPayStorageKey(entryId),
            JSON.stringify(payload),
        )
    } catch {
        /* ignore storage failures in probes */
    }
}

function clearPendingPay(entryId: string): void {
    pendingPays.delete(entryId)
    try {
        sessionStorage.removeItem(pendingPayStorageKey(entryId))
    } catch {
        /* ignore */
    }
}

function shiftMonth(value: string, delta: number): string {
    const [year, month] = value.split('-').map(Number)
    const date = new Date(year, month - 1 + delta, 1)
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

function monthLabel(value: string): string {
    const [year, month] = value.split('-')
    return financeMonthLabel(year, month)
}

const auth = useAuthStore()
const canWrite = computed(() => auth.user?.role === 'OWNER')
const canSeeCompany = computed(() => auth.user?.role !== 'STAFF')
const canSeeLedger = computed(() => auth.user?.role !== 'STAFF')
const fileInput = ref<HTMLInputElement | null>(null)
const monthCostYuan = ref('')
const notice = ref('')
const month = ref(currentMonth())
const summary = ref<FinanceSummary>()
const entries = ref<FinanceEntry[]>([])
const projects = ref<Project[]>([])
const alerts = ref<{ project_id: string; message: string }[]>([])
const loading = ref(true)
const errorMessage = ref('')
const drawerOpen = ref(false)
const adjustOpen = ref(false)
const kind = ref<FinanceKind>('COST')
const scope = ref<FinanceScope>('COMPANY')
const projectId = ref('')
const amountYuan = ref('')
const occurredOn = ref(new Date().toISOString().slice(0, 10))
const category = ref('')
const memo = ref('')
const visibility = ref<FinanceVisibility | ''>('')
const saving = ref(false)
const adjusting = ref<FinanceEntry>()
const adjustField = ref<
    'amount_cents' | 'occurred_on' | 'category' | 'memo' | 'visibility'
>('amount_cents')
const adjustValue = ref('')
const adjustReason = ref('')
const payOpen = ref(false)
const paying = ref<FinanceEntry>()
const payYuan = ref('')
const payOn = ref(todayLocal())
const payKey = ref('')
const payBusy = ref(false)
const payRetrying = ref(false)
const importRows = ref<unknown[]>([])
const importInserted = ref<unknown[]>([])
const storedImportRows = ref<FinanceImportRowRecord[]>([])
const importRowOffset = ref(0)
const importRowTotal = ref(0)
const importPageSize = 50
const retryBatchKey = ref('')
const resolvingImportKey = ref('')
const processedImportRows = ref<unknown[]>([])
const processedInsertedOffset = ref(0)
const processedLinkedOffset = ref(0)
const processedInsertedTotal = ref(0)
const processedLinkedTotal = ref(0)
const processedPageSize = 200
const processedLoading = ref(false)
let processedLoadGeneration = 0

function mapStoredImportRow(
    item: FinanceImportRowRecord,
): Record<string, unknown> {
    const payload = item.payload
    return {
        ...payload,
        row: item.row_index,
        row_index: item.row_index,
        source_row: payload.source_row ?? item.row_index,
        source_sheet: payload.source_sheet,
        reason: item.reason,
        batch_key: item.batch_key,
        status: item.status,
        entry_id: item.entry_id,
        id: item.entry_id,
        candidate_entry_id: payload.candidate_entry_id,
        candidate_entry_ids: payload.candidate_entry_ids,
        memo: payload.memo,
        voucher: payload.voucher,
        candidate_memo: payload.candidate_memo,
        candidate_voucher: payload.candidate_voucher,
        occurred_on: payload.occurred_on,
    }
}

const companyCost = computed(() => summary.value?.company?.cost_cents ?? 0)
const companyIncome = computed(() => summary.value?.company?.income_cents ?? 0)
const projectCostTotal = computed(
    () =>
        summary.value?.projects.reduce(
            (sum, item) => sum + item.cost_cents,
            0,
        ) ?? 0,
)
const projectIncomeTotal = computed(
    () =>
        summary.value?.projects.reduce(
            (sum, item) => sum + (item.income_cents ?? 0),
            0,
        ) ?? 0,
)
const margin = computed(
    () =>
        companyIncome.value +
        projectIncomeTotal.value -
        (companyCost.value + projectCostTotal.value),
)

function processedRowKey(item: unknown): string {
    if (!isRecord(item)) return String(item)
    return [
        String(item.batch_key || ''),
        String(item.row_index ?? ''),
        String(item.entry_id || item.id || ''),
    ].join('-')
}

async function loadProcessed(append = false): Promise<void> {
    if (append && processedLoading.value) return
    const generation = ++processedLoadGeneration
    processedLoading.value = true
    const insertedOffset = append ? processedInsertedOffset.value : 0
    const linkedOffset = append ? processedLinkedOffset.value : 0
    if (!append) {
        processedInsertedOffset.value = 0
        processedLinkedOffset.value = 0
        processedImportRows.value = []
    }
    try {
        const [insertedRows, linkedRows] = await Promise.all([
            financeApi
                .listImportRows('INSERTED', {
                    offset: insertedOffset,
                    limit: processedPageSize,
                })
                .catch(() => ({ items: [], total: 0 })),
            financeApi
                .listImportRows('LINKED', {
                    offset: linkedOffset,
                    limit: processedPageSize,
                })
                .catch(() => ({ items: [], total: 0 })),
        ])
        if (generation !== processedLoadGeneration) return
        const mapped = [...insertedRows.items, ...linkedRows.items].map(
            mapStoredImportRow,
        )
        if (append) {
            const seen = new Set(processedImportRows.value.map(processedRowKey))
            processedImportRows.value = [
                ...processedImportRows.value,
                ...mapped.filter((item) => !seen.has(processedRowKey(item))),
            ]
        } else {
            processedImportRows.value = mapped
        }
        importInserted.value = processedImportRows.value
        processedInsertedTotal.value = insertedRows.total
        processedLinkedTotal.value = linkedRows.total
        processedInsertedOffset.value =
            insertedOffset + insertedRows.items.length
        processedLinkedOffset.value = linkedOffset + linkedRows.items.length
    } finally {
        if (generation === processedLoadGeneration) {
            processedLoading.value = false
        }
    }
}

function loadMoreProcessed(): void {
    void loadProcessed(true)
}

async function load(appendPending = false): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    if (!appendPending) {
        importRowOffset.value = 0
    }
    try {
        const [
            nextSummary,
            nextEntries,
            nextProjects,
            nextAlerts,
            pendingRows,
        ] = await Promise.all([
            financeApi.summary(month.value),
            financeApi.list(month.value),
            projectsApi.list(),
            financeApi.alerts().catch(() => []),
            financeApi
                .listImportRows('UNRESOLVED', {
                    offset: importRowOffset.value,
                    limit: importPageSize,
                })
                .catch(() => ({ items: [], total: 0 })),
        ])
        summary.value = nextSummary
        entries.value = nextEntries
        projects.value = nextProjects
        alerts.value = nextAlerts
        storedImportRows.value = pendingRows.items
        importRowTotal.value = pendingRows.total
        const mappedPending = pendingRows.items.map(mapStoredImportRow)
        importRows.value = appendPending
            ? [...importRows.value, ...mappedPending]
            : mappedPending
        if (!appendPending) {
            await loadProcessed(false)
        }
    } catch {
        errorMessage.value = errorCopy.generic
    } finally {
        loading.value = false
    }
}

async function createEntry(): Promise<void> {
    const cents = centsFromYuan(amountYuan.value)
    const canonicalCategory = category.value.trim()
    if (!cents || !canonicalCategory) {
        errorMessage.value = financeCopy.amountAndCategory
        return
    }
    if (scope.value === 'PROJECT' && !projectId.value) {
        errorMessage.value = financeCopy.projectRequired
        return
    }
    saving.value = true
    errorMessage.value = ''
    try {
        await financeApi.create({
            kind: kind.value,
            scope: scope.value,
            project_id: scope.value === 'PROJECT' ? projectId.value : null,
            amount_cents: cents,
            occurred_on: occurredOn.value,
            category: canonicalCategory,
            memo: memo.value.trim(),
            visibility: visibility.value || undefined,
        })
        amountYuan.value = ''
        category.value = ''
        memo.value = ''
        visibility.value = ''
        drawerOpen.value = false
        await load()
    } catch (error) {
        errorMessage.value = financeErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function submitAdjustment(): Promise<void> {
    if (!adjusting.value) return
    if (!adjustValue.value.trim() || !adjustReason.value.trim()) {
        errorMessage.value = financeCopy.adjustRequired
        return
    }
    saving.value = true
    errorMessage.value = ''
    try {
        const newValue =
            adjustField.value === 'amount_cents'
                ? String(centsFromYuan(adjustValue.value) ?? '')
                : adjustValue.value.trim()
        if (adjustField.value === 'amount_cents' && !newValue) {
            errorMessage.value = financeCopy.adjustAmountInvalid
            return
        }
        await financeApi.adjust(adjusting.value.id, {
            field: adjustField.value,
            new_value: newValue,
            reason: adjustReason.value.trim(),
        })
        adjustOpen.value = false
        adjusting.value = undefined
        adjustValue.value = ''
        adjustReason.value = ''
        await load()
    } catch (error) {
        errorMessage.value = financeErrorMessage(error)
    } finally {
        saving.value = false
    }
}

function beginAdjust(entry: FinanceEntry): void {
    adjusting.value = entry
    adjustField.value = 'amount_cents'
    adjustValue.value = (entry.amount_cents / 100).toFixed(2)
    adjustReason.value = ''
    adjustOpen.value = true
}

async function importWorkbook(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file || !canWrite.value) return
    try {
        const reusedKey = retryBatchKey.value
        retryBatchKey.value = ''
        const result = await financeApi.importFile(file, reusedKey || undefined)
        const pending = [
            ...(result.unresolved || []),
            ...(result.parse_unresolved || []),
        ]
        importRowOffset.value = 0
        importRows.value = pending.map((item) =>
            isRecord(item)
                ? { ...item, batch_key: result.batch_key }
                : { reason: String(item), batch_key: result.batch_key },
        )
        importInserted.value = result.entries || []
        notice.value = result.replayed
            ? `${financeCopy.batchReplay} ${financeCopy.importInserted} ${result.inserted}，待处理 ${pending.length}`
            : `${financeCopy.importBatch} ${result.inserted}，跳过 ${result.skipped}，待处理 ${pending.length}`
        await load()
    } catch (error) {
        errorMessage.value = financeErrorMessage(error)
    }
}

function beginPay(entry: FinanceEntry): void {
    paying.value = entry
    const remaining = entry.unpaid_cents ?? 0
    const pending = readPendingPay(entry.id)
    if (pending) {
        payYuan.value = (pending.cents / 100).toFixed(2)
        payOn.value = pending.paidOn
        payKey.value = pending.key
        payRetrying.value = true
    } else {
        payYuan.value = remaining ? (remaining / 100).toFixed(2) : ''
        payOn.value = todayLocal()
        payKey.value =
            globalThis.crypto?.randomUUID?.() ||
            `pay:${entry.id}:${remaining}:${Date.now()}`
        payRetrying.value = false
    }
    payOpen.value = true
}

function startFreshPay(): void {
    if (!paying.value) return
    clearPendingPay(paying.value.id)
    const remaining = paying.value.unpaid_cents ?? 0
    payYuan.value = remaining ? (remaining / 100).toFixed(2) : ''
    payOn.value = todayLocal()
    payKey.value =
        globalThis.crypto?.randomUUID?.() ||
        `pay:${paying.value.id}:${remaining}:${Date.now()}`
    payRetrying.value = false
}

async function confirmPay(): Promise<void> {
    if (!paying.value || payBusy.value) return
    const pending = readPendingPay(paying.value.id)
    const useOriginal = Boolean(payRetrying.value && pending)
    const cents =
        useOriginal && pending ? pending.cents : centsFromYuan(payYuan.value)
    const paidOn = useOriginal && pending ? pending.paidOn : payOn.value
    const key = useOriginal && pending ? pending.key : payKey.value
    if (!cents) return
    payBusy.value = true
    if (!useOriginal) {
        writePendingPay(paying.value.id, {
            key,
            cents,
            paidOn,
        })
    }
    try {
        const result = await financeApi.markPaid(
            paying.value.id,
            paidOn,
            cents,
            key,
        )
        const recorded = (result.payments || []).some(
            (item) => item.amount_cents === cents && item.paid_on === paidOn,
        )
        if (useOriginal && !recorded) {
            notice.value = financeCopy.payRestoredOriginal
        }
        clearPendingPay(paying.value.id)
        payOpen.value = false
        paying.value = undefined
        payRetrying.value = false
        await load()
    } catch (error) {
        errorMessage.value = financeErrorMessage(error)
    } finally {
        payBusy.value = false
    }
}

function importRowPayload(item: unknown): Record<string, unknown> {
    if (!isRecord(item)) return {}
    if (isRecord(item.payload)) return item.payload
    return item
}

async function resolveImport(
    item: unknown,
    action: 'link_voucher' | 'insert_independent',
    entryId?: string,
): Promise<void> {
    const record = isRecord(item) ? item : {}
    const payload = importRowPayload(item)
    const batchKey = String(record.batch_key || payload.batch_key || '')
    const rowIndex = Number(
        record.row_index ?? payload.source_row ?? payload.row ?? 0,
    )
    if (!batchKey || !rowIndex || resolvingImportKey.value) return
    const key = `${batchKey}:${rowIndex}`
    resolvingImportKey.value = key
    try {
        await financeApi.resolveImportRow(
            batchKey,
            rowIndex,
            action,
            entryId ||
                (typeof payload.candidate_entry_id === 'string'
                    ? payload.candidate_entry_id
                    : undefined),
        )
        importRowOffset.value = 0
        await load()
    } catch (error) {
        errorMessage.value = financeErrorMessage(error)
    } finally {
        resolvingImportKey.value = ''
    }
}

function candidateIds(item: unknown): string[] {
    const payload = importRowPayload(item)
    const record = isRecord(item) ? item : {}
    const raw = payload.candidate_entry_ids ?? record.candidate_entry_ids
    if (Array.isArray(raw)) {
        return raw.filter((value): value is string => typeof value === 'string')
    }
    const single = payload.candidate_entry_id ?? record.candidate_entry_id
    return typeof single === 'string' ? [single] : []
}

function retryIncomplete(item: unknown): void {
    const record = isRecord(item) ? item : {}
    const payload = importRowPayload(item)
    retryBatchKey.value = String(record.batch_key || payload.batch_key || '')
    fileInput.value?.click()
}

function loadMoreImportRows(): void {
    importRowOffset.value += importPageSize
    void load(true)
}

async function openImportedEntry(item: unknown): Promise<void> {
    const record = isRecord(item) ? item : {}
    const payload = importRowPayload(item)
    const entryId = String(
        record.id || record.entry_id || payload.entry_id || payload.id || '',
    )
    const occurred = String(payload.occurred_on || record.occurred_on || '')
    const targetMonth = occurred.slice(0, 7)
    if (
        /^\d{4}-(0[1-9]|1[0-2])$/.test(targetMonth) &&
        targetMonth !== month.value
    ) {
        month.value = targetMonth
        await load()
    }
    if (!entryId) return
    await nextTick()
    globalThis.document.getElementById(`entry-${entryId}`)?.scrollIntoView({
        block: 'center',
    })
    window.location.hash = `entry-${entryId}`
}

async function saveMonthCost(): Promise<void> {
    const raw = monthCostYuan.value.trim()
    const cents = raw === '0' || raw === '0.00' ? 0 : centsFromYuan(raw)
    if (cents == null) return
    try {
        await financeApi.upsertMonthCost(month.value, cents)
        monthCostYuan.value = ''
        notice.value = financeCopy.monthCost
    } catch (error) {
        errorMessage.value = financeErrorMessage(error)
    }
}

watch(month, () => {
    void load()
})
onMounted(load)
</script>

<template>
    <section class="finance-page" aria-labelledby="finance-title">
        <PageHeader :title="financeCopy.title" heading-id="finance-title">
            <el-button text @click="month = shiftMonth(month, -1)">‹</el-button>
            <span class="month-label">{{ monthLabel(month) }}</span>
            <el-button text @click="month = shiftMonth(month, 1)">›</el-button>
            <el-button v-if="canWrite" text @click="drawerOpen = true">{{
                financeCopy.record
            }}</el-button>
            <template v-if="canWrite">
                <input
                    ref="fileInput"
                    class="sr-only"
                    type="file"
                    accept=".xlsx"
                    @change="importWorkbook"
                />
                <el-button text @click="fileInput?.click()">{{
                    financeCopy.importBatch
                }}</el-button>
            </template>
            <a
                v-if="canSeeLedger"
                class="export"
                :href="`/api/v1/finance/export?month=${month}`"
                >{{ financeCopy.export }}</a
            >
        </PageHeader>
        <InlineError :message="errorMessage || notice" />
        <p v-if="!canSeeLedger" class="staff-hint">
            {{ financeCopy.staffRestricted }}
        </p>
        <div v-if="canSeeLedger" v-loading="loading" class="summary">
            <div v-if="canSeeCompany" class="stat">
                <span>{{ financeCopy.companyCost }}</span>
                <strong class="tabular">{{ moneyLabel(companyCost) }}</strong>
            </div>
            <div class="stat">
                <span>{{ financeCopy.projectCost }}</span>
                <strong class="tabular">{{
                    moneyLabel(projectCostTotal)
                }}</strong>
            </div>
            <div v-if="canSeeCompany" class="stat">
                <span>{{ financeCopy.income }}</span>
                <strong class="tabular">{{ moneyLabel(companyIncome) }}</strong>
            </div>
            <div v-if="canSeeCompany" class="stat">
                <span>{{ financeCopy.margin }}</span>
                <strong class="tabular">{{ moneyLabel(margin) }}</strong>
            </div>
        </div>
        <template v-if="canSeeLedger">
            <p v-for="item in alerts" :key="item.project_id" class="alert-line">
                {{ item.message }}
            </p>
            <el-table
                :data="entries"
                class="plain-table"
                :empty-text="financeCopy.empty"
            >
                <el-table-column :label="financeCopy.date" min-width="90">
                    <template #default="{ row }">
                        <DateText :value="row.occurred_on" format="short" />
                    </template>
                </el-table-column>
                <el-table-column :label="financeCopy.kind" min-width="80">
                    <template #default="{ row }">{{
                        FINANCE_KIND_LABEL[row.kind as FinanceKind]
                    }}</template>
                </el-table-column>
                <el-table-column :label="financeCopy.scope" min-width="120">
                    <template #default="{ row }">
                        {{
                            row.project_name ||
                            FINANCE_SCOPE_LABEL[row.scope as FinanceScope]
                        }}
                    </template>
                </el-table-column>
                <el-table-column
                    prop="category"
                    :label="financeCopy.category"
                />
                <el-table-column prop="memo" :label="financeCopy.memo" />
                <el-table-column :label="financeCopy.amount" align="right">
                    <template #default="{ row }">
                        <Money :cents="row.amount_cents" />
                    </template>
                </el-table-column>
                <el-table-column :label="financeCopy.paidTotal" align="right">
                    <template #default="{ row }">
                        <Money :cents="row.paid_total_cents ?? 0" />
                    </template>
                </el-table-column>
                <el-table-column :label="financeCopy.unpaidTotal" align="right">
                    <template #default="{ row }">
                        <Money :cents="row.unpaid_cents ?? 0" />
                    </template>
                </el-table-column>
                <el-table-column
                    v-if="canWrite"
                    :label="financeCopy.visibility"
                    width="90"
                >
                    <template #default="{ row }">{{
                        VISIBILITY_LABEL[row.visibility as FinanceVisibility]
                    }}</template>
                </el-table-column>
                <el-table-column prop="id" width="0">
                    <template #default="{ row }">
                        <span :id="`entry-${row.id}`" class="sr-only"></span>
                    </template>
                </el-table-column>
                <el-table-column v-if="canWrite" width="140">
                    <template #default="{ row }">
                        <el-button text @click="beginAdjust(row)">{{
                            financeCopy.adjust
                        }}</el-button>
                        <el-button
                            v-if="
                                row.kind === 'COST' &&
                                (row.unpaid_cents ?? 0) > 0
                            "
                            text
                            :disabled="payBusy"
                            @click="beginPay(row)"
                            >{{ financeCopy.markPaid }}</el-button
                        >
                    </template>
                </el-table-column>
            </el-table>
            <section
                v-if="
                    importRows.length ||
                    importInserted.length ||
                    processedImportRows.length
                "
                class="import-review"
            >
                <p v-if="processedImportRows.length">
                    {{ financeCopy.importSuccess }}
                    {{ processedImportRows.length }}
                </p>
                <ul v-if="processedImportRows.length">
                    <li
                        v-for="(item, index) in processedImportRows"
                        :key="
                            isRecord(item)
                                ? `${String(item.batch_key || '')}-${String(item.row_index ?? index)}-${String(item.entry_id || item.id || index)}`
                                : index
                        "
                    >
                        <template
                            v-if="isRecord(item) && (item.id || item.entry_id)"
                        >
                            <a
                                href="#"
                                @click.prevent="openImportedEntry(item)"
                                >{{ financeCopy.importOpenEntry }}</a
                            >
                            · {{ rowLabel(item) }}
                        </template>
                        <template v-else>{{ rowLabel(item) }}</template>
                    </li>
                </ul>
                <p
                    v-if="
                        processedInsertedOffset < processedInsertedTotal ||
                        processedLinkedOffset < processedLinkedTotal
                    "
                    class="muted"
                >
                    {{ financeCopy.processedSources }}
                    {{ processedInsertedTotal + processedLinkedTotal }}
                    <el-button
                        text
                        :disabled="processedLoading"
                        :loading="processedLoading"
                        @click="loadMoreProcessed"
                        >{{ financeCopy.importMoreProcessed }}</el-button
                    >
                </p>
                <ul v-if="importRows.length">
                    <li>{{ financeCopy.importPending }}</li>
                    <li v-for="(item, index) in importRows" :key="index">
                        <p>{{ rowLabel(item) }}</p>
                        <p
                            v-if="
                                isRecord(item) &&
                                (item.memo ||
                                    item.voucher ||
                                    item.candidate_memo)
                            "
                        >
                            {{ item.memo || item.payload }}
                            <span v-if="isRecord(item) && item.voucher">
                                · {{ item.voucher }}
                            </span>
                            <span v-if="isRecord(item) && item.candidate_memo">
                                · {{ financeCopy.candidateMemo }}
                                {{ item.candidate_memo }}
                            </span>
                            <span
                                v-if="isRecord(item) && item.candidate_voucher"
                            >
                                · {{ financeCopy.candidateVoucher }}
                                {{ item.candidate_voucher }}
                            </span>
                        </p>
                        <template v-if="candidateIds(item).length > 1">
                            <el-button
                                v-for="candidateId in candidateIds(item)"
                                :key="candidateId"
                                text
                                :disabled="Boolean(resolvingImportKey)"
                                @click="
                                    resolveImport(
                                        item,
                                        'link_voucher',
                                        candidateId,
                                    )
                                "
                                >{{ financeCopy.confirmCandidate }}
                                {{ candidateId.slice(0, 8) }}</el-button
                            >
                        </template>
                        <el-button
                            v-else-if="
                                isRecord(item) &&
                                (item.reason === 'DUPLICATE_CANDIDATE' ||
                                    item.candidate_entry_id)
                            "
                            text
                            :disabled="Boolean(resolvingImportKey)"
                            @click="resolveImport(item, 'link_voucher')"
                            >{{ financeCopy.confirmSame }}</el-button
                        >
                        <el-button
                            v-if="
                                isRecord(item) &&
                                (item.reason === 'DUPLICATE_CANDIDATE' ||
                                    item.reason === 'PROJECT_UNRESOLVED')
                            "
                            text
                            :disabled="Boolean(resolvingImportKey)"
                            @click="resolveImport(item, 'insert_independent')"
                            >{{ financeCopy.insertIndependent }}</el-button
                        >
                        <el-button
                            v-if="
                                isRecord(item) &&
                                item.reason === 'ROW_INCOMPLETE'
                            "
                            text
                            @click="retryIncomplete(item)"
                            >{{ financeCopy.retryOriginalBatch }}</el-button
                        >
                    </li>
                </ul>
                <p v-if="importRowTotal > importRows.length" class="muted">
                    {{ financeCopy.importPending }} {{ importRowTotal }}
                    <el-button
                        v-if="importRowOffset + importPageSize < importRowTotal"
                        text
                        @click="loadMoreImportRows"
                        >{{ financeCopy.importMore }}</el-button
                    >
                </p>
            </section>
            <form
                v-if="canWrite"
                class="month-cost"
                @submit.prevent="saveMonthCost"
            >
                <label>{{ financeCopy.monthCost }}</label>
                <el-input v-model="monthCostYuan" />
                <el-button native-type="submit" text>{{
                    financeCopy.saveMonth
                }}</el-button>
            </form>
            <section v-if="summary?.projects.length" class="pivot">
                <h2>{{ financeCopy.pivot }}</h2>
                <el-table :data="summary.projects" class="plain-table">
                    <el-table-column
                        prop="project_name"
                        :label="financeCopy.project"
                    />
                    <el-table-column
                        :label="financeCopy.projectCost"
                        align="right"
                    >
                        <template #default="{ row }">
                            <Money :cents="row.cost_cents" />
                        </template>
                    </el-table-column>
                    <el-table-column
                        v-if="canSeeCompany"
                        :label="financeCopy.income"
                        align="right"
                    >
                        <template #default="{ row }">
                            <Money :cents="row.income_cents ?? 0" />
                        </template>
                    </el-table-column>
                </el-table>
            </section>
        </template>
        <el-drawer
            v-model="drawerOpen"
            :title="financeCopy.record"
            size="400px"
        >
            <form class="drawer-form" @submit.prevent="createEntry">
                <label>
                    {{ financeCopy.kind }}
                    <el-select v-model="kind">
                        <el-option
                            :label="FINANCE_KIND_LABEL.COST"
                            value="COST"
                        />
                        <el-option
                            :label="FINANCE_KIND_LABEL.INCOME"
                            value="INCOME"
                        />
                    </el-select>
                </label>
                <label>
                    {{ financeCopy.scope }}
                    <el-select v-model="scope">
                        <el-option
                            :label="FINANCE_SCOPE_LABEL.COMPANY"
                            value="COMPANY"
                        />
                        <el-option
                            :label="FINANCE_SCOPE_LABEL.PROJECT"
                            value="PROJECT"
                        />
                    </el-select>
                </label>
                <label v-if="scope === 'PROJECT'">
                    {{ financeCopy.project }}
                    <el-select v-model="projectId">
                        <el-option
                            v-for="project in projects"
                            :key="project.id"
                            :label="project.name"
                            :value="project.id"
                        />
                    </el-select>
                </label>
                <label for="amount-yuan">{{ financeCopy.amountYuan }}</label>
                <el-input id="amount-yuan" v-model="amountYuan" />
                <label for="occurred-on">{{ financeCopy.date }}</label>
                <el-date-picker
                    id="occurred-on"
                    v-model="occurredOn"
                    type="date"
                    value-format="YYYY-MM-DD"
                />
                <label for="finance-category">{{ financeCopy.category }}</label>
                <el-input id="finance-category" v-model="category" />
                <label>
                    {{ financeCopy.memo }}
                    <el-input v-model="memo" />
                </label>
                <label>
                    {{ financeCopy.visibility }}
                    <el-select v-model="visibility">
                        <el-option
                            :label="financeCopy.defaultVisibility"
                            value=""
                        />
                        <el-option :label="VISIBILITY_LABEL.ALL" value="ALL" />
                        <el-option
                            :label="VISIBILITY_LABEL.MANAGEMENT"
                            value="MANAGEMENT"
                        />
                        <el-option
                            :label="VISIBILITY_LABEL.OWNER_ONLY"
                            value="OWNER_ONLY"
                        />
                    </el-select>
                </label>
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="saving"
                    :disabled="saving"
                    >{{ financeCopy.save }}</el-button
                >
            </form>
        </el-drawer>
        <el-drawer
            v-model="adjustOpen"
            :title="financeCopy.adjust"
            size="400px"
        >
            <form class="drawer-form" @submit.prevent="submitAdjustment">
                <label>
                    {{ financeCopy.adjust }}
                    <el-select v-model="adjustField">
                        <el-option
                            :label="financeCopy.amount"
                            value="amount_cents"
                        />
                        <el-option
                            :label="financeCopy.date"
                            value="occurred_on"
                        />
                        <el-option
                            :label="financeCopy.category"
                            value="category"
                        />
                        <el-option :label="financeCopy.memo" value="memo" />
                        <el-option
                            :label="FIELD_LABEL.visibility"
                            value="visibility"
                        />
                    </el-select>
                </label>
                <el-input
                    v-model="adjustValue"
                    :aria-label="financeCopy.adjustValue"
                />
                <el-input
                    v-model="adjustReason"
                    :placeholder="financeCopy.reason"
                    :aria-label="financeCopy.adjustReason"
                />
                <el-button native-type="submit" type="primary">{{
                    financeCopy.save
                }}</el-button>
            </form>
        </el-drawer>
        <el-dialog
            v-model="payOpen"
            :title="financeCopy.markPaid"
            width="360px"
            :close-on-click-modal="false"
        >
            <label>{{ financeCopy.payDate }}</label>
            <el-date-picker
                v-model="payOn"
                type="date"
                value-format="YYYY-MM-DD"
                :disabled="payRetrying"
            />
            <label>{{ financeCopy.payAmount }}</label>
            <el-input v-model="payYuan" :disabled="payRetrying" />
            <p v-if="payRetrying" class="muted">
                {{ financeCopy.payRetryHint }}
            </p>
            <template #footer>
                <el-button @click="payOpen = false">{{
                    financeCopy.payClose
                }}</el-button>
                <el-button v-if="payRetrying" text @click="startFreshPay">{{
                    financeCopy.payNew
                }}</el-button>
                <el-button
                    type="primary"
                    :loading="payBusy"
                    @click="confirmPay"
                    >{{ financeCopy.markPaid }}</el-button
                >
            </template>
        </el-dialog>
    </section>
</template>

<style scoped>
.summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 24px;
    margin-bottom: 16px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--sb-line);
}
.stat {
    display: grid;
    gap: 6px;
}
.stat span {
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
.stat strong {
    font-size: var(--sb-xl);
    font-weight: 600;
    letter-spacing: -0.01em;
}
.month-label {
    min-width: 7em;
    text-align: center;
}
.export {
    font-size: var(--sb-sm);
}
.alert-line {
    color: var(--sb-warn);
    font-size: var(--sb-sm);
}
.plain-table {
    width: 100%;
}
.muted {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
.import-review {
    margin: 16px 0;
}
.pivot {
    margin-top: 48px;
}
.pivot h2 {
    font-size: var(--sb-lg);
    margin-bottom: 16px;
}
.month-cost {
    display: flex;
    gap: 12px;
    align-items: center;
    margin: 16px 0;
}
.drawer-form {
    display: grid;
    gap: 12px;
}
@media print {
    .summary strong {
        color: #000;
    }
}
</style>
