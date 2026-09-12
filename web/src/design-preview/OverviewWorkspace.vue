<script setup lang="ts">
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import { money, operatingMonths, projects } from './previewState'

const emit = defineEmits<{
    navigate: [view: 'projects' | 'finance' | 'map']
    ask: [prompt: string]
}>()
const period = ref('six')
const chart = ref('profit')
const selectedMonth = ref(5)
const months = computed(() =>
    period.value === 'six' ? operatingMonths.slice(-6) : operatingMonths,
)
const selected = computed(
    () => months.value[Math.min(selectedMonth.value, months.value.length - 1)],
)
const current = computed(() => months.value[months.value.length - 1])
const totalRevenue = computed(() =>
    months.value.reduce((sum, row) => sum + row.revenue, 0),
)
const totalGross = computed(() =>
    months.value.reduce((sum, row) => sum + row.gross, 0),
)
const totalFixed = computed(() =>
    months.value.reduce((sum, row) => sum + row.fixed, 0),
)
const netFlow = computed(() =>
    months.value.reduce((sum, row) => sum + row.received - row.paid, 0),
)
const max = computed(() => (chart.value === 'profit' ? 50000 : 80000))
const ticks = computed(() =>
    chart.value === 'profit'
        ? [0, 10000, 20000, 30000, 40000, 50000]
        : [0, 20000, 40000, 60000, 80000],
)
const x = (index: number) =>
    62 + index * (644 / Math.max(months.value.length - 1, 1))
const y = (value: number) => 226 - (value / max.value) * 188
const first = (row: (typeof operatingMonths)[number]) =>
    chart.value === 'profit' ? row.gross : row.received
const second = (row: (typeof operatingMonths)[number]) =>
    chart.value === 'profit' ? row.fixed : row.paid
const line = computed(() =>
    months.value
        .map((row, index) => `${index ? 'L' : 'M'}${x(index)},${y(first(row))}`)
        .join(' '),
)
const area = computed(
    () => `${line.value} L${x(months.value.length - 1)},226 L62,226 Z`,
)
const secondLine = computed(() =>
    months.value
        .map(
            (row, index) => `${index ? 'L' : 'M'}${x(index)},${y(second(row))}`,
        )
        .join(' '),
)
const businessSplit = ref('revenue')
</script>

