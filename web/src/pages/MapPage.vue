<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
    directoryApi,
    directoryErrorMessage,
    type CommunicationStatus,
    type DirectoryCommunication,
    type DirectoryEntry,
} from '../api/directory'
import { isRecord } from '../api/parse'
import EmptyLine from '../components/ui/EmptyLine.vue'
import InlineError from '../components/ui/InlineError.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import {
    communicationStatusLabel,
    directoryCopy,
} from '../copy/pages/directory'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const canEdit = computed(() => auth.user?.role === 'OWNER')
const query = ref('')
const district = ref('')
const street = ref('')
const items = ref<DirectoryEntry[]>([])
const total = ref(0)
const selectedId = ref('')
const communications = ref<DirectoryCommunication[]>([])
const loading = ref(false)
const errorMessage = ref('')
const notice = ref('')
const showForm = ref(false)
const demand = ref('')
const result = ref('')
const nextStep = ref('')
const contactName = ref('')
const contactRole = ref('')
const occurredOn = ref(new Date().toISOString().slice(0, 10))
const status = ref<CommunicationStatus>('LEARNING')
const fileInput = ref<HTMLInputElement | null>(null)
const offset = ref(0)
const pageSize = 50
const allDistricts = ref<string[]>([])
const allStreets = ref<string[]>([])
const streetsByDistrict = ref<Record<string, string[]>>({})
const conflicts = ref<unknown[]>([])
const conflictTotal = ref(0)
const conflictOffset = ref(0)
const conflictPageSize = 50
const resolvingConflictId = ref('')

const selected = computed(
    () =>
        items.value.find((item) => item.id === selectedId.value) ??
        items.value[0],
)
const districts = computed(() =>
    (allDistricts.value.length
        ? allDistricts.value
        : [...new Set(items.value.map((item) => item.district).filter(Boolean))]
    ).sort((left, right) => left.localeCompare(right, 'zh-CN')),
)
const streets = computed(() => {
    const fromFacets = district.value
        ? streetsByDistrict.value[district.value] || []
        : allStreets.value
    if (fromFacets.length) return fromFacets
    return [
        ...new Set(
            items.value
                .filter(
                    (item) =>
                        !district.value || item.district === district.value,
                )
                .map((item) => item.street)
                .filter(Boolean),
        ),
    ].sort((left, right) => left.localeCompare(right, 'zh-CN'))
})
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        await loadFacets()
        if (district.value && street.value) {
            const allowed = streetsByDistrict.value[district.value] || []
            if (!allowed.includes(street.value)) {
                street.value = ''
                return
            }
        }
        const listed = await directoryApi.list({
            q: query.value.trim() || undefined,
            district: district.value || undefined,
            street: street.value || undefined,
            offset: offset.value,
            limit: pageSize,
        })
        items.value = listed.items
        total.value = listed.total
        if (!items.value.some((item) => item.id === selectedId.value)) {
            selectedId.value = items.value[0]?.id ?? ''
        }
        await loadCommunications()
    } catch (error) {
        errorMessage.value = directoryCopy.loadFailed
        void directoryErrorMessage(error)
    } finally {
        loading.value = false
    }
}

async function loadFacets(): Promise<void> {
    const facets = await directoryApi.facets().catch(() => null)
    if (!facets) return
    allDistricts.value = facets.districts
    allStreets.value = facets.streets
    streetsByDistrict.value = facets.streets_by_district
}

async function loadConflicts(append = false): Promise<void> {
    if (!canEdit.value) return
    const listed = await directoryApi
        .listConflicts({
            offset: conflictOffset.value,
            limit: conflictPageSize,
        })
        .catch(() => ({ items: [], total: 0 }))
    conflicts.value = append
        ? [...conflicts.value, ...listed.items]
        : listed.items
    conflictTotal.value = listed.total
}

function conflictPayload(value: unknown): Record<string, unknown> {
    if (!isRecord(value)) return {}
    return isRecord(value.payload) ? value.payload : value
}

