<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import Icon from './Icon.vue'
import type { PreviewRole } from './previewState'
import { registry, type RegistryEntry } from './registry'

const props = withDefaults(
    defineProps<{ role: PreviewRole; initialQuery?: string }>(),
    { initialQuery: '' },
)

const emit = defineEmits<{
    ask: [prompt: string]
    notify: [message: string]
}>()

type Communication = {
    id: number
    entryId: string
    demand: string
    occurredAt: string
    contact: string
    contactRole: string
    businessType: (typeof businessTypes)[number]
    businessStatus: (typeof businessStatuses)[number]
    result: string
}

const businessTypes = ['会务协助', '招投标代理'] as const
const businessStatuses = [
    '初步接触',
    '需求沟通',
    '待确定委托',
    '已确定委托',
    '暂缓',
] as const

const view = ref<'map' | 'list'>('map')
const query = ref(props.initialQuery)
const district = ref('')
const street = ref('')
const page = ref(1)
const pageSize = 12
const selectedId = ref(registry[0]?.id ?? '')
const showForm = ref(false)
const communications = ref<Communication[]>([])
const canEdit = computed(() => props.role === 'owner')
const canView = computed(() => props.role !== 'shareholder')
const districts = ['芗城区', '龙文区']
const districtCount = (name: string) =>
    registry.filter((entry) => entry.district === name).length

const availableStreets = computed(() =>
    [
        ...new Set(
            registry
                .filter(
                    (entry) =>
                        !district.value || entry.district === district.value,
                )
                .map((entry) => entry.street)
                .filter(Boolean),
        ),
    ].sort((a, b) => a.localeCompare(b, 'zh-CN')),
)

const filtered = computed(() => {
    const term = query.value.trim().toLocaleLowerCase()
    return registry.filter((entry) => {
        if (district.value && entry.district !== district.value) return false
        if (street.value && entry.street !== street.value) return false
        return (
            !term ||
            [
                entry.name,
                entry.company,
                entry.district,
                entry.street,
                entry.community,
                entry.manager,
            ]
                .join(' ')
                .toLocaleLowerCase()
                .includes(term)
        )
    })
})

const pageCount = computed(() =>
    Math.max(1, Math.ceil(filtered.value.length / pageSize)),
)
const offset = computed(() => (page.value - 1) * pageSize)
const visibleEntries = computed(() =>
    filtered.value.slice(offset.value, offset.value + pageSize),
)
const selected = computed(() =>
    filtered.value.find((entry) => entry.id === selectedId.value),
)
const selectedCommunications = computed(() =>
    communications.value.filter((entry) => entry.entryId === selectedId.value),
)
const contactCount = computed(
    () => filtered.value.filter((entry) => entry.phoneMasked).length,
)

