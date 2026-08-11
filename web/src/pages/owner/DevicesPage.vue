<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import {
    deviceErrorMessage,
    devicesApi,
    type OwnerDevice,
    type PairingCode,
} from '../../api/devices'
import { projectsApi, type Project } from '../../api/projects'

const devices = ref<OwnerDevice[]>([])
const activeProjects = ref<Project[]>([])
const selectedProjectIds = ref<string[]>([])
const pairingCode = ref<PairingCode>()
const loading = ref(true)
const creating = ref(false)
const errorMessage = ref('')
let expiryTimer: ReturnType<typeof globalThis.setTimeout> | undefined
const timestampFormatter = new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'short',
    timeStyle: 'medium',
    timeZone: 'Asia/Shanghai',
})

function formatTimestamp(value: string | null): string {
    if (!value) return '暂无'
    const timestamp = new Date(value)
    return Number.isNaN(timestamp.getTime())
        ? '暂无'
        : timestampFormatter.format(timestamp)
}

function clearPairingCode(): void {
    pairingCode.value = undefined
    if (expiryTimer !== undefined) globalThis.clearTimeout(expiryTimer)
    expiryTimer = undefined
}

function scheduleExpiry(expiresAt: string): void {
    if (expiryTimer !== undefined) globalThis.clearTimeout(expiryTimer)
    const delay = Math.max(0, Date.parse(expiresAt) - Date.now())
    expiryTimer = globalThis.setTimeout(() => {
        clearPairingCode()
        errorMessage.value = '配对码已过期，请重新生成。'
    }, delay)
}

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        devices.value = await devicesApi.list()
    } catch {
        errorMessage.value = '设备列表暂时无法加载，请稍后重试。'
    }
    try {
        activeProjects.value = (await projectsApi.list()).filter(
            (project) => project.status === 'ACTIVE',
        )
    } catch {
        if (!errorMessage.value) {
            errorMessage.value = '项目列表暂时无法加载，请稍后重试。'
        }
    } finally {
        loading.value = false
    }
}

async function createPairingCode(): Promise<void> {
    if (creating.value) return
    if (selectedProjectIds.value.length < 1) {
        errorMessage.value = '请至少选择一个启用中的项目。'
        return
    }
    creating.value = true
    errorMessage.value = ''
    clearPairingCode()
    try {
        const created = await devicesApi.createPairingCode({
            project_ids: [...selectedProjectIds.value],
        })
        pairingCode.value = created
        scheduleExpiry(created.expires_at)
    } catch (error) {
        errorMessage.value = deviceErrorMessage(error)
    } finally {
        creating.value = false
    }
}

async function revoke(device: OwnerDevice): Promise<void> {
    if (!globalThis.confirm('确认撤销该设备吗？')) return
    errorMessage.value = ''
    try {
        await devicesApi.revoke(device.id)
        devices.value = await devicesApi.list()
    } catch (error) {
        errorMessage.value = deviceErrorMessage(error)
    }
}

onMounted(load)
onBeforeUnmount(clearPairingCode)
</script>

<template>
    <section class="devices-page" aria-labelledby="devices-title">
        <header>
            <p class="devices-page__eyebrow">OWNER</p>
            <h1 id="devices-title">设备管理</h1>
        </header>

        <el-card shadow="never">
            <h2>新设备配对</h2>
            <div class="project-options">
                <label v-for="project in activeProjects" :key="project.id">
                    <input
                        v-model="selectedProjectIds"
                        type="checkbox"
                        :value="project.id"
                        :disabled="creating"
                    />
                    {{ project.name }}
                </label>
            </div>
            <el-button
                type="primary"
                :loading="creating"
                :disabled="creating"
                @click="createPairingCode"
            >
                生成配对码
            </el-button>

            <div v-if="pairingCode" class="pairing-code">
                <p>一次性配对码</p>
                <code>{{ pairingCode.raw_code }}</code>
                <p>
                    有效期至
                    {{ formatTimestamp(pairingCode.expires_at) }}（约 10 分钟）
                </p>
                <el-button @click="clearPairingCode">我已安全保存</el-button>
            </div>
        </el-card>

        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>

        <div v-loading="loading" class="device-list" aria-live="polite">
            <el-card v-for="device in devices" :key="device.id" shadow="never">
                <div class="device-row">
                    <div>
                        <h2>{{ device.name }}</h2>
                        <p>
                            {{
                                device.status === 'ACTIVE' ? '已启用' : '已撤销'
                            }}
                        </p>
                        <p>首次配对：{{ formatTimestamp(device.paired_at) }}</p>
                        <p>
                            最近使用：{{ formatTimestamp(device.last_used_at) }}
                        </p>
                        <p v-if="device.revoked_at">
                            撤销时间：{{ formatTimestamp(device.revoked_at) }}
                        </p>
                        <p>
                            授权项目：{{
                                device.projects
                                    .map((project) => project.name)
                                    .join('、')
                            }}
                        </p>
                    </div>
                    <el-button
                        v-if="device.status === 'ACTIVE'"
                        type="danger"
                        plain
                        @click="revoke(device)"
                    >
                        撤销设备
                    </el-button>
                </div>
            </el-card>
        </div>
    </section>
</template>

<style scoped>
.devices-page,
.device-list,
.project-options {
    display: grid;
    gap: 1rem;
}

.devices-page__eyebrow,
.device-row p,
.pairing-code p {
    margin: 0.35rem 0;
    color: #606266;
}

.device-row {
    display: flex;
    gap: 1rem;
    align-items: flex-start;
    justify-content: space-between;
}

.pairing-code {
    padding: 1rem;
    margin-top: 1rem;
    background: #f5f7fa;
    border-radius: 8px;
}

.pairing-code code {
    display: block;
    margin: 0.75rem 0;
    font-size: 1.35rem;
    letter-spacing: 0.08em;
}
</style>