function conflictLabel(value: unknown): string {
    if (!value || typeof value !== 'object') return ''
    const row = value as Record<string, unknown>
    const payload = conflictPayload(value)
    const reason = String(row.reason || payload.reason || '')
    const incoming = String(payload.incoming_name || '')
    const existing = String(payload.existing_name || '')
    const sourceRow = payload.row ?? row.row
    return [
        reason,
        sourceRow != null ? `第${sourceRow}行` : '',
        incoming,
        existing,
    ]
        .filter(Boolean)
        .join(' · ')
}

function conflictScale(value: unknown): string {
    const payload = conflictPayload(value)
    const incomingHouseholds = payload.incoming_households
    const existingHouseholds = payload.existing_households
    const incomingArea = payload.incoming_floor_area
    const existingArea = payload.existing_floor_area
    return [
        incomingHouseholds != null || incomingArea
            ? `${directoryCopy.incomingScale} ${incomingHouseholds ?? directoryCopy.unknown}${directoryCopy.households} / ${incomingArea || directoryCopy.unknown}`
            : '',
        existingHouseholds != null || existingArea
            ? `${directoryCopy.existingScale} ${existingHouseholds ?? directoryCopy.unknown}${directoryCopy.households} / ${existingArea || directoryCopy.unknown}`
            : '',
        payload.incoming_property_company
            ? `${directoryCopy.incomingCompany} ${payload.incoming_property_company}`
            : '',
        payload.existing_property_company
            ? `${directoryCopy.existingCompany} ${payload.existing_property_company}`
            : '',
    ]
        .filter(Boolean)
        .join(' · ')
}

function loadMoreConflicts(): void {
    conflictOffset.value += conflictPageSize
    void loadConflicts(true)
}

async function resolveConflict(
    value: unknown,
    action: 'link' | 'split',
): Promise<void> {
    if (!isRecord(value) || typeof value.id !== 'string') return
    if (resolvingConflictId.value) return
    resolvingConflictId.value = value.id
    try {
        await directoryApi.resolveConflict(value.id, action)
        conflictOffset.value = 0
        await loadConflicts(false)
        await load()
    } catch (error) {
        errorMessage.value = directoryErrorMessage(error)
    } finally {
        resolvingConflictId.value = ''
    }
}

async function loadCommunications(): Promise<void> {
    if (!selectedId.value) {
        communications.value = []
        return
    }
    communications.value = await directoryApi.listCommunications(
        selectedId.value,
    )
}

async function importFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file || !canEdit.value) return
    try {
        const result = await directoryApi.importFile(file)
        conflictOffset.value = 0
        await loadConflicts()
        const conflictCount =
            conflictTotal.value || (result.conflicts || []).length
        notice.value = conflictCount
            ? `${directoryCopy.importOk} ${result.inserted} / ${result.updated}，${directoryCopy.conflicts} ${conflictCount}`
            : `${directoryCopy.importOk} ${result.inserted} / ${result.updated}`
        await loadFacets()
        await load()
    } catch {
        errorMessage.value = directoryCopy.importFailed
    }
}

async function saveCommunication(): Promise<void> {
    if (!canEdit.value || !selected.value) return
    try {
        await directoryApi.addCommunication(selected.value.id, {
            occurred_on: occurredOn.value,
            contact_name: contactName.value.trim(),
            contact_role: contactRole.value.trim(),
            demand: demand.value.trim(),
            result: result.value.trim(),
            next_step: nextStep.value.trim(),
            status: status.value,
        })
        showForm.value = false
        demand.value = ''
        result.value = ''
        await loadCommunications()
    } catch (error) {
        errorMessage.value = directoryErrorMessage(error)
    }
}

function choose(entry: DirectoryEntry): void {
    selectedId.value = entry.id
    showForm.value = false
}

async function convertSelected(): Promise<void> {
    if (!canEdit.value || !selected.value) return
    try {
        const project = await directoryApi.convertToProject(selected.value.id, {
            name: `${selected.value.name}会务`,
            new_engagement: Boolean(selected.value.project_id),
        })
        notice.value = `${directoryCopy.converted} ${project.name}`
        await load()
        await router.push(`/projects/${project.id}`)
    } catch (error) {
        errorMessage.value = directoryErrorMessage(error)
    }
}