<template>
    <section class="overview-workspace">
        <header class="sd-page-heading">
            <div>
                <h1>经营总览</h1>
            </div>
            <label class="period-control"
                ><Icon name="calendar" :size="16" /><select
                    v-model="period"
                    aria-label="经营统计期间"
                >
                    <option value="six">近 6 个月 · 2026</option>
                    <option value="year">今年至今 · 2026</option>
                </select></label
            >
        </header>

        <div class="overview-metrics sd-panel">
            <div>
                <span>完结项目服务费</span
                ><strong class="sd-number"
                    ><small>¥</small>{{ money(totalRevenue) }}</strong
                >
                <p>按服务完结月份统计</p>
            </div>
            <div>
                <span>完结项目完整毛利</span
                ><strong class="sd-number"
                    ><small>¥</small>{{ money(totalGross) }}</strong
                >
                <p>已计直接成本、节余奖金及抽成</p>
            </div>
            <div>
                <span>扣除固定开支后</span
                ><strong class="sd-number"
                    ><small>¥</small
                    >{{ money(totalGross - totalFixed) }}</strong
                >
                <p>同期公司固定开支 ¥{{ money(totalFixed) }}</p>
            </div>
            <div>
                <span>累计现金净流入</span
                ><strong class="sd-number"
                    ><small>¥</small>{{ money(netFlow) }}</strong
                >
                <p>实际收减支 · 非账户余额</p>
            </div>
        </div>

        <div class="overview-main">
            <section class="sd-panel curve-panel">
                <div class="sd-section-title">
                    <div>
                        <h2>月度经营趋势</h2>
                    </div>
                    <div class="chart-switch">
                        <button
                            :class="{ active: chart === 'profit' }"
                            @click="chart = 'profit'"
                        >
                            经营成果</button
                        ><button
                            :class="{ active: chart === 'cash' }"
                            @click="chart = 'cash'"
                        >
                            资金收支
                        </button>
                    </div>
                </div>
                <div class="chart-legend">
                    <span
                        ><i></i
                        >{{
                            chart === 'profit' ? '完结项目完整毛利' : '实际收款'
                        }}</span
                    ><span
                        ><i class="secondary"></i
                        >{{
                            chart === 'profit' ? '公司固定开支' : '实际付款'
                        }}</span
                    ><small>单位：元</small>
                </div>
                <svg
                    class="operating-chart"
                    viewBox="0 0 760 270"
                    role="img"
                    :aria-label="
                        chart === 'profit'
                            ? '月度完结项目毛利与固定开支曲线，单位元'
                            : '月度实际收款与付款曲线，单位元'
                    "
                >
                    <g v-for="tick in ticks" :key="tick">
                        <line
                            x1="62"
                            x2="706"
                            :y1="y(tick)"
                            :y2="y(tick)"
                            stroke="var(--sd-line)"
                            stroke-dasharray="3 5"
                        />
                        <text x="49" :y="y(tick) + 4" text-anchor="end">
                            {{ tick ? `${tick / 10000}万` : '0' }}
                        </text>
                    </g>
                    <path :d="area" fill="var(--sd-soft)" />
                    <path
                        :d="secondLine"
                        fill="none"
                        stroke="#94a3b8"
                        stroke-width="2"
                        stroke-dasharray="5 5"
                    />
                    <path
                        :d="line"
                        fill="none"
                        stroke="var(--sd-accent)"
                        stroke-width="2.8"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                    />
                    <g
                        v-for="(row, index) in months"
                        :key="row.month"
                        tabindex="0"
                        role="button"
                        :aria-label="`查看${row.month}，${chart === 'profit' ? '毛利' : '收款'}${money(first(row))}元`"
                        @click="selectedMonth = index"
                        @keydown.enter="selectedMonth = index"
                        @keydown.space.prevent="selectedMonth = index"
                    >
                        <circle
                            :cx="x(index)"
                            :cy="y(first(row))"
                            r="14"
                            fill="transparent"
                        />
                        <circle
                            :cx="x(index)"
                            :cy="y(first(row))"
                            :r="index === selectedMonth ? 5.5 : 3.2"
                            fill="white"
                            stroke="var(--sd-accent)"
                            stroke-width="2"
                        />
                        <text :x="x(index)" y="254" text-anchor="middle">
                            {{ row.month }}
                        </text>
                    </g>
                </svg>
                <div class="chart-detail">
                    <span
                        ><b>{{ selected?.month }}</b> ·
                        {{
                            chart === 'profit'
                                ? '按完结项目统计'
                                : '按实际收付日期统计'
                        }}</span
                    ><strong
                        >{{ chart === 'profit' ? '完整毛利' : '实际收款' }} ¥{{
                            money(selected ? first(selected) : 0)
                        }}</strong
                    ><span
                        >{{ chart === 'profit' ? '固定开支' : '实际付款' }} ¥{{
                            money(selected ? second(selected) : 0)
                        }}</span
                    >
                </div>
            </section>

            <aside class="sd-panel outlook-panel">
                <div class="sd-section-title">
                    <h2>在手项目</h2>
                    <Icon name="arrow-right" :size="17" />
                </div>
                <p class="outlook-label">已确认委托 · 执行中</p>
                <div class="outlook-total">
                    <strong>{{
                        projects.filter((p) => p.status === 'active').length
                    }}</strong
                    ><span>个项目</span>
                </div>
                <div class="outlook-fee">
                    <span>约定服务费合计</span
                    ><b
                        >¥{{
                            money(
                                projects
                                    .filter((p) => p.status === 'active')
                                    .reduce((s, p) => s + p.fee, 0),
                            )
                        }}</b
                    >
                </div>
                <div
                    class="outlook-project"
                    v-for="project in projects.filter(
                        (p) => p.status === 'active',
                    )"
                    :key="project.id"
                >
                    <span><i></i>{{ project.name }}</span
                    ><small
                        >{{
                            project.end.slice(5).replace('-', '.')
                        }}
                        预计完结</small
                    >
                </div>
                <button class="sd-button" @click="emit('navigate', 'projects')">
                    查看在手项目 <Icon name="arrow-right" :size="15" />
                </button>
                <div class="outlook-note">
                    <Icon name="info" :size="16" />
                    <p>未确认回款不计入收款预测。</p>
                </div>
            </aside>
        </div>

        <div class="overview-lower">
            <section class="sd-panel business-panel">
                <div class="sd-section-title">
                    <div>
                        <h2>业务结构</h2>
                        <p>
                            {{ period === 'six' ? '近6个月' : '今年至今' }} ·
                            完结项目
                        </p>
                    </div>
                    <select
                        v-model="businessSplit"
                        aria-label="业务结构统计口径"
                    >
                        <option value="revenue">按服务费</option>
                        <option value="gross">按完整毛利</option>
                    </select>
                </div>
                <div class="business-bar"><span></span><span></span></div>
                <div class="business-line">
                    <span><i></i>业主大会会务协助</span><strong>82%</strong
                    ><b
                        >¥{{
                            money(
                                (businessSplit === 'gross'
                                    ? totalGross
                                    : totalRevenue) * 0.82,
                            )
                        }}</b
                    >
                </div>
                <div class="business-line">
                    <span><i class="tender"></i>招投标代理</span
                    ><strong>18%</strong
                    ><b
                        >¥{{
                            money(
                                (businessSplit === 'gross'
                                    ? totalGross
                                    : totalRevenue) * 0.18,
                            )
                        }}</b
                    >
                </div>
            </section>
            <section class="sd-panel cash-panel">
                <div class="sd-section-title">
                    <div>
                        <h2>待收待付</h2>
                        <p>截至 2026.09.10</p>
                    </div>
                    <Icon name="wallet" :size="19" />
                </div>
                <div class="cash-line">
                    <span>云栖里 · 待回款</span><strong>¥18,000</strong
                    ><em class="sd-pill sd-pill--amber">部分到账</em>
                </div>
                <div class="cash-line">
                    <span>已核定 · 待结算奖励</span><strong>¥1,700</strong
                    ><em class="sd-pill">09.20 发薪日</em>
                </div>
                <div class="cash-note">
                    收付与毛利分别统计，付款不重复计成本。
                </div>
            </section>
        </div>
        <p class="overview-source">示例经营数据 · 统计至{{ current?.month }}</p>
    </section>
