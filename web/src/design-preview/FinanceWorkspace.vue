<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { projects, type PreviewRole } from './previewState'

const props = defineProps<{ role: PreviewRole }>()
const emit = defineEmits<{
    notify: [message: string]
    ask: [prompt: string]
}>()

type FinanceTab = 'projects' | 'imports' | 'rewards'
type Ledger = {
    cost: number
    received: number
    checked: boolean
    lastReceipt: string | null
}
type PaymentRecord = { date: string; reference: string; files: string[] }

const activeTab = ref<FinanceTab>('projects')
const tabs: { id: FinanceTab; label: string }[] = [
    { id: 'projects', label: '项目收支' },
    { id: 'imports', label: '财务导入' },
    { id: 'rewards', label: '奖励结算' },
]
const ledgers: Record<string, Ledger> = {
    p1: { cost: 4200, received: 12000, checked: false, lastReceipt: null },
    p2: {
        cost: 6100,
        received: 26000,
        checked: true,
        lastReceipt: '2026-09-08',
    },
    p3: {
        cost: 7200,
        received: 22000,
        checked: true,
        lastReceipt: '2026-08-20',
    },
    p4: {
        cost: 4600,
        received: 28000,
        checked: true,
        lastReceipt: '2026-08-12',
    },
}
const fees = computed<Record<string, number>>(() =>
    Object.fromEntries(projects.map((project) => [project.id, project.fee])),
)
const emptyLedger: Ledger = {
    cost: 0,
    received: 0,
    checked: false,
    lastReceipt: null,
}
const selectedId = ref('p1')
const selectedProject = computed(() =>
    projects.find((project) => project.id === selectedId.value),
)
const ledger = computed(() => ledgers[selectedId.value] ?? emptyLedger)
const fee = computed(() => fees.value[selectedId.value] ?? 0)
const costInput = ref<string | number>('4200')
watch(selectedId, () => {
    costInput.value = String(ledger.value.cost)
})
const costIsValid = computed(
    () =>
        String(costInput.value).trim() !== '' &&
        Number.isFinite(Number(costInput.value)) &&
        Number(costInput.value) >= 0,
)
const cost = computed(() =>
    costIsValid.value ? Math.round(Number(costInput.value) * 100) / 100 : 0,
)
const isTrial = computed(() => cost.value !== ledger.value.cost)

function calculate(amount: number, directCost: number) {
    const pool = amount * 0.2
    const initialShare = amount * 0.1
    const bonus = Math.max(pool - directCost, 0)
    const deduction = Math.min(Math.max(directCost - pool, 0), initialShare)
    const share = Math.max(initialShare - Math.max(directCost - pool, 0), 0)
    return {
        pool,
        initialShare,
        bonus,
        deduction,
        share,
        excess: Math.max(directCost - amount * 0.3, 0),
        reward: bonus + share,
        contribution: amount - directCost - bonus - share,
    }
}

const calculation = computed(() => calculate(fee.value, cost.value))
const money = (amount: number) =>
    new Intl.NumberFormat('zh-CN', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
    }).format(amount)
const totalFee = computed(() =>
    Object.values(fees.value).reduce((total, amount) => total + amount, 0),
)
const totalCost = computed(() =>
    Object.values(ledgers).reduce((total, item) => total + item.cost, 0),
)
const totalReceived = computed(() =>
    Object.values(ledgers).reduce((total, item) => total + item.received, 0),
)
const chartWidth = (amount: number) =>
    `${Math.min(100, (amount / (fee.value * 0.3 || 1)) * 100)}%`

