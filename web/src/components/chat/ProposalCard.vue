<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import type { AgentCard } from '../../api/agent'
import { moneyLabel } from '../../api/parse'
import { FIELD_LABEL } from '../../copy/glossary'
import { chatCopy } from '../../copy/pages/chat'
import {
    committedHref,
    committedLabel,
    displayRows,
    draftsFromPayload,
    editFields,
    headline,
    kindLabel,
    payloadFromDrafts,
} from './cardFields'

const props = defineProps<{
    card: AgentCard
    projectNames: Record<string, string>
    folderNames?: Record<string, string>
}>()

const emit = defineEmits<{
    confirm: []
    reject: []
    revise: [instruction: string]
    patch: [payload: Record<string, unknown>, note: string]
}>()

const revising = ref(false)
const editing = ref(false)
const retries = ref(0)
const instruction = ref('')
const drafts = reactive<Record<string, string>>({})
const fields = computed(() => editFields(props.card.kind))
const rows = computed(() =>
    displayRows(props.card, props.projectNames, props.folderNames ?? {}),
)
const title = computed(() => headline(props.card, props.projectNames))
const amountText = computed(() => {
    const cents = props.card.payload.amount_cents
    return typeof cents === 'number' ? moneyLabel(cents) : ''
})

watch(
    () => props.card.payload,
    (payload) => {
        Object.assign(drafts, draftsFromPayload(payload))
    },
    { immediate: true },
)

function beginEdit(): void {
    Object.assign(drafts, draftsFromPayload(props.card.payload))
    editing.value = true
}

function retry(): void {
    retries.value += 1
    emit('confirm')
}

function submitPatch(): void {
    emit('patch', payloadFromDrafts(drafts, props.card.payload), '')
    editing.value = false
}

function submitRevise(): void {
    if (!instruction.value.trim()) return
    emit('revise', instruction.value.trim())
    instruction.value = ''
    revising.value = false
}
</script>

<template>
    <article
        class="proposal"
        :data-status="card.status"
        :class="{
            'proposal--folded':
                card.status === 'COMMITTED' ||
                card.status === 'REJECTED' ||
                card.status === 'REVISED' ||
                card.status === 'FAILED',
        }"
    >
        <p v-if="card.status === 'COMMITTED'" class="folded folded--ok">
            {{ chatCopy.committed }} · {{ kindLabel(card.kind) }} {{ title }}
            <span v-if="amountText">{{ amountText }}</span>
            <router-link :to="committedHref(card)">{{
                committedLabel(card.kind)
            }}</router-link>
        </p>
        <p v-else-if="card.status === 'REJECTED'" class="folded">
            {{ chatCopy.rejected }} · {{ kindLabel(card.kind) }} {{ title }}
        </p>
        <p v-else-if="card.status === 'REVISED'" class="folded">
            {{ chatCopy.revised }} · {{ kindLabel(card.kind) }} {{ title }}
        </p>
        <p v-else-if="card.status === 'FAILED'" class="folded folded--danger">
            {{ chatCopy.failed }} · {{ kindLabel(card.kind) }} {{ title }}
            <el-button
                v-if="retries < 2"
                text
                native-type="button"
                @click="retry"
                >{{ chatCopy.retry }}</el-button
            >
            <el-button v-else text native-type="button" @click="beginEdit">{{
                chatCopy.editFields
            }}</el-button>
        </p>
        <template v-else>
            <header>
                <strong>{{ kindLabel(card.kind) }}</strong>
            </header>
            <form v-if="editing" class="edit" @submit.prevent="submitPatch">
                <label v-for="field in fields" :key="field.key">
                    {{ field.label }}
                    <el-select
                        v-if="field.type === 'select'"
                        v-model="drafts[field.key]"
                    >
                        <el-option
                            v-for="option in field.options"
                            :key="option.value"
                            :label="option.label"
                            :value="option.value"
                        />
                    </el-select>
                    <el-select
                        v-else-if="field.type === 'project'"
                        v-model="drafts[field.key]"
                    >
                        <el-option
                            v-for="(name, id) in projectNames"
                            :key="id"
                            :label="name"
                            :value="id"
                        />
                    </el-select>
                    <el-date-picker
                        v-else-if="field.type === 'date'"
                        v-model="drafts[field.key]"
                        type="date"
                        value-format="YYYY-MM-DD"
                    />
                    <el-input v-else v-model="drafts[field.key]" />
                </label>
                <template v-if="!fields.length">
                    <label v-for="(value, key) in drafts" :key="String(key)">
                        {{
                            key === 'category'
                                ? FIELD_LABEL.category
                                : String(key)
                        }}
                        <el-input v-model="drafts[key]" />
                    </label>
                </template>
                <el-button native-type="submit">{{
                    chatCopy.saveFields
                }}</el-button>
            </form>
            <dl v-else>
                <div
                    v-for="row in rows"
                    :key="row.label"
                    :class="{ warn: row.warn }"
                >
                    <dt>{{ row.label }}</dt>
                    <dd>{{ row.value }}</dd>
                </div>
            </dl>
            <div v-if="card.status === 'PROPOSED'" class="actions">
                <el-button
                    type="primary"
                    native-type="button"
                    @click="emit('confirm')"
                    >{{ chatCopy.confirm }}</el-button
                >
                <el-button native-type="button" @click="revising = true">{{
                    chatCopy.revise
                }}</el-button>
                <el-button native-type="button" @click="beginEdit">{{
                    chatCopy.editFields
                }}</el-button>
                <el-button native-type="button" @click="emit('reject')">{{
                    chatCopy.reject
                }}</el-button>
            </div>
            <form v-if="revising" class="revise" @submit.prevent="submitRevise">
                <label class="sr-only" for="revise-hint">{{
                    chatCopy.reviseHint
                }}</label>
                <el-input
                    id="revise-hint"
                    v-model="instruction"
                    :placeholder="chatCopy.reviseHint"
                    @keydown.enter.exact.prevent="submitRevise"
                />
            </form>
        </template>
    </article>
</template>

<style scoped>
.proposal {
    padding: 24px;
    border: 1px solid var(--sb-line);
    border-radius: var(--sb-radius-lg);
    background: var(--sb-surface);
}
.proposal--folded {
    padding: 12px 16px;
    border: 0;
}
.folded {
    margin: 0;
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
.folded--ok {
    padding: 10px 12px;
    border-radius: var(--sb-radius);
    background: var(--sb-ok-bg);
    color: var(--sb-ok);
}
.folded--danger {
    padding: 10px 12px;
    border-radius: var(--sb-radius);
    background: var(--sb-danger-bg);
    color: var(--sb-danger);
}
header {
    display: flex;
    justify-content: space-between;
    margin-bottom: 12px;
    font-size: var(--sb-sm);
    color: var(--sb-ink-2);
}
dl {
    display: grid;
    gap: 8px;
    margin: 0;
}
dl div {
    display: flex;
    justify-content: space-between;
    gap: 16px;
}
dt {
    color: var(--sb-ink-2);
}
.warn dd {
    color: var(--sb-warn);
}
.actions,
.edit,
.revise {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 16px;
}
.edit {
    display: grid;
}
</style>
