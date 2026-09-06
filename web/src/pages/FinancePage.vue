<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
    centsFromYuan,
    financeApi,
    financeErrorMessage,
    type FinanceEntry,
    type FinanceKind,
    type FinanceScope,
    type FinanceSummary,
    type FinanceVisibility,
} from '../api/finance'
import { moneyLabel } from '../api/parse'
import { errorCopy } from '../copy/errors'
import { projectsApi, type Project } from '../api/projects'
import DateText from '../components/ui/DateText.vue'
import EmptyLine from '../components/ui/EmptyLine.vue'
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

const companyCost = computed(() => summary.value?.company?.cost_cents ?? 0)
const companyIncome = computed(() => summary.value?.company?.income_cents ?? 0)
const projectCostTotal = computed(
    () =>
        summary.value?.projects.reduce(
            (sum, item) => sum + item.cost_cents,
            0,
        ) ?? 0,
)
const margin = computed(() => companyIncome.value - companyCost.value)

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        const [nextSummary, nextEntries, nextProjects, nextAlerts] =
            await Promise.all([
                financeApi.summary(month.value),
                financeApi.list(month.value),
                projectsApi.list(),
                financeApi.alerts().catch(() => []),
            ])
        summary.value = nextSummary
        entries.value = nextEntries
        projects.value = nextProjects
        alerts.value = nextAlerts
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
            <a class="export" :href="`/api/v1/finance/export?month=${month}`">{{
                financeCopy.export
            }}</a>
        </PageHeader>
        <InlineError :message="errorMessage" />
        <div v-loading="loading" class="summary">
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
        <p v-for="item in alerts" :key="item.project_id" class="alert-line">
            {{ item.message }}
        </p>
        <el-table :data="entries" class="plain-table">
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
            <el-table-column prop="category" :label="financeCopy.category" />
            <el-table-column prop="memo" :label="financeCopy.memo" />
            <el-table-column :label="financeCopy.amount" align="right">
                <template #default="{ row }">
                    <Money :cents="row.amount_cents" />
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
            <el-table-column v-if="canWrite" width="72">
                <template #default="{ row }">
                    <el-button text @click="beginAdjust(row)">{{
                        financeCopy.adjust
                    }}</el-button>
                </template>
            </el-table-column>
        </el-table>
        <EmptyLine v-if="!entries.length" :message="financeCopy.empty" />
        <section v-if="summary?.projects.length" class="pivot">
            <h2>{{ financeCopy.pivot }}</h2>
            <el-table :data="summary.projects" class="plain-table">
                <el-table-column
                    prop="project_name"
                    :label="financeCopy.project"
                />
                <el-table-column :label="financeCopy.projectCost" align="right">
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
.pivot {
    margin-top: 48px;
}
.pivot h2 {
    font-size: var(--sb-lg);
    margin-bottom: 16px;
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