</template>

<style scoped>
.period-control {
    display: flex;
    align-items: center;
    gap: 9px;
    border: 1px solid var(--sd-line);
    background: var(--sd-surface);
    padding: 10px 12px;
    border-radius: 6px;
    font-size: 12px;
}
.period-control select,
.business-panel select {
    border: 0;
    background: transparent;
    color: var(--sd-muted);
    cursor: pointer;
}
.overview-metrics {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    margin-bottom: 16px;
    padding: 16px 0;
}
.overview-metrics > div {
    padding: 0 16px;
    border-right: 1px solid var(--sd-line);
}
.overview-metrics > div:last-child {
    border: 0;
}
.overview-metrics span {
    color: var(--sd-muted);
    font-size: 11px;
}
.overview-metrics strong {
    display: block;
    font-size: 24px;
    font-weight: 550;
    margin: 12px 0 8px;
}
.overview-metrics strong small {
    font-size: 16px;
    font-weight: 400;
    margin-right: 5px;
    color: var(--sd-muted);
}
.overview-metrics p {
    font-size: 10px;
    color: var(--sd-muted);
    line-height: 1.6;
}
.overview-main {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 290px;
    gap: 16px;
}
.curve-panel {
    padding: 16px;
}
.chart-switch {
    display: flex;
    padding: 3px;
    border-radius: 6px;
    background: var(--sd-bg);
}
.chart-switch button {
    border: 0;
    background: transparent;
    padding: 7px 9px;
    border-radius: 4px;
    font-size: 11px;
    color: var(--sd-muted);
}
.chart-switch button.active {
    background: var(--sd-surface);
    color: var(--sd-accent);
}
.chart-legend {
    display: flex;
    gap: 16px;
    font-size: 10px;
    color: var(--sd-muted);
    margin-top: 16px;
}
.chart-legend span {
    display: flex;
    align-items: center;
    gap: 6px;
}
.chart-legend i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--sd-accent);
}
.chart-legend i.secondary {
    background: #94a3b8;
}
.chart-legend small {
    margin-left: auto;
    font-size: 9px;
}
.operating-chart {
    width: 100%;
    min-height: 220px;
    margin-top: 3px;
}
.operating-chart text {
    font:
        10px 'Inter',
        'Microsoft YaHei',
        sans-serif;
    fill: var(--sd-muted);
}
.operating-chart g[role='button'] {
    cursor: pointer;
}
.chart-detail {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    padding-top: 14px;
    border-top: 1px solid var(--sd-line);
    color: var(--sd-muted);
    font-size: 10px;
}
.chart-detail strong {
    color: var(--sd-accent);
    font-weight: 500;
}
.outlook-panel {
    padding: 16px;
}
.outlook-label {
    color: var(--sd-muted);
    font-size: 11px;
    margin-top: 16px;
}
.outlook-total {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin: 10px 0 17px;
}
.outlook-total strong {
    font-size: 32px;
    line-height: 1;
    font-weight: 500;
}
.outlook-total span {
    color: var(--sd-muted);
    font-size: 11px;
}
.outlook-fee {
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: var(--sd-muted);
    padding-bottom: 17px;
    border-bottom: 1px solid var(--sd-line);
    margin-bottom: 6px;
}
.outlook-fee b {
    font-size: 13px;
    color: var(--sd-accent);
    font-weight: 500;
}
.outlook-project {
    display: flex;
    justify-content: space-between;
    gap: 5px;
    padding: 12px 0;
    font-size: 11px;
}
.outlook-project i {
    display: inline-block;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--sd-accent);
    margin-right: 8px;
}
.outlook-project small {
    color: var(--sd-muted);
    font-size: 10px;
}
.outlook-panel > button {
    width: 100%;
    margin-top: 13px;
}
.outlook-note {
    display: flex;
    align-items: flex-start;
    gap: 7px;
    margin-top: 16px;
    color: var(--sd-muted);
}
.outlook-note svg {
    flex-shrink: 0;
    margin-top: 1px;
}
.outlook-note p {
    font-size: 10px;
    line-height: 1.8;
}
.overview-lower {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-top: 16px;
}
.business-panel,
.cash-panel {
    padding: 16px;
}
.business-panel select {
    font-size: 11px;
}
.business-bar {
    display: flex;
    height: 10px;
    gap: 3px;
    margin: 16px 0;
}
.business-bar span:first-child {
    width: 82%;
    background: var(--sd-accent);
    border-radius: 3px 0 0 3px;
}
.business-bar span:last-child {
    width: 18%;
    background: #94a3b8;
    border-radius: 0 3px 3px 0;
}
.business-line {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 9px 0;
    font-size: 11px;
}
.business-line span {
    flex: 1;
    color: var(--sd-muted);
}
.business-line i {
    display: inline-block;
    width: 7px;
    height: 7px;
    background: var(--sd-accent);
    margin-right: 8px;
    border-radius: 2px;
}
.business-line i.tender {
    background: #94a3b8;
}
.business-line b {
    font-weight: 500;
    min-width: 83px;
    text-align: right;
}
.business-line strong {
    font-size: 10px;
    color: var(--sd-muted);
    font-weight: 400;
}
.cash-line {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    padding: 16px 0;
    border-bottom: 1px solid var(--sd-line);
    font-size: 11px;
}
.cash-line span {
    flex: 1;
}
.cash-line strong {
    font-size: 16px;
    font-weight: 500;
}
.cash-line em {
    font-style: normal;
    min-width: 82px;
    justify-content: center;
}
.cash-note,
.overview-source {
    font-size: 10px;
    line-height: 1.8;
    color: var(--sd-muted);
    margin-top: 15px;
}
.overview-source {
    text-align: right;
}
@media (max-width: 1150px) {
    .overview-main {
        grid-template-columns: minmax(0, 1fr) 260px;
        gap: 16px;
    }
    .overview-metrics > div {
        padding: 0 16px;
    }
    .overview-metrics strong {
        font-size: 24px;
    }
    .curve-panel {
        padding: 16px;
    }
}
@media (max-width: 950px) {
    .overview-main {
        grid-template-columns: 1fr;
    }
    .overview-lower {
        gap: 16px;
    }
    .chart-switch {
        flex-shrink: 0;
    }
}
@media (max-width: 650px) {
    .overview-metrics {
        grid-template-columns: 1fr 1fr;
        gap: 16px 0;
    }
    .overview-metrics > div:nth-child(2) {
        border: 0;
    }
    .overview-lower {
        grid-template-columns: 1fr;
    }
    .curve-panel .sd-section-title {
        align-items: flex-start;
        flex-wrap: wrap;
    }
    .chart-legend {
        flex-wrap: wrap;
        gap: 10px;
    }
}

.overview-workspace .sd-panel {
    border-radius: 6px;
}
</style>
