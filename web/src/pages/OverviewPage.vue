<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { financeApi, type FinanceOverview } from '../api/finance'
import { placeholderApi } from '../api/placeholder'
import { moneyLabel } from '../api/parse'
import InlineError from '../components/ui/InlineError.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import { shellCopy } from '../copy/pages/shell'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const canSeed = computed(() => auth.user?.role === 'OWNER')
const errorMessage = ref('')
const notice = ref('')
const data = ref<FinanceOverview>()
const seeding = ref(false)

async function load(): Promise<void> {
    data.value = await financeApi.overview()
}

async function seed(): Promise<void> {
    if (!canSeed.value || seeding.value) return
    seeding.value = true
    try {
        await placeholderApi.seed()
        notice.value = shellCopy.placeholder
        await load()
    } catch {
        errorMessage.value = '占位数据写入失败。'
    } finally {
        seeding.value = false
    }
}

onMounted(async () => {
    try {
        await load()
    } catch {
        errorMessage.value = '经营总览加载失败。'
    }
})
</script>

<template>
    <section aria-labelledby="overview-title">
        <PageHeader :title="shellCopy.overview" heading-id="overview-title">
            <el-button v-if="canSeed" text :loading="seeding" @click="seed">{{
                shellCopy.seedPlaceholder
            }}</el-button>
        </PageHeader>
        <InlineError :message="errorMessage || notice" />
        <p v-if="data && data.placeholder" class="hint">
            {{ shellCopy.placeholder }}
        </p>
        <p v-else-if="data && !data.has_opening_balance" class="hint">
            未提供期初余额，下面的净流入不是银行可用余额。
        </p>
        <dl v-if="data" class="facts">
            <div>
                <dt>在执行</dt>
                <dd>{{ data.active_count }}</dd>
            </div>
            <div>
                <dt>已完结</dt>
                <dd>{{ data.completed_count }}</dd>
            </div>
            <div>
                <dt>完结项目服务费</dt>
                <dd>{{ moneyLabel(data.completed_fee_cents) }}</dd>
            </div>
            <div>
                <dt>完结项目毛利</dt>
                <dd>{{ moneyLabel(data.completed_gross_cents) }}</dd>
            </div>
            <div>
                <dt>累计收款</dt>
                <dd>{{ moneyLabel(data.received_cents) }}</dd>
            </div>
            <div>
                <dt>累计付款</dt>
                <dd>{{ moneyLabel(data.paid_cents) }}</dd>
            </div>
            <div>
                <dt>未付费用</dt>
                <dd>{{ moneyLabel(data.unpaid_cents) }}</dd>
            </div>
            <div>
                <dt>累计净流入</dt>
                <dd>{{ moneyLabel(data.net_inflow_cents) }}</dd>
            </div>
            <div v-if="data.has_opening_balance">
                <dt>期初余额{{ data.placeholder ? '（占位）' : '' }}</dt>
                <dd>{{ moneyLabel(data.opening_balance_cents || 0) }}</dd>
            </div>
            <div v-if="data.available_cents != null">
                <dt>占位可用资金</dt>
                <dd>{{ moneyLabel(data.available_cents) }}</dd>
            </div>
            <div>
                <dt>已确定委托</dt>
                <dd>{{ data.pipeline.commissioned }}</dd>
            </div>
            <div>
                <dt>在谈机会</dt>
                <dd>{{ data.pipeline.talking }}</dd>
            </div>
        </dl>
        <section v-if="data?.months.length" class="block">
            <h2>按月收付</h2>
            <ul>
                <li v-for="item in data.months" :key="item.month">
                    {{ item.month }} · 投入 {{ moneyLabel(item.cost_cents) }} ·
                    收款 {{ moneyLabel(item.income_cents) }} · 固定开支
                    {{ moneyLabel(item.company_fixed_cents) }}
                </li>
            </ul>
        </section>
        <section v-if="data?.receivables.length" class="block">
            <h2>待回款</h2>
            <ul>
                <li v-for="item in data.receivables" :key="item.project_id">
                    {{ item.project_name }} · 待收
                    {{ moneyLabel(item.outstanding_cents) }}
                </li>
            </ul>
        </section>
        <section v-if="data?.pending_rewards.length" class="block">
            <h2>待发奖励</h2>
            <ul>
                <li v-for="item in data.pending_rewards" :key="item.project_id">
                    {{ item.project_name }} · 节余
                    {{ moneyLabel(item.surplus_bonus_cents) }} · 抽成
                    {{ moneyLabel(item.pool_pay_cents) }}
                    <span v-if="item.payroll_on"> · {{ item.payroll_on }}</span>
                    <span v-else> · 20日当天到账批次待定</span>
                </li>
            </ul>
        </section>
    </section>
</template>

<style scoped>
.hint {
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
.facts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px;
}
.facts div {
    border-bottom: 1px solid var(--sb-line);
    padding-bottom: 8px;
}
dt {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
dd {
    margin: 0;
    font-variant-numeric: tabular-nums;
}
.block {
    margin-top: 32px;
}
.block h2 {
    font-size: var(--sb-lg);
    margin-bottom: 12px;
}
.block ul {
    list-style: none;
    margin: 0;
    padding: 0;
}
.block li {
    padding: 8px 0;
    border-bottom: 1px solid var(--sb-line);
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
</style>
