<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
    centsFromYuan,
    financeApi,
    financeErrorMessage,
    yuanFromCents,
    type FinanceEntry,
    type FinanceKind,
    type FinanceScope,
    type FinanceSummary,
    type FinanceVisibility,
} from '../api/finance'
import { projectsApi, type Project } from '../api/projects'
import { useAuthStore } from '../stores/auth'

const KIND_LABEL: Record<FinanceKind, string> = {
    COST: '成本',
    INCOME: '收入',
}
const SCOPE_LABEL: Record<FinanceScope, string> = {
    COMPANY: '公司',
    PROJECT: '项目',
}
const VISIBILITY_LABEL: Record<FinanceVisibility, string> = {
    ALL: '全员',
    MANAGEMENT: '管理层',
    OWNER_ONLY: '仅老板',
}

function currentMonth(): string {
    const now = new Date()
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

const auth = useAuthStore()
const canWrite = computed(() => auth.user?.role === 'OWNER')
const canSeeCompany = computed(() => auth.user?.role !== 'STAFF')
const month = ref(currentMonth())
const summary = ref<FinanceSummary>()
const entries = ref<FinanceEntry[]>([])
const projects = ref<Project[]>([])
const loading = ref(true)
const errorMessage = ref('')
const kind = ref<FinanceKind>('COST')
const scope = ref<FinanceScope>('COMPANY')
const projectId = ref('')
const amountYuan = ref('')
const occurredOn = ref(new Date().toISOString().slice(0, 10))
const category = ref('')
const memo = ref('')
const visibility = ref<FinanceVisibility | ''>('')
const saving = ref(false)
const adjustingId = ref('')
const adjustField = ref<
    'amount_cents' | 'occurred_on' | 'category' | 'memo' | 'visibility'
>('amount_cents')
const adjustValue = ref('')
const adjustReason = ref('')

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        const [nextSummary, nextEntries, nextProjects] = await Promise.all([
            financeApi.summary(month.value),
            financeApi.list(month.value),
            projectsApi.list(),
        ])
        summary.value = nextSummary
        entries.value = nextEntries
        projects.value = nextProjects
    } catch {
        errorMessage.value = '财务数据暂时无法加载，请稍后重试。'
    } finally {
        loading.value = false
    }
}