function localDateTime() {
    const now = new Date()
    const pad = (value: number) => String(value).padStart(2, '0')
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`
}

const form = reactive<Omit<Communication, 'id' | 'entryId'>>({
    demand: '',
    occurredAt: localDateTime(),
    contact: '',
    contactRole: '',
    businessType: '会务协助',
    businessStatus: '初步接触',
    result: '',
})

watch(
    () => props.initialQuery,
    (value) => {
        query.value = value
    },
)
watch(district, () => {
    street.value = ''
})
watch(
    [query, district, street],
    () => {
        page.value = 1
        selectedId.value = filtered.value[0]?.id ?? ''
        showForm.value = false
    },
    { immediate: true },
)
watch(canEdit, (allowed) => {
    if (!allowed) showForm.value = false
})

function chooseDistrict(name: string) {
    district.value = district.value === name ? '' : name
}

function clearFilters() {
    query.value = ''
    district.value = ''
    street.value = ''
}

function selectEntry(entry: RegistryEntry) {
    selectedId.value = entry.id
    showForm.value = false
}

function changePage(next: number) {
    page.value = Math.max(1, Math.min(pageCount.value, next))
    selectedId.value = visibleEntries.value[0]?.id ?? ''
    showForm.value = false
}

function householdLabel(value: RegistryEntry['households']) {
    if (value === null || value === '') return '未提供'
    return typeof value === 'number'
        ? `${value.toLocaleString('zh-CN')} 户`
        : value
}

function beginCommunication() {
    if (!canEdit.value || !selected.value) return
    Object.assign(form, {
        demand: '',
        occurredAt: localDateTime(),
        contact: selected.value.manager,
        contactRole: '',
        businessType: '会务协助',
        businessStatus: '初步接触',
        result: '',
    })
    showForm.value = true
}

function saveCommunication() {
    if (!canEdit.value || !selected.value) return
    if (
        !form.demand.trim() ||
        !form.occurredAt ||
        !form.contact.trim() ||
        !form.contactRole.trim() ||
        !businessTypes.includes(form.businessType) ||
        !businessStatuses.includes(form.businessStatus) ||
        !form.result.trim()
    ) {
        emit('notify', '请完整填写沟通信息、联系人身份、业务类型和状态。')
        return
    }
    communications.value.unshift({
        id: Date.now(),
        entryId: selected.value.id,
        demand: form.demand.trim(),
        occurredAt: form.occurredAt,
        contact: form.contact.trim(),
        contactRole: form.contactRole.trim(),
        businessType: form.businessType,
        businessStatus: form.businessStatus,
        result: form.result.trim(),
    })
    showForm.value = false
    emit('notify', '沟通已记入本次预览记录，刷新页面后清除。')
}

function askAboutEntry() {
    if (!canEdit.value || !selected.value) return
    emit(
        'ask',
        `请帮我整理${selected.value.district}「${selected.value.name}」的首次拜访提纲。原始名录只提供物业资料，需求、业务进展和业委会情况都还待核实，请列出需要确认的问题。`,
    )
}
</script>

<template>
    <section v-if="canView" class="bm-workspace">
        <header class="sd-page-heading bm-heading">
            <h1>业务地图</h1>
            <div class="bm-heading-actions">
                <div class="sd-tabs bm-view-switch" aria-label="切换资源视图">
                    <button
                        type="button"
                        :class="{ active: view === 'map' }"
                        :aria-pressed="view === 'map'"
                        @click="view = 'map'"
                    >
                        <Icon name="map" /> 地图
                    </button>
                    <button
                        type="button"
                        :class="{ active: view === 'list' }"
                        :aria-pressed="view === 'list'"
                        @click="view = 'list'"
                    >
                        <Icon name="list" /> 名录
                    </button>
                </div>
            </div>
        </header>

        <div class="bm-overview" aria-label="名录概况">
            <div class="bm-stat">
                <span>原始记录</span><strong>304<small>条</small></strong>
            </div>
            <div class="bm-stat">
                <span>覆盖地区</span><strong>2<small>个区</small></strong>
            </div>
            <div class="bm-stat">
                <span>当前筛选</span
                ><strong>{{ filtered.length }}<small>条</small></strong>
            </div>
            <div class="bm-stat bm-stat--quiet">
                <span>精确点位</span><strong>0<small>条已定位</small></strong>
            </div>
        </div>

        <div class="sd-panel bm-filterbar">
            <label class="bm-search">
                <Icon name="search" />
                <input
                    v-model="query"
                    aria-label="搜索小区、物业公司或负责人"
                    placeholder="搜索小区、物业公司或负责人"
                    type="search"
                />
            </label>
            <label class="bm-select-label">
                <span>地区</span>
                <select
                    v-model="district"
                    class="sd-field"
                    aria-label="筛选地区"
                >
                    <option value="">全部地区</option>
                    <option v-for="name in districts" :key="name" :value="name">
                        {{ name }}
                    </option>
                </select>
            </label>
            <label class="bm-select-label bm-street-filter">
                <span>街道 / 乡镇</span>
                <select
                    v-model="street"
                    class="sd-field"
                    aria-label="筛选街道或乡镇"
                >
                    <option value="">全部街道</option>
                    <option
                        v-for="name in availableStreets"
                        :key="name"
                        :value="name"
                    >
                        {{ name }}
                    </option>
                </select>
            </label>
            <button
                v-if="query || district || street"
                class="sd-button sd-button--quiet bm-clear"
                type="button"
                @click="clearFilters"
            >
                清除筛选
            </button>
        </div>

        <div
            class="bm-columns"
            :class="{ 'bm-columns--list': view === 'list' }"
        >
            <div class="bm-main">
                <section v-if="view === 'map'" class="sd-panel bm-map-panel">
                    <div class="bm-panel-heading">
                        <h2>区域分布</h2>
                        <span class="bm-map-label"
                            ><span></span> 区域示意，非精确点位</span
                        >
                    </div>
                    <div class="bm-map-canvas">
                        <svg
                            viewBox="0 0 780 180"
                            role="group"
                            aria-label="芗城区与龙文区名录区域示意，点击区域筛选，无精确小区点位"
                        >
                            <path
                                class="bm-map-connection"
                                d="M 334 90 H 446"
                            />
                            <g
                                class="bm-region"
                                :class="{
                                    'bm-region--selected':
                                        district === '芗城区',
                                }"
                                role="button"
                                tabindex="0"
                                :aria-pressed="district === '芗城区'"
                                aria-label="筛选芗城区，202条记录"
                                @click="chooseDistrict('芗城区')"
                                @keydown.enter.prevent="
                                    chooseDistrict('芗城区')
                                "
                                @keydown.space.prevent="
                                    chooseDistrict('芗城区')
                                "
                            >
                                <rect
                                    class="bm-region-shape"
                                    x="84"
                                    y="25"
                                    width="250"
                                    height="130"
                                    rx="6"
                                />
                                <text class="bm-region-name" x="209" y="76">
                                    芗城区
                                </text>
                                <text class="bm-region-count" x="209" y="106">
                                    202 条名录
                                </text>
                                <text
                                    v-if="district === '芗城区'"
                                    class="bm-region-hint"
                                    x="209"
                                    y="132"
                                >
                                    已选中
                                </text>
                            </g>
                            <g
                                class="bm-region"
                                :class="{
                                    'bm-region--selected':
                                        district === '龙文区',
                                }"
                                role="button"
                                tabindex="0"
                                :aria-pressed="district === '龙文区'"
                                aria-label="筛选龙文区，102条记录"
                                @click="chooseDistrict('龙文区')"
                                @keydown.enter.prevent="
                                    chooseDistrict('龙文区')
                                "
                                @keydown.space.prevent="
                                    chooseDistrict('龙文区')
                                "
                            >
                                <rect
                                    class="bm-region-shape"
                                    x="446"
                                    y="25"
                                    width="250"
                                    height="130"
                                    rx="6"
                                />
                                <text class="bm-region-name" x="571" y="76">
                                    龙文区
                                </text>
                                <text class="bm-region-count" x="571" y="106">
                                    102 条名录
                                </text>
                                <text
                                    v-if="district === '龙文区'"
                                    class="bm-region-hint"
                                    x="571"
                                    y="132"
                                >
                                    已选中
                                </text>
                            </g>
                        </svg>
                    </div>
                    <div class="bm-district-buttons">
                        <button
                            type="button"
                            :class="{ active: !district }"
                            @click="district = ''"
                        >
                            全部地区 <strong>304</strong>
                        </button>
                        <button
                            v-for="name in districts"
                            :key="name"
                            type="button"
                            :class="{ active: district === name }"
                            @click="district = name"
                        >
                            {{ name }}
                            <strong>{{ districtCount(name) }}</strong>
                        </button>
                    </div>
                </section>

                <section class="sd-panel bm-list-panel">
                    <div class="bm-panel-heading bm-list-heading">
                        <div>
                            <h2>
                                {{ view === 'map' ? '未定位名录' : '小区名录'
                                }}<span class="bm-count-label">{{
                                    filtered.length
                                }}</span>
                            </h2>
                        </div>
                        <span class="bm-compact-note"
                            ><Icon name="phone" />
                            {{ contactCount }} 条有脱敏电话</span
                        >
                    </div>
                    <div class="bm-table-scroll">
                        <table v-if="visibleEntries.length" class="bm-table">
                            <thead>
                                <tr>
                                    <th scope="col">小区 / 所属区域</th>
                                    <th scope="col">物业公司</th>
                                    <th scope="col" class="bm-household-cell">
                                        总户数
                                    </th>
                                    <th scope="col" class="bm-status-cell">
                                        业务情况
                                    </th>
                                    <th scope="col">
                                        <span class="bm-sr-only">查看资料</span>
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr
                                    v-for="entry in visibleEntries"
                                    :key="entry.id"
                                    :class="{
                                        'bm-row--selected':
                                            entry.id === selectedId,
                                    }"
                                    @click="selectEntry(entry)"
                                >
                                    <td>
                                        <button
                                            class="bm-entry-name"
                                            type="button"
                                            @click.stop="selectEntry(entry)"
                                        >
                                            {{ entry.name }}</button
                                        ><span class="bm-entry-location"
                                            >{{ entry.district }} ·
                                            {{
                                                entry.street || '街道未提供'
                                            }}</span
                                        >
                                    </td>
                                    <td>
                                        <span
                                            class="bm-company"
                                            :title="entry.company"
                                            >{{ entry.company }}</span
                                        >
                                    </td>
                                    <td class="bm-household-cell">
                                        {{ householdLabel(entry.households) }}
                                    </td>
                                    <td class="bm-status-cell">
                                        <span class="bm-unknown"
                                            ><span></span> 待核实</span
                                        >
                                    </td>
                                    <td>
                                        <Icon
                                            name="chevron-right"
                                            class="bm-row-arrow"
                                        />
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                        <div v-else class="bm-empty">
                            <Icon name="search" />
                            <h3>没有找到匹配的小区</h3>
                            <button
                                class="sd-button sd-button--quiet"
                                type="button"
                                @click="clearFilters"
                            >
                                查看全部名录
                            </button>
                        </div>
                    </div>
                    <footer v-if="filtered.length" class="bm-pagination">
                        <span
                            >第 {{ offset + 1 }}–{{
                                Math.min(offset + pageSize, filtered.length)
                            }}
                            条，共 {{ filtered.length }} 条</span
                        >
                        <div>
                            <button
                                class="sd-button sd-button--quiet"
                                type="button"
                                :disabled="page === 1"
                                aria-label="上一页"
                                @click="changePage(page - 1)"
                            >
                                <Icon
                                    name="chevron-right"
                                    class="bm-previous"
                                /></button
                            ><span>{{ page }} / {{ pageCount }}</span
                            ><button
                                class="sd-button sd-button--quiet"
                                type="button"
                                :disabled="page === pageCount"
                                aria-label="下一页"
                                @click="changePage(page + 1)"
                            >
                                <Icon name="chevron-right" />
                            </button>
                        </div>
                    </footer>
                </section>
            </div>

            <aside class="sd-panel bm-detail" aria-label="小区资料">
                <template v-if="selected">
                    <div class="bm-detail-heading">
                        <span class="bm-detail-title">小区资料</span
                        ><span class="sd-pill bm-pending-pill">未定位</span>
                    </div>
                    <h2 class="bm-property-name">{{ selected.name }}</h2>
                    <p class="bm-detail-location">
                        <Icon name="pin" /> {{ selected.district }} ·
                        {{ selected.street || '街道未提供' }}
                    </p>
                    <dl class="bm-facts">
                        <div>
                            <dt>所属社区</dt>
                            <dd>
                                {{ selected.community || '未提供' }}
                            </dd>
                        </div>
                        <div>
                            <dt>物业公司</dt>
                            <dd>{{ selected.company }}</dd>
                        </div>
                        <div>
                            <dt>总户数</dt>
                            <dd>{{ householdLabel(selected.households) }}</dd>
                        </div>
                    </dl>

                    <section class="bm-contact-section">
                        <h3>物业联系人</h3>
                        <div class="bm-contact-card">
                            <span class="bm-contact-avatar"
                                ><Icon name="user"
                            /></span>
                            <div>
                                <strong>{{
                                    selected.manager || '负责人待补充'
                                }}</strong
                                ><span>物业负责人 · 待核实</span>
                            </div>
                        </div>
                        <div class="bm-phone">
                            <Icon name="phone" /><span>{{
                                selected.phoneMasked || '电话未提供'
                            }}</span
                            ><small v-if="selected.phoneMasked">已脱敏</small>
                        </div>
                    </section>

                    <section class="bm-business-section">
                        <div class="bm-section-title">
                            <h3>业务情况</h3>
                            <span class="bm-unknown"><span></span> 待核实</span>
                        </div>
                        <dl class="bm-facts bm-facts--compact">
                            <div>
                                <dt>当前需求</dt>
                                <dd class="bm-muted-value">未提供</dd>
                            </div>
                            <div>
                                <dt>业委会信息</dt>
                                <dd class="bm-muted-value">待核实</dd>
                            </div>
                            <div>
                                <dt>业务进展</dt>
                                <dd class="bm-muted-value">待核实</dd>
                            </div>
                        </dl>
                    </section>

                    <section
                        v-if="showForm && canEdit"
                        class="bm-record-form-section"
                    >
                        <div class="bm-section-title">
                            <h3>记录沟通</h3>
                            <button
                                type="button"
                                class="bm-icon-button"
                                aria-label="关闭沟通表单"
                                @click="showForm = false"
                            >
                                <Icon name="close" />
                            </button>
                        </div>
                        <form
                            class="bm-record-form"
                            @submit.prevent="saveCommunication"
                        >
                            <label
                                >需求
                                <textarea
                                    v-model="form.demand"
                                    class="sd-field"
                                    rows="2"
                                    required
                                    maxlength="400"
                                    placeholder="沟通需求"
                                />
                            </label>
                            <label
                                >沟通时间
                                <input
                                    v-model="form.occurredAt"
                                    class="sd-field"
                                    type="datetime-local"
                                    required
                            /></label>
                            <label
                                >联系人
                                <input
                                    v-model="form.contact"
                                    class="sd-field"
                                    type="text"
                                    required
                                    maxlength="60"
                                    placeholder="联系人姓名"
                            /></label>
                            <label
                                >联系人身份
                                <input
                                    v-model="form.contactRole"
                                    class="sd-field"
                                    type="text"
                                    required
                                    maxlength="60"
                                    placeholder="物业负责人 / 业委会委员等"
                            /></label>
                            <label
                                >业务类型
                                <select
                                    v-model="form.businessType"
                                    class="sd-field"
                                    required
                                >
                                    <option
                                        v-for="type in businessTypes"
                                        :key="type"
                                        :value="type"
                                    >
                                        {{ type }}
                                    </option>
                                </select>
                            </label>
                            <label
                                >业务状态
                                <select
                                    v-model="form.businessStatus"
                                    class="sd-field"
                                    required
                                >
                                    <option
                                        v-for="status in businessStatuses"
                                        :key="status"
                                        :value="status"
                                    >
                                        {{ status }}
                                    </option>
                                </select>
                            </label>
                            <label
                                >沟通结果
                                <textarea
                                    v-model="form.result"
                                    class="sd-field"
                                    rows="3"
                                    required
                                    maxlength="600"
                                    placeholder="记录已确认的信息和下一步"
                                />
                            </label>
                            <p class="bm-field-help">
                                本次预览记录 · 刷新后清除
                            </p>
                            <button
                                class="sd-button sd-button--primary bm-full-button"
                                type="submit"
                            >
                                保存记录
                            </button>
                        </form>
                    </section>

                    <section
                        v-if="selectedCommunications.length"
                        class="bm-communications"
                        aria-live="polite"
                    >
                        <h3>
                            本次预览记录
                            <span>{{ selectedCommunications.length }}</span>
                        </h3>
                        <article
                            v-for="record in selectedCommunications"
                            :key="record.id"
                        >
                            <p class="bm-communication-date">
                                {{ record.occurredAt.replace('T', ' ') }} ·
                                {{ record.contact }}
                            </p>
                            <p class="bm-communication-identity">
                                联系人身份：{{ record.contactRole }}
                            </p>
                            <div class="bm-communication-tags">
                                <span>{{ record.businessType }}</span>
                                <span>{{ record.businessStatus }}</span>
                            </div>
                            <h4>{{ record.demand }}</h4>
                            <p>{{ record.result }}</p>
                            <p
                                v-if="record.businessStatus === '已确定委托'"
                                class="bm-project-pending"
                            >
                                待建立项目<span>本次预览尚未创建项目</span>
                            </p>
                        </article>
                    </section>

                    <div v-if="!showForm" class="bm-detail-actions">
                        <button
                            v-if="canEdit"
                            class="sd-button sd-button--primary bm-full-button"
                            type="button"
                            @click="beginCommunication"
                        >
                            <Icon name="plus" /> 记录沟通
                        </button>
                        <span v-else class="bm-readonly-note">只读</span>
                        <button
                            v-if="canEdit"
                            class="sd-button sd-button--quiet bm-full-button"
                            type="button"
                            @click="askAboutEntry"
                        >
                            生成拜访提纲 <Icon name="arrow-right" />
                        </button>
                    </div>
                    <div class="bm-source">
                        <p :title="selected.sourceLabel">
                            来源：{{ selected.sourceLabel }}
                        </p>
                        <small>Sheet1 · 第 {{ selected.sourceRow }} 行</small>
                    </div>
                </template>
                <div v-else class="bm-empty bm-detail-empty">
                    <Icon name="building" />
                    <h3>小区资料</h3>
                    <p>未选择小区</p>
                </div>
            </aside>
        </div>
    </section>
    <section v-else class="sd-panel bm-access-message">
        <Icon name="building" />
        <h2>业务地图暂不向股东开放</h2>
    </section>
</template>

<style scoped>
.bm-workspace {
    color: var(--sd-ink);
}
.bm-workspace :is(button, input, select, textarea) {
    font: inherit;
}
.bm-workspace button {
    cursor: pointer;
}
.bm-workspace button:disabled {
    cursor: default;
    opacity: 0.4;
}
.bm-workspace button:focus-visible,
.bm-region:focus-visible {
    outline: 2px solid var(--sd-accent);
    outline-offset: 3px;
}
.bm-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
}
.bm-heading h1 {
    margin: 0;
    font-size: 24px;
    font-weight: 600;
}
.bm-heading-actions {
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    justify-content: flex-end;
}
.bm-view-switch {
    display: inline-flex;
    padding: 4px;
    border: 1px solid var(--sd-line);
    background: var(--sd-surface);
    border-radius: 6px;
}
.bm-view-switch button {
    display: flex;
    align-items: center;
    gap: 7px;
    border: 0;
    border-radius: 6px;
    padding: 8px 14px;
    background: transparent;
    color: var(--sd-muted);
    font-size: 12px;
}
.bm-view-switch button.active {
    background: var(--sd-accent);
    color: white;
}
.bm-view-switch svg {
    width: 15px;
    height: 15px;
}
.bm-overview {
    display: grid;
    grid-template-columns: repeat(4, minmax(100px, 1fr));
    border-top: 1px solid var(--sd-line);
    border-bottom: 1px solid var(--sd-line);
    margin-bottom: 16px;
    padding: 16px 0;
}
.bm-stat {
    padding: 0 16px;
    border-right: 1px solid var(--sd-line);
}
.bm-stat:first-child {
    padding-left: 0;
}
.bm-stat:last-child {
    border-right: 0;
}
.bm-stat > span {
    display: block;
    color: var(--sd-muted);
    font-size: 11px;
    margin-bottom: 6px;
}
.bm-stat strong {
    display: block;
    font-size: 24px;
    font-weight: 600;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
}
.bm-stat small {
    font-size: 11px;
    font-weight: 400;
    color: var(--sd-muted);
    margin-left: 8px;
    letter-spacing: 0;
}
.bm-stat--quiet strong {
    color: var(--sd-muted);
}
.bm-filterbar {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 12px 16px;
    margin-bottom: 16px;
    border: 1px solid var(--sd-line);
    background: var(--sd-surface);
    border-radius: 6px;
}
.bm-search {
    display: flex;
    align-items: center;
    gap: 9px;
    flex: 1;
    min-width: 180px;
}
.bm-search svg {
    width: 17px;
    height: 17px;
    color: var(--sd-muted);
    flex-shrink: 0;
}
.bm-search input {
    width: 100%;
    background: transparent;
    border: 0;
    color: var(--sd-ink);
    outline: 0;
    padding: 6px 0;
    font-size: 12px;
}
.bm-search:focus-within {
    outline: 2px solid var(--sd-soft);
    outline-offset: 5px;
    border-radius: 3px;
}
.bm-search input::placeholder {
    color: var(--sd-muted);
}
.bm-select-label {
    display: flex;
    align-items: center;
    gap: 9px;
    flex-shrink: 0;
}
.bm-select-label > span {
    font-size: 11px;
    color: var(--sd-muted);
    white-space: nowrap;
}
.bm-select-label select {
    max-width: 180px;
    min-width: 108px;
    padding: 7px 28px 7px 10px;
    font-size: 11px;
    background: var(--sd-bg);
    border: 1px solid var(--sd-line);
    border-radius: 5px;
    color: var(--sd-ink);
}
.bm-clear {
    font-size: 11px;
    white-space: nowrap;
}
.bm-columns {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 310px;
    align-items: start;
    gap: 16px;
}
.bm-main {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 16px;
}
.bm-map-panel,
.bm-list-panel,
.bm-detail {
    border: 1px solid var(--sd-line);
    border-radius: 6px;
    background: var(--sd-surface);
    overflow: hidden;
}
.bm-panel-heading {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    padding: 16px;
}
.bm-panel-heading h2 {
    font-size: 14px;
    line-height: 1.4;
    font-weight: 600;
    margin: 0;
}
.bm-map-label {
    color: var(--sd-muted);
    font-size: 10px;
    white-space: nowrap;
    display: flex;
    align-items: center;
    gap: 6px;
}
.bm-map-label > span {
    display: block;
    border: 1px solid var(--sd-muted);
    border-radius: 50%;
    width: 5px;
    height: 5px;
}
.bm-map-canvas {
    background: var(--sd-bg);
    border-top: 1px solid var(--sd-line);
    border-bottom: 1px solid var(--sd-line);
}
.bm-map-canvas > svg {
    display: block;
    width: 100%;
    height: 150px;
}
.bm-map-connection {
    fill: none;
    stroke: var(--sd-line);
    stroke-width: 1.5;
}
.bm-region {
    cursor: pointer;
}
.bm-region-shape {
    fill: var(--sd-surface);
    stroke: var(--sd-line);
    stroke-width: 1.5;
    transition:
        fill 0.18s,
        stroke 0.18s;
}
.bm-region:hover .bm-region-shape,
.bm-region:focus-visible .bm-region-shape {
    fill: var(--sd-soft);
    stroke: var(--sd-accent);
}
.bm-region--selected .bm-region-shape {
    fill: var(--sd-soft);
    stroke: var(--sd-accent);
    stroke-width: 2;
}
.bm-region text {
    text-anchor: middle;
    pointer-events: none;
}
.bm-region-name {
    font-size: 20px;
    font-weight: 600;
    fill: var(--sd-ink);
}
.bm-region-count {
    font-size: 14px;
    fill: var(--sd-accent);
}
.bm-region-hint {
    font-size: 10px;
    fill: var(--sd-muted);
}
.bm-district-buttons {
    padding: 8px 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
}
.bm-district-buttons button {
    border: 1px solid transparent;
    background: transparent;
    border-radius: 5px;
    color: var(--sd-muted);
    padding: 7px 10px;
    font-size: 11px;
}
.bm-district-buttons button.active {
    color: var(--sd-accent);
    background: var(--sd-soft);
}
.bm-district-buttons strong {
    margin-left: 8px;
    font-size: 10px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
}
.bm-list-heading h2 {
    margin: 0;
    display: flex;
    align-items: center;
    gap: 9px;
}
.bm-count-label {
    border: 1px solid var(--sd-line);
    color: var(--sd-muted);
    font-size: 10px;
    font-weight: 500;
    padding: 2px 6px;
    border-radius: 4px;
}
.bm-compact-note {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 10px;
    color: var(--sd-muted);
    white-space: nowrap;
}
.bm-compact-note svg {
    width: 12px;
    height: 12px;
}
.bm-table-scroll {
    overflow-x: auto;
}
.bm-table {
    width: 100%;
    border-collapse: collapse;
    text-align: left;
    table-layout: fixed;
}
.bm-table th {
    background: var(--sd-bg);
    color: var(--sd-muted);
    border-top: 1px solid var(--sd-line);
    border-bottom: 1px solid var(--sd-line);
    padding: 10px 12px;
    font-size: 10px;
    font-weight: 500;
}
.bm-table th:first-child {
    width: 36%;
    padding-left: 16px;
}
.bm-table th:nth-child(2) {
    width: 27%;
}
.bm-table th:nth-child(3) {
    width: 16%;
}
.bm-table th:nth-child(4) {
    width: 16%;
}
.bm-table th:last-child {
    width: 5%;
    padding: 0 8px 0 0;
}
.bm-table td {
    border-bottom: 1px solid var(--sd-line);
    padding: 11px 12px;
    font-size: 11px;
    vertical-align: middle;
}
.bm-table td:first-child {
    padding-left: 16px;
    position: relative;
}
.bm-table td:last-child {
    padding: 0 8px 0 0;
}
.bm-table tr:last-child td {
    border-bottom: 0;
}
.bm-table tbody tr {
    cursor: pointer;
    transition: background 0.15s;
}
.bm-table tbody tr:hover {
    background: var(--sd-bg);
}
.bm-table tbody tr.bm-row--selected {
    background: var(--sd-soft);
}
.bm-row--selected td:first-child::before {
    content: '';
    position: absolute;
    left: 0;
    top: 12px;
    bottom: 12px;
    width: 3px;
    background: var(--sd-accent);
    border-radius: 0 2px 2px 0;
}
.bm-entry-name {
    display: block;
    padding: 0;
    margin: 0 0 3px;
    background: transparent;
    border: 0;
    text-align: left;
    color: var(--sd-ink);
    line-height: 1.5;
    font-size: 12px;
    font-weight: 550;
}
.bm-row--selected .bm-entry-name {
    color: var(--sd-accent);
}
.bm-entry-location {
    display: block;
    color: var(--sd-muted);
    font-size: 9px;
    line-height: 1.6;
}
.bm-company {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    line-height: 1.6;
    color: var(--sd-muted);
    font-size: 10px;
}
.bm-household-cell {
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
}
.bm-unknown {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--sd-muted);
    font-size: 10px;
    white-space: nowrap;
}
.bm-unknown > span {
    width: 4px;
    height: 4px;
    background: var(--sd-muted);
    border-radius: 50%;
}
.bm-row-arrow {
    width: 13px;
    height: 13px;
    color: var(--sd-muted);
}
.bm-pagination {
    border-top: 1px solid var(--sd-line);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    gap: 12px;
    color: var(--sd-muted);
    font-size: 10px;
}
.bm-pagination > div {
    display: flex;
    align-items: center;
    gap: 12px;
}
.bm-pagination button {
    padding: 4px;
    min-height: 25px;
}
.bm-pagination svg {
    width: 14px;
    height: 14px;
}
.bm-previous {
    transform: rotate(180deg);
}
.bm-detail {
    padding: 16px;
    position: sticky;
    top: 16px;
}
.bm-detail-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
}
.bm-detail-title {
    font-size: 12px;
    font-weight: 600;
}
.bm-pending-pill {
    background: var(--sd-bg);
    color: var(--sd-muted);
    border: 1px solid var(--sd-line);
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 9px;
}
.bm-property-name {
    font-size: 18px;
    line-height: 1.45;
    font-weight: 600;
    margin: 16px 0 8px;
}
.bm-detail-location {
    display: flex;
    gap: 5px;
    align-items: center;
    font-size: 11px;
    color: var(--sd-muted);
    margin: 0;
    line-height: 1.6;
}
.bm-detail-location svg {
    width: 12px;
    height: 12px;
    flex-shrink: 0;
}
.bm-facts {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin: 16px 0;
}
.bm-facts > div {
    display: grid;
    grid-template-columns: 65px 1fr;
    gap: 10px;
    line-height: 1.65;
    font-size: 11px;
}
.bm-facts dt {
    color: var(--sd-muted);
}
.bm-facts dd {
    margin: 0;
    overflow-wrap: anywhere;
}
.bm-detail h3 {
    font-size: 12px;
    font-weight: 600;
    line-height: 1.5;
    margin: 0;
}
.bm-contact-section,
.bm-business-section,
.bm-record-form-section,
.bm-communications {
    border-top: 1px solid var(--sd-line);
    padding-top: 16px;
    margin-top: 16px;
}
.bm-contact-card {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 12px;
}
.bm-contact-avatar {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 33px;
    height: 33px;
    border-radius: 50%;
    background: var(--sd-bg);
    color: var(--sd-muted);
}
.bm-contact-avatar svg {
    width: 16px;
    height: 16px;
}
.bm-contact-card strong {
    display: block;
    font-size: 12px;
    font-weight: 550;
    line-height: 1.5;
}
.bm-contact-card div > span {
    display: block;
    color: var(--sd-muted);
    margin-top: 2px;
    font-size: 9px;
}
.bm-phone {
    display: flex;
    gap: 7px;
    align-items: center;
    margin-top: 12px;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
}
.bm-phone svg {
    width: 13px;
    height: 13px;
    color: var(--sd-muted);
}
.bm-phone small {
    margin-left: auto;
    font-size: 9px;
    color: var(--sd-muted);
}
.bm-field-help {
    margin: 11px 0 0;
    color: var(--sd-muted);
    font-size: 9px;
    line-height: 1.8;
}
.bm-section-title {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    align-items: center;
}
.bm-facts--compact {
    gap: 11px;
    margin: 12px 0 0;
}
.bm-muted-value {
    color: var(--sd-muted);
}
.bm-detail-actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 16px;
}
.bm-full-button {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    min-height: 37px;
    font-size: 11px;
    border-radius: 6px;
}
.bm-full-button svg {
    width: 14px;
    height: 14px;
}
.bm-readonly-note {
    margin: 0 0 5px;
    font-size: 10px;
    line-height: 1.7;
    color: var(--sd-muted);
}
.bm-source {
    border-top: 1px solid var(--sd-line);
    padding-top: 16px;
    margin-top: 16px;
    color: var(--sd-muted);
}
.bm-source p {
    margin: 0 0 3px;
    font-size: 9px;
    overflow-wrap: anywhere;
    line-height: 1.6;
}
.bm-source small {
    font-size: 9px;
}
.bm-icon-button {
    background: transparent;
    border: 0;
    padding: 0;
    color: var(--sd-muted);
    display: flex;
}
.bm-icon-button svg {
    width: 15px;
    height: 15px;
}
.bm-record-form {
    display: flex;
    flex-direction: column;
    gap: 13px;
    margin-top: 15px;
}
.bm-record-form label {
    display: flex;
    flex-direction: column;
    gap: 7px;
    font-size: 10px;
}
.bm-record-form .sd-field {
    width: 100%;
    box-sizing: border-box;
    border: 1px solid var(--sd-line);
    border-radius: 5px;
    background: var(--sd-surface);
    color: var(--sd-ink);
    padding: 9px;
    font-size: 11px;
    line-height: 1.5;
}
.bm-record-form .sd-field:focus {
    outline: 1px solid var(--sd-accent);
    border-color: var(--sd-accent);
}
.bm-record-form textarea {
    resize: vertical;
}
.bm-record-form .bm-field-help {
    margin: 0;
}
.bm-communications h3 span {
    color: var(--sd-muted);
    font-size: 10px;
    margin-left: 5px;
}
.bm-communications article {
    margin-top: 15px;
    padding-left: 12px;
    border-left: 2px solid var(--sd-line);
}
.bm-communications article p {
    font-size: 10px;
    line-height: 1.75;
    margin: 5px 0 0;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
}
.bm-communications article p.bm-communication-date {
    font-size: 9px;
    color: var(--sd-muted);
}
.bm-communications h4 {
    font-size: 11px;
    font-weight: 550;
    margin: 7px 0 0;
    overflow-wrap: anywhere;
}
.bm-communications article p.bm-communication-identity {
    color: var(--sd-muted);
    font-size: 9px;
}
.bm-communication-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 8px;
}
.bm-communication-tags span {
    border-radius: 4px;
    background: var(--sd-bg);
    color: var(--sd-muted);
    padding: 3px 6px;
    font-size: 9px;
}
.bm-communications article p.bm-project-pending {
    margin-top: 10px;
    border-left: 2px solid var(--sd-accent);
    padding-left: 8px;
    color: var(--sd-accent);
    font-size: 10px;
}
.bm-project-pending span {
    display: block;
    color: var(--sd-muted);
    font-size: 9px;
}
.bm-empty {
    padding: 40px 16px;
    text-align: center;
    color: var(--sd-muted);
}
.bm-empty > svg {
    width: 28px;
    height: 28px;
}
.bm-empty h3 {
    margin: 14px 0 8px;
    color: var(--sd-ink);
    font-size: 14px;
}
.bm-empty p {
    font-size: 11px;
    line-height: 1.8;
    margin: 0 0 16px;
}
.bm-detail-empty {
    padding: 40px 0;
}
.bm-access-message {
    padding: 40px 16px;
    text-align: center;
    color: var(--sd-muted);
}
.bm-access-message > svg {
    width: 34px;
    height: 34px;
}
.bm-access-message h2 {
    color: var(--sd-ink);
    font-size: 20px;
}
.bm-sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
}
@media (max-width: 1200px) {
    .bm-columns {
        grid-template-columns: minmax(0, 1fr) 285px;
        gap: 16px;
    }
    .bm-filterbar {
        gap: 13px;
    }
    .bm-select-label > span {
        display: none;
    }
    .bm-overview {
        grid-template-columns: repeat(4, minmax(85px, 1fr));
    }
    .bm-stat:last-of-type {
        border-right: 0;
    }
    .bm-table th,
    .bm-table td {
        padding-left: 11px;
        padding-right: 11px;
    }
    .bm-table th:first-child,
    .bm-table td:first-child {
        padding-left: 16px;
    }
    .bm-table th:nth-child(2) {
        width: 28%;
    }
    .bm-map-label {
        font-size: 9px;
    }
}
@media (max-width: 960px) {
    .bm-columns {
        grid-template-columns: minmax(0, 1fr);
    }
    .bm-detail {
        position: static;
    }
    .bm-detail .bm-facts {
        max-width: 650px;
    }
    .bm-filterbar {
        flex-wrap: wrap;
    }
    .bm-search {
        flex-basis: 100%;
        padding-bottom: 10px;
        border-bottom: 1px solid var(--sd-line);
    }
    .bm-select-label > span {
        display: inline;
    }
    .bm-detail-actions {
        max-width: 400px;
    }
}
@media (max-width: 600px) {
    .bm-heading {
        align-items: flex-start;
        gap: 12px;
    }
    .bm-heading h1 {
        font-size: 22px;
    }
    .bm-view-switch button {
        padding: 8px 10px;
    }
    .bm-view-switch svg {
        display: none;
    }
    .bm-stat {
        padding: 0 10px;
    }
    .bm-stat strong {
        font-size: 23px;
    }
    .bm-stat small {
        display: block;
        margin: 5px 0 0;
        font-size: 9px;
    }
    .bm-stat > span {
        font-size: 10px;
    }
    .bm-overview {
        grid-template-columns: repeat(4, 1fr);
    }
    .bm-street-filter {
        flex: 1;
    }
    .bm-street-filter select {
        min-width: 0;
        max-width: 135px;
    }
    .bm-select-label > span {
        display: none;
    }
    .bm-panel-heading {
        padding: 16px;
    }
    .bm-map-label {
        max-width: 86px;
        white-space: normal;
        line-height: 1.5;
    }
    .bm-map-canvas > svg {
        height: 120px;
    }
    .bm-compact-note {
        display: none;
    }
    .bm-table {
        min-width: 510px;
    }
    .bm-pagination {
        padding-left: 16px;
        font-size: 9px;
    }
    .bm-pagination > div {
        gap: 8px;
    }
}
</style>