function settlementDate(receipt: string | null) {
    if (!receipt) return null
    const [year, month, day] = receipt.split('-').map(Number)
    if (day === 20 || !year || !month || !day) return null
    const date = new Date(year, month - 1 + (day > 20 ? 1 : 0), 20)
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-20`
}

function resetTrial() {
    costInput.value = String(ledger.value.cost)
    emit('notify', '已恢复该项目的已登记成本。')
}

function askAboutTrial() {
    emit(
        'ask',
        `请核对示例项目「${selectedProject.value?.name ?? '当前项目'}」的奖励试算：约定服务费 ${fee.value} 元，直接成本 ${cost.value} 元，节余奖金 ${calculation.value.bonus} 元，执行部最终抽成 ${calculation.value.share} 元，超出 30% 的成本 ${calculation.value.excess} 元。本次试算尚未登记，请先列出需要核对的项目。`,
    )
}

const fileInput = ref<HTMLInputElement | null>(null)
const selectedFiles = ref<{ name: string; size: string }[]>([])
function removeSelectedFile(index: number) {
    selectedFiles.value.splice(index, 1)
    emit('notify', '已移除所选文件。')
}
function chooseFiles(event: Event) {
    const input = event.target as HTMLInputElement
    selectedFiles.value = Array.from(input.files ?? []).map((file) => ({
        name: file.name,
        size:
            file.size < 1024 * 1024
                ? `${Math.max(1, Math.round(file.size / 1024))} KB`
                : `${(file.size / 1024 / 1024).toFixed(1)} MB`,
    }))
    if (selectedFiles.value.length)
        emit('notify', '已选择文件。本原型仅展示文件名，没有解析或上传文件。')
    input.value = ''
}

const exampleLoaded = ref(false)
const exampleRegistered = ref(false)
const exampleRows = [
    {
        id: 'ex-1',
        date: '09-06',
        project: '云栖里',
        category: '物料费',
        amount: 860,
        state: 'ready',
        detail: '费用与凭证对应',
    },
    {
        id: 'ex-2',
        date: '09-07',
        project: '澄湖花园',
        category: '交通费',
        amount: 240,
        state: 'ready',
        detail: '项目、金额一致',
    },
    {
        id: 'ex-3',
        date: '09-08',
        project: '南溪雅苑',
        category: '专家费',
        amount: 1200,
        state: 'ready',
        detail: '凭证信息完整',
    },
    {
        id: 'ex-4',
        date: '09-06',
        project: '云栖里',
        category: '物料费',
        amount: 860,
        state: 'duplicate',
        detail: '与第 1 行疑似重复',
    },
    {
        id: 'ex-5',
        date: '09-08',
        project: '项目待确认',
        category: '餐补',
        amount: 360,
        state: 'review',
        detail: '凭证未注明所属项目',
    },
]
function loadExample() {
    exampleLoaded.value = true
    emit(
        'notify',
        exampleRegistered.value
            ? '已打开已登记的示例批次。'
            : '已载入 5 条预设示例，与您选择的文件无关。',
    )
}
function registerExample() {
    if (exampleRegistered.value) return
    exampleRegistered.value = true
    emit(
        'notify',
        '已在本地示例批次登记 3 条，共 2,300 元；重复行与待核对行未登记。项目账本未变动。',
    )
}

const payments = ref<Record<string, PaymentRecord>>({
    p4: {
        date: '2026-08-20',
        reference: '示例工资付款凭证 PAY-0820-04',
        files: ['8月执行奖励付款凭证（示例）.png'],
    },
})
function rewardStatus(id: string) {
    if (payments.value[id]) return '已支付'
    const item = ledgers[id]
    if (!item || item.received < (fees.value[id] ?? 0)) return '待回款'
    if (
        !item.checked ||
        !settlementDate(item.lastReceipt) ||
        calculate(fees.value[id] ?? 0, item.cost).excess > 0
    )
        return '待核对'
    return '待结算'
}
const pendingRewards = computed(() =>
    Object.entries(ledgers).reduce(
        (total, [id, item]) =>
            total +
            (rewardStatus(id) === '待结算'
                ? calculate(fees.value[id] ?? 0, item.cost).reward
                : 0),
        0,
    ),
)
const voucherProjectId = ref<string | null>(null)
const voucherProject = computed(() =>
    projects.find((project) => project.id === voucherProjectId.value),
)
const existingVoucher = computed(() =>
    voucherProjectId.value ? payments.value[voucherProjectId.value] : undefined,
)
const voucherDate = ref('')
const voucherReference = ref('')
const voucherFileNames = ref<string[]>([])
const voucherInput = ref<HTMLInputElement | null>(null)
const voucherAmount = computed(() => {
    const id = voucherProjectId.value
    return id && ledgers[id]
        ? calculate(fees.value[id] ?? 0, ledgers[id].cost).reward
        : 0
})
const voucherDateValid = computed(() => {
    const id = voucherProjectId.value
    const scheduled = id
        ? settlementDate(ledgers[id]?.lastReceipt ?? null)
        : null
    return Boolean(
        voucherDate.value && scheduled && voucherDate.value >= scheduled,
    )
})

function openVoucher(id: string) {
    voucherProjectId.value = id
    const existing = payments.value[id]
    voucherDate.value =
        existing?.date ?? settlementDate(ledgers[id]?.lastReceipt ?? null) ?? ''
    voucherReference.value = existing?.reference ?? ''
    voucherFileNames.value = existing?.files ?? []
}
function selectVoucherFiles(event: Event) {
    const input = event.target as HTMLInputElement
    voucherFileNames.value = Array.from(input.files ?? []).map(
        (file) => file.name,
    )
    input.value = ''
    if (voucherFileNames.value.length)
        emit('notify', '已选择凭证附件，仅保留本地文件名，未上传。')
}
function saveVoucher() {
    const id = voucherProjectId.value
    if (
        !id ||
        !voucherReference.value.trim() ||
        !voucherDateValid.value ||
        rewardStatus(id) !== '待结算'
    )
        return
    payments.value = {
        ...payments.value,
        [id]: {
            date: voucherDate.value,
            reference: voucherReference.value.trim(),
            files: [...voucherFileNames.value],
        },
    }
    voucherProjectId.value = null
    emit(
        'notify',
        '已保存本地示例付款记录，并标记为已支付。未发起转账，也未重复计入成本。',
    )
}

function askAboutReward(id: string) {
    const project = projects.find((item) => item.id === id)
    emit(
        'ask',
        `请核对示例项目「${project?.name ?? id}」的奖励结算条件，列出尾款到账、成本确认和结算批次中尚待处理的事项。20 日当天到账的归属规则仍待老板确定，不要自动推算或付款。`,
    )
}
</script>

<template>
    <div v-if="props.role !== 'owner'" class="sd-panel finance-denied">
        <Icon name="wallet" :size="28" />
        <h2>财务台账仅老板可见</h2>
        <p class="sd-muted">项目约定服务费请在项目详情查看。</p>
    </div>

    <div v-else class="finance-workspace">
        <header class="sd-page-heading finance-heading">
            <div>
                <h1>财务台账</h1>
            </div>
            <button
                class="sd-button"
                @click="
                    emit(
                        'ask',
                        '请帮我梳理示例项目的待回款、待核对费用和下一批奖励结算事项。',
                    )
                "
            >
                <Icon name="coins" :size="17" /> 请霜月核对
            </button>
        </header>

        <section class="finance-metrics" aria-label="财务概览">
            <article class="metric-card">
                <span>约定服务费合计</span>
                <strong><small>¥</small>{{ money(totalFee) }}</strong>
                <p>{{ projects.length }} 个项目 · 已确认约定</p>
            </article>
            <article class="metric-card">
                <span>已登记直接成本</span>
                <strong><small>¥</small>{{ money(totalCost) }}</strong>
                <p>不含节余奖金与执行部抽成</p>
            </article>
            <article class="metric-card">
                <span>项目待回款</span>
                <strong
                    ><small>¥</small
                    >{{ money(totalFee - totalReceived) }}</strong
                >
                <p>实收 {{ money(totalReceived) }} 元</p>
            </article>
            <article class="metric-card metric-card--accent">
                <span>待结算执行奖励</span>
                <strong><small>¥</small>{{ money(pendingRewards) }}</strong>
                <p>付款后登记凭证</p>
            </article>
        </section>

        <nav class="sd-tabs finance-tabs" aria-label="财务视图">
            <button
                v-for="tab in tabs"
                :key="tab.id"
                :class="{ active: activeTab === tab.id }"
                :aria-pressed="activeTab === tab.id"
                @click="activeTab = tab.id"
            >
                {{ tab.label }}
                <span v-if="tab.id === 'imports'" class="tab-count"
                    >待核对 1</span
                >
            </button>
        </nav>

        <template v-if="activeTab === 'projects'">
            <section class="sd-panel project-selector">
                <div class="project-selector__intro">
                    <span class="section-label">项目账本</span>
                    <label class="sr-only" for="finance-project"
                        >选择项目</label
                    >
                    <select
                        id="finance-project"
                        v-model="selectedId"
                        class="sd-field"
                    >
                        <option
                            v-for="project in projects"
                            :key="project.id"
                            :value="project.id"
                        >
                            {{ project.name }}
                        </option>
                    </select>
                    <span
                        class="sd-pill"
                        :class="ledger.checked ? 'pill-checked' : 'pill-warn'"
                        >{{ ledger.checked ? '成本已核对' : '成本暂估' }}</span
                    >
                </div>
                <div class="receipt-summary">
                    <span
                        >实收 <b>¥{{ money(ledger.received) }}</b></span
                    >
                    <span
                        >待回款
                        <b
                            >¥{{ money(Math.max(fee - ledger.received, 0)) }}</b
                        ></span
                    >
                    <button
                        class="text-button"
                        @click="
                            emit(
                                'ask',
                                `请展示示例项目「${selectedProject?.name}」的回款计划与收款凭证，并区分约定服务费和实收金额。`,
                            )
                        "
                    >
                        核对回款 <Icon name="arrow-right" :size="15" />
                    </button>
                </div>
            </section>

            <div class="project-finance-grid">
                <section class="sd-panel calculator-panel">
                    <div class="section-heading">
                        <div>
                            <h2>成本与奖励试算</h2>
                        </div>
                        <span class="sd-pill">{{
                            isTrial
                                ? '本地试算'
                                : ledger.checked
                                  ? '已核对口径'
                                  : '暂估口径'
                        }}</span>
                    </div>

                    <div class="cost-input-row">
                        <label for="finance-cost"
                            >直接成本 C <span>不含奖金与抽成</span></label
                        >
                        <div class="money-input">
                            <span>¥</span
                            ><input
                                id="finance-cost"
                                v-model="costInput"
                                type="number"
                                min="0"
                                step="100"
                                :aria-invalid="!costIsValid"
                            />
                        </div>
                    </div>
                    <p v-if="!costIsValid" class="field-error">
                        请输入不小于 0 的有效成本金额。
                    </p>
                    <p v-else class="input-help">
                        已登记 ¥{{ money(ledger.cost) }} · 试算不入账
                    </p>

                    <div class="allocation-heading">
                        <span>服务费的 30% · 成本与执行奖励</span
                        ><b>¥{{ money(fee * 0.3) }}</b>
                    </div>
                    <div class="allocation-bar" aria-hidden="true">
                        <span
                            class="allocation-cost"
                            :class="{
                                'allocation-cost--over': calculation.excess > 0,
                            }"
                            :style="{ width: chartWidth(cost) }"
                        ></span>
                        <span
                            class="allocation-bonus"
                            :style="{ width: chartWidth(calculation.bonus) }"
                        ></span>
                        <span
                            class="allocation-share"
                            :style="{ width: chartWidth(calculation.share) }"
                        ></span>
                    </div>
                    <div class="allocation-legend">
                        <span><i class="dot dot-cost"></i>直接成本</span
                        ><span><i class="dot dot-bonus"></i>节余奖金</span
                        ><span><i class="dot dot-share"></i>执行部抽成</span>
                    </div>

                    <dl class="calculation-lines">
                        <div>
                            <dt>约定服务费 F</dt>
                            <dd>¥{{ money(fee) }}</dd>
                        </div>
                        <div>
                            <dt>直接成本额度 <small>F × 20%</small></dt>
                            <dd>¥{{ money(calculation.pool) }}</dd>
                        </div>
                        <div>
                            <dt>
                                节余奖金 <small>额度减直接成本，最低为 0</small>
                            </dt>
                            <dd class="value-accent">
                                ¥{{ money(calculation.bonus) }}
                            </dd>
                        </div>
                        <div>
                            <dt>执行部初始抽成 <small>F × 10%</small></dt>
                            <dd>¥{{ money(calculation.initialShare) }}</dd>
                        </div>
                        <div>
                            <dt>
                                成本超额抵扣 <small>从执行部抽成中扣减</small>
                            </dt>
                            <dd
                                :class="{
                                    'value-warn': calculation.deduction > 0,
                                }"
                            >
                                {{ calculation.deduction > 0 ? '−' : '' }}¥{{
                                    money(calculation.deduction)
                                }}
                            </dd>
                        </div>
                        <div class="calculation-subtotal">
                            <dt>最终执行部抽成</dt>
                            <dd class="value-accent">
                                ¥{{ money(calculation.share) }}
                            </dd>
                        </div>
                    </dl>

                    <div
                        v-if="calculation.excess > 0"
                        class="finance-notice finance-notice--warn"
                    >
                        <Icon name="info" :size="18" />
                        <p>
                            超出 30% 额度 <b>¥{{ money(calculation.excess) }}</b
                            >，待老板处理。奖励归零，不形成员工负债。
                        </p>
                    </div>
                    <div class="calculator-actions">
                        <button
                            class="sd-button sd-button--quiet"
                            @click="resetTrial"
                        >
                            恢复已登记成本</button
                        ><button
                            class="sd-button sd-button--primary"
                            :disabled="!costIsValid"
                            @click="askAboutTrial"
                        >
                            交给霜月核对 <Icon name="arrow-right" :size="16" />
                        </button>
                    </div>
                </section>

                <aside class="finance-side">
                    <section class="contribution-panel">
                        <div class="contribution-top">
                            <span>项目贡献毛利</span
                            ><span class="sd-pill">{{
                                isTrial || !ledger.checked ? '暂估' : '已核对'
                            }}</span>
                        </div>
                        <strong
                            ><small>¥</small
                            >{{ money(calculation.contribution) }}</strong
                        >
                        <p>服务费 − 直接成本 − 两项执行奖励</p>
                        <div class="contribution-divider"></div>
                        <div>
                            <span>扣奖励前项目毛利</span
                            ><b>¥{{ money(fee - cost) }}</b>
                        </div>
                        <div>
                            <span>执行奖励合计</span
                            ><b>¥{{ money(calculation.reward) }}</b>
                        </div>
                        <p class="contribution-note">
                            公司固定费用另计，非公司净利润。
                        </p>
                    </section>

                    <section class="sd-panel settlement-note">
                        <div class="section-heading">
                            <h3>结算条件</h3>
                            <Icon name="clock" :size="19" />
                        </div>
                        <ol class="settlement-steps">
                            <li :class="{ done: ledger.received >= fee }">
                                <span>{{
                                    ledger.received >= fee ? '✓' : '1'
                                }}</span>
                                <div>
                                    <b>尾款全部到账</b>
                                    <p>
                                        {{
                                            ledger.received >= fee
                                                ? `已于 ${ledger.lastReceipt} 到账`
                                                : `还需回款 ¥${money(fee - ledger.received)}`
                                        }}
                                    </p>
                                </div>
                            </li>
                            <li :class="{ done: ledger.checked }">
                                <span>{{ ledger.checked ? '✓' : '2' }}</span>
                                <div>
                                    <b>直接成本核对完成</b>
                                    <p>
                                        {{
                                            ledger.checked
                                                ? '已核对'
                                                : '暂估，待核对'
                                        }}
                                    </p>
                                </div>
                            </li>
                            <li>
                                <span>3</span>
                                <div>
                                    <b>结算批次 · 每月 20 日</b>
                                    <p>
                                        {{
                                            !ledger.lastReceipt
                                                ? '尾款到账后确定结算批次'
                                                : (settlementDate(
                                                      ledger.lastReceipt,
                                                  ) ??
                                                  '20 日当天到账，批次待老板确认')
                                        }}
                                    </p>
                                </div>
                            </li>
                        </ol>
                        <button
                            class="text-button"
                            @click="activeTab = 'rewards'"
                        >
                            查看奖励结算 <Icon name="arrow-right" :size="15" />
                        </button>
                    </section>

                    <div class="finance-notice">
                        <Icon name="info" :size="18" />
                        <p>费用与支付分别登记，付款不重复计成本。</p>
                    </div>
                </aside>
            </div>
        </template>

        <template v-else-if="activeTab === 'imports'">
            <div class="import-grid">
                <section class="sd-panel upload-panel">
                    <span class="upload-icon"
                        ><Icon name="upload" :size="24"
                    /></span>
                    <h2>导入财务资料</h2>
                    <p>Excel 明细及对应凭证截图</p>
                    <input
                        ref="fileInput"
                        class="sr-only"
                        type="file"
                        multiple
                        accept=".xlsx,.xls,.csv,.png,.jpg,.jpeg,.webp,.pdf"
                        aria-label="选择财务表格和凭证文件"
                        @change="chooseFiles"
                    />
                    <button
                        class="sd-button sd-button--primary"
                        @click="fileInput?.click()"
                    >
                        <Icon name="file" :size="17" />选择本地文件
                    </button>
                    <p class="upload-disclosure">
                        原型仅展示文件名，不解析或上传真实文件。
                    </p>
                    <ul v-if="selectedFiles.length" class="chosen-files">
                        <li
                            v-for="(file, index) in selectedFiles"
                            :key="`${file.name}-${index}`"
                        >
                            <Icon name="file" :size="17" /><span
                                >{{ file.name
                                }}<small
                                    >{{ file.size }} · 尚未解析</small
                                ></span
                            ><button
                                class="icon-button"
                                :aria-label="`移除 ${file.name}`"
                                @click="removeSelectedFile(index)"
                            >
                                <Icon name="close" :size="15" />
                            </button>
                        </li>
                    </ul>
                </section>

                <section class="sd-panel import-flow">
                    <h2>示例导入批次</h2>
                    <p class="example-summary">
                        3 条可登记 · 1 条重复 · 1 条待核对
                    </p>
                    <button class="sd-button" @click="loadExample">
                        {{ exampleLoaded ? '查看示例批次' : '载入示例'
                        }}<Icon name="arrow-right" :size="16" />
                    </button>
                    <p class="sd-muted example-source">
                        使用独立预设数据，与所选文件无关。
                    </p>
                </section>
            </div>

            <section v-if="exampleLoaded" class="sd-panel import-review">
                <div class="section-heading">
                    <div>
                        <p class="section-label">示例批次 0908</p>
                        <h2>
                            {{
                                exampleRegistered
                                    ? '示例登记结果'
                                    : '费用核对（5 条）'
                            }}
                        </h2>
                    </div>
                    <span class="sd-pill">{{
                        exampleRegistered ? '3 条已登记' : '3 条可登记'
                    }}</span>
                </div>
                <div class="review-summary">
                    <span
                        ><i class="dot dot-cost"></i
                        >{{ exampleRegistered ? '已登记' : '可登记' }} 3</span
                    ><span><i class="dot dot-muted"></i>疑似重复 1</span
                    ><span><i class="dot dot-warn"></i>待核对 1</span>
                </div>
                <div class="finance-table-wrap">
                    <table class="finance-table">
                        <thead>
                            <tr>
                                <th>发生日期</th>
                                <th>所属项目</th>
                                <th>费用类型</th>
                                <th class="numeric">金额</th>
                                <th>核对结果</th>
                                <th>说明</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="row in exampleRows" :key="row.id">
                                <td>{{ row.date }}</td>
                                <td class="cell-strong">{{ row.project }}</td>
                                <td>{{ row.category }}</td>
                                <td class="numeric">
                                    ¥{{ money(row.amount) }}
                                </td>
                                <td>
                                    <span
                                        class="sd-pill"
                                        :class="
                                            row.state === 'ready'
                                                ? 'pill-checked'
                                                : row.state === 'review'
                                                  ? 'pill-warn'
                                                  : ''
                                        "
                                        >{{
                                            row.state === 'ready'
                                                ? exampleRegistered
                                                    ? '已登记'
                                                    : '可登记'
                                                : row.state === 'duplicate'
                                                  ? '不重复登记'
                                                  : '待核对'
                                        }}</span
                                    >
                                </td>
                                <td class="sd-muted">{{ row.detail }}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                <div
                    v-if="exampleRegistered"
                    class="finance-notice import-result"
                >
                    <Icon name="check" :size="18" />
                    <p>
                        已登记 3 条示例，共 <b>¥2,300</b>；另 2
                        条未登记。项目账本未变动。
                    </p>
                </div>
                <footer class="review-actions">
                    <p>费用与凭证关联登记，不重复入账。</p>
                    <div>
                        <button
                            class="sd-button sd-button--quiet"
                            @click="
                                emit(
                                    'ask',
                                    '请帮我核对预设导入示例：一条云栖里物料费疑似重复，一条 360 元餐补未注明项目。不要把我选择的文件声称为已识别或已登记。',
                                )
                            "
                        >
                            请霜月核对</button
                        ><button
                            class="sd-button sd-button--primary"
                            :disabled="exampleRegistered"
                            @click="registerExample"
                        >
                            <Icon name="check" :size="16" />{{
                                exampleRegistered
                                    ? '示例已登记'
                                    : '仅登记 3 条示例'
                            }}
                        </button>
                    </div>
                </footer>
            </section>
        </template>

        <template v-else>
            <section class="sd-panel reward-panel">
                <div class="section-heading">
                    <div>
                        <h2>奖励结算明细</h2>
                        <p class="sd-muted">
                            采用已登记成本；尾款结清后，两项奖励同批结算。
                        </p>
                    </div>
                    <span class="sd-pill">每月 20 日</span>
                </div>
                <div class="finance-table-wrap">
                    <table class="finance-table reward-table">
                        <thead>
                            <tr>
                                <th>项目</th>
                                <th class="numeric">节余奖金</th>
                                <th class="numeric">执行部抽成</th>
                                <th class="numeric">合计奖励</th>
                                <th>尾款到账</th>
                                <th>结算批次</th>
                                <th>状态</th>
                                <th>操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="project in projects" :key="project.id">
                                <td>
                                    <span class="cell-strong">{{
                                        project.name
                                    }}</span
                                    ><small class="cell-note">{{
                                        ledgers[project.id]?.checked
                                            ? '成本已核对'
                                            : '奖励暂估'
                                    }}</small>
                                </td>
                                <td class="numeric">
                                    ¥{{
                                        money(
                                            calculate(
                                                fees[project.id] ?? 0,
                                                ledgers[project.id]?.cost ?? 0,
                                            ).bonus,
                                        )
                                    }}
                                </td>
                                <td class="numeric">
                                    ¥{{
                                        money(
                                            calculate(
                                                fees[project.id] ?? 0,
                                                ledgers[project.id]?.cost ?? 0,
                                            ).share,
                                        )
                                    }}
                                </td>
                                <td class="numeric cell-strong">
                                    ¥{{
                                        money(
                                            calculate(
                                                fees[project.id] ?? 0,
                                                ledgers[project.id]?.cost ?? 0,
                                            ).reward,
                                        )
                                    }}
                                </td>
                                <td>
                                    {{
                                        ledgers[project.id]?.lastReceipt ??
                                        '尚未结清'
                                    }}
                                </td>
                                <td>
                                    {{
                                        settlementDate(
                                            ledgers[project.id]?.lastReceipt ??
                                                null,
                                        ) ??
                                        (ledgers[project.id]?.lastReceipt
                                            ? '20 日到账 · 待核对'
                                            : '待回款后确定')
                                    }}
                                </td>
                                <td>
                                    <span
                                        class="sd-pill"
                                        :class="
                                            rewardStatus(project.id) ===
                                            '已支付'
                                                ? 'pill-checked'
                                                : rewardStatus(project.id) ===
                                                    '待核对'
                                                  ? 'pill-warn'
                                                  : ''
                                        "
                                        >{{ rewardStatus(project.id) }}</span
                                    >
                                </td>
                                <td>
                                    <button
                                        v-if="
                                            rewardStatus(project.id) ===
                                                '待结算' ||
                                            rewardStatus(project.id) ===
                                                '已支付'
                                        "
                                        class="text-button"
                                        @click="openVoucher(project.id)"
                                    >
                                        {{
                                            rewardStatus(project.id) ===
                                            '已支付'
                                                ? '查看付款记录'
                                                : '登记付款凭证'
                                        }}<Icon
                                            name="chevron-right"
                                            :size="14"
                                        /></button
                                    ><button
                                        v-else
                                        class="text-button"
                                        @click="askAboutReward(project.id)"
                                    >
                                        {{
                                            rewardStatus(project.id) ===
                                            '待回款'
                                                ? '核对回款'
                                                : '处理待核对项'
                                        }}<Icon
                                            name="chevron-right"
                                            :size="14"
                                        />
                                    </button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                <div class="finance-notice reward-notice">
                    <Icon name="info" :size="18" />
                    <p>
                        实际支付后登记凭证；到期不自动付款，付款不重复计成本。
                    </p>
                </div>
            </section>
            <p class="reward-footnote">
                10% 为执行部总额度；20 日当天到账批次待定；超 30%
                由老板处理，不形成员工负债。
            </p>
        </template>

        <Teleport to="body">
            <div
                v-if="voucherProjectId"
                class="finance-modal-backdrop"
                @click.self="voucherProjectId = null"
                @keydown.esc="voucherProjectId = null"
            >
                <section
                    class="finance-modal sd-panel"
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="voucher-title"
                    tabindex="-1"
                >
                    <header>
                        <div>
                            <p class="section-label">
                                {{ voucherProject?.name }} · 奖励结算
                            </p>
                            <h2 id="voucher-title">
                                {{
                                    existingVoucher
                                        ? '付款记录'
                                        : '登记付款凭证'
                                }}
                            </h2>
                        </div>
                        <button
                            class="icon-button"
                            aria-label="关闭付款凭证"
                            @click="voucherProjectId = null"
                        >
                            <Icon name="close" :size="20" />
                        </button>
                    </header>
                    <div class="voucher-total">
                        <span>执行奖励合计</span
                        ><strong>¥{{ money(voucherAmount) }}</strong>
                    </div>
                    <label class="voucher-field"
                        >实际付款日期<input
                            v-model="voucherDate"
                            class="sd-field"
                            type="date"
                            :disabled="Boolean(existingVoucher)"
                    /></label>
                    <p
                        v-if="
                            !existingVoucher && voucherDate && !voucherDateValid
                        "
                        class="field-error"
                    >
                        付款日期不能早于已确定的结算批次。
                    </p>
                    <label class="voucher-field"
                        >凭证说明 / 编号<textarea
                            v-model="voucherReference"
                            class="sd-field"
                            rows="3"
                            placeholder="填写示例付款凭证编号或说明"
                            :disabled="Boolean(existingVoucher)"
                        ></textarea>
                    </label>
                    <div class="voucher-attachments">
                        <span>凭证附件 <small>选填</small></span
                        ><button
                            v-if="!existingVoucher"
                            class="text-button"
                            @click="voucherInput?.click()"
                        >
                            <Icon name="upload" :size="16" />选择附件
                        </button>
                    </div>
                    <input
                        ref="voucherInput"
                        class="sr-only"
                        type="file"
                        multiple
                        accept=".png,.jpg,.jpeg,.pdf"
                        aria-label="选择示例付款凭证附件"
                        @change="selectVoucherFiles"
                    />
                    <ul
                        v-if="voucherFileNames.length"
                        class="voucher-file-list"
                    >
                        <li
                            v-for="(name, index) in voucherFileNames"
                            :key="`${name}-${index}`"
                        >
                            <Icon name="file" :size="16" />{{ name }}
                        </li>
                    </ul>
                    <p class="voucher-disclosure">
                        仅保存本地示例记录；不上传附件，不发起付款。
                    </p>
                    <footer>
                        <button
                            class="sd-button sd-button--quiet"
                            @click="voucherProjectId = null"
                        >
                            {{ existingVoucher ? '关闭' : '取消' }}</button
                        ><button
                            v-if="!existingVoucher"
                            class="sd-button sd-button--primary"
                            :disabled="
                                !voucherReference.trim() || !voucherDateValid
                            "
                            @click="saveVoucher"
                        >
                            <Icon name="check" :size="16" />保存示例付款记录
                        </button>
                    </footer>
                </section>
            </div>
        </Teleport>
    </div>
</template>

<style scoped>
.finance-workspace {
    display: grid;
    gap: 16px;
    color: var(--sd-ink);
}
.finance-heading,
.section-heading,
.finance-modal header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
}
.finance-heading > .sd-button {
    margin-top: 0;
    flex-shrink: 0;
}
.finance-heading {
    margin-bottom: 0;
}
.finance-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
}
.metric-card {
    padding: 16px;
    background: var(--sd-surface);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
}
.metric-card > span {
    color: var(--sd-muted);
    font-size: 12px;
}
.metric-card strong {
    display: block;
    margin-top: 13px;
    font-size: 26px;
    font-weight: 600;
    letter-spacing: -1px;
    font-variant-numeric: tabular-nums;
}
.metric-card strong small,
.contribution-panel > strong small {
    font-size: 16px;
    margin-right: 6px;
    font-weight: 400;
}
.metric-card p {
    margin: 11px 0 0;
    color: var(--sd-muted);
    font-size: 11px;
    line-height: 1.5;
}
.metric-card--accent {
    background: var(--sd-soft);
    border-color: var(--sd-line);
}
.metric-card--accent strong {
    color: var(--sd-accent);
}
.finance-tabs {
    display: flex;
    align-items: center;
    gap: 16px;
    border-bottom: 1px solid var(--sd-line);
}
.finance-tabs > button {
    display: flex;
    align-items: center;
    gap: 8px;
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 0 1px 15px;
    background: transparent;
    color: var(--sd-muted);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
}
.finance-tabs > button.active {
    color: var(--sd-accent);
    border-bottom-color: var(--sd-accent);
    font-weight: 600;
}
.tab-count {
    padding: 3px 6px;
    background: #f0eee8;
    color: #83744c;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 400;
}
.project-selector {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
    padding: 16px;
}
.project-selector__intro {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 14px;
}
.project-selector select {
    width: 170px;
    min-width: 155px;
    font-weight: 600;
    font-size: 13px;
}
.section-label {
    color: var(--sd-muted);
    font-size: 11px;
    letter-spacing: 0.5px;
    margin: 0 0 8px;
}
.project-selector .section-label {
    margin: 0;
}
.receipt-summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 16px;
    font-size: 12px;
    color: var(--sd-muted);
}
.receipt-summary b {
    margin-left: 6px;
    font-weight: 500;
    color: var(--sd-ink);
}
.text-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    border: none;
    background: transparent;
    color: var(--sd-accent);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    padding: 3px 0;
}
.text-button:hover {
    text-decoration: underline;
    text-underline-offset: 4px;
}
.project-finance-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.55fr) minmax(275px, 1fr);
    gap: 16px;
}
.calculator-panel,
.settlement-note,
.import-flow,
.import-review,
.reward-panel {
    padding: 16px;
}
.section-heading h2,
.import-flow h2,
.upload-panel h2 {
    margin: 0;
    font-size: 16px;
    letter-spacing: -0.3px;
    font-weight: 600;
}
.section-heading h3 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
}
.section-heading > .sd-pill {
    flex-shrink: 0;
}
.cost-input-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-top: 16px;
}
.cost-input-row label {
    font-size: 13px;
    font-weight: 500;
}
.cost-input-row label span {
    display: block;
    margin-top: 6px;
    font-size: 11px;
    font-weight: 400;
    color: var(--sd-muted);
}
.money-input {
    display: flex;
    align-items: center;
    gap: 9px;
    max-width: 180px;
    border: 1px solid var(--sd-line);
    padding: 10px 12px;
    border-radius: 6px;
    background: var(--sd-surface);
}
.money-input > span {
    color: var(--sd-muted);
    font-size: 13px;
}
.money-input input {
    width: 100%;
    min-width: 0;
    padding: 0;
    border: none;
    outline: none;
    font: inherit;
    font-size: 19px;
    font-weight: 600;
    color: var(--sd-ink);
    background: transparent;
    font-variant-numeric: tabular-nums;
}
.money-input:focus-within {
    outline: 2px solid var(--sd-soft);
    border-color: var(--sd-accent);
}
.input-help {
    margin: 10px 0 0;
    font-size: 11px;
    color: var(--sd-muted);
    line-height: 1.6;
}
.field-error {
    color: #a04535;
    font-size: 12px;
    margin: 8px 0;
}
.allocation-heading {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    margin-top: 16px;
    font-size: 11px;
    color: var(--sd-muted);
}
.allocation-heading b {
    font-weight: 500;
    color: var(--sd-ink);
}
.allocation-bar {
    display: flex;
    height: 11px;
    border-radius: 4px;
    overflow: hidden;
    background: var(--sd-bg);
    margin-top: 12px;
}
.allocation-bar > span {
    flex-shrink: 0;
    transition: width 0.2s ease;
}
.allocation-cost,
.dot-cost {
    background: var(--sd-accent);
}
.allocation-cost--over {
    background: #a9794c;
}
.allocation-bonus,
.dot-bonus {
    background: #94a3b8;
}
.allocation-share,
.dot-share {
    background: #cbd5e1;
}
.allocation-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    margin-top: 11px;
    color: var(--sd-muted);
    font-size: 10px;
}
.allocation-legend > span,
.review-summary > span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
}
.dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 2px;
}
.dot-muted {
    background: #b5bbb8;
}
.dot-warn {
    background: #b99a60;
}
.calculation-lines {
    margin: 16px 0 0;
}
.calculation-lines > div {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 13px 0;
    border-bottom: 1px solid var(--sd-line);
    font-size: 13px;
}
.calculation-lines dt small {
    display: block;
    color: var(--sd-muted);
    margin-top: 5px;
    font-size: 10px;
    font-weight: 400;
}
.calculation-lines dd {
    margin: 0;
    font-weight: 500;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
}
.calculation-lines .calculation-subtotal {
    padding-top: 16px;
    border-bottom: 0;
    font-weight: 600;
}
.calculation-subtotal dd {
    font-size: 20px;
    font-weight: 600;
}
.value-accent {
    color: var(--sd-accent);
}
.value-warn {
    color: var(--sd-warn);
}
.finance-notice {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 15px 16px;
    border: 1px solid var(--sd-line);
    background: var(--sd-bg);
    border-radius: 6px;
    color: var(--sd-muted);
}
.finance-notice > svg {
    flex-shrink: 0;
    margin-top: 1px;
}
.finance-notice p {
    margin: 0;
    font-size: 11px;
    line-height: 1.85;
}
.finance-notice--warn {
    margin-top: 14px;
    color: #946c3b;
    background: #fbf7ef;
    border-color: #eadfc8;
}
.calculator-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 10px;
    margin-top: 16px;
}
.finance-workspace :deep(.sd-button),
.finance-modal .sd-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
}
.finance-workspace button:disabled,
.finance-modal button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
}
.finance-side {
    display: flex;
    flex-direction: column;
    gap: 16px;
}
.contribution-panel {
    padding: 16px;
    background: var(--sd-soft);
    border: 1px solid var(--sd-line);
    border-radius: 6px;
}
.contribution-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    color: var(--sd-accent);
}
.contribution-panel > strong {
    display: block;
    margin-top: 16px;
    font-size: 30px;
    font-weight: 500;
    letter-spacing: -1px;
    color: var(--sd-accent);
    font-variant-numeric: tabular-nums;
}
.contribution-panel > p {
    margin: 9px 0 0;
    color: var(--sd-muted);
    font-size: 11px;
}
.contribution-divider {
    height: 1px;
    background: var(--sd-line);
    margin: 16px 0;
}
.contribution-panel > div:not(.contribution-top, .contribution-divider) {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    margin-top: 12px;
    font-size: 12px;
}
.contribution-panel > div > b {
    font-weight: 500;
}
.contribution-panel .contribution-note {
    margin-top: 16px;
    line-height: 1.7;
    font-size: 10px;
}
.settlement-note {
    flex: 1;
}
.settlement-steps {
    list-style: none;
    margin: 16px 0;
    padding: 0;
    display: grid;
    gap: 16px;
}
.settlement-steps li {
    display: flex;
    gap: 12px;
    align-items: flex-start;
}
.settlement-steps li > span {
    display: grid;
    place-items: center;
    width: 23px;
    height: 23px;
    flex-shrink: 0;
    border: 1px solid var(--sd-line);
    border-radius: 50%;
    color: var(--sd-muted);
    font-size: 10px;
}
.settlement-steps li.done > span {
    background: var(--sd-soft);
    border-color: var(--sd-soft);
    color: var(--sd-accent);
}
.settlement-steps b {
    font-size: 12px;
    font-weight: 500;
}
.settlement-steps p {
    font-size: 11px;
    line-height: 1.6;
    margin: 5px 0 0;
    color: var(--sd-muted);
}
.pill-checked {
    background: #eaf2eb !important;
    color: #416b53 !important;
}
.pill-warn {
    background: #f7efdf !important;
    color: #9b773f !important;
}
.import-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(290px, 1fr);
    gap: 16px;
}
.upload-panel {
    text-align: center;
    padding: 16px;
    border-style: dashed;
}
.upload-icon {
    display: inline-grid;
    place-items: center;
    width: 36px;
    height: 36px;
    border-radius: 6px;
    background: var(--sd-soft);
    color: var(--sd-accent);
    margin-bottom: 12px;
}
.upload-panel > p {
    max-width: 350px;
    margin: 12px auto 16px;
    line-height: 1.9;
    color: var(--sd-muted);
    font-size: 12px;
}
.upload-panel > .upload-disclosure {
    font-size: 10px;
    margin-top: 13px;
    margin-bottom: 0;
}
.chosen-files {
    margin: 21px 0 0;
    padding: 0;
    list-style: none;
    display: grid;
    gap: 8px;
}
.chosen-files li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px;
    border-radius: 6px;
    background: var(--sd-bg);
    text-align: left;
}
.chosen-files li > span {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
    font-size: 11px;
}
.chosen-files small {
    display: block;
    color: var(--sd-muted);
    margin-top: 4px;
    font-size: 10px;
}
.icon-button {
    display: inline-grid;
    place-items: center;
    border: none;
    background: transparent;
    color: var(--sd-muted);
    cursor: pointer;
    padding: 5px;
    border-radius: 5px;
}
.icon-button:hover {
    background: var(--sd-soft);
}
.example-source {
    font-size: 10px;
    margin: 12px 0 0;
    line-height: 1.7;
}
.review-summary {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    margin: 16px 0;
    font-size: 11px;
    color: var(--sd-muted);
}
.finance-table-wrap {
    overflow-x: auto;
}
.finance-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    white-space: nowrap;
}
.finance-table th {
    background: #f8fafc;
    text-align: left;
    color: var(--sd-muted);
    font-size: 10px;
    font-weight: 500;
    padding: 13px 14px;
    border-bottom: 1px solid var(--sd-line);
}
.finance-table td {
    padding: 18px 14px;
    border-bottom: 1px solid var(--sd-line);
    font-variant-numeric: tabular-nums;
}
.finance-table .numeric {
    text-align: right;
}
.cell-strong {
    font-weight: 500;
}
.cell-note {
    display: block;
    font-size: 10px;
    color: var(--sd-muted);
    margin-top: 5px;
}
.finance-table .sd-pill {
    font-size: 10px;
}
.import-result {
    margin-top: 16px;
}
.review-actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
    margin-top: 16px;
}
.review-actions > p {
    color: var(--sd-muted);
    font-size: 10px;
    line-height: 1.7;
    margin: 0;
}
.review-actions > div {
    display: flex;
    gap: 10px;
}
.reward-panel .section-heading .sd-muted {
    font-size: 11px;
    line-height: 1.8;
    margin-top: 9px;
}
.reward-table {
    margin-top: 16px;
}
.reward-table th,
.reward-table td {
    padding-left: 11px;
    padding-right: 11px;
}
.reward-notice {
    margin-top: 16px;
}
.finance-denied {
    display: grid;
    justify-items: center;
    padding: 48px 16px;
    text-align: center;
    color: var(--sd-muted);
}
.finance-denied h2 {
    font-size: 16px;
    color: var(--sd-ink);
}
.finance-denied p {
    font-size: 13px;
}
.finance-modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: grid;
    place-items: center;
    padding: 16px;
    background: rgb(15 23 42 / 28%);
}
.finance-modal {
    width: min(100%, 480px);
    max-height: calc(100dvh - 44px);
    overflow-y: auto;
    padding: 16px;
    color: var(--sd-ink);
    box-shadow: 0 20px 80px rgb(15 23 42 / 12%);
}
.finance-modal h2 {
    font-size: 20px;
    font-weight: 600;
    margin: 0;
}
.voucher-total {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 15px;
    padding: 16px;
    margin: 16px 0;
    border-radius: 6px;
    background: var(--sd-soft);
    color: var(--sd-accent);
}
.voucher-total > span {
    font-size: 12px;
}
.voucher-total strong {
    font-size: 24px;
    font-weight: 500;
}
.voucher-field {
    display: grid;
    gap: 9px;
    font-size: 12px;
    margin-top: 16px;
}
.voucher-field .sd-field {
    width: 100%;
    box-sizing: border-box;
    font: inherit;
    padding: 11px 12px;
}
.voucher-field textarea {
    resize: vertical;
    line-height: 1.7;
}
.voucher-field input:disabled,
.voucher-field textarea:disabled {
    opacity: 1;
    background: var(--sd-bg);
    color: var(--sd-ink);
}
.voucher-attachments {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    font-size: 12px;
    margin-top: 16px;
}
.voucher-attachments small {
    font-size: 10px;
    color: var(--sd-muted);
    margin-left: 5px;
}
.voucher-file-list {
    display: grid;
    gap: 7px;
    list-style: none;
    padding: 0;
    margin: 14px 0;
}
.voucher-file-list li {
    display: flex;
    gap: 8px;
    align-items: center;
    overflow-wrap: anywhere;
    font-size: 11px;
    padding: 10px;
    background: var(--sd-bg);
    border-radius: 6px;
}
.voucher-file-list svg {
    flex-shrink: 0;
}
.voucher-disclosure {
    color: var(--sd-muted);
    font-size: 10px;
    line-height: 1.8;
    margin: 16px 0;
}
.finance-modal footer {
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 10px;
}
.sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}
@media (max-width: 1160px) {
    .finance-metrics {
        gap: 12px;
    }
    .metric-card {
        padding: 16px;
    }
    .metric-card strong {
        font-size: 26px;
    }
    .project-finance-grid {
        grid-template-columns: minmax(0, 1.35fr) minmax(260px, 1fr);
    }
    .calculator-panel,
    .settlement-note,
    .import-flow,
    .import-review,
    .reward-panel {
        padding: 16px;
    }
    .receipt-summary {
        gap: 14px;
    }
}
@media (max-width: 850px) {
    .finance-metrics {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .project-finance-grid,
    .import-grid {
        grid-template-columns: 1fr;
    }
    .finance-side {
        display: grid;
        grid-template-columns: 1fr 1fr;
    }
    .finance-side > .finance-notice {
        grid-column: 1 / -1;
    }
    .finance-heading {
        flex-wrap: wrap;
    }
    .finance-heading > .sd-button {
        margin-top: 0;
    }
}
@media (max-width: 560px) {
    .finance-workspace {
        gap: 16px;
    }
    .finance-side {
        display: flex;
    }
    .finance-tabs {
        gap: 16px;
    }
    .finance-tabs > button {
        font-size: 12px;
    }
    .tab-count {
        display: none;
    }
    .metric-card {
        padding: 16px;
    }
    .metric-card strong {
        font-size: 24px;
    }
    .metric-card p {
        font-size: 10px;
    }
    .project-selector,
    .calculator-panel,
    .import-review,
    .reward-panel {
        padding: 16px;
    }
    .receipt-summary {
        gap: 12px;
    }
    .money-input {
        max-width: 140px;
    }
    .allocation-heading {
        font-size: 10px;
    }
    .calculator-actions {
        flex-direction: column-reverse;
    }
    .calculator-actions > button {
        width: 100%;
    }
    .section-heading {
        gap: 10px;
    }
    .section-heading h2 {
        font-size: 16px;
    }
    .review-actions > div {
        flex-wrap: wrap;
    }
    .finance-modal {
        padding: 16px;
    }
    .finance-modal-backdrop {
        padding: 14px;
    }
}

.finance-workspace .sd-panel,
.finance-modal,
.finance-denied {
    border-radius: 6px;
}
.import-flow {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: flex-start;
    gap: 16px;
}
.example-summary {
    color: var(--sd-muted);
    font-size: 12px;
    line-height: 1.7;
}
.import-flow .example-source {
    margin: 0;
}
.reward-footnote {
    margin: 0;
    color: var(--sd-muted);
    font-size: 11px;
    line-height: 1.7;
}
</style>