async function createEntry(): Promise<void> {
    const cents = centsFromYuan(amountYuan.value)
    const canonicalCategory = category.value.trim()
    if (!cents || !canonicalCategory) {
        errorMessage.value = '请填写金额和类别。'
        return
    }
    if (scope.value === 'PROJECT' && !projectId.value) {
        errorMessage.value = '项目成本需要选择项目。'
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
        await load()
    } catch (error) {
        errorMessage.value = financeErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function submitAdjustment(entry: FinanceEntry): Promise<void> {
    if (!adjustValue.value.trim() || !adjustReason.value.trim()) {
        errorMessage.value = '请填写调整后的值和原因。'
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
            errorMessage.value = '调整金额无效。'
            return
        }
        await financeApi.adjust(entry.id, {
            field: adjustField.value,
            new_value: newValue,
            reason: adjustReason.value.trim(),
        })
        adjustingId.value = ''
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
    adjustingId.value = entry.id
    adjustField.value = 'amount_cents'
    adjustValue.value = yuanFromCents(entry.amount_cents)
    adjustReason.value = ''
}

watch(month, () => {
    void load()
})
onMounted(load)
</script>

<template>
    <section class="finance-page" aria-labelledby="finance-title">
        <header>
            <p class="finance-page__eyebrow">
                {{ auth.user?.role || '工作台' }}
            </p>
            <h1 id="finance-title">财务</h1>
        </header>
        <label class="month-picker">
            月份
            <input v-model="month" type="month" />
        </label>
        <a class="export" :href="`/api/v1/finance/export?month=${month}`"
            >导出 CSV</a
        >
        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>
        <div v-loading="loading" class="summary-grid">
            <el-card v-if="canSeeCompany" shadow="never">
                <h2>公司</h2>
                <p>
                    成本
                    {{ yuanFromCents(summary?.company?.cost_cents ?? 0) }} 元
                </p>
                <p>
                    收入
                    {{ yuanFromCents(summary?.company?.income_cents ?? 0) }} 元
                </p>
                <p>
                    毛利
                    {{
                        yuanFromCents(
                            (summary?.company?.income_cents ?? 0) -
                                (summary?.company?.cost_cents ?? 0),
                        )
                    }}
                    元
                </p>
            </el-card>
            <el-card shadow="never">
                <h2>项目成本</h2>
                <p v-if="!summary?.projects.length">本月暂无项目成本</p>
                <p
                    v-for="item in summary?.projects ?? []"
                    :key="item.project_id"
                >
                    {{ item.project_name }}
                    {{ yuanFromCents(item.cost_cents) }} 元
                    <span v-if="item.income_cents !== undefined">
                        · 收入 {{ yuanFromCents(item.income_cents) }} 元
                    </span>
                </p>
            </el-card>
        </div>
        <el-card v-if="canWrite" shadow="never">
            <form class="entry-form" @submit.prevent="createEntry">
                <h2>录入</h2>
                <label>
                    类型
                    <select v-model="kind">
                        <option value="COST">成本</option>
                        <option value="INCOME">收入</option>
                    </select>
                </label>
                <label>
                    范围
                    <select v-model="scope">
                        <option value="COMPANY">公司</option>
                        <option value="PROJECT">项目</option>
                    </select>
                </label>
                <label v-if="scope === 'PROJECT'">
                    项目
                    <select v-model="projectId">
                        <option value="" disabled>选择项目</option>
                        <option
                            v-for="project in projects"
                            :key="project.id"
                            :value="project.id"
                        >
                            {{ project.name }}
                        </option>
                    </select>
                </label>
                <label for="amount-yuan">金额（元）</label>
                <el-input id="amount-yuan" v-model="amountYuan" />
                <label for="occurred-on">发生日</label>
                <input id="occurred-on" v-model="occurredOn" type="date" />
                <label for="finance-category">类别</label>
                <el-input id="finance-category" v-model="category" />
                <label>
                    备注
                    <el-input v-model="memo" />
                </label>
                <label>
                    可见范围
                    <select v-model="visibility">
                        <option value="">默认</option>
                        <option value="ALL">全员</option>
                        <option value="MANAGEMENT">管理层</option>
                        <option value="OWNER_ONLY">仅老板</option>
                    </select>
                </label>
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="saving"
                    :disabled="saving"
                    >保存</el-button
                >
            </form>
        </el-card>
        <el-card shadow="never">
            <h2>明细</h2>
            <ul class="entry-list">
                <li v-for="entry in entries" :key="entry.id">
                    <strong>{{ KIND_LABEL[entry.kind] }}</strong>
                    <span>{{ SCOPE_LABEL[entry.scope] }}</span>
                    <span v-if="entry.project_name">{{
                        entry.project_name
                    }}</span>
                    <span>{{ yuanFromCents(entry.amount_cents) }} 元</span>
                    <span>{{ entry.category }}</span>
                    <span>{{ entry.occurred_on }}</span>
                    <span>{{ VISIBILITY_LABEL[entry.visibility] }}</span>
                    <template v-if="canWrite">
                        <el-button text @click="beginAdjust(entry)"
                            >调整</el-button
                        >
                    </template>
                    <form
                        v-if="adjustingId === entry.id"
                        class="adjust-form"
                        @submit.prevent="submitAdjustment(entry)"
                    >
                        <select v-model="adjustField" aria-label="调整字段">
                            <option value="amount_cents">金额</option>
                            <option value="occurred_on">日期</option>
                            <option value="category">类别</option>
                            <option value="memo">备注</option>
                            <option value="visibility">可见范围</option>
                        </select>
                        <el-input
                            v-model="adjustValue"
                            aria-label="调整后的值"
                        />
                        <el-input
                            v-model="adjustReason"
                            aria-label="调整原因"
                        />
                        <el-button native-type="submit">确定调整</el-button>
                    </form>
                </li>
            </ul>
            <p v-if="!entries.length">本月暂无可见条目</p>
        </el-card>
    </section>
</template>

<style scoped>
.finance-page,
.summary-grid,
.entry-form,
.entry-list,
.adjust-form {
    display: grid;
    gap: 1rem;
}
.finance-page__eyebrow {
    margin: 0;
    color: #909399;
}
.month-picker,
.entry-form label {
    display: grid;
    gap: 6px;
    font-weight: 600;
}
.summary-grid {
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}
.entry-list {
    list-style: none;
    padding: 0;
    margin: 0;
}
.entry-list li {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
}
</style>
