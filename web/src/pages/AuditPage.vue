<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { auditApi, auditErrorMessage, type AuditEvent } from '../api/audit'
import { dateTimeShort } from '../api/parse'
import InlineError from '../components/ui/InlineError.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import {
    AUDIT_ACTION_LABEL,
    AUDIT_OUTCOME_LABEL,
    auditActionLabel,
    auditObjectLabel,
    isKnownAuditAction,
} from '../copy/audit'
import { auditPageCopy } from '../copy/pages/audit'

const events = ref<AuditEvent[]>([])
const action = ref('')
const errorMessage = ref('')
const loading = ref(true)
const actions = Object.keys(AUDIT_ACTION_LABEL)

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        events.value = await auditApi.list(50, action.value.trim() || undefined)
    } catch (error) {
        errorMessage.value = auditErrorMessage(error)
    } finally {
        loading.value = false
    }
}

onMounted(load)
</script>

<template>
    <section class="audit-page" aria-labelledby="audit-title">
        <PageHeader :title="auditPageCopy.title" heading-id="audit-title">
            <el-select
                v-model="action"
                clearable
                :placeholder="auditPageCopy.action"
                @change="load"
            >
                <el-option
                    v-for="item in actions"
                    :key="item"
                    :label="AUDIT_ACTION_LABEL[item]"
                    :value="item"
                />
            </el-select>
        </PageHeader>
        <InlineError :message="errorMessage" />
        <el-table v-loading="loading" :data="events" class="plain-table">
            <el-table-column :label="auditPageCopy.time" min-width="140">
                <template #default="{ row }">{{
                    dateTimeShort(row.created_at)
                }}</template>
            </el-table-column>
            <el-table-column :label="auditPageCopy.who" min-width="120">
                <template #default="{ row }">{{
                    row.actor_name || ''
                }}</template>
            </el-table-column>
            <el-table-column :label="auditPageCopy.action" min-width="160">
                <template #default="{ row }">
                    <span
                        :class="{ unknown: !isKnownAuditAction(row.action) }"
                        >{{ auditActionLabel(row.action) }}</span
                    >
                </template>
            </el-table-column>
            <el-table-column :label="auditPageCopy.object" min-width="120">
                <template #default="{ row }">{{
                    auditObjectLabel(row.object_type)
                }}</template>
            </el-table-column>
            <el-table-column :label="auditPageCopy.result" width="90">
                <template #default="{ row }">{{
                    AUDIT_OUTCOME_LABEL[
                        row.outcome as keyof typeof AUDIT_OUTCOME_LABEL
                    ] || row.outcome
                }}</template>
            </el-table-column>
        </el-table>
    </section>
</template>

<style scoped>
.unknown {
    color: var(--sb-ink-3);
}
</style>
