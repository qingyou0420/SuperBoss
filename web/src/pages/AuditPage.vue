<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { auditApi, auditErrorMessage, type AuditEvent } from '../api/audit'

const events = ref<AuditEvent[]>([])
const action = ref('')
const errorMessage = ref('')
const loading = ref(true)

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
        <h1 id="audit-title">审计</h1>
        <form class="filter" @submit.prevent="load">
            <label for="audit-action">动作</label>
            <el-input
                id="audit-action"
                v-model="action"
                placeholder="例如 finance.entry.create"
            />
            <el-button native-type="submit" type="primary">筛选</el-button>
        </form>
        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>
        <el-table v-loading="loading" :data="events" stripe>
            <el-table-column prop="created_at" label="时间" min-width="180" />
            <el-table-column prop="action" label="动作" min-width="180" />
            <el-table-column prop="outcome" label="结果" width="100" />
            <el-table-column prop="object_type" label="对象" width="140" />
            <el-table-column prop="actor_kind" label="主体" width="100" />
        </el-table>
    </section>
</template>

<style scoped>
.filter {
    display: flex;
    gap: 8px;
    align-items: end;
    margin-bottom: 1rem;
}
.filter label {
    font-weight: 600;
}
</style>