watch(district, (value) => {
    if (!street.value || !value) return
    const allowed = streetsByDistrict.value[value] || []
    if (!allowed.includes(street.value)) {
        street.value = ''
    }
})
watch([query, district, street], () => {
    offset.value = 0
    void load()
})
watch(offset, () => {
    void load()
})
watch(selectedId, () => {
    void loadCommunications()
})
onMounted(() => {
    void load()
    void loadConflicts()
})
</script>

<template>
    <section class="map-page" aria-labelledby="map-title">
        <PageHeader :title="directoryCopy.title" heading-id="map-title">
            <span class="muted">{{ total }}</span>
            <template v-if="canEdit">
                <input
                    ref="fileInput"
                    class="sr-only"
                    type="file"
                    accept=".xlsx"
                    @change="importFile"
                />
                <el-button text @click="fileInput?.click()">{{
                    directoryCopy.import
                }}</el-button>
            </template>
        </PageHeader>
        <InlineError :message="errorMessage || notice" />
        <p v-if="conflicts.length" class="muted">
            {{ directoryCopy.conflictHint }}
        </p>
        <ul v-if="conflicts.length" class="conflicts">
            <li
                v-for="(item, index) in conflicts"
                :key="isRecord(item) && item.id ? String(item.id) : index"
            >
                <p>{{ conflictLabel(item) }}</p>
                <p v-if="conflictScale(item)" class="muted">
                    {{ conflictScale(item) }}
                </p>
                <el-button
                    v-if="isRecord(item) && item.id"
                    text
                    :disabled="Boolean(resolvingConflictId)"
                    @click="resolveConflict(item, 'link')"
                    >{{ directoryCopy.confirmLink }}</el-button
                >
                <el-button
                    v-if="isRecord(item) && item.id"
                    text
                    :disabled="Boolean(resolvingConflictId)"
                    @click="resolveConflict(item, 'split')"
                    >{{ directoryCopy.keepSeparate }}</el-button
                >
            </li>
        </ul>
        <p v-if="conflictTotal > conflicts.length" class="muted">
            {{ directoryCopy.conflicts }} {{ conflictTotal }}
            <el-button
                v-if="conflictOffset + conflictPageSize < conflictTotal"
                text
                @click="loadMoreConflicts"
                >{{ directoryCopy.reviewConflicts }}</el-button
            >
        </p>
        <div class="toolbar">
            <el-input v-model="query" :placeholder="directoryCopy.search" />
            <el-select
                v-model="district"
                clearable
                :placeholder="directoryCopy.district"
            >
                <el-option
                    v-for="item in districts"
                    :key="item"
                    :label="item"
                    :value="item"
                />
            </el-select>
            <el-select
                v-model="street"
                clearable
                :placeholder="directoryCopy.street"
            >
                <el-option
                    v-for="item in streets"
                    :key="item"
                    :label="item"
                    :value="item"
                />
            </el-select>
        </div>
        <div v-loading="loading" class="layout">
            <ul class="rows">
                <li
                    v-for="item in items"
                    :key="item.id"
                    :class="{ active: item.id === selected?.id }"
                >
                    <button type="button" @click="choose(item)">
                        <strong>{{ item.name }}</strong>
                        <span
                            >{{ item.district }} {{ item.street }}
                            {{ item.community }}</span
                        >
                    </button>
                </li>
            </ul>
            <EmptyLine v-if="!items.length" :message="directoryCopy.empty" />
            <div v-if="total > pageSize" class="pager">
                <el-button
                    text
                    :disabled="offset === 0"
                    @click="offset = Math.max(0, offset - pageSize)"
                    >上一页</el-button
                >
                <span
                    >{{ Math.floor(offset / pageSize) + 1 }} /
                    {{ pageCount }}</span
                >
                <el-button
                    text
                    :disabled="offset + pageSize >= total"
                    @click="offset += pageSize"
                    >下一页</el-button
                >
            </div>
            <aside v-if="selected">
                <h2>{{ selected.name }}</h2>
                <p>
                    {{ selected.district }} · {{ selected.street }} ·
                    {{ selected.community || directoryCopy.unknown }}
                </p>
                <p>
                    {{ directoryCopy.company }}：{{
                        selected.property_company || directoryCopy.unknown
                    }}
                </p>
                <p>
                    {{ directoryCopy.manager }}：{{
                        selected.manager_name || directoryCopy.unknown
                    }}
                    ·
                    {{
                        selected.phone ||
                        selected.phone_masked ||
                        directoryCopy.unknown
                    }}
                </p>
                <p>
                    {{ directoryCopy.source }}：{{
                        selected.source_filename
                    }}
                    #{{ selected.source_row }}
                </p>
                <p v-if="selected.extra && selected.extra['业委会']">
                    业委会：{{ String(selected.extra['业委会']) }}
                </p>
                <p v-if="selected.extra && selected.extra['临委会']">
                    临委会：{{ String(selected.extra['临委会']) }}
                </p>
                <p v-if="selected.extra && selected.extra['定位']">
                    {{ String(selected.extra['定位']) }}
                </p>
                <el-button
                    v-if="canEdit && !selected.project_id"
                    text
                    @click="convertSelected"
                    >{{ directoryCopy.convert }}</el-button
                >
                <el-button
                    v-if="selected.project_id"
                    text
                    @click="router.push(`/projects/${selected.project_id}`)"
                    >{{ directoryCopy.openProject }}</el-button
                >
                <el-button
                    v-if="canEdit && selected.project_id"
                    text
                    @click="convertSelected"
                    >{{ directoryCopy.newEngagement }}</el-button
                >
                <h3>{{ directoryCopy.communications }}</h3>
                <el-button v-if="canEdit" text @click="showForm = true">{{
                    directoryCopy.addCommunication
                }}</el-button>
                <form
                    v-if="showForm"
                    class="form"
                    @submit.prevent="saveCommunication"
                >
                    <label>{{ directoryCopy.occurredOn }}</label>
                    <el-date-picker
                        v-model="occurredOn"
                        type="date"
                        value-format="YYYY-MM-DD"
                    />
                    <label>{{ directoryCopy.contact }}</label>
                    <el-input v-model="contactName" />
                    <label>{{ directoryCopy.contactRole }}</label>
                    <el-input v-model="contactRole" />
                    <label>{{ directoryCopy.demand }}</label>
                    <el-input v-model="demand" type="textarea" />
                    <label>{{ directoryCopy.result }}</label>
                    <el-input v-model="result" type="textarea" />
                    <label>{{ directoryCopy.status }}</label>
                    <el-select v-model="status">
                        <el-option
                            v-for="(label, key) in communicationStatusLabel"
                            :key="key"
                            :label="label"
                            :value="key"
                        />
                    </el-select>
                    <label>{{ directoryCopy.nextStep }}</label>
                    <el-input v-model="nextStep" />
                    <el-button type="primary" native-type="submit">{{
                        directoryCopy.save
                    }}</el-button>
                </form>
                <ol>
                    <li v-for="item in communications" :key="item.id">
                        <strong>{{ item.occurred_on }}</strong>
                        {{ item.contact_name }} · {{ item.contact_role }} ·
                        {{ communicationStatusLabel[item.status] }}
                        <p>{{ item.demand }}</p>
                        <p v-if="item.result">{{ item.result }}</p>
                        <p v-if="item.next_step">{{ item.next_step }}</p>
                    </li>
                </ol>
            </aside>
        </div>
    </section>
</template>

<style scoped>
.muted {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
.conflicts {
    list-style: none;
    margin: 0 0 16px;
    padding: 0;
    font-size: var(--sb-sm);
    color: var(--sb-ink-2);
}
.toolbar {
    display: grid;
    grid-template-columns: 1fr 160px 160px;
    gap: 8px;
    margin-bottom: 16px;
}
.layout {
    display: grid;
    grid-template-columns: minmax(240px, 1fr) minmax(280px, 1fr);
    gap: 24px;
}
.rows {
    list-style: none;
    margin: 0;
    padding: 0;
}
.rows li {
    border-bottom: 1px solid var(--sb-line);
}
.rows button {
    display: grid;
    width: 100%;
    padding: 10px 0;
    text-align: left;
    background: none;
    border: 0;
    color: inherit;
    cursor: pointer;
}
.rows .active strong {
    color: var(--sb-accent);
}
.rows span,
aside p {
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
.form {
    display: grid;
    gap: 8px;
    margin: 12px 0;
}
@media (max-width: 760px) {
    .toolbar,
    .layout {
        grid-template-columns: 1fr;
    }
}
</style>
